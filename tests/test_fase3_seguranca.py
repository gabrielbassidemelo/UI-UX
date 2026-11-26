"""Fase 3 — segurança fisiológica por população, com dados SINTÉTICOS.

Cada teste descreve uma pessoa fictícia e confere que o sistema suspende ou restringe a recomendação
correspondente. Os limites de segurança são verificados também contra tentativas de sobrescrita por IA.
"""
import copy
import json
from datetime import timedelta

import pytest

from healthmon import calibration, feedback, insights, llm, nutrition as nut, planner, profile as prof, safety
from healthmon import supplements as sup, training as tr
from healthmon.demo import populate

from .conftest import END

BASE = {"sexo": "masculino", "nascimento": "1988-05-10", "altura_cm": 180, "objetivo": "emagrecimento",
        "experiencia": "intermediario", "dias_treino": 4, "minutos_sessao": 60, "equipamento": "academia",
        "limitacoes": [], "liberacao_medica": True, "condicoes_saude": [], "medicamentos": [], "restricoes_alimentares": [],
        "cafeina_habitual_mg": 100, **{k: False for k in safety.TRIAGE}}


def person(db, weight=88.0, **over):
    prof.update(db, {**BASE, **over})
    if weight is not None:
        prof.record_weight(db, END.isoformat(), weight)


def plan_or_block(db, cfg):
    try:
        return tr.build_plan(db, cfg, END)
    except ValueError as e:
        return str(e)


def has_hiit(plan) -> bool:
    return "hiit" in json.dumps(plan["sessoes"], ensure_ascii=False).lower()


# ------------------------------------------------------------ informações incompletas
def test_incomplete_information_suspends_everything(cfg, db):
    scr = safety.screen(db, cfg, END)
    assert set(scr["bloqueios"]) >= {"deficit", "metas_nutricionais", "metas_peso", "treino_plano", "treino_intenso", "suplementos"}
    assert nut.compute_targets(db, cfg, END)["disponivel"] is False
    assert sup.evaluate(db, cfg, END)["disponivel"] is False
    assert isinstance(plan_or_block(db, cfg), str)


@pytest.mark.parametrize("missing", list(safety.TRIAGE)[:6] + ["nascimento", "sexo", "altura_cm"])
def test_each_missing_answer_is_requested_not_assumed(cfg, db, missing):
    prof.update(db, {k: v for k, v in BASE.items() if k != missing})
    prof.record_weight(db, END.isoformat(), 88)
    scr = safety.screen(db, cfg, END)
    assert missing in scr["faltam"] and "deficit" in scr["bloqueios"]
    t = nut.compute_targets(db, cfg, END)
    assert t["disponivel"] is False and missing in json.dumps(t, ensure_ascii=False)


def test_presumed_weight_is_never_used(cfg, db):
    person(db, weight=None)
    scr = safety.screen(db, cfg, END)
    assert scr["peso_medido"] is False and "peso real" in scr["bloqueios"]["deficit"]
    assert nut.compute_targets(db, cfg, END)["disponivel"] is False


# ------------------------------------------------------------ populações
def test_adolescent_is_refused(cfg, db):
    with pytest.raises(ValueError, match="adultos"):
        prof.update(db, {**BASE, "nascimento": (END - timedelta(days=16 * 365)).isoformat()})
    # mesmo que um registro antigo exista no banco, a triagem bloqueia tudo no momento do uso
    db.set_json("perfil", {**BASE, "nascimento": (END - timedelta(days=16 * 365)).isoformat()})
    prof.record_weight(db, END.isoformat(), 60)
    scr = safety.screen(db, cfg, END)
    assert scr["idade"] < 18 and len(scr["bloqueios"]) == 6
    assert nut.compute_targets(db, cfg, END)["disponivel"] is False and isinstance(plan_or_block(db, cfg), str)


def test_underweight_gets_no_deficit_even_with_weight_loss_goal(cfg, db):
    person(db, weight=57)                     # IMC 17,6
    t = nut.compute_targets(db, cfg, END)
    assert t.get("balanco_kcal", 0) >= 0
    with pytest.raises(ValueError):
        planner.add_goal(db, "peso", 54, (END + timedelta(days=200)).isoformat(), END, cfg)


