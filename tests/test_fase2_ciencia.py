"""Testes científicos e de limites de segurança (dados SINTÉTICOS)."""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

import healthmon
from healthmon import analysis as an
from healthmon import kb, labs, learning, metabolism as met, nutrition as nut, planner, profile as prof
from healthmon import stats, supplements as sup, training as tr
from healthmon.demo import populate, populate_phase2

from .conftest import END

BASE = {"sexo": "masculino", "nascimento": "1988-05-10", "altura_cm": 180, "objetivo": "emagrecimento",
        "experiencia": "intermediario", "dias_treino": 4, "minutos_sessao": 60, "equipamento": "academia",
        "limitacoes": [], "liberacao_medica": True, "condicoes_saude": [], "medicamentos": [], "restricoes_alimentares": []}


@pytest.fixture
def full(cfg, db):
    populate(db, 120, END)
    populate_phase2(db, cfg, 60, END)
    return db


# ------------------------------------------------------------------ fórmulas
def test_mifflin_and_cunningham_reference_values():
    assert met.mifflin("masculino", 88, 180, 38) == pytest.approx(10 * 88 + 6.25 * 180 - 5 * 38 + 5)
    assert met.mifflin("feminino", 60, 165, 30) == pytest.approx(600 + 1031.25 - 150 - 161)
    assert met.cunningham(70) == 2040
    assert tr.e1rm(100, 10) == pytest.approx(133.33, abs=0.01)


def test_energy_density_is_a_range_not_7700(cfg, db):
    d = met.energy_density(db, 88, END)
    assert d["lo"] < 7700 < d["hi"] == met.KCAL_FAT
    prof.record_weight(db, END.isoformat(), 88, 25)
    d2 = met.energy_density(db, 88, END)
    fm = 22.0
    assert d2["central"] == pytest.approx(9400 * (1 - 10.4 / (10.4 + fm)) + 1800 * 10.4 / (10.4 + fm), abs=1)


def test_bmr_requires_profile_and_reports_interval(cfg, db):
    assert met.bmr(db, cfg, END)["disponivel"] is False
    prof.update(db, BASE)
    b = met.bmr(db, cfg, END)
    assert b["intervalo"][0] < b["mifflin"] < b["intervalo"][1] and "cunningham" not in b
    assert "calorimetria" in b["nota"]


def test_adaptive_tdee_recovers_known_truth(cfg, db):
    """Peso caindo 0,5 kg/semana com ingestão de 2.000 kcal → gasto verdadeiro ≈ 2.000 + 0,5×ρ/7."""
    prof.update(db, BASE)
    for i in range(42):
        d = (END - timedelta(days=41 - i)).isoformat()
        prof.record_weight(db, d, 90 - 0.5 * i / 7 + (0.3 if i % 2 else -0.3))
        nut.log_meal(db, d, "a", 1000)
        nut.log_meal(db, d, "b", 1000)
    t = met.tdee(db, cfg, END, 42)
    a = t["vias"]["adaptativa"]
    assert a["kcal"] == pytest.approx(2000 + 0.5 * 7700 / 7, abs=60)
    assert a["intervalo"][0] < a["kcal"] < a["intervalo"][1]
    assert a["intervalo"][1] - a["intervalo"][0] > 300        # incerteza explícita, não um número só
    assert t["estimativa"]["origem"].startswith("adaptativa")


def test_adaptive_tdee_refuses_insufficient_data(cfg, db):
    prof.update(db, BASE)
    for i in range(6):
        prof.record_weight(db, (END - timedelta(days=i)).isoformat(), 88)
    t = met.tdee(db, cfg, END)
    assert "adaptativa" not in t["vias"] and "adaptativa_indisponivel" in t
    assert t["estimativa"]["confianca"] == "baixa"


def test_adaptation_flag_is_hypothesis_with_alternative(cfg, db):
    prof.update(db, BASE)
    for i in range(42):
        d = (END - timedelta(days=41 - i)).isoformat()
        prof.record_weight(db, d, 90 - 0.2 * i / 7)
        nut.log_meal(db, d, "a", 700)
        nut.log_meal(db, d, "b", 700)
    t = met.tdee(db, cfg, END, 42)
    assert t["adaptacao"]["hipotese"] and "registro alimentar incompleto" in t["adaptacao"]["texto"]


