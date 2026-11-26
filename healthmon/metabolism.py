"""Inteligência metabólica: TMB, gasto total (TDEE) e recalibração pelo balanço energético.

TUDO aqui é ESTIMATIVA. Medição clínica de gasto de repouso exige calorimetria indireta;
gasto total, água duplamente marcada. Cada resultado traz intervalo de incerteza e a
origem dos dados. Referências (chaves da base em kb/evidencias.json):

- TMB: Mifflin-St Jeor (mifflin1990), validada em adultos com e sem obesidade; erro
  individual típico de ±10% (frankenfield2005). Cunningham (cunningham1980) usa massa livre
  de gordura e foi derivada/validada sobretudo em pessoas pessoas ativas; só é calculada
  com percentual de gordura informado, e herda o erro dessa medida.
- TDEE por três vias independentes: (1) fórmula × nível de atividade (heurística por passos,
  tudorlocke2004); (2) calorias totais do Garmin (erro de vestíveis: shcherbina2017);
  (3) ADAPTATIVA: ingestão registrada − variação de energia corporal estimada pela
  tendência de peso em ≥21 dias.
- Densidade energética da variação de peso: NÃO é fixa em 7.700 kcal/kg. Gordura ≈ 9.400
  kcal/kg e massa magra ≈ 1.800 kcal/kg (hall2008); a fração magra depende da gordura
  corporal (regra de Forbes: forbes2000, hall2007). Usamos um intervalo.
- Possível adaptação metabólica (rosenbaum2010): só é sinalizada como HIPÓTESE, e sempre
  ao lado da explicação alternativa mais comum, o sub-registro alimentar (lichtman1992).
"""
from __future__ import annotations

import statistics as st
from datetime import date, timedelta
from typing import Any

from . import profile as prof
from .config import Config
from .db import Database
from .stats import ewma, ols_slope

KCAL_FAT, KCAL_LEAN = 9400.0, 1800.0      # kcal/kg (Hall 2008: 39,5 e 7,6 MJ/kg)
MIN_SPAN_DAYS, MIN_WEIGHINS, MIN_LOG_DAYS, MIN_LOG_COVERAGE = 21, 8, 14, 0.7
BMR_REL_ERR = 0.10                         # erro individual típico das equações
INTAKE_UNDERREPORT = (0.0, 0.20)           # sub-registro plausível assumido: 0 a 20%
REFS = {"tmb": ["mifflin1990", "frankenfield2005", "cunningham1980"],
        "tdee": ["hall2011", "hall2008", "shcherbina2017", "tudorlocke2004"],
        "adaptacao": ["rosenbaum2010", "lichtman1992"]}


def mifflin(sex: str, weight_kg: float, height_cm: float, age: int) -> float:
    return 10 * weight_kg + 6.25 * height_cm - 5 * age + (5 if sex == "masculino" else -161)


def cunningham(ffm_kg: float) -> float:
    return 500 + 22 * ffm_kg


def bmr(db: Database, cfg: Config, day: date | None = None) -> dict[str, Any]:
    day = day or date.today()
    p = prof.get(db)
    w, w_src = prof.current_weight(db, cfg, day.isoformat())
    miss = [f for f in ("sexo", "nascimento", "altura_cm") if f not in p]
    if miss or w is None:
        return {"disponivel": False, "faltam": miss + ([] if w else ["peso"])}
    a = prof.age(p, day)
    m = mifflin(p["sexo"], w, p["altura_cm"], a)
    out = {"disponivel": True, "peso_kg": w, "peso_origem": w_src, "idade": a,
           "mifflin": round(m), "intervalo": [round(m * (1 - BMR_REL_ERR)), round(m * (1 + BMR_REL_ERR))],
           "equacao_principal": "Mifflin-St Jeor", "refs": REFS["tmb"],
           "nota": "Estimativa por equação. Mesmo a melhor equação erra mais de 10% em cerca de 2 a 3 de cada "
                   "10 pessoas; o intervalo de ±10% mostrado não é garantia. Não equivale a calorimetria indireta."}
    bf = db.q1("SELECT body_fat_pct, date, source FROM body_measurements WHERE body_fat_pct IS NOT NULL "
               "AND date<=? ORDER BY date DESC LIMIT 1", (day.isoformat(),))
    if bf:
        ffm = w * (1 - bf["body_fat_pct"] / 100)
        out.update(cunningham=round(cunningham(ffm)), massa_livre_gordura_kg=round(ffm, 1),
                   gordura_pct=bf["body_fat_pct"], gordura_origem=f"{bf['source']} em {bf['date']}",
                   nota_cunningham="Cunningham depende do percentual de gordura informado (bioimpedância e "
                                   "dobras têm erro de vários pontos percentuais). É mais usada e estudada em "
                                   "pessoas ativas e magras; fora desse perfil a confiabilidade é menor.")
    return out


