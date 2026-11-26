"""Motor de análise estatística — determinístico, local e auditável.

Métodos (ver docs/METODOLOGIA.md para referências):
- Linha de base individual: mediana e desvio robusto (1,4826 × MAD) dos dias anteriores
  (janela configurável, padrão 28 dias), excluindo o próprio dia. HRV em escala log.
- Desvio diário: z robusto = (valor − mediana) / desvio robusto.
- Pontual × persistente: pontual = só o dia atual foge da faixa (|z| ≥ 1,5); persistente =
  a média dos últimos 7 dias está ≥ 1,25 desvio da linha de base anterior E ≥4 dos 7 dias
  estão fora da faixa no mesmo sentido.
- Tendência: inclinação de Theil–Sen + teste de Mann–Kendall (robustos a outliers).
- Associações: correlação de Spearman (com defasagem de 1 dia quando faz sentido);
  são associações, nunca causalidade.
"""
from __future__ import annotations

import math
import statistics as st
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .db import Database
from .metrics import CORE, METRICS, display_value

Z_FORA = 1.0      # fora da faixa usual
Z_PONTUAL = 1.5   # desvio pontual relevante
Z_PERSIST = 1.25  # média de 7 dias afastada da base (calibrado: ~0–3% de falsos positivos em dados estáveis)


def d(s: str) -> date:
    return date.fromisoformat(s)


def _tf(key: str, v: float) -> float:
    return math.log(v) if METRICS[key].log and v > 0 else v


def _itf(key: str, v: float) -> float:
    return math.exp(v) if METRICS[key].log else v


class Store:
    """Cache em memória das séries para uma janela de análise."""

    def __init__(self, db: Database, start: date, end: date):
        self.rows = {r["date"]: r for r in db.daily_range(start.isoformat(), end.isoformat())}
        self.start, self.end = start, end

    def value(self, key: str, day: date) -> float | None:
        r = self.rows.get(day.isoformat())
        v = r.get(key) if r else None
        return float(v) if v is not None else None

    def values(self, key: str, start: date, end: date) -> list[tuple[date, float]]:
        out = []
        cur = start
        while cur <= end:
            v = self.value(key, cur)
            if v is not None:
                out.append((cur, v))
            cur += timedelta(days=1)
        return out

    def raw(self, day: date) -> dict[str, Any] | None:
        return self.rows.get(day.isoformat())


def load_store(db: Database, end: date, days_back: int = 400) -> Store:
    return Store(db, end - timedelta(days=days_back), end)


# --------------------------------------------------------------------------- base
@dataclass
class Baseline:
    key: str
    n: int
    median: float | None = None   # escala transformada
    rsd: float | None = None
    confiavel: bool = False

    def z(self, v: float | None) -> float | None:
        if v is None or self.median is None or not self.rsd:
            return None
        return (_tf(self.key, v) - self.median) / self.rsd

    @property
    def mediana(self) -> float | None:
        return _itf(self.key, self.median) if self.median is not None else None

    @property
    def faixa(self) -> tuple[float, float] | None:
        if self.median is None or self.rsd is None:
            return None
        return (_itf(self.key, self.median - Z_FORA * self.rsd),
                _itf(self.key, self.median + Z_FORA * self.rsd))


def baseline(store: Store, key: str, day: date, window: int = 28, min_n: int = 10) -> Baseline:
    vals = [_tf(key, v) for _, v in store.values(key, day - timedelta(days=window), day - timedelta(days=1))]
    b = Baseline(key=key, n=len(vals))
    if len(vals) < 3:
        return b
    med = st.median(vals)
    mad = st.median([abs(v - med) for v in vals])
    # Piso: MAD de 28 dias pode subestimar a dispersão (valores inteiros, amostra pequena);
    # usar ao menos 0,85 × desvio padrão reduz falsos alarmes sem perder sensibilidade.
    sd = st.stdev(vals) if len(vals) > 1 else 0.0
    rsd = max(1.4826 * mad, 0.85 * sd)
    if rsd == 0:
        rsd = abs(med) * 0.02 or 1.0
    b.median, b.rsd, b.confiavel = med, rsd, len(vals) >= min_n
    return b