# ------------------------------------------------------------- nutrição: limites
def test_deficit_floor_and_caps(cfg, db):
    prof.update(db, {**BASE, "sexo": "feminino", "altura_cm": 155})
    prof.record_weight(db, END.isoformat(), 45)
    t = nut.compute_targets(db, cfg, END)
    assert t["kcal"] >= nut.SAFETY["piso_kcal"]["feminino"]
    assert t["kcal"] >= t["tdee"]["kcal"] * 0.75 - 10
    assert t["gordura_g"] >= 0.6 * 45 - 1 and t["proteina_g"] <= 2.5 * 45 + 1


def test_fast_loss_reduces_deficit(cfg, db):
    prof.update(db, BASE)
    for i in range(28):
        prof.record_weight(db, (END - timedelta(days=27 - i)).isoformat(), 90 - 1.4 * i / 7)
    t = nut.compute_targets(db, cfg, END)
    assert t["kcal"] == pytest.approx(t["tdee"]["kcal"] * 0.9, abs=10)
    assert any("acima do limite de 1%" in o for o in t["observacoes"])


def test_surplus_capped_and_targets_need_confirmation(cfg, db):
    prof.update(db, {**BASE, "objetivo": "hipertrofia", "experiencia": "iniciante"})
    t = nut.compute_targets(db, cfg, END)
    assert 0 < t["balanco_kcal"] <= nut.SAFETY["superavit_max_kcal"] + 10
    assert nut.active_targets(db) is None                     # nada vale sem confirmação
    assert nut.review_targets(db, cfg, END)["estado"] == "proposta"
    nut.confirm_targets(db, cfg, END)
    assert nut.review_targets(db, cfg, END)["estado"] == "em_dia"


def test_weight_goal_rejects_unsafe_rate(cfg, db):
    prof.record_weight(db, END.isoformat(), 90)
    with pytest.raises(ValueError, match="1% do peso"):
        planner.add_goal(db, "peso", 78, (END + timedelta(days=28)).isoformat(), END)
    assert planner.add_goal(db, "peso", 86, (END + timedelta(days=84)).isoformat(), END)["id"] == 1


def test_meal_text_parsing_and_food_validation(cfg, db):
    nut.add_food(db, "Arroz, tipo 1, cozido", "T", 128, 2.5, 28.1, 0.2, 1.6)
    nut.add_food(db, "Banana, prata, crua", "T", 98, 1.3, 26, 0.1, 2, portion_g=90)
    p = nut.parse_meal_text(db, "200 g arroz cozido, 2 banana, comida inexistente xyz")
    assert [i["gramas"] for i in p["itens"]] == [200, 180]
    assert p["total"]["kcal"] == 256 + 176 and p["nao_encontrados"] == ["comida inexistente xyz"]
    assert p["itens"][1]["incerteza_pct"] > p["itens"][0]["incerteza_pct"]
    with pytest.raises(ValueError):
        nut.add_food(db, "x", "T", 5000, 0, 0, 0)
    with pytest.raises(ValueError):
        nut.log_meal(db, END.isoformat(), "x", 99999)


def test_food_csv_import_taco_format(cfg, db, tmp_path):
    f = tmp_path / "taco.csv"
    f.write_text('"Número do Alimento","Categoria do alimento","Descrição dos alimentos","Energia..kcal.","Proteína..g.",'
                 '"Lipídeos..g.","Carboidrato..g.","Fibra.Alimentar..g."\n"1","Cereais","Alimento sintético A","100","2.5","Tr","20","NA"\n'
                 '"2","Cereais","Sem energia","NA","1","1","1","1"\n', encoding="utf-8")
    assert nut.import_foods_csv(db, f, "TESTE") == {"importados": 1, "ignorados": 1}
    r = nut.search_foods(db, "sintetico")[0]
    assert r["fat_g"] == 0 and r["fiber_g"] is None and r["kcal"] == 100


def test_lp_meal_suggestion_hits_targets(cfg, db):
    ids = [nut.add_food(db, n, "T", k, p, c, f) for n, k, p, c, f in
           [("Arroz", 128, 2.5, 28, 0.2), ("Frango", 159, 32, 0, 2.5), ("Azeite", 884, 0, 0, 100), ("Feijão", 76, 4.8, 13.6, 0.5),
            ("Aveia", 394, 14, 67, 8.5), ("Ovo", 146, 13, 0.6, 9.5)]]
    r = nut.suggest_day(db, {"kcal": 2200, "proteina_g": 170, "carboidrato_g": 230, "gordura_g": 65}, ids, max_g=600)
    assert r["disponivel"] and abs(r["total"]["kcal"] - 2200) < 120 and abs(r["total"]["protein_g"] - 170) < 15
    assert all(i["gramas"] <= 600 for i in r["itens"])


