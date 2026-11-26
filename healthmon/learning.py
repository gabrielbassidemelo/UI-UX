"""Aprendizado contínuo: experimentos individuais (n-de-1), padrões longitudinais e
acompanhamento das próprias recomendações.

- Experimentos: só fatores de baixo risco de uma lista fechada; blocos A/B alternados em
  ordem sorteada (semente guardada); análise por diferença de medianas com teste de
  permutação; exige mínimo de observações por condição e registra confundidores.
- Padrões: comparação de grupos de dias (ex.: treino de manhã × à noite, com × sem cafeína
  após as 14h) sobre desfechos do dia seguinte. São associações observacionais.
- Nada aqui altera limites de segurança nem o plano automaticamente.
"""
from __future__ import annotations

import random
import statistics as st
from datetime import date, timedelta
from typing import Any, Callable

from . import analysis as an
from .db import Database, utcnow
from .metrics import METRICS, display_value

MIN_PER_ARM = 6
SAFE_FACTORS = {
    "cafeina_corte": ("Horário do último café", "sem cafeína após as 12h", "cafeína habitual à tarde"),
    "horario_treino": ("Horário do treino", "treino pela manhã", "treino à noite"),
    "horario_dormir": ("Horário de dormir", "deitar 30 min mais cedo", "horário habitual"),
    "telas_noite": ("Telas antes de dormir", "sem telas na última hora", "uso habitual de telas"),
    "caminhada_pos_refeicao": ("Caminhada após refeições", "10 min de caminhada após o jantar", "sem caminhada"),
    "recuperacao_ativa": ("Dia de descanso", "recuperação ativa leve", "descanso total"),
    "alcool": ("Álcool à noite", "sem álcool", "consumo habitual"),
    "refeicao_noturna": ("Horário do jantar", "jantar até 3 h antes de dormir", "jantar no horário habitual"),
}
OUTCOMES = ["sleep_score", "sleep_seconds", "deep_seconds", "hrv_last_night", "resting_hr", "stress_avg", "bb_max",
            "sleep_stress_avg", "awake_count"]


def create_experiment(db: Database, factor: str, outcome: str, start: date, planned_days: int = 28,
                      block_days: int = 2, lag_days: int = 1, seed: int | None = None) -> int:
    if factor not in SAFE_FACTORS:
        raise ValueError("Fator não permitido. Experimentos são restritos a mudanças de rotina de baixo risco: "
                         + ", ".join(SAFE_FACTORS))
    if outcome not in OUTCOMES:
        raise ValueError(f"Desfecho inválido; use um de {OUTCOMES}")
    if not (14 <= planned_days <= 56) or not (1 <= block_days <= 7):
        raise ValueError("Duração de 14 a 56 dias; blocos de 1 a 7 dias")
    if db.q1("SELECT 1 FROM experiments WHERE status='ativo'"):
        raise ValueError("Já existe um experimento ativo; conduza um por vez para não misturar efeitos.")
    seed = seed if seed is not None else random.SystemRandom().randrange(1, 10**6)
    name, a, b = SAFE_FACTORS[factor]
    eid = db.insert("experiments", {"name": name, "factor": factor, "condition_a": a, "condition_b": b, "outcome": outcome,
                                    "lag_days": lag_days, "block_days": block_days, "start_date": start.isoformat(),
                                    "planned_days": planned_days, "seed": seed, "created_at": utcnow()})
    rng = random.Random(seed)
    day, i = start, 0
    while i < planned_days:
        pair = ["A", "B"]
        rng.shuffle(pair)                       # pares de blocos em ordem sorteada (balanceado)
        for cond in pair:
            for _ in range(block_days):
                if i >= planned_days:
                    break
                db.insert("experiment_days", {"experiment_id": eid, "date": (day + timedelta(days=i)).isoformat(),
                                              "assigned": cond}, upsert_on="experiment_id,date")
                i += 1
    return eid


def record_day(db: Database, eid: int, day: str, followed: bool, confounders: str = "") -> None:
    if not db.q1("SELECT 1 FROM experiment_days WHERE experiment_id=? AND date=?", (eid, day)):
        raise ValueError("Dia fora do experimento")
    db.x("UPDATE experiment_days SET followed=?, confounders=? WHERE experiment_id=? AND date=?",
         (1 if followed else 0, confounders[:200], eid, day))


def permutation_p(a: list[float], b: list[float], n_iter: int = 5000, seed: int = 1) -> float:
    obs = abs(st.median(a) - st.median(b))
    pool, na, rng, hits = a + b, len(a), random.Random(seed), 0
    for _ in range(n_iter):
        rng.shuffle(pool)
        if abs(st.median(pool[:na]) - st.median(pool[na:])) >= obs - 1e-12:
            hits += 1
    return (hits + 1) / (n_iter + 1)