def daily_intake(db: Database, start: str, end: str) -> dict[str, dict[str, Any]]:
    rows = db.q("SELECT date, COUNT(*) n, SUM(kcal) kcal, SUM(protein_g) protein_g, SUM(carb_g) carb_g, "
                "SUM(fat_g) fat_g, SUM(fiber_g) fiber_g FROM meals WHERE date BETWEEN ? AND ? GROUP BY date",
                (start, end))
    out = {}
    for r in rows:
        r["completo"] = bool(r["n"] >= 2 and (r["kcal"] or 0) >= 800)
        out[r["date"]] = r
    return out


def weight_trend(db: Database, end: date, days: int = 28) -> dict[str, Any]:
    start = end - timedelta(days=days - 1)
    rows = [r for r in prof.weights(db, start.isoformat(), end.isoformat()) if r["weight_kg"]]
    out: dict[str, Any] = {"n": len(rows), "dias": days, "suficiente": False}
    if len(rows) < 2:
        return out
    xs = [(date.fromisoformat(r["date"]) - start).days for r in rows]
    ys = [r["weight_kg"] for r in rows]
    out["amplitude_dias"] = xs[-1] - xs[0]
    series: list[float | None] = [None] * days
    for x, y in zip(xs, ys):
        series[x] = y
    out["suavizado_kg"] = round(next(v for v in reversed(ewma(series, 7)) if v is not None), 2)
    fit = ols_slope(xs, ys)
    if fit and len(rows) >= MIN_WEIGHINS and out["amplitude_dias"] >= MIN_SPAN_DAYS - 1:
        out.update(suficiente=True, kg_semana=round(fit["slope"] * 7, 3),
                   kg_semana_ic=[round(fit["lo"] * 7, 3), round(fit["hi"] * 7, 3)],
                   pct_semana=round(100 * fit["slope"] * 7 / st.mean(ys), 2),
                   ruido_diario_kg=round(fit["resid_sd"], 2), _slope=fit)
    else:
        out["motivo"] = (f"São necessárias ≥{MIN_WEIGHINS} pesagens cobrindo ≥{MIN_SPAN_DAYS} dias "
                         f"(há {len(rows)} em {out['amplitude_dias']} dias).")
    return out


def energy_density(db: Database, weight_kg: float, end: date) -> dict[str, Any]:
    """Intervalo de kcal por kg de variação de peso, conforme a composição provável da variação."""
    bf = db.q1("SELECT body_fat_pct FROM body_measurements WHERE body_fat_pct IS NOT NULL AND date<=? "
               "ORDER BY date DESC LIMIT 1", (end.isoformat(),))
    if bf:
        fm = weight_kg * bf["body_fat_pct"] / 100
        p_lean = 10.4 / (10.4 + fm)                       # Forbes: fração magra da variação
        central = KCAL_FAT * (1 - p_lean) + KCAL_LEAN * p_lean
        return {"central": round(central), "lo": round(central * 0.85), "hi": KCAL_FAT,
                "base": f"regra de Forbes com {bf['body_fat_pct']:.0f}% de gordura informado; limite "
                        "superior = variação só de gordura (plausível com treino de força e proteína adequada)"}
    return {"central": 7700.0, "lo": 6000.0, "hi": KCAL_FAT,
            "base": "sem percentual de gordura: intervalo amplo de 6.000 a 9.400 kcal/kg; 7.700 é só o "
                    "ponto central convencional, não uma constante fisiológica"}


def pal_from_steps(steps: float | None, sessions_week: float) -> tuple[float, str]:
    if steps is None:
        return 1.5, "sem passos: fator 1,5 assumido"
    pal = 1.4 if steps < 5000 else 1.5 if steps < 7500 else 1.6 if steps < 10000 else 1.7 if steps < 12500 else 1.8
    if sessions_week >= 3:
        pal += 0.05
    return pal, f"heurística: {steps:,.0f} passos/dia".replace(",", ".") + f" e {sessions_week:.0f} treinos/semana"