# ---------------------------------------------------------------- treino: limites
@pytest.mark.parametrize("exp,days", [("iniciante", 7), ("intermediario", 7), ("avancado", 7), ("iniciante", 2)])
def test_plan_respects_safety_caps(cfg, db, exp, days):
    prof.update(db, {**BASE, "experiencia": exp, "dias_treino": days, "minutos_sessao": 150})
    p = tr.build_plan(db, cfg, END)
    assert p["dias_forca"] <= tr.SAFETY["max_dias_forca"][exp] and p["dias_forca"] <= 6     # ≥1 dia de descanso
    assert max(p["series_semanais_por_grupo"].values()) <= tr.SAFETY["max_series_grupo_semana"][exp]
    assert all(s["duracao_min"] <= tr.SAFETY["max_minutos_sessao"] for s in p["sessoes"])
    assert sum(c["tipo"] == "HIIT" for c in p["cardio"]) <= tr.SAFETY["max_hiit_semana"]
    for s in p["sessoes"]:
        for e in s["exercicios"]:
            assert e["series"] >= 1 and e["reps"][0] < e["reps"][1] and e["descanso_s"] >= 60 and e["tecnica"]


def test_plan_respects_limitations_and_clearance(cfg, db):
    prof.update(db, {**BASE, "limitacoes": ["ombro", "joelho", "lombar"], "liberacao_medica": False, "objetivo": "condicionamento"})
    p = tr.build_plan(db, cfg, END)
    used = {e["chave"] for s in p["sessoes"] for e in s["exercicios"]}
    for k in used:
        assert not (tr.EX[k][4] & {"ombro", "joelho", "lombar"}), k
    assert all(c["tipo"] != "HIIT" for c in p["cardio"])
    assert all(e["rir"] >= 3 for s in p["sessoes"] for e in s["exercicios"])
    assert any("liberação médica" in o for o in p["observacoes"])


def test_plan_requires_profile_and_confirmation(cfg, db):
    with pytest.raises(ValueError):
        tr.build_plan(db, cfg)
    prof.update(db, BASE)
    pid = tr.propose_plan(db, cfg, "teste", END)
    assert tr.get_plan(db) is None and tr.get_plan(db, "proposto")["id"] == pid
    tr.confirm_plan(db, pid)
    assert tr.get_plan(db)["id"] == pid
    with pytest.raises(ValueError):
        tr.confirm_plan(db, pid)


def test_progression_rules_and_load_cap(cfg, db):
    d = END.isoformat()
    for _ in range(3):
        tr.log_set(db, d, "supino", 10, 80, 2)
    up = tr.progression(db, "supino", [6, 10], 2)
    assert up["acao"] == "aumentar" and 80 < up["carga_kg"] <= 80 * 1.10
    for _ in range(3):
        tr.log_set(db, d, "agachamento", 4, 100, 0)
    assert tr.progression(db, "agachamento", [6, 10], 2)["acao"] == "reduzir"
    for r in (8, 8, 7):
        tr.log_set(db, d, "puxada", r, 50, 2)
    assert tr.progression(db, "puxada", [6, 10], 2)["acao"] == "manter"
    with pytest.raises(ValueError):
        tr.log_set(db, d, "supino", 10, 5000)


def _flat(db, days=40):
    """Série sintética estável e determinística (pequena oscilação), sem nenhum indicador fora do padrão."""
    for i in range(days):
        w = (i % 3) - 1
        db.upsert_daily((END - timedelta(days=days - 1 - i)).isoformat(), {
            "hrv_last_night": 50 + 2 * w, "resting_hr": 54 + w, "sleep_score": 78 + 2 * w, "sleep_seconds": 26000 + 600 * w,
            "bb_max": 85 + 2 * w, "sleep_stress_avg": 15 + w, "stress_avg": 28 + w, "steps": 8000 + 300 * w})


def test_single_metric_never_changes_training(cfg, db):
    _flat(db)
    assert tr.readiness(db, cfg, END)["pontos"] == 0
    db.upsert_daily(END.isoformat(), {"hrv_last_night": 30})   # só a HRV despenca (−40%)
    r = tr.readiness(db, cfg, END)
    assert r["nivel"] == "normal"
    assert any("isoladamente não altera" in s for s in r["sinais"])


