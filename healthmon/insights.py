"""Interpretação em português (motor de regras local).

Princípios: comparar só com o próprio histórico; separar pontual de persistente;
associações ≠ causalidade; nenhuma frase diagnóstica; sugestões de rotina genéricas e
reversíveis. Sinais que persistem ou sintomas → orientar procurar profissional de saúde.
"""
from __future__ import annotations

from typing import Any

from .metrics import METRICS, fmt

AVISO = ("Estas análises comparam você com seu próprio histórico a partir de medições de um "
         "dispositivo vestível. Não são diagnóstico nem exame clínico. Se houver sintomas ou "
         "alterações persistentes que preocupem, procure um profissional de saúde.")

STATUS_TXT = {
    "boa": "Recuperação dentro do seu padrão habitual",
    "moderada": "Recuperação moderada — algum indicador abaixo do seu padrão",
    "atencao": "Sinais de recuperação insuficiente em relação ao seu padrão",
    "indeterminada": "Recuperação indeterminada — histórico ou dados insuficientes",
}


def _desc(a: dict[str, Any]) -> str:
    k = a["metrica"]
    m = METRICS[k]
    val = fmt(k, _raw(k, a["valor"]))
    med = fmt(k, _raw(k, a["mediana"])) if a.get("mediana") is not None else "—"
    sent = {"acima": "acima", "abaixo": "abaixo", "dentro": "dentro"}.get(a["sentido"], "")
    if a["padrao"] == "persistente" and a.get("media7") is not None:
        m7 = fmt(k, _raw(k, a["media7"]))
        return (f"{m.label}: hoje {val}; média dos últimos 7 dias {m7}, {sent} da sua faixa usual "
                f"(mediana {med}) em {a['dias_fora7']} dos 7 dias — padrão persistente")
    pad = " (pontual)" if a["padrao"] == "pontual" else ""
    return f"{m.label}: {val} — {sent} da sua faixa usual (mediana {med}){pad}"


def _raw(key: str, display: float | None) -> float | None:
    """Converte de volta para unidade armazenada (para reutilizar fmt)."""
    if display is None:
        return None
    m = METRICS[key]
    if key.endswith("seconds"):
        return display * (3600 if m.unit == "h" else 60)
    if key == "distance_m":
        return display * 1000
    return display