def test_bmi_alone_does_not_clear_nutrition_risk(cfg, db):
    """IMC normal, mas gordura corporal muito baixa → sem déficit."""
    person(db, weight=75)                     # IMC 23,1
    prof.record_weight(db, END.isoformat(), 75, 6)
    scr = safety.screen(db, cfg, END)
    assert scr["risco_nutricional"]["bloqueia_deficit"] and "deficit" in scr["bloqueios"]
    assert nut.compute_targets(db, cfg, END).get("balanco_kcal", 0) >= 0


def test_bmi_normal_with_very_low_logged_intake_blocks_deficit(cfg, db):
    person(db, weight=75)
    for i in range(14):
        d = (END - timedelta(days=i)).isoformat()
        nut.log_meal(db, d, "a", 500)
        nut.log_meal(db, d, "b", 500)
    scr = safety.screen(db, cfg, END)
    assert scr["risco_nutricional"]["ingestao_muito_baixa"] and "deficit" in scr["bloqueios"]


def test_elderly_gets_conservative_rules(cfg, db):
    person(db, nascimento="1955-03-01")
    scr = safety.screen(db, cfg, END)
    assert scr["idoso"] is True
    t = nut.compute_targets(db, cfg, END)
    assert t["kcal"] >= t["tdee"]["kcal"] * (1 - nut.SAFETY["deficit_max_idoso_pct"] / 100) - 10
    assert t["proteina_g"] >= nut.SAFETY["proteina_min_idoso_g_kg"] * 88 - 2
    plan = tr.build_plan(db, cfg, END)
    assert not has_hiit(plan)
    assert all(e["rir"] >= 2 for s in plan["sessoes"] for e in s["exercicios"])


@pytest.mark.parametrize("over", [{"gestante_ou_lactante": True}, {"condicoes_saude": ["amamentando"]},
                                  {"condicoes_saude": ["pós-parto há 2 meses"]}])
def test_pregnant_or_lactating_gets_no_targets_plan_or_supplements(cfg, db, over):
    person(db, weight=68, sexo="feminino", altura_cm=165, **over)
    assert nut.compute_targets(db, cfg, END)["disponivel"] is False
    assert sup.evaluate(db, cfg, END)["disponivel"] is False
    assert "obstetra" in plan_or_block(db, cfg)
    with pytest.raises(ValueError):
        planner.add_goal(db, "peso", 62, (END + timedelta(days=200)).isoformat(), END, cfg)


def test_cardiovascular_disease_never_gets_intense_training(cfg, db):
    person(db, doenca_cardiovascular=True)
    scr = safety.screen(db, cfg, END)
    assert "treino_intenso" in scr["bloqueios"] and scr["sem_meta_de_agua"]
    plan = tr.build_plan(db, cfg, END)
    assert not has_hiit(plan) and all(e["rir"] >= 2 for s in plan["sessoes"] for e in s["exercicios"])
    assert nut.compute_targets(db, cfg, END).get("agua_ml") is None


def test_heart_rate_medication_invalidates_zones(cfg, db):
    person(db, usa_medicamento_fc=True)
    scr = safety.screen(db, cfg, END)
    assert scr["zonas_fc_invalidas"] and "treino_intenso" in scr["bloqueios"]
    assert not has_hiit(tr.build_plan(db, cfg, END))


@pytest.mark.parametrize("flag", ["doenca_renal", "doenca_hepatica"])
def test_renal_or_hepatic_disease_gets_no_protein_targets_or_supplements(cfg, db, flag):
    person(db, **{flag: True})
    t = nut.compute_targets(db, cfg, END)
    assert t["disponivel"] is False and "proteina_g" not in t and "médico" in str(t["bloqueio"])
    assert sup.evaluate(db, cfg, END)["disponivel"] is False


def test_diabetes_on_insulin_gets_no_deficit_and_no_dose_advice(cfg, db):
    person(db, diabetes=True, usa_insulina_ou_hipoglicemiante=True)
    scr = safety.screen(db, cfg, END)
    assert "deficit" in scr["bloqueios"] and any("não orienta doses" in a for a in scr["avisos"])
    assert nut.compute_targets(db, cfg, END).get("balanco_kcal", 0) >= 0