def test_subjective_and_objective_combine(cfg, db):
    populate(db, 60, END, stress_last=6)                       # vários indicadores ruins
    assert tr.readiness(db, cfg, END)["nivel"] in ("reduzido", "recuperacao")
    db.insert("checkins", {"date": END.isoformat(), "perceived_recovery": 3, "soreness": 5}, upsert_on="date")
    r = tr.readiness(db, cfg, END)
    assert r["nivel"] == "recuperacao" and r["confianca"] == "alta"


def test_red_flag_symptoms_stop_training(cfg, db):
    _flat(db)
    prof.update(db, BASE)
    tr.confirm_plan(db, tr.propose_plan(db, cfg, "t", END - timedelta(days=7)))
    db.insert("checkins", {"date": END.isoformat(), "perceived_recovery": 9, "symptoms": "dor no peito ao subir escada"}, upsert_on="date")
    s = tr.todays_session(db, cfg, END)
    assert s["tipo"] == "descanso" and "avaliação profissional" in s["texto"]


def test_poor_recovery_blocks_progression_and_hiit(cfg, full):
    db = full
    day = END
    while day.weekday() not in tr.get_plan(db)["dias_semana"]:
        day -= timedelta(days=1)
    s = tr.todays_session(db, cfg, day)
    if s["tipo"] == "forca":
        assert all(e["progressao"]["acao"] != "aumentar" for e in s["sessao"]["exercicios"])
    else:
        assert s["tipo"] == "recuperacao"


# ----------------------------------------------------------------- suplementos
def test_supplements_require_health_profile(cfg, db):
    r = sup.evaluate(db, cfg, END)
    assert r["disponivel"] is False and "medicamentos" in r["faltam"]


def test_supplements_contraindications_and_interactions(cfg, db):
    prof.update(db, {**BASE, "objetivo": "hipertrofia", "condicoes_saude": ["doença renal crônica", "hipertensão"],
                     "medicamentos": ["varfarina 5 mg", "levotiroxina"]})
    by = {i["chave"]: i for i in sup.evaluate(db, cfg, END)["itens"]}
    assert by["creatina"]["status"] == "inadequado" and by["magnesio"]["status"] == "inadequado"
    assert by["cafeina"]["status"] == "inadequado"
    assert any("varfarina" in x for x in by["omega-3"]["interacoes_aplicaveis"])
    assert any("levotiroxina" in x for x in by["magnesio"]["interacoes_aplicaveis"])


def test_supplements_not_driven_by_wearable_and_can_be_unnecessary(cfg, db):
    populate(db, 60, END, stress_last=10)                      # sono e HRV ruins no relógio
    prof.update(db, {**BASE, "objetivo": "saude"})
    by = {i["chave"]: i for i in sup.evaluate(db, cfg, END)["itens"]}
    assert by["magnesio"]["status"] == "desnecessario"
    assert by["vitamina d"]["status"] == "dados_insuficientes" and "exame" in by["vitamina d"]["justificativa"]
    assert sum(i["status"] == "desnecessario" for i in by.values()) >= 4
    assert all(i["status"] != "potencialmente_relevante" for k, i in by.items() if k in ("magnesio", "vitamina d", "omega-3"))


def test_vitamin_d_only_from_lab_and_refers_to_doctor(cfg, db):
    prof.update(db, BASE)
    labs.add_result(db, END.isoformat(), "25-OH Vitamina D", "18,5", "ng/mL", 30, 100)
    v = {i["chave"]: i for i in sup.evaluate(db, cfg, END)["itens"]}["vitamina d"]
    assert v["status"] == "requer_avaliacao" and "médico" in v["justificativa"]


@pytest.mark.parametrize("name", ["oxandrolona", "SARMs", "clembuterol", "DNP", "testosterona", "efedrina", "anabolizante"])
def test_dangerous_substances_refused(name):
    assert sup.check_substance(name)["recusado"] is True


def test_sheets_complete_and_no_dose_above_reference_limits():
    need = {"finalidade", "populacao", "evidencia", "beneficios", "dose", "adversos", "contra", "interacoes", "sono", "custo", "profissional", "refs"}
    for k, s in sup.SHEETS.items():
        assert need <= set(s), k
    assert "400 mg/dia" in sup.SHEETS["cafeina"]["dose"] and "4.000 UI" in sup.SHEETS["vitamina d"]["dose"]
    assert "350 mg" in sup.SHEETS["magnesio"]["dose"]