# ---------------------------------------------------------------------- avaliação
@dataclass
class Assessment:
    key: str
    valor: float | None
    base: Baseline
    z: float | None
    sentido: str          # acima | abaixo | dentro | sem_base | sem_dado
    leitura: str          # favoravel | desfavoravel | neutro
    padrao: str = "estavel"  # estavel | pontual | persistente
    media7: float | None = None
    dias_fora7: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "metrica": self.key, "rotulo": METRICS[self.key].label,
            "valor": display_value(self.key, self.valor),
            "mediana": display_value(self.key, self.base.mediana),
            "faixa": [display_value(self.key, x) for x in self.base.faixa] if self.base.faixa else None,
            "z": round(self.z, 2) if self.z is not None else None,
            "sentido": self.sentido, "leitura": self.leitura, "padrao": self.padrao,
            "base_dias": self.base.n, "base_confiavel": self.base.confiavel,
            "media7": display_value(self.key, self.media7), "dias_fora7": self.dias_fora7,
            "unidade": METRICS[self.key].unit,
        }


def _leitura(key: str, sentido: str) -> str:
    bom = METRICS[key].bom
    if sentido not in ("acima", "abaixo") or bom == "neutro":
        return "neutro"
    return "favoravel" if (sentido == "acima") == (bom == "alto") else "desfavoravel"


def assess(store: Store, key: str, day: date, window: int = 28, min_n: int = 10) -> Assessment:
    v = store.value(key, day)
    b = baseline(store, key, day, window, min_n)
    if v is None:
        return Assessment(key, None, b, None, "sem_dado", "neutro")
    z = b.z(v)
    if z is None:
        return Assessment(key, v, b, None, "sem_base", "neutro")
    sentido = "acima" if z >= Z_FORA else "abaixo" if z <= -Z_FORA else "dentro"
    a = Assessment(key, v, b, z, sentido, _leitura(key, sentido))

    # Persistência: últimos 7 dias contra a linha de base que antecede esses 7 dias
    ref = baseline(store, key, day - timedelta(days=6), window, min_n)
    recent = store.values(key, day - timedelta(days=6), day)
    if ref.median is not None and ref.rsd and len(recent) >= 4:
        zs = [ref.z(x) for _, x in recent]
        mean7 = st.mean(_tf(key, x) for _, x in recent)
        a.media7 = _itf(key, mean7)
        zmean = (mean7 - ref.median) / ref.rsd
        up = sum(1 for z_ in zs if z_ is not None and z_ >= Z_FORA)
        down = sum(1 for z_ in zs if z_ is not None and z_ <= -Z_FORA)
        a.dias_fora7 = max(up, down)
        if (up >= 4 and zmean >= Z_PERSIST) or (down >= 4 and zmean <= -Z_PERSIST):
            a.padrao = "persistente"
            if a.sentido == "dentro":  # tendência persistente mesmo que hoje esteja na faixa
                a.sentido = "acima" if up >= 4 else "abaixo"
                a.leitura = _leitura(key, a.sentido)
        elif abs(z) >= Z_PONTUAL:
            a.padrao = "pontual"
    elif abs(z) >= Z_PONTUAL:
        a.padrao = "pontual"
    return a


# ---------------------------------------------------------------------- tendência
def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


@dataclass
class Trend:
    key: str
    n: int
    inclinacao_semana: float | None = None  # unidade de exibição por semana
    p: float | None = None
    direcao: str = "indefinida"  # subindo | descendo | estavel | indefinida
    variacao_pct: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"metrica": self.key, "rotulo": METRICS[self.key].label, "n": self.n,
                "inclinacao_semana": None if self.inclinacao_semana is None else round(self.inclinacao_semana, 2),
                "p": None if self.p is None else round(self.p, 4), "direcao": self.direcao,
                "variacao_pct": None if self.variacao_pct is None else round(self.variacao_pct, 1),
                "unidade": METRICS[self.key].unit}