def compare_groups(a: list[float], b: list[float], key: str) -> dict[str, Any]:
    out: dict[str, Any] = {"n_a": len(a), "n_b": len(b)}
    if len(a) < MIN_PER_ARM or len(b) < MIN_PER_ARM:
        out.update(conclusao="dados_insuficientes",
                   texto=f"São necessárias pelo menos {MIN_PER_ARM} observações válidas em cada condição "
                         f"(há {len(a)} e {len(b)}). Nenhuma conclusão ainda.")
        return out
    diff = st.median(a) - st.median(b)
    sd = st.pstdev(a + b) or 1.0
    p = permutation_p(a, b)
    eff = diff / sd
    out.update(mediana_a=round(st.median(a), 2), mediana_b=round(st.median(b), 2), diferenca=round(diff, 2),
               efeito_padronizado=round(eff, 2), p=round(p, 4), unidade=METRICS[key].unit)
    if p < 0.05 and abs(eff) >= 0.5:
        out["conclusao"] = "diferenca_provavel"
    elif p < 0.2 and abs(eff) >= 0.3:
        out["conclusao"] = "sinal_fraco"
    else:
        out["conclusao"] = "sem_diferenca_detectavel"
    return out


def analyze_experiment(db: Database, eid: int) -> dict[str, Any]:
    e = db.q1("SELECT * FROM experiments WHERE id=?", (eid,))
    if not e:
        raise ValueError("Experimento não encontrado")
    days = db.q("SELECT * FROM experiment_days WHERE experiment_id=? ORDER BY date", (eid,))
    end = date.fromisoformat(days[-1]["date"]) + timedelta(days=e["lag_days"])
    store = an.load_store(db, end, len(days) + 10)
    arms: dict[str, list[float]] = {"A": [], "B": []}
    excluded = conf = 0
    for d_ in days:
        if d_["followed"] != 1:
            excluded += 1
            continue
        if d_["confounders"]:
            conf += 1
        v = store.value(e["outcome"], date.fromisoformat(d_["date"]) + timedelta(days=e["lag_days"]))
        if v is not None:
            arms[d_["assigned"]].append(display_value(e["outcome"], v))
    res = compare_groups(arms["A"], arms["B"], e["outcome"])
    m = METRICS[e["outcome"]]
    text = res.get("texto")
    if not text:
        better = None
        if m.bom != "neutro" and res["conclusao"] != "sem_diferenca_detectavel":
            better = "A" if (res["diferenca"] > 0) == (m.bom == "alto") else "B"
        label = {"A": e["condition_a"], "B": e["condition_b"]}
        if res["conclusao"] == "diferenca_provavel":
            text = (f"Nos seus dados, {m.label} foi {'melhor' if better else 'diferente'} com \"{label.get(better, label['A'])}\" "
                    f"(diferença de {abs(res['diferenca']):g} {m.unit}; p = {res['p']:.3f}).")
        elif res["conclusao"] == "sinal_fraco":
            text = "Há um sinal fraco de diferença, insuficiente para concluir. Vale repetir ou prolongar o experimento."
        else:
            text = "Não foi detectada diferença entre as condições neste período."
    return {"experimento": e, **res, "texto": text, "dias_excluidos_sem_adesao": excluded, "dias_com_confundidores": conf,
            "limitacoes": "Experimento individual sem cegamento: expectativa, dia da semana e outros fatores podem influenciar. "
                          "O resultado vale para você, neste período, e deve ser lido como indício."}


def list_experiments(db: Database) -> list[dict[str, Any]]:
    out = []
    for e in db.q("SELECT * FROM experiments ORDER BY id DESC"):
        d = db.q1("SELECT COUNT(*) n, SUM(followed IS NOT NULL) reg FROM experiment_days WHERE experiment_id=?", (e["id"],))
        out.append({**e, "dias": d["n"], "dias_registrados": d["reg"] or 0})
    return out


def today_assignment(db: Database, day: date) -> dict[str, Any] | None:
    r = db.q1("SELECT e.id, e.name, e.condition_a, e.condition_b, d.assigned, d.followed FROM experiments e "
              "JOIN experiment_days d ON d.experiment_id=e.id WHERE e.status='ativo' AND d.date=?", (day.isoformat(),))
    if not r:
        return None
    return {"id": r["id"], "nome": r["name"], "condicao": r["condition_a"] if r["assigned"] == "A" else r["condition_b"],
            "registrado": r["followed"] is not None}


# ---------------------------------------------------------- padrões observacionais
def _split(db: Database, end: date, days: int, outcome: str, lag: int,
           group_of: Callable[[date], str | None]) -> dict[str, list[float]]:
    store = an.load_store(db, end, days + 5)
    groups: dict[str, list[float]] = {}
    for i in range(days):
        d_ = end - timedelta(days=i)
        g = group_of(d_)
        v = store.value(outcome, d_ + timedelta(days=lag))
        if g and v is not None:
            groups.setdefault(g, []).append(display_value(outcome, v))
    return groups