def tdee(db: Database, cfg: Config, end: date | None = None, days: int = 28) -> dict[str, Any]:
    end = end or date.today()
    start = end - timedelta(days=days - 1)
    b = bmr(db, cfg, end)
    out: dict[str, Any] = {"periodo": [start.isoformat(), end.isoformat()], "tmb": b, "vias": {},
                           "refs": REFS["tdee"]}
    daily = db.daily_range(start.isoformat(), end.isoformat())
    steps = [r["steps"] for r in daily if r.get("steps")]
    acts = db.q1("SELECT COUNT(*) n FROM activities WHERE date BETWEEN ? AND ?", (start.isoformat(), end.isoformat()))
    sessions_week = (acts["n"] if acts else 0) * 7 / days

    if b.get("disponivel"):
        pal, why = pal_from_steps(st.mean(steps) if steps else None, sessions_week)
        v = b["mifflin"] * pal
        out["vias"]["formula"] = {"kcal": round(v), "intervalo": [round(b["intervalo"][0] * (pal - 0.1)),
                                                                   round(b["intervalo"][1] * (pal + 0.1))],
                                  "detalhe": f"TMB × {pal:.2f} ({why})".replace(".", ",", 1)}
    gc = [r["calories_total"] for r in daily if r.get("calories_total")]
    if len(gc) >= 7:
        m = st.mean(gc)
        out["vias"]["garmin"] = {"kcal": round(m), "intervalo": [round(m * 0.8), round(m * 1.2)], "n_dias": len(gc),
                                 "detalhe": "média das calorias totais do Garmin; vestíveis de pulso erram o "
                                            "gasto energético com frequência em 20% ou mais"}

    wt = weight_trend(db, end, days)
    intake = daily_intake(db, start.isoformat(), end.isoformat())
    logged = [v for v in intake.values() if v["completo"]]
    out["peso"] = {k: v for k, v in wt.items() if not k.startswith("_")}
    out["registro_alimentar"] = {"dias_completos": len(logged), "cobertura": round(len(logged) / days, 2)}
    from . import calibration
    # A recalibração usa peso e alimentação: vale o estágio geral OU ≥28 dias cobertos por pesagens na janela.
    first_w = db.q1("SELECT MIN(date) AS d FROM body_measurements WHERE weight_kg IS NOT NULL AND date BETWEEN ? AND ?",
                    (start.isoformat(), end.isoformat()))
    span = (end - date.fromisoformat(first_w["d"])).days + 1 if first_w and first_w["d"] else 0
    calib_ok = calibration.stage(db, end)["permite"]["calibracao_metabolica"] or span >= 28
    if not calib_ok:
        out["adaptativa_indisponivel"] = ("Período de calibração: a recalibração do gasto só começa com pelo menos 28 dias "
                                          "de pesagens e registro alimentar.")
    elif wt.get("suficiente") and len(logged) >= MIN_LOG_DAYS and len(logged) / days >= MIN_LOG_COVERAGE:
        w, _ = prof.current_weight(db, cfg, end.isoformat())
        rho = energy_density(db, w, end)
        mean_in = st.mean(v["kcal"] for v in logged)
        slope = wt["_slope"]
        central = mean_in - rho["central"] * slope["slope"]
        cands = [mean_in * (1 + u) - r * s for u in INTAKE_UNDERREPORT for r in (rho["lo"], rho["hi"])
                 for s in (slope["lo"], slope["hi"])]
        out["vias"]["adaptativa"] = {
            "kcal": round(central), "intervalo": [round(min(cands)), round(max(cands))],
            "ingestao_media": round(mean_in), "densidade": rho,
            "detalhe": "ingestão registrada menos a variação de energia corporal estimada pela tendência "
                       "de peso; o intervalo combina erro da tendência, composição da variação e "
                       "sub-registro alimentar de 0 a 20%"}
        if days < 28:
            out["vias"]["adaptativa"]["aviso"] = ("Janela inferior a 4 semanas: água e glicogênio distorcem "
                                                  "a tendência de peso, sobretudo após mudança de dieta.")
    else:
        out["adaptativa_indisponivel"] = (wt.get("motivo") or
                                          f"São necessários ≥{MIN_LOG_DAYS} dias com registro alimentar completo "
                                          f"e cobertura ≥{int(MIN_LOG_COVERAGE*100)}% (há {len(logged)} de {days}).")

    v = out["vias"]
    # Salvaguarda: estimativa recalibrada muito abaixo da fórmula, com qualquer tendência de peso, indica
    # com mais probabilidade registro alimentar incompleto (lichtman1992) do que metabolismo baixo.
    # Nesse caso ela é mostrada, mas NÃO é usada para definir metas.
    if "adaptativa" in v and "formula" in v and v["adaptativa"]["intervalo"][1] < 0.85 * v["formula"]["kcal"]:
        out["sub_registro"] = {"refs": REFS["adaptacao"], "texto": (
            "O gasto calculado pelos seus registros ficou muito abaixo do previsto para o seu corpo. A explicação mais "
            "comum é registro alimentar incompleto (estudos mostram sub-registro de até ~50%). Por segurança, as metas "
            "usam a estimativa por fórmula e Garmin, não a recalibrada.")}
        v = {k: x for k, x in v.items() if k != "adaptativa"}
    if "adaptativa" in v:
        best, src, conf = v["adaptativa"], "adaptativa (seus dados de ingestão e peso)", "média"
        if days >= 42 and out["registro_alimentar"]["cobertura"] >= 0.85:
            conf = "alta"
    elif "garmin" in v and "formula" in v:
        k = (v["garmin"]["kcal"] + v["formula"]["kcal"]) / 2
        best = {"kcal": round(k), "intervalo": [min(v["garmin"]["intervalo"][0], v["formula"]["intervalo"][0]),
                                                max(v["garmin"]["intervalo"][1], v["formula"]["intervalo"][1])]}
        src, conf = "média entre Garmin e fórmula (ainda sem dados para recalibrar)", "baixa"
    elif v:
        name = next(iter(v))
        best, src, conf = v[name], name, "baixa"
    else:
        out["estimativa"] = None
        return out
    out["estimativa"] = {"kcal": best["kcal"], "intervalo": best["intervalo"], "origem": src, "confianca": conf}
    out["adaptacao"] = _adaptation_flag(out, days)
    return out


