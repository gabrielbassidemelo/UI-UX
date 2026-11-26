"""Motor estatístico (fase 2) — determinístico, sem dependências pesadas.

Complementa analysis.py com: EWMA, linhas de base 7/28/90 dias, efeito do dia da semana,
qualidade dos dados, detecção de mudança de nível (CUSUM robusto), intervalos de
incerteza e backtesting temporal (walk-forward, sem vazamento de futuro) de previsões
simples × suavização exponencial × Holt.
"""
from __future__ import annotations

import math
import statistics as st
from datetime import date, timedelta
from typing import Any, Callable, Sequence

from . import analysis as an
from .metrics import CORE, METRICS, display_value

T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23,
       12: 2.18, 15: 2.13, 20: 2.09, 25: 2.06, 30: 2.04, 40: 2.02, 60: 2.00, 120: 1.98}


def t95(df: int) -> float:
    if df <= 0:
        return float("inf")
    for k in sorted(T95):
        if df <= k:
            return T95[k]
    return 1.96


# ------------------------------------------------------------------ suavização
def ewma(values: Sequence[float | None], halflife: float = 7.0) -> list[float | None]:
    """Média móvel exponencial tolerante a ausências (o peso decai com os dias sem dado)."""
    alpha = 1 - 0.5 ** (1 / halflife)
    out: list[float | None] = []
    level: float | None = None
    gap = 0
    for v in values:
        if v is None:
            gap += 1
            out.append(level)
            continue
        if level is None:
            level = float(v)
        else:
            a = 1 - (1 - alpha) ** (gap + 1)
            level = level + a * (v - level)
        gap = 0
        out.append(level)
    return out


def mean_ci(values: Sequence[float]) -> tuple[float, float, float] | None:
    n = len(values)
    if n < 3:
        return None
    m = st.mean(values)
    half = t95(n - 1) * st.stdev(values) / math.sqrt(n)
    return m, m - half, m + half


def ols_slope(xs: Sequence[float], ys: Sequence[float]) -> dict[str, float] | None:
    """Regressão linear simples com erro-padrão e IC95% da inclinação."""
    n = len(xs)
    if n < 5:
        return None
    mx, my = st.mean(xs), st.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    res = [y - (a + b * x) for x, y in zip(xs, ys)]
    s2 = sum(r * r for r in res) / (n - 2)
    se = math.sqrt(s2 / sxx)
    t = t95(n - 2)
    return {"slope": b, "intercept": a, "se": se, "lo": b - t * se, "hi": b + t * se,
            "n": n, "resid_sd": math.sqrt(s2)}


# ------------------------------------------------------------- bases múltiplas
def multi_baseline(store: an.Store, key: str, day: date) -> dict[str, Any]:
    out = {}
    for w, mn in ((7, 4), (28, 10), (90, 30)):
        b = an.baseline(store, key, day, w, mn)
        out[str(w)] = {"n": b.n, "confiavel": b.confiavel,
                       "mediana": display_value(key, b.mediana),
                       "faixa": [display_value(key, x) for x in b.faixa] if b.faixa else None}
    return out


def weekday_effect(store: an.Store, key: str, end: date, days: int = 90) -> dict[str, Any] | None:
    """Desvio mediano por dia da semana; só reportado com ≥4 observações por dia."""
    pts = store.values(key, end - timedelta(days=days - 1), end)
    if len(pts) < 35:
        return None
    vals = [display_value(key, v) for _, v in pts]
    med = st.median(vals)
    mad = 1.4826 * st.median([abs(v - med) for v in vals]) or (st.pstdev(vals) or 1.0)
    by: dict[int, list[float]] = {}
    for (d_, _), v in zip(pts, vals):
        by.setdefault(d_.weekday(), []).append(v)
    if any(len(v) < 4 for v in by.values()) or len(by) < 7:
        return None
    dev = {wd: (st.median(v) - med) / mad for wd, v in by.items()}
    spread = max(dev.values()) - min(dev.values())
    names = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
    return {"n": len(pts), "amplitude_z": round(spread, 2), "relevante": spread >= 1.0,
            "desvios": {names[k]: round(v, 2) for k, v in sorted(dev.items())}}


# ----------------------------------------------------------- qualidade dos dados
def data_quality(store: an.Store, end: date, days: int = 14) -> dict[str, Any]:
    """Cobertura dos indicadores principais. Pontuação = média da cobertura (0–100) dos
    indicadores que o dispositivo fornece; indicadores nunca vistos em 90 dias são
    listados como 'não fornecidos' e não entram na conta. É uma medida de completude
    dos dados, não uma métrica de saúde."""
    start = end - timedelta(days=days - 1)
    cov, never = {}, []
    for k in CORE:
        if not store.values(k, end - timedelta(days=89), end):
            never.append(METRICS[k].label)
            continue
        cov[k] = len(store.values(k, start, end)) / days
    score = round(100 * st.mean(cov.values())) if cov else 0
    last = max((d_ for k in cov for d_, _ in store.values(k, start, end)), default=None)
    level = "boa" if score >= 80 else "parcial" if score >= 50 else "insuficiente"
    return {"pontuacao": score, "nivel": level, "dias": days,
            "cobertura": {METRICS[k].label: round(100 * v) for k, v in cov.items()},
            "nao_fornecidos": never, "ultimo_dia_com_dado": last.isoformat() if last else None,
            "defasagem_dias": (end - last).days if last else None}