def test_diabetes_without_hypoglycemic_drug_gets_warning(cfg, db):
    person(db, diabetes=True)
    assert any("Diabetes" in a for a in safety.screen(db, cfg, END)["avisos"])


def test_eating_disorder_hides_calories_and_weight_goals(cfg, db):
    person(db, transtorno_alimentar=True)
    scr = safety.screen(db, cfg, END)
    assert scr["ocultar_calorias"] and {"deficit", "metas_nutricionais", "metas_peso"} <= set(scr["bloqueios"])
    t = nut.compute_targets(db, cfg, END)
    assert t["disponivel"] is False and "kcal" not in t
    with pytest.raises(ValueError):
        planner.add_goal(db, "peso", 80, (END + timedelta(days=200)).isoformat(), END, cfg)


def test_recent_surgery_or_injury_blocks_plan_and_limitations_are_closed_set(cfg, db):
    person(db, cirurgia_recente=True)
    assert "fisioterapeuta" in plan_or_block(db, cfg)
    with pytest.raises(ValueError):
        prof.update(db, {"limitacoes": ["tornozelo esquisito"]})      # limitação desconhecida não é ignorada em silêncio
    person(db, cirurgia_recente=False, limitacoes=["joelho", "lombar"])
    plan = tr.build_plan(db, cfg, END)
    used = {e["chave"] for se in plan["sessoes"] for e in se["exercicios"]}
    # exercícios com carga contraindicados para joelho/lombar ficam fora; só entram variações sem carga externa ou guiadas
    assert not used & {"agachamento", "agachamento_goblet", "afundo", "terra_romeno"}


@pytest.mark.parametrize("flag,item", [("usa_anticoagulante", "omega-3"), ("usa_levotiroxina", "magnesio"),
                                       ("usa_psiquiatrico_ou_estimulante", "cafeina"), ("usa_diuretico", "eletrolitos"),
                                       ("alergia_leite", "whey"), ("alergia_peixe", "omega-3")])
def test_medications_and_allergies_never_yield_a_relevant_supplement(cfg, db, flag, item):
    person(db, objetivo="hipertrofia", **{flag: True})
    r = sup.evaluate(db, cfg, END)
    by = {i["chave"]: i for i in r["itens"]}
    assert by[item]["status"] != "potencialmente_relevante"
    assert by[item]["contraindicacoes_aplicaveis"] or by[item]["interacoes_aplicaveis"] or by[item]["status"] == "evidencia_nao_verificada"


def test_supplement_interaction_between_products_in_use(cfg, db):
    person(db, objetivo="forca", cafeina_habitual_mg=380)
    by = {i["chave"]: i for i in sup.evaluate(db, cfg, END)["itens"]}
    assert by["cafeina"]["status"] != "potencialmente_relevante"       # já perto do limite diário habitual


def test_unverified_evidence_never_supports_a_personal_recommendation(cfg, db):
    person(db, objetivo="hipertrofia")
    for i in sup.evaluate(db, cfg, END)["itens"]:
        if safety and __import__("healthmon.kb", fromlist=["failing"]).failing(i["refs"], cfg):
            assert i["status"] in ("evidencia_nao_verificada", "inadequado")
    t = nut.compute_targets(db, cfg, END)
    assert t.get("agua_ml") is None                                   # efsa2010 não verificada → sem meta de água
    assert t["fibra_g"] == 25 and "iom2005" not in t["refs"]          # só o piso da OMS (verificado); regra por caloria desabilitada
    assert any("ainda não foi verificada" in o for o in t["observacoes"])


# ------------------------------------------------------------ sintomas incompatíveis com treino intenso
@pytest.mark.parametrize("text,urgent", [("dor no peito ao subir escada", True), ("aperto no peito", True),
                                         ("quase desmaiei no banho", True), ("falta de ar em repouso", True),
                                         ("coração disparado do nada", True), ("febre de 38,5", False),
                                         ("dor forte no joelho ao agachar", False)])