def _adaptation_flag(t: dict[str, Any], days: int) -> dict[str, Any] | None:
    v = t["vias"]
    if "adaptativa" not in v or "formula" not in v or days < 28:
        return None
    if (t["peso"].get("kg_semana") or 0) >= -0.1:
        return None
    if t.get("sub_registro") or v["adaptativa"]["intervalo"][1] < 0.9 * v["formula"]["kcal"]:
        return {"hipotese": True, "refs": REFS["adaptacao"],
                "texto": "Seu gasto estimado pelos seus dados está abaixo do previsto pela fórmula, mesmo "
                         "considerando a incerteza. Isso é compatível com adaptação metabólica durante a "
                         "perda de peso, mas a explicação mais frequente é registro alimentar incompleto. "
                         "Os dados não permitem distinguir as duas; a confirmação exigiria calorimetria."}
    return None


def strategies(goal: str) -> list[dict[str, Any]]:
    """Estratégias com respaldo científico; nada de 'acelerar o metabolismo'."""
    base = [
        {"titulo": "Movimento diário fora do treino (NEAT)", "refs": ["levine2002", "bull2020"],
         "texto": "Caminhar mais ao longo do dia é a forma mais sustentável de elevar o gasto diário; "
                  "aumente em passos pequenos (ex.: +1.000 a 2.000 passos/dia) e mantenha."},
        {"titulo": "Treino de força 2+ vezes por semana", "refs": ["bull2020", "acsm2009"],
         "texto": "Preserva ou aumenta massa magra, que sustenta o gasto de repouso; o efeito direto no "
                  "gasto diário é modesto, o benefício principal é composição corporal e função."},
        {"titulo": "150–300 min/semana de atividade aeróbica moderada", "refs": ["bull2020", "garber2011"],
         "texto": "Melhora o condicionamento cardiorrespiratório e contribui para o gasto energético."},
        {"titulo": "Sono suficiente e regular", "refs": ["watson2015"],
         "texto": "Sono curto dificulta o controle do apetite e a recuperação."},
    ]
    extra = {
        "emagrecimento": [{"titulo": "Déficit moderado, com proteína alta", "refs": ["garthe2011", "helms2014", "morton2018"],
                           "texto": "Perder cerca de 0,5–1% do peso por semana, com proteína adequada e treino "
                                    "de força, preserva melhor a massa magra que déficits agressivos."}],
        "hipertrofia": [{"titulo": "Superávit pequeno", "refs": ["slater2019", "morton2018"],
                         "texto": "Um superávit modesto com progressão de carga tende a limitar o ganho de gordura."}],
        "recomposicao": [{"titulo": "Manutenção calórica com proteína alta e progressão de carga", "refs": ["barakat2020"],
                          "texto": "Mais provável em iniciantes, em quem retorna ao treino ou tem mais gordura corporal; "
                                   "a evolução é lenta e se mede em semanas."}],
    }
    from . import kb
    return [s_ for s_ in base + extra.get(goal, []) if not kb.failing(s_["refs"])]