# ------------------------------------------------------------------------ exames
def test_labs_preserve_units_and_use_lab_reference_only(cfg, db):
    labs.add_result(db, "2026-05-01", "Glicemia de jejum", 92, "mg/dL", 70, 99, "Lab A")
    labs.add_result(db, "2026-11-01", "Glicemia de jejum", 104, "mg/dL", 70, 99, "Lab A")
    labs.add_result(db, "2026-11-01", "Exame sem faixa", 5, "U")
    o = labs.overview(db)
    g = next(i for i in o["itens"] if i["chave"] == "glicemia")
    assert (g["valor"], g["unidade"], g["ref_min"], g["ref_max"], g["situacao"]) == (104, "mg/dL", 70, 99, "acima")
    assert g["tendencia"]["variacao_pct"] == pytest.approx(13.0, abs=0.1)
    assert next(i for i in o["itens"] if i["analito"] == "Exame sem faixa")["situacao"] == "sem_referencia"
    text = json.dumps(o, ensure_ascii=False).lower()
    assert "médico" in text and "diabetes" not in text and "tratamento" not in text.replace("não é diagnóstico", "")


def test_lab_file_storage_is_private_and_restricted(cfg):
    name = labs.store_original(cfg, "../../etc/laudo final.pdf", b"%PDF-1.4 x")
    f = cfg.home / "exames" / name
    assert f.exists() and (f.stat().st_mode & 0o077) == 0 and ".." not in name and "/" not in name
    with pytest.raises(ValueError):
        labs.store_original(cfg, "malware.exe", b"x")


# ------------------------------------------------------------------ aprendizado
def test_experiment_safe_factors_only_and_balanced(cfg, db):
    with pytest.raises(ValueError):
        learning.create_experiment(db, "jejum_72h", "sleep_score", END)
    eid = learning.create_experiment(db, "cafeina_corte", "sleep_score", END, 28, 2, 1, seed=3)
    days = db.q("SELECT assigned FROM experiment_days WHERE experiment_id=?", (eid,))
    assert sum(d["assigned"] == "A" for d in days) == 14 == sum(d["assigned"] == "B" for d in days)
    with pytest.raises(ValueError, match="um por vez"):
        learning.create_experiment(db, "alcool", "sleep_score", END)


def test_experiment_needs_min_observations_then_detects_effect(cfg, db):
    start = END - timedelta(days=30)
    eid = learning.create_experiment(db, "cafeina_corte", "sleep_score", start, 28, 2, 1, seed=5)
    assert learning.analyze_experiment(db, eid)["conclusao"] == "dados_insuficientes"
    for r in db.q("SELECT date, assigned FROM experiment_days WHERE experiment_id=?", (eid,)):
        learning.record_day(db, eid, r["date"], True)
        nxt = (date.fromisoformat(r["date"]) + timedelta(days=1)).isoformat()
        db.upsert_daily(nxt, {"sleep_score": (82 if r["assigned"] == "A" else 70) + (date.fromisoformat(nxt).day % 3)})
    res = learning.analyze_experiment(db, eid)
    assert res["conclusao"] == "diferenca_provavel" and res["p"] < 0.05 and "indício" in res["limitacoes"]


def test_no_conclusion_from_noise():
    import random
    rng = random.Random(4)
    a, b = [rng.gauss(70, 5) for _ in range(12)], [rng.gauss(70, 5) for _ in range(12)]
    assert learning.compare_groups(a, b, "sleep_score")["conclusao"] != "diferenca_provavel"
    assert learning.compare_groups(a[:3], b, "sleep_score")["conclusao"] == "dados_insuficientes"


# -------------------------------------------------------------------- estatística
def test_ewma_and_ols():
    assert stats.ewma([10, None, None, 10], 7)[-1] == 10
    f = stats.ols_slope(list(range(10)), [2 * x + 1 for x in range(10)])
    assert f["slope"] == pytest.approx(2) and f["lo"] <= 2 <= f["hi"]
    assert stats.ols_slope([1, 2], [1, 2]) is None