def patterns(db: Database, end: date, days: int = 90) -> list[dict[str, Any]]:
    start = (end - timedelta(days=days)).isoformat()
    ck = {r["date"]: r for r in db.q("SELECT * FROM checkins WHERE date>=?", (start,))}
    ses = {}
    for r in db.q("SELECT date, time_of_day FROM workout_sessions WHERE date>=? AND time_of_day IS NOT NULL", (start,)):
        ses[r["date"]] = r["time_of_day"]
    for r in db.q("SELECT date, start_local FROM activities WHERE date>=?", (start,)):
        if r["start_local"] and len(r["start_local"]) >= 13 and r["date"] not in ses:
            h = int(r["start_local"][11:13])
            ses[r["date"]] = "manhã" if h < 12 else "tarde" if h < 18 else "noite"

    def caffeine(d_: date) -> str | None:
        c = ck.get(d_.isoformat())
        if not c or not c.get("caffeine_last_time"):
            return None
        return "cafeína após as 14h" if c["caffeine_last_time"] >= "14:00" else "cafeína só até as 14h"

    def alcohol(d_: date) -> str | None:
        c = ck.get(d_.isoformat())
        if not c or c.get("alcohol_units") is None:
            return None
        return "com álcool" if c["alcohol_units"] > 0 else "sem álcool"

    def train_time(d_: date) -> str | None:
        t = ses.get(d_.isoformat())
        return {"manhã": "treino de manhã", "noite": "treino à noite"}.get(t)

    def trained(d_: date) -> str | None:
        return "dia com treino" if d_.isoformat() in ses else "dia sem treino"

    specs = [("Cafeína e sono da noite seguinte", caffeine, "sleep_score", 1),
             ("Álcool e HRV da noite seguinte", alcohol, "hrv_last_night", 1),
             ("Horário do treino e sono da noite seguinte", train_time, "sleep_score", 1),
             ("Treino e HRV da noite seguinte", trained, "hrv_last_night", 1)]
    out = []
    for title, fn, outcome, lag in specs:
        g = _split(db, end, days, outcome, lag, fn)
        if len(g) != 2:
            out.append({"titulo": title, "conclusao": "dados_insuficientes",
                        "texto": "Faltam registros (check-ins ou treinos) para comparar os dois grupos."})
            continue
        (na, a), (nb, b) = sorted(g.items())
        r = compare_groups(a, b, outcome)
        r.update(titulo=title, grupo_a=na, grupo_b=nb, desfecho=METRICS[outcome].label)
        if "texto" not in r:
            verb = {"diferenca_provavel": "Diferença provável", "sinal_fraco": "Sinal fraco de diferença",
                    "sem_diferenca_detectavel": "Sem diferença detectável"}[r["conclusao"]]
            r["texto"] = (f"{verb}: {METRICS[outcome].label} mediana {r['mediana_a']:g} em \"{na}\" (n={r['n_a']}) e "
                          f"{r['mediana_b']:g} em \"{nb}\" (n={r['n_b']}). Associação observacional, não prova causa.")
        out.append(r)
    return out


# ------------------------------------------------- histórico de decisões e resultados
def record_decision(db: Database, day: str, area: str, title: str, detail: str = "", rationale: str = "",
                    confidence: str = "", refs: str = "", status: str = "automatica") -> None:
    db.insert("decisions", {"created_at": utcnow(), "date": day, "area": area, "title": title[:200], "detail": detail[:1000],
                            "rationale": rationale[:1500], "confidence": confidence, "refs": refs, "status": status},
              upsert_on="date,area,title")


def set_followed(db: Database, decision_id: int, followed: bool) -> None:
    db.x("UPDATE decisions SET followed=? WHERE id=?", (1 if followed else 0, decision_id))


def decisions(db: Database, limit: int = 60) -> list[dict[str, Any]]:
    return db.q("SELECT * FROM decisions ORDER BY date DESC, id DESC LIMIT ?", (limit,))


def recommendation_outcomes(db: Database, end: date, days: int = 60) -> dict[str, Any]:
    """Os dias em que você seguiu a recomendação de treino tiveram recuperação diferente no dia seguinte?"""
    rows = db.q("SELECT date, followed FROM decisions WHERE area='treino' AND followed IS NOT NULL AND date>=?",
                ((end - timedelta(days=days)).isoformat(),))
    store = an.load_store(db, end, days + 5)
    g: dict[str, list[float]] = {"seguiu": [], "não seguiu": []}
    for r in rows:
        v = store.value("hrv_last_night", date.fromisoformat(r["date"]) + timedelta(days=1))
        if v is not None:
            g["seguiu" if r["followed"] else "não seguiu"].append(v)
    res = compare_groups(g["seguiu"], g["não seguiu"], "hrv_last_night")
    res["avaliadas"] = len(rows)
    if "texto" not in res:
        res["texto"] = (f"HRV mediana na noite seguinte: {res['mediana_a']:g} ms quando seguiu (n={res['n_a']}) e "
                        f"{res['mediana_b']:g} ms quando não seguiu (n={res['n_b']}). Observacional.")
    return res