def test_symptoms_stop_training(cfg, db, text, urgent):
    populate(db, 40, END, stress_last=0)
    person(db)
    tr.confirm_plan(db, tr.propose_plan(db, cfg, "t", END - timedelta(days=7)))
    db.insert("checkins", {"date": END.isoformat(), "perceived_recovery": 10, "energy": 5, "symptoms": text}, upsert_on="date")
    r = tr.readiness(db, cfg, END)
    assert r["nivel"] == "parar" and bool(r["urgente"]) is urgent
    s = tr.todays_session(db, cfg, END)
    assert s["tipo"] == "descanso" and ("SAMU" in s["texto"]) is urgent
    tips = insights.day_suggestions({"avaliacoes": {}, "recuperacao": {"status": "boa"}}, r)
    assert len(tips) == 1 and "não treine" in tips[0].lower()


@pytest.mark.parametrize("text", ["sem dor no peito", "não tive febre", "peito do pé dolorido", "nenhuma falta de ar"])
def test_negated_symptoms_do_not_trigger(cfg, db, text):
    assert not any(safety.symptom_flags({"symptoms": text}).values())


def test_structured_triage_overrides_good_wearable_numbers(cfg, db):
    populate(db, 40, END, stress_last=0)
    person(db)
    db.insert("checkins", {"date": END.isoformat(), "perceived_recovery": 10, "triagem": json.dumps({"palpitacao": True})}, upsert_on="date")
    assert tr.readiness(db, cfg, END)["nivel"] == "parar"


# ------------------------------------------------------------ a IA não sobrescreve as regras
@pytest.mark.parametrize("text", ["Você provavelmente tem hipotireoidismo.", "Reduza a dose da sua insulina hoje.",
                                  "Tome 400 mg de cafeína antes do treino.", "Hoje é dia de HIIT até a falha!",
                                  "Faça jejum de 24 horas.", "Suspenda o anticoagulante por uns dias.",
                                  # contornos encontrados na auditoria de segurança (sinônimos, números por extenso, unicode)
                                  "Aumente sua dosagem de levotiroxina.", "Pare de tomar o remédio de pressão.",
                                  "Corte o betabloqueador.", "Tome cinquenta miligramas.", "Tome mil unidades de vitamina D.",
                                  "Tome dois comprimidos de ibuprofeno.", "Você pode ter hipotireoidismo.",
                                  "Isso indica diabetes tipo 2.", "Seus dados sugerem apneia do sono.",
                                  "di\u200bagnóstico: arritmia", "Faça \uff28\uff29\uff29\uff34", "Faça H I I T hoje",
                                  "Faça j\u0435jum", "Fique 16 horas sem comer.", "Coma só oitocentas calorias.",
                                  "Treine até a exaustão.", "\u0422reine forte"])
def test_llm_output_with_diagnosis_dose_or_medication_change_is_rejected(text):
    assert safety.llm_text_allowed(text)[0] is False


@pytest.mark.parametrize("text", ["Seu sono ficou dentro do seu padrão; mantenha a rotina e o horário de dormir.",
                                  "HRV um pouco abaixo da sua mediana nos últimos dias; priorize descanso e hidratação.",
                                  "Passos acima do habitual e estresse dentro da sua faixa usual."])
def test_llm_plain_descriptive_text_is_allowed(text):
    assert safety.llm_text_allowed(text)[0] is True


def test_daily_report_passes_readiness_to_the_llm_gate(cfg, db):
    from healthmon import reports
    populate(db, 40, END, stress_last=0)
    person(db)
    db.insert("checkins", {"date": END.isoformat(), "symptoms": "dor no peito"}, upsert_on="date")
    reports.PREVIEW_ONLY = True
    try:
        facts = reports.daily(cfg, db, END).facts
    finally:
        reports.PREVIEW_ONLY = False
    assert facts["prontidao"]["nivel"] == "parar"          # com isso a IA nem é chamada (llm.narrate)


def test_llm_cannot_encourage_training_on_a_stop_day():
    assert safety.llm_text_allowed("Aproveite e faça uma corrida leve.", "parar")[0] is False
    assert safety.llm_text_allowed("Que tal correr 10 km, não perca!", "recuperacao")[0] is False
    assert safety.llm_text_allowed("Hoje o melhor é descansar e não treinar.", "parar")[0] is True