# ------------------------------------------------------------ mudança de nível
def changepoint(store: an.Store, key: str, end: date, days: int = 60, k: float = 0.5,
                h: float = 5.0) -> dict[str, Any] | None:
    """CUSUM bilateral sobre z robusto (referência: primeiros 28 dias da janela).
    Retorna a data estimada do início da mudança mais recente, se houver."""
    pts = store.values(key, end - timedelta(days=days - 1), end)
    if len(pts) < 35:
        return None
    ref = [an._tf(key, v) for _, v in pts[:28]]
    med = st.median(ref)
    sd = max(1.4826 * st.median([abs(v - med) for v in ref]), 0.85 * st.stdev(ref)) or 1.0
    hi = lo = 0.0
    start_hi = start_lo = None
    found = None
    for d_, v in pts[28:]:
        z = (an._tf(key, v) - med) / sd
        if hi == 0:
            start_hi = d_
        if lo == 0:
            start_lo = d_
        hi, lo = max(0.0, hi + z - k), max(0.0, lo - z - k)
        if hi > h:
            found, hi = {"direcao": "alta", "inicio": start_hi.isoformat(), "detectada": d_.isoformat()}, 0.0
        if lo > h:
            found, lo = {"direcao": "queda", "inicio": start_lo.isoformat(), "detectada": d_.isoformat()}, 0.0
    return found


# ------------------------------------------------------------------- previsão
def _ses_fit(train: Sequence[float]) -> float:
    best_a, best = 0.3, float("inf")
    for a in (0.1, 0.2, 0.3, 0.5, 0.7):
        lvl, err = train[0], 0.0
        for v in train[1:]:
            err += abs(v - lvl)
            lvl += a * (v - lvl)
        if err < best:
            best, best_a = err, a
    return best_a


def f_naive(tr: Sequence[float]) -> float:
    return tr[-1]


def f_seasonal(tr: Sequence[float]) -> float:
    return tr[-7] if len(tr) >= 7 else tr[-1]


def f_median7(tr: Sequence[float]) -> float:
    return st.median(tr[-7:])


def f_ses(tr: Sequence[float]) -> float:
    a = _ses_fit(tr)
    lvl = tr[0]
    for v in tr[1:]:
        lvl += a * (v - lvl)
    return lvl


def f_holt(tr: Sequence[float], a: float = 0.3, b: float = 0.1, phi: float = 0.9) -> float:
    lvl, trend = tr[0], 0.0
    for v in tr[1:]:
        prev = lvl
        lvl = a * v + (1 - a) * (lvl + phi * trend)
        trend = b * (lvl - prev) + (1 - b) * phi * trend
    return lvl + phi * trend


MODELS: dict[str, Callable[[Sequence[float]], float]] = {
    "ingênuo (último valor)": f_naive, "sazonal semanal": f_seasonal, "mediana 7 dias": f_median7,
    "suavização exponencial": f_ses, "Holt amortecido": f_holt}


def backtest(values: Sequence[float], min_train: int = 28) -> dict[str, Any] | None:
    """Validação walk-forward 1 passo à frente: cada previsão usa só o passado.
    Um modelo só é preferido ao ingênuo se reduzir o MAE em ≥5% também no trecho de teste."""
    vals = [float(v) for v in values if v is not None]
    if len(vals) < min_train + 14:
        return None
    errs: dict[str, list[float]] = {m: [] for m in MODELS}
    for t in range(min_train, len(vals)):
        tr = vals[:t]
        for name, f in MODELS.items():
            errs[name].append(abs(f(tr) - vals[t]))
    # Seleção e avaliação em trechos diferentes: escolhe-se o modelo nos primeiros 60% das
    # previsões e mede-se o ganho só nos 40% finais (que não participaram da escolha).
    n = len(vals) - min_train
    cut = max(7, int(n * 0.6))
    naive = "ingênuo (último valor)"
    sel = {m: st.mean(e[:cut]) for m, e in errs.items()}
    best = min(sel, key=sel.get)
    candidate = best if sel[best] <= 0.95 * (sel[naive] or 1e-9) else naive
    test = {m: st.mean(e[cut:]) for m, e in errs.items()}
    base = test[naive] or 1e-9
    chosen = candidate if test[candidate] <= 0.95 * base else naive
    return {"n_previsoes": n, "n_selecao": cut, "n_teste": n - cut,
            "mae_teste": {m: round(v, 3) for m, v in test.items()},
            "mase_teste": {m: round(v / base, 3) for m, v in test.items()},
            "candidato": candidate, "escolhido": chosen,
            "ganho_pct_teste": round(100 * (1 - test[chosen] / base), 1)}


def backtest_metric(store: an.Store, key: str, end: date, days: int = 180) -> dict[str, Any] | None:
    pts = store.values(key, end - timedelta(days=days - 1), end)
    r = backtest([display_value(key, v) for _, v in pts])
    if r:
        r["metrica"] = METRICS[key].label
    return r