def trend(store: Store, key: str, end: date, days: int = 28) -> Trend:
    pts = store.values(key, end - timedelta(days=days - 1), end)
    t = Trend(key, len(pts))
    if len(pts) < 10:
        return t
    xs = [(p[0] - pts[0][0]).days for p in pts]
    ys = [display_value(key, p[1]) for p in pts]
    slopes = [(ys[j] - ys[i]) / (xs[j] - xs[i])
              for i in range(len(xs)) for j in range(i + 1, len(xs)) if xs[j] != xs[i]]
    slope = st.median(slopes)
    # Mann–Kendall
    n = len(ys)
    s = sum((ys[j] > ys[i]) - (ys[j] < ys[i]) for i in range(n) for j in range(i + 1, n))
    var = n * (n - 1) * (2 * n + 5) / 18
    zmk = (s - 1) / math.sqrt(var) if s > 0 else (s + 1) / math.sqrt(var) if s < 0 else 0
    p = 2 * (1 - _norm_cdf(abs(zmk)))
    t.inclinacao_semana, t.p = slope * 7, p
    med = st.median(ys)
    span = xs[-1] - xs[0]
    if med:
        t.variacao_pct = 100 * slope * span / abs(med)
    if p < 0.05 and t.variacao_pct is not None and abs(t.variacao_pct) >= 3:
        t.direcao = "subindo" if slope > 0 else "descendo"
    else:
        t.direcao = "estavel"
    return t


# ------------------------------------------------------------------- associações
PAIRS = [
    # (x, defasagem em dias, y, descrição)
    ("sleep_seconds", 0, "hrv_last_night", "duração do sono e HRV da mesma noite"),
    ("sleep_seconds", 0, "bb_max", "duração do sono e Body Battery ao acordar"),
    ("stress_avg", 1, "sleep_score", "estresse do dia e pontuação do sono da noite seguinte"),
    ("training_load", 1, "hrv_last_night", "carga de treino e HRV da noite seguinte"),
    ("training_load", 1, "resting_hr", "carga de treino e FC de repouso do dia seguinte"),
    ("steps", 1, "sleep_score", "passos do dia e pontuação do sono da noite seguinte"),
    ("sleep_score", 0, "stress_avg", "pontuação do sono e estresse médio do dia"),
    ("intensity_minutes", 1, "training_readiness", "minutos de intensidade e prontidão do dia seguinte"),
]


def _ranks(v: list[float]) -> list[float]:
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x: list[float], y: list[float]) -> tuple[float, float] | None:
    n = len(x)
    if n < 8:
        return None
    rx, ry = _ranks(x), _ranks(y)
    try:
        rho = st.correlation(rx, ry)
    except st.StatisticsError:
        return None
    rho = max(-0.999999, min(0.999999, rho))
    z = math.atanh(rho) * math.sqrt(n - 3)
    return rho, 2 * (1 - _norm_cdf(abs(z)))


def correlations(store: Store, end: date, days: int = 90, min_n: int = 14) -> list[dict[str, Any]]:
    out = []
    start = end - timedelta(days=days - 1)
    for xk, lag, yk, desc in PAIRS:
        xs, ys = [], []
        cur = start
        while cur <= end:
            xv, yv = store.value(xk, cur - timedelta(days=lag)), store.value(yk, cur)
            if xv is not None and yv is not None:
                xs.append(xv)
                ys.append(yv)
            cur += timedelta(days=1)
        res = spearman(xs, ys) if len(xs) >= min_n else None
        if res is None:
            out.append({"x": xk, "y": yk, "defasagem": lag, "descricao": desc, "n": len(xs),
                        "rho": None, "p": None, "forca": "dados insuficientes"})
            continue
        rho, p = res
        a = abs(rho)
        forca = "fraca" if a < 0.3 else "moderada" if a < 0.5 else "forte"
        if p >= 0.05:
            forca = "sem associação clara"
        out.append({"x": xk, "y": yk, "defasagem": lag, "descricao": desc, "n": len(xs),
                    "rho": round(rho, 2), "p": round(p, 4), "forca": forca})
    return out


# ------------------------------------------------------------------- recuperação
@dataclass
class Recovery:
    status: str                    # boa | moderada | atencao | indeterminada
    sinais: list[str] = field(default_factory=list)
    positivos: list[str] = field(default_factory=list)
    persistente: bool = False
    dias_com_sinais: int = 0


RECOVERY_KEYS = ["hrv_last_night", "resting_hr", "sleep_score", "sleep_seconds",
                 "bb_max", "training_readiness", "sleep_stress_avg"]