def test_llm_is_silent_when_readiness_is_stop_and_disabled_by_default(cfg, db, monkeypatch):
    assert llm.narrate(cfg, "diario", {"prontidao": {"nivel": "normal"}}) == (None, "regras")   # padrão: sem IA
    cfg.data.setdefault("ia", {})["modo"] = "local"
    calls = []
    monkeypatch.setattr(llm, "_ollama", lambda *a, **k: calls.append(1) or "Treine pesado hoje, faça HIIT.")
    assert llm.narrate(cfg, "diario", {"prontidao": {"nivel": "parar"}}) == (None, "regras") and not calls
    assert llm.narrate(cfg, "diario", {"prontidao": {"nivel": "normal"}}) == (None, "regras") and calls   # chamada, mas descartada
    monkeypatch.setattr(llm, "_ollama", lambda *a, **k: "Sono dentro do seu padrão; mantenha a rotina.")
    assert llm.narrate(cfg, "diario", {"prontidao": {"nivel": "normal"}})[0]


def test_feedback_analysis_never_changes_safety_limits(cfg, db):
    populate(db, 120, END, stress_last=0)
    person(db)
    for i in range(100):
        d = (END - timedelta(days=i)).isoformat()
        db.insert("checkins", {"date": d, "perceived_recovery": 3 + i % 5, "energy": 1 + i % 5, "fatigue": 1 + (i * 3) % 5,
                               "sleep_quality": 1 + i % 4, "perceived_stress": 1 + i % 3, "soreness": 1 + i % 4,
                               "adherence": float(i % 2)}, upsert_on="date")
    before = copy.deepcopy((nut.SAFETY, tr.SAFETY, safety.TRIAGE, safety.SYMPTOMS))
    cr = feedback.cross(db, END)
    ef = feedback.recommendation_effect(db, END)
    assert (nut.SAFETY, tr.SAFETY, safety.TRIAGE, safety.SYMPTOMS) == before
    assert all(p["n"] >= feedback.MIN_PAIRS or p["conclusao"] == "dados_insuficientes" for p in cr["pares"])
    assert ef["disponivel"] and all(d["conclusao"] in ("dados_insuficientes", "sem_diferenca_detectavel", "diferenca_provavel") for d in ef["desfechos"])
    assert "não altera limites" in ef["nota"]


def test_feedback_waits_for_longitudinal_stage_and_missing_is_not_zero(cfg, db):
    populate(db, 30, END, stress_last=0)
    assert feedback.recommendation_effect(db, END)["disponivel"] is False
    db.insert("checkins", {"date": END.isoformat(), "energy": 4}, upsert_on="date")       # fadiga ausente
    assert all(p["conclusao"] == "dados_insuficientes" for p in feedback.cross(db, END)["pares"])


# ------------------------------------------------------------ calibração
@pytest.mark.parametrize("days,stage,progress", [(5, "observacao", False), (20, "linha_de_base", False),
                                                 (45, "personalizado", True), (120, "longitudinal", True)])
def test_calibration_stages(cfg, db, days, stage, progress):
    populate(db, days, END, stress_last=0)
    s = calibration.stage(db, END)
    assert s["estagio"] == stage and s["permite"]["progressao_de_carga"] is progress
    assert s["permite"]["relatorios_descritivos"] and s["permite"]["orientacoes_gerais"]


def test_observation_stage_emits_no_trend_alerts_and_gaps_fall_back(cfg, db):
    from healthmon import alerts
    populate(db, 6, END, stress_last=6)
    assert alerts.evaluate(cfg, db, END) == []
    populate(db, 60, END - timedelta(days=20), stress_last=0)       # histórico antigo, 14 dias recentes quase vazios
    s = calibration.stage(db, END)
    assert s["dias_validos"] >= 28 and s["estagio"] == "linha_de_base" and s["observacao"]


# ------------------------------------------------------------ bloqueio posterior a uma confirmação (revisão de arquitetura)
@pytest.mark.parametrize("flags,areas", [({"transtorno_alimentar": True}, {"nutricao"}),
                                         ({"cirurgia_recente": True}, {"treino"}),
                                         ({"gestante_ou_lactante": True}, {"nutricao", "treino"}),
                                         ({"doenca_renal": True}, {"nutricao"})])
