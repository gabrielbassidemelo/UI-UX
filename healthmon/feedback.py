"""Feedback diário: cruza o que você relata com o que a pulseira mede e avalia se as recomendações ajudaram.

Limites deliberados:
- Tudo aqui é DESCRITIVO e observacional. Quem não segue uma recomendação costuma estar num dia diferente
  (cansado, doente, sem tempo), então "seguiu vs. não seguiu" não prova efeito da recomendação.
- Este módulo só lê dados e devolve texto e números. Ele NÃO altera limites de segurança, regras críticas,
  metas nem planos: qualquer mudança continua passando pelas regras fixas e pela sua confirmação.
- Sem dados suficientes a resposta é "dados insuficientes", nunca um número fraco apresentado como achado.
"""
from __future__ import annotations

import statistics as st
from datetime import date, timedelta
from typing import Any

from . import analysis as an
from . import calibration
from .db import Database
from .learning import permutation_p

MIN_PAIRS = 14
MIN_ARM = 10
# (campo do check-in, rótulo, indicador Garmin, defasagem em dias do indicador, sinal esperado da associação)
PAIRS = [
    ("sleep_quality", "Qualidade do sono percebida", "sleep_score", 0, +1),
    ("sleep_quality", "Qualidade do sono percebida", "sleep_seconds", 0, +1),
    ("fatigue", "Fadiga", "hrv_last_night", 0, -1),
    ("fatigue", "Fadiga", "resting_hr", 0, +1),
    ("energy", "Disposição", "bb_max", 0, +1),
    ("perceived_stress", "Estresse percebido", "stress_avg", 0, +1),
    ("soreness", "Dor muscular", "training_load", -1, +1),
    ("perceived_recovery", "Recuperação percebida", "hrv_last_night", 0, +1),
]


def _rank(v: list[float]) -> list[float]:
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    rx, ry = _rank(x), _rank(y)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None


def _checkins(db: Database, end: date, days: int) -> dict[str, dict]:
    rows = db.q("SELECT * FROM checkins WHERE date BETWEEN ? AND ?", ((end - timedelta(days=days - 1)).isoformat(), end.isoformat()))
    return {r["date"]: r for r in rows}


def cross(db: Database, end: date, days: int = 60) -> dict[str, Any]:
    """O que você relata acompanha o que a pulseira mede? Associação de postos, com n e ressalvas explícitas."""
    cks = _checkins(db, end, days)
    store = an.load_store(db, end, days + 3)
    out = []
    for field, label, metric, lag, expected in PAIRS:
        xs, ys = [], []
        for ds, r in cks.items():
            s = r.get(field)
            g = store.value(metric, date.fromisoformat(ds) + timedelta(days=lag))
            if s is not None and g is not None:      # ausência nunca é tratada como zero
                xs.append(float(s))
                ys.append(float(g))
        item: dict[str, Any] = {"relato": field, "rotulo": label, "indicador": metric, "n": len(xs)}
        rho = spearman(xs, ys) if len(xs) >= MIN_PAIRS else None
        if rho is None:
            item.update(conclusao="dados_insuficientes",
                        texto=f"{label} × {an.METRICS[metric].label}: são necessários {MIN_PAIRS} dias com os dois registros "
                              f"e variação nas respostas (há {len(xs)}).")
        else:
            agree = rho * expected
            concl = "concordam" if agree >= 0.3 else "discordam" if agree <= -0.3 else "sem_relacao_clara"
            item.update(rho=round(rho, 2), conclusao=concl, confianca="baixa" if len(xs) < 28 else "média",
                        texto=f"{label} × {an.METRICS[metric].label}: ρ = {rho:+.2f} em {len(xs)} dias — "
                              + {"concordam": "o que você sente acompanha o que a pulseira mede.",
                                 "discordam": "o que você sente vai no sentido contrário ao da pulseira; vale conferir o uso "
                                              "do sensor e dar mais peso ao que você relata.",
                                 "sem_relacao_clara": "sem relação clara; a pulseira e a sua percepção trazem informações diferentes."}[concl])
        out.append(item)
    return {"dias": days, "check_ins": len(cks), "pares": out,
            "nota": "Associação descritiva em poucas semanas de dados; não indica causa. Várias comparações são feitas ao mesmo "
                    "tempo, então associações fracas aparecem por acaso."}


def recommendation_effect(db: Database, end: date, days: int = 90) -> dict[str, Any]:
    """Nos dias em que você seguiu a recomendação, o dia seguinte foi diferente? Só após o estágio longitudinal."""
    stage = calibration.stage(db, end)
    if not stage["permite"]["avaliacao_das_recomendacoes"]:
        return {"disponivel": False, "estagio": stage["estagio"],
                "texto": "A avaliação das recomendações começa depois de 90 dias de dados válidos. Até lá o sistema só registra."}
    cks = _checkins(db, end, days)
    store = an.load_store(db, end, days + 3)
    res: dict[str, Any] = {"disponivel": True, "desfechos": []}
    # (rótulo, função que lê o desfecho do dia seguinte, maior é melhor?)
    outcomes = [("Recuperação percebida no dia seguinte", lambda d: (cks.get(d.isoformat()) or {}).get("perceived_recovery")),
                ("Disposição no dia seguinte", lambda d: (cks.get(d.isoformat()) or {}).get("energy")),
                ("HRV na noite seguinte (ms)", lambda d: store.value("hrv_last_night", d))]
    alpha = 0.05 / len(outcomes)                               # Bonferroni
    for label, getter in outcomes:
        a, b = [], []
        for ds, r in cks.items():
            adh = r.get("adherence")
            v = getter(date.fromisoformat(ds) + timedelta(days=1))
            if adh is None or v is None:
                continue
            (a if adh >= 1 else b if adh <= 0 else []).append(float(v))   # adesão parcial fica fora da comparação
        item: dict[str, Any] = {"desfecho": label, "n_seguiu": len(a), "n_nao_seguiu": len(b)}
        if len(a) < MIN_ARM or len(b) < MIN_ARM:
            item.update(conclusao="dados_insuficientes",
                        texto=f"{label}: são necessários {MIN_ARM} dias em cada situação (há {len(a)} e {len(b)}).")
        else:
            diff = st.mean(a) - st.mean(b)
            p = permutation_p(a, b)
            sd = st.pstdev(a + b) or 1.0
            concl = "diferenca_provavel" if p < alpha and abs(diff / sd) >= 0.5 else "sem_diferenca_detectavel"
            item.update(media_seguiu=round(st.mean(a), 2), media_nao_seguiu=round(st.mean(b), 2), diferenca=round(diff, 2),
                        p=round(p, 4), alfa=round(alpha, 4), conclusao=concl,
                        texto=f"{label}: média {st.mean(a):.1f} quando seguiu e {st.mean(b):.1f} quando não seguiu "
                              f"(n = {len(a)} e {len(b)}; p = {p:.3f}).")
        res["desfechos"].append(item)
    res["nota"] = ("Comparação observacional: os dias em que você não segue a recomendação costumam ser diferentes por outros "
                   "motivos. Para testar uma estratégia de verdade, use um experimento pessoal alternado. Este resultado não "
                   "altera limites de segurança nem regras do sistema.")
    return res