def attention_points(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    pts = []
    for k, a in snapshot["avaliacoes"].items():
        if a["leitura"] == "desfavoravel" and (a["padrao"] in ("persistente", "pontual") or abs(a["z"] or 0) >= 1):
            pts.append({"metrica": k, "padrao": a["padrao"], "texto": _desc(a),
                        "prioridade": "alta" if a["padrao"] == "persistente" else "media"})
        elif METRICS[k].bom == "neutro" and a["padrao"] == "persistente" and k in (
                "sleep_resp_avg", "skin_temp_dev", "sleep_spo2_avg", "resp_waking_avg"):
            pts.append({"metrica": k, "padrao": "persistente", "texto": _desc(a), "prioridade": "media"})
    pts.sort(key=lambda p: (p["prioridade"] != "alta", p["metrica"]))
    return pts


def positives(snapshot: dict[str, Any]) -> list[str]:
    return [_desc(a) for a in snapshot["avaliacoes"].values() if a["leitura"] == "favoravel"][:4]


def day_suggestions(snapshot: dict[str, Any], readiness: dict[str, Any] | None = None) -> list[str]:
    av = snapshot["avaliacoes"]
    rec = snapshot["recuperacao"]
    tips: list[str] = []
    if readiness and readiness.get("nivel") == "parar":
        # sintomas de alerta sobrepõem qualquer outra sugestão do dia
        if readiness.get("urgente"):
            return ["Você relatou sintomas de alerta hoje. Não treine e procure atendimento de urgência (SAMU 192) "
                    "se houver dor ou pressão no peito, desmaio, falta de ar intensa ou palpitações fortes."]
        return ["Você relatou sintomas hoje: não treine. Descanse, hidrate-se e procure avaliação profissional se piorar "
                "ou não melhorar em poucos dias."]
    if readiness and readiness.get("nivel") == "recuperacao":
        rec = {**rec, "status": "atencao"}

    def bad(k: str) -> bool:
        return av.get(k, {}).get("leitura") == "desfavoravel"

    if rec["status"] == "atencao":
        tips.append("Dia indicado para intensidade baixa: caminhada leve, mobilidade ou treino "
                    "regenerativo em vez de sessões intensas ou longas.")
        if rec.get("persistente"):
            tips.append("O padrão de recuperação reduzida se repete há alguns dias: vale rever "
                        "volume de treino, horário de dormir e fontes de estresse antes de voltar a progredir.")
    elif rec["status"] == "moderada":
        tips.append("Se houver treino planejado, mantenha-o mas considere reduzir volume ou intensidade "
                    "e observe como o corpo responde.")
    elif rec["status"] == "boa":
        tips.append("Indicadores dentro do seu padrão: dia adequado para o treino planejado, "
                    "inclusive sessões mais exigentes, se fizerem parte do seu plano.")

    if bad("sleep_seconds") or bad("sleep_score"):
        tips.append("A noite foi mais curta ou de menor qualidade que o seu habitual: se possível, "
                    "antecipe o horário de dormir hoje e evite cafeína após o início da tarde.")
    if bad("stress_avg") or bad("sleep_stress_avg"):
        tips.append("Estresse acima do seu padrão: pausas curtas ao longo do dia, respiração lenta "
                    "(ex.: 5–6 respirações por minuto por alguns minutos) e exposição à luz natural podem ajudar.")
    if bad("hrv_last_night") and bad("resting_hr"):
        tips.append("HRV abaixo e FC de repouso acima do seu padrão ao mesmo tempo costumam acompanhar "
                    "carga acumulada, sono curto, álcool, calor ou início de quadros virais. Se surgir "
                    "algum sintoma, priorize descanso.")
    for k in ("sleep_resp_avg", "skin_temp_dev"):
        if av.get(k, {}).get("padrao") == "persistente":
            tips.append(f"{METRICS[k].label} fora do seu padrão por vários dias. Isso pode ter muitas "
                        "causas (ambiente, sono, posição do sensor); se vier com sintomas, consulte um profissional.")
    hyd_steps = av.get("steps")
    if hyd_steps and hyd_steps["leitura"] == "desfavoravel":
        tips.append("Passos abaixo do seu habitual: pequenas caminhadas após as refeições ajudam a manter o movimento.")
    if not tips:
        tips.append("Sem alterações relevantes: mantenha a rotina e o horário de sono regulares.")
    return tips


def night_suggestions(snapshot: dict[str, Any], regularity: dict[str, Any] | None) -> list[str]:
    av = snapshot["avaliacoes"]
    tips = []
    if regularity and regularity["desvio_min"] >= 60:
        tips.append(f"Seu horário de dormir variou cerca de ±{regularity['desvio_min']} min nas últimas "
                    f"duas semanas (mediana {regularity['horario_mediano']}). Horários mais regulares "
                    "costumam favorecer a qualidade do sono.")
    elif regularity:
        tips.append(f"Seu horário mediano de dormir é {regularity['horario_mediano']}; manter esse horário hoje "
                    "ajuda a preservar a regularidade.")
    if av.get("stress_avg", {}).get("leitura") == "desfavoravel":
        tips.append("O estresse de hoje ficou acima do seu padrão: reserve 30–60 min antes de deitar "
                    "sem telas e com luz baixa, e uma atividade calma (leitura, alongamento leve).")
    if (av.get("training_load", {}).get("valor") or 0) > 0 and av.get("training_load", {}).get("sentido") == "acima":
        tips.append("Dia de carga de treino acima do habitual: hidratação ao longo da noite (sem exagero "
                    "perto de deitar) e uma refeição adequada ajudam a recuperação.")
    if av.get("bb_min", {}).get("leitura") == "desfavoravel":
        tips.append("Body Battery terminou o dia abaixo do seu padrão: priorize dormir no horário e "
                    "evite atividades estimulantes à noite.")
    tips.append("Ambiente escuro, silencioso e fresco, e evitar álcool e refeições pesadas nas horas "
                "antes de deitar, são medidas gerais com bom respaldo para higiene do sono.")
    return tips


def trend_sentence(t: dict[str, Any]) -> str | None:
    if t["direcao"] not in ("subindo", "descendo"):
        return None
    k = t["metrica"]
    m = METRICS[k]
    bom = m.bom
    good = (t["direcao"] == "subindo") == (bom == "alto") if bom != "neutro" else None
    qual = "" if good is None else (" (favorável)" if good else " (desfavorável)")
    rate = f"{abs(t['inclinacao_semana']):.1f}".replace(".", ",")
    unit = m.unit or "pontos"
    return (f"{m.label} em tendência de {'alta' if t['direcao'] == 'subindo' else 'queda'}{qual}: "
            f"≈ {rate} {unit}/semana ({t['variacao_pct']:+.0f}% no período, {t['n']} dias).")


def correlation_sentence(c: dict[str, Any]) -> str | None:
    if c["rho"] is None or c["forca"] in ("sem associação clara", "fraca"):
        return None
    sentido = "andam juntos (quando um sobe, o outro tende a subir)" if c["rho"] > 0 else \
        "variam em sentidos opostos (quando um sobe, o outro tende a cair)"
    rho = f"{c['rho']:+.2f}".replace(".", ",")
    return (f"Associação {c['forca']} entre {c['descricao']}: {sentido}; ρ = {rho}, "
            f"{c['n']} dias. Associação não implica causa.")