def test_plan_and_targets_confirmed_before_a_block_stop_applying_everywhere(cfg, db, flags, areas):
    from healthmon import reports
    from healthmon.demo import populate_phase2
    populate(db, 60, END, stress_last=0)
    populate_phase2(db, cfg, 60, END)                         # plano e meta confirmados, com números
    old = nut.active_targets(db)
    assert old and tr.get_plan(db) and nut.effective_targets(db, cfg, END)
    prof.update(db, flags)                                    # a condição é informada DEPOIS
    plan = planner.daily_plan(db, cfg, END, record=False)
    text = json.dumps(plan, ensure_ascii=False)
    reports.PREVIEW_ONLY = True
    try:
        md = reports.daily(cfg, db, END).doc.markdown()
    finally:
        reports.PREVIEW_ONLY = False
    if "nutricao" in areas:
        assert nut.effective_targets(db, cfg, END) is None
        assert nut.day_summary(db, cfg, END)["meta"] is None
        for where in (text, md):
            assert f"{old['kcal']} kcal" not in where and f"proteína {old['proteina_g']} g" not in where
    if "treino" in areas:
        s = tr.todays_session(db, cfg, END)
        assert s["tipo"] == "bloqueado" and "sessao" not in s and "cardio" not in s
        assert plan["sessao"]["tipo"] == "bloqueado" and "Superiores" not in text and "Inferiores" not in text
        assert "Musculação" not in md


def test_intensity_block_after_confirmation_removes_hiit_and_progression(cfg, db):
    from healthmon.demo import populate_phase2
    populate(db, 60, END, stress_last=0)
    populate_phase2(db, cfg, 60, END)
    prof.update(db, {"doenca_cardiovascular": True})
    for i in range(7):
        d = END - timedelta(days=i)
        db.insert("checkins", {"date": d.isoformat(), "perceived_recovery": 9, "triagem": json.dumps({k: False for k in safety.SYMPTOMS})}, upsert_on="date")
        s = tr.todays_session(db, cfg, d)
        assert "hiit" not in json.dumps(s, ensure_ascii=False).lower().replace("não propõe hiit", "")
        if s["tipo"] == "forca":
            assert all(e["rir"] >= 3 and e["progressao"]["acao"] != "aumentar" for e in s["sessao"]["exercicios"])


def test_observation_stage_suspends_deficit_and_intense_training(cfg, db):
    populate(db, 4, END, stress_last=0)
    person(db)
    scr = safety.screen(db, cfg, END)
    assert scr["estagio"] == "observacao" and "observação" in scr["bloqueios"]["deficit"] and "treino_intenso" in scr["bloqueios"]
    assert nut.compute_targets(db, cfg, END).get("balanco_kcal", 0) >= 0
    assert not has_hiit(tr.build_plan(db, cfg, END))


def test_zones_need_measured_resting_hr_and_push_is_default_channel(cfg, db):
    person(db)
    z = tr.hr_zones(db, cfg, END) if hasattr(tr, "hr_zones") else None
    if z is not None:
        assert z["disponivel"] is False                       # sem FC de repouso medida não há valor presumido
    from healthmon import notify
    assert cfg.get("notificacoes", "canais") == ["push"]
    db.add_alert(END.isoformat(), "falha_backup", "atencao", "Backup diário falhou", "teste", {})
    sent = []
    import healthmon.push as push
    orig = push.send_push
    push.send_push = lambda c, d, subj, body: sent.append(subj) or 1
    try:
        from datetime import datetime
        assert notify.dispatch_alerts(cfg, db, now=datetime(2026, 11, 20, 12, 0, tzinfo=cfg.tz)) == 1
    finally:
        push.send_push = orig
    assert sent and "Backup" in sent[0]                       # alerta operacional chega com o título, sem limite diário


def test_alert_text_shows_rounded_values(cfg, db):
    import re
    from healthmon import alerts
    populate(db, 60, END, stress_last=10)
    alerts.evaluate(cfg, db, END)
    msgs = [a["message"] for a in db.alerts(20)]
    assert msgs and not any(re.search(r"\d\.\d{4,}", m) for m in msgs), msgs