def test_backtest_has_no_leakage_and_prefers_naive_on_random_walk():
    import random
    rng = random.Random(2)
    walk, v = [], 50.0
    for _ in range(160):
        v += rng.gauss(0, 1)
        walk.append(v)
    r = stats.backtest(walk)
    assert r["escolhido"] == "ingênuo (último valor)"         # em passeio aleatório nada supera o ingênuo
    assert r["n_selecao"] + r["n_teste"] == r["n_previsoes"]
    seen = []
    orig = dict(stats.MODELS)
    try:
        stats.MODELS["espião"] = lambda tr_: seen.append(len(tr_)) or tr_[-1]
        stats.backtest(list(range(60)))
    finally:
        stats.MODELS.clear()
        stats.MODELS.update(orig)
    assert seen == list(range(28, 60))                        # cada previsão só vê o passado
    assert stats.backtest([1.0] * 20) is None


def test_backtest_picks_smoother_on_noisy_stationary_series():
    import random
    rng = random.Random(1)
    r = stats.backtest([50 + rng.gauss(0, 4) for _ in range(200)])
    assert r["escolhido"] != "ingênuo (último valor)" and r["ganho_pct_teste"] > 5


def test_data_quality_and_changepoint(cfg, db):
    populate(db, 100, END, stress_last=10)
    store = an.load_store(db, END, 120)
    q = stats.data_quality(store, END)
    assert q["pontuacao"] == 100 and q["nivel"] == "boa"
    cp = stats.changepoint(store, "resting_hr", END)
    assert cp and cp["direcao"] == "alta" and date.fromisoformat(cp["inicio"]) >= END - timedelta(days=14)
    with db.conn() as c:
        c.execute("UPDATE daily_metrics SET hrv_last_night=NULL WHERE date>?", ((END - timedelta(days=10)).isoformat(),))
    assert stats.data_quality(an.load_store(db, END, 120), END)["pontuacao"] < 100


# ----------------------------------------------------- plano diário e base científica
def test_daily_plan_structure(cfg, full):
    p = planner.daily_plan(full, cfg, END)
    assert len(p["itens"]) == 7
    for i in p["itens"]:
        assert i["o_que_fazer"] and i["confianca"] in ("alta", "média", "baixa") and "limitacoes" in i and isinstance(i["dados"], list)
    assert sum(1 for i in p["itens"] if i["evidencias"]) >= 6
    assert full.q1("SELECT COUNT(*) n FROM decisions WHERE area='treino' AND date=?", (END.isoformat(),))["n"] == 1


def test_daily_plan_with_empty_database_is_honest(cfg, db):
    p = planner.daily_plan(db, cfg, END)
    assert p["qualidade_dados"]["nivel"] == "insuficiente"
    assert "indeterminada" in p["itens"][0]["o_que_fazer"].lower()
    assert any("perfil" in a.lower() for a in p["proximas_acoes"])


def test_every_cited_reference_exists_in_kb():
    ents = kb.entries()
    src = "".join(p.read_text(encoding="utf-8") for p in Path(healthmon.__file__).parent.rglob("*.py"))
    cited = set(re.findall(r'"([a-z]+(?:19|20)\d\d[a-z0-9]*)"', src))
    assert len(cited) > 30
    assert not (cited - set(ents)), f"referências citadas sem registro na base: {cited - set(ents)}"


def test_kb_entries_are_complete_and_marked_unverified():
    for k, e in kb.entries().items():
        for f in ("titulo", "autores", "ano", "periodico", "tipo", "populacao", "resultados", "limitacoes", "grau", "revisado_em"):
            assert e.get(f), (k, f)
        assert e["grau"] in kb.GRADE and (e["doi"] is None or e["doi"].startswith("10."))
        assert e["verificado"] is False                       # nada foi conferido online durante o desenvolvimento
    r = kb.search("creatina força massa magra", 3)
    assert r and r[0]["chave"] in ("kreider2017", "antonio2021")
    assert kb.cite(["nao_existe"]) == []


def test_kb_verify_uses_only_doi(cfg):
    calls = []

    class S:
        def get(self, url, timeout=None, headers=None):
            calls.append(url)

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return {"message": {"title": ["A new predictive equation for resting energy expenditure in healthy individuals"],
                                        "issued": {"date-parts": [[1990]]}}}
            return R()
    out = kb.verify(cfg, S())
    assert all(u.startswith("https://api.crossref.org/works/10.") for u in calls)
    assert "mifflin1990" not in out["pendentes"] and "kreider2017" in out["pendentes"]   # título diferente = não confirma
    assert kb.cite(["mifflin1990"], cfg)[0]["verificado"] is True