def recovery(store: Store, day: date, window: int = 28, min_n: int = 10) -> Recovery:
    def signals_for(dd: date) -> tuple[list[str], list[str], int]:
        neg, pos, avail = [], [], 0
        for k in RECOVERY_KEYS:
            a = assess(store, k, dd, window, min_n) if dd == day else _quick(store, k, dd, window, min_n)
            if a.z is None:
                continue
            avail += 1
            if a.leitura == "desfavoravel":
                neg.append(METRICS[k].label)
            elif a.leitura == "favoravel":
                pos.append(METRICS[k].label)
        return neg, pos, avail

    neg, pos, avail = signals_for(day)
    if avail < 2:
        return Recovery("indeterminada")
    hist = [len(signals_for(day - timedelta(days=i))[0]) >= 2 for i in range(1, 4)]
    r = Recovery("boa", neg, pos)
    if len(neg) >= 3 or ("HRV (média da noite)" in neg and "FC de repouso" in neg):
        r.status = "atencao"
    elif len(neg) >= 1:
        r.status = "moderada"
    r.dias_com_sinais = sum(hist) + (1 if len(neg) >= 2 else 0)
    r.persistente = len(neg) >= 2 and sum(hist) >= 2
    return r


def _quick(store: Store, key: str, day: date, window: int, min_n: int) -> Assessment:
    v = store.value(key, day)
    b = baseline(store, key, day, window, min_n)
    z = b.z(v)
    if z is None:
        return Assessment(key, v, b, None, "sem_base", "neutro")
    sentido = "acima" if z >= Z_FORA else "abaixo" if z <= -Z_FORA else "dentro"
    return Assessment(key, v, b, z, sentido, _leitura(key, sentido))


# ------------------------------------------------------------------ regularidade
def sleep_regularity(store: Store, end: date, days: int = 14) -> dict[str, Any] | None:
    starts = []
    for i in range(days):
        r = store.raw(end - timedelta(days=i))
        s = r.get("sleep_start") if r else None
        if s and "T" in s:
            hh, mm = s.split("T")[1][:5].split(":")
            minutes = int(hh) * 60 + int(mm)
            starts.append((minutes - 18 * 60) % (24 * 60))  # minutos após 18h
    if len(starts) < 5:
        return None
    sd = st.pstdev(starts)
    med = st.median(starts)
    hh = int((med + 18 * 60) // 60) % 24
    return {"n": len(starts), "desvio_min": round(sd), "horario_mediano": f"{hh:02d}:{int(med % 60):02d}"}


# --------------------------------------------------------------------- períodos
def period_stats(store: Store, key: str, start: date, end: date) -> dict[str, Any]:
    vals = [display_value(key, v) for _, v in store.values(key, start, end)]
    if not vals:
        return {"n": 0, "media": None, "min": None, "max": None}
    return {"n": len(vals), "media": round(st.mean(vals), 2), "min": min(vals), "max": max(vals)}


def compare_periods(store: Store, keys: list[str], a: tuple[date, date], b: tuple[date, date]) -> list[dict[str, Any]]:
    out = []
    for k in keys:
        pa, pb = period_stats(store, k, *a), period_stats(store, k, *b)
        diff = pct = None
        if pa["media"] is not None and pb["media"] is not None:
            diff = round(pa["media"] - pb["media"], 2)
            pct = round(100 * diff / pb["media"], 1) if pb["media"] else None
        sentido = "neutro"
        if diff is not None and pct is not None and abs(pct) >= 5:
            sentido = _leitura(k, "acima" if diff > 0 else "abaixo")
        out.append({"metrica": k, "rotulo": METRICS[k].label, "unidade": METRICS[k].unit,
                    "atual": pa, "anterior": pb, "diferenca": diff, "variacao_pct": pct,
                    "leitura": sentido})
    return out


def day_snapshot(db: Database, day: date, window: int = 28, min_n: int = 10) -> dict[str, Any]:
    store = load_store(db, day, window + 60)
    assessments = {k: assess(store, k, day, window, min_n) for k in METRICS}
    rec = recovery(store, day, window, min_n)
    return {
        "dia": day.isoformat(),
        "avaliacoes": {k: a.as_dict() for k, a in assessments.items() if a.valor is not None},
        "ausentes": [k for k in CORE if assessments[k].valor is None],
        "recuperacao": rec.__dict__,
        "regularidade_sono": sleep_regularity(store, day),
    }
