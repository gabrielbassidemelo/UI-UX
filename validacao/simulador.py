"""Simulador SINTETICO de series diarias de saude (independente de healthmon/demo.py).

Tudo aqui e SIMULADO. Nenhum dado real e usado e nada disto valida o sistema com dados reais.

Modelo (por serie / "pessoa" simulada):
- 7 indicadores principais com media e desvio marginal plausiveis e variados entre pessoas;
- ruido AR(1) por indicador (phi sorteado em [0,3; 0,7]) + um fator latente comum de
  "recuperacao" (tambem AR(1)) que correlaciona os indicadores: HRV, pontuacao e duracao
  do sono sobem juntos; FC de repouso, estresse e estresse no sono descem;
- efeito de dia da semana (mais passos no sabado, menos no domingo, sono mais longo no fim de semana);
- HRV em escala log (como o sistema), FC/escores arredondados, valores limitados a faixas fisicas.

Corrupcoes e eventos (aplicados depois do ruido):
- dados ausentes aleatorios (por valor) e em blocos de dias (pulseira fora do pulso);
- erros de sensor: valores absurdos isolados e leituras "travadas" (mesmo valor por 3 a 6 dias);
- mudancas de rotina (degraus temporarios em passos/sono: ferias, viagem) - NAO sao evento de saude;
- perturbacao fisiologica temporaria de 3 a 10 dias (tipo doenca leve: HRV cai, FC de repouso
  sobe, sono piora, estresse sobe, passos caem, todos juntos);
- mudanca abrupta persistente (degrau) e deriva gradual (rampa de 60 dias que depois se mantem).

A magnitude M de um evento e dada em DESVIOS marginais de cada indicador (HRV em escala log).
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

DIAS = 180
INICIO = date(2026, 1, 5)  # uma segunda-feira

# Sementes: dois conjuntos DISJUNTOS por construcao (faixas de inteiros que nao se cruzam).
SEMENTE_BASE = {"desenvolvimento": 10_000, "avaliacao": 5_000_000, "confirmacao": 20_000_000}  # >= 9.000.000; 9M exato colidiria com avaliacao (5M + cid 40..43 * 100k)
PASSO_CENARIO = 100_000  # cada cenario/celula usa uma faixa propria de ate 100 mil sementes

INDICADORES = ["hrv_last_night", "resting_hr", "sleep_score", "sleep_seconds",
               "sleep_stress_avg", "stress_avg", "steps"]

# media tipica, desvio marginal tipico (HRV: desvio em log), carga no fator latente "boa recuperacao"
# e peso do efeito de evento (fracao de M aplicada ao indicador; sinal = direcao ADVERSA).
PARAMS = {
    "hrv_last_night":   dict(mu=55.0,    sd=0.18, carga=+0.65, evento=-1.0, log=True),
    "resting_hr":       dict(mu=58.0,    sd=2.6,  carga=-0.60, evento=+1.0),
    "sleep_score":      dict(mu=76.0,    sd=8.0,  carga=+0.50, evento=-1.0),
    "sleep_seconds":    dict(mu=25920.0, sd=3000.0, carga=+0.40, evento=-0.8),
    "sleep_stress_avg": dict(mu=17.0,    sd=4.0,  carga=-0.50, evento=+1.0),
    "stress_avg":       dict(mu=30.0,    sd=6.0,  carga=-0.35, evento=+0.8),
    "steps":            dict(mu=8500.0,  sd=2800.0, carga=+0.15, evento=-0.5),
}
# efeito de dia da semana em desvios (seg..dom)
SEMANA = {
    "steps":         [0.0, 0.0, 0.0, 0.0, -0.1, +0.3, -0.5],
    "sleep_seconds": [-0.1, -0.1, -0.1, 0.0, +0.2, +0.4, +0.2],
    "stress_avg":    [+0.2, +0.2, +0.1, 0.0, -0.1, -0.3, -0.3],
    "sleep_score":   [0.0, 0.0, 0.0, 0.0, 0.0, +0.1, +0.1],
}
LIMITES = {"hrv_last_night": (8, 250), "resting_hr": (38, 110), "sleep_score": (20, 100),
           "sleep_seconds": (7200, 43200), "sleep_stress_avg": (3, 80), "stress_avg": (5, 90),
           "steps": (0, 60000)}
ABSURDOS = {"hrv_last_night": [4, 400], "resting_hr": [22, 200], "sleep_score": [0],
            "sleep_seconds": [0, 86000], "sleep_stress_avg": [0, 99], "stress_avg": [0, 100],
            "steps": [0, 90000]}


def semente(conjunto: str, cenario_id: int, i: int) -> int:
    """Semente deterministica; conjuntos disjuntos (ver SEMENTE_BASE)."""
    if i >= PASSO_CENARIO:
        raise ValueError("indice de serie fora da faixa do cenario")
    return SEMENTE_BASE[conjunto] + cenario_id * PASSO_CENARIO + i


def _ar1(rng: random.Random, n: int, phi: float, cauda_pesada: bool) -> list[float]:
    """AR(1) estacionario com variancia marginal 1."""
    sig = math.sqrt(1 - phi * phi)
    x = rng.gauss(0, 1)
    out = []
    for _ in range(n + 30):  # 30 dias de aquecimento descartados
        if cauda_pesada:  # t de Student com 3 gl, reescalada para variancia 1
            z = rng.gauss(0, 1) / math.sqrt(rng.gammavariate(1.5, 2.0) / 3.0) / math.sqrt(3.0)
        else:
            z = rng.gauss(0, 1)
        x = phi * x + sig * z
        out.append(x)
    return out[30:]


def gerar_serie(seed: int, cenario: dict) -> dict:
    """Gera uma serie SIMULADA.

    cenario (todas as chaves opcionais):
      ausentes_aleatorios (prob por valor), blocos (n de blocos de 3-10 dias), absurdos (prob por valor),
      travados (n de episodios), rotina (n de episodios), cauda_pesada (bool),
      evento: {"tipo": "perturbacao"|"degrau"|"deriva", "mag": M, "dur": D (so perturbacao),
               "ini": dia, "indicadores": lista (padrao: todos)}
    Retorna {"dias": [ {indicador: valor} x DIAS ], "evento": {...} ou None, "seed": seed}.
    """
    rng = random.Random(seed)
    n = DIAS
    phi_l = rng.uniform(0.3, 0.7)
    lat = _ar1(rng, n, phi_l, False)
    cauda = bool(cenario.get("cauda_pesada"))
    ev = cenario.get("evento")
    ini = None
    if ev:
        # a deriva dura 60 dias e precisa terminar antes do fim da serie; os demais eventos comecam entre os dias 90 e 130
        ini = ev["ini"] if ev.get("ini") is not None else (
            rng.randint(85, 100) if ev["tipo"] == "deriva" else rng.randint(90, 130))
        ev = dict(ev, ini=ini)
    inds_ev = set(ev.get("indicadores", INDICADORES)) if ev else set()

    brutas: dict[str, list[float]] = {}
    sds: dict[str, float] = {}
    for k in INDICADORES:
        p = PARAMS[k]
        mu = p["mu"] * rng.uniform(0.65, 1.6) if p.get("log") else p["mu"] * rng.uniform(0.92, 1.08)
        sd = p["sd"] * rng.uniform(0.8, 1.25)
        sds[k] = sd
        phi = rng.uniform(0.3, 0.7)
        e = _ar1(rng, n, phi, cauda)
        c = p["carga"]
        w = math.sqrt(1 - c * c)
        amp = rng.uniform(0.6, 1.4)
        serie = []
        for t in range(n):
            wd = (INICIO + timedelta(days=t)).weekday()
            z = c * lat[t] + w * e[t] + amp * SEMANA.get(k, [0] * 7)[wd]
            # efeito do evento em desvios (direcao adversa ja embutida no sinal)
            if ev and k in inds_ev and t >= ini:
                M, tipo = ev["mag"], ev["tipo"]
                if tipo == "perturbacao" and t < ini + ev["dur"]:
                    z += p["evento"] * M
                elif tipo == "degrau":
                    z += p["evento"] * M
                elif tipo == "deriva":
                    z += p["evento"] * M * min(1.0, (t - ini + 1) / 60.0)
            v = mu * math.exp(sd * z) if p.get("log") else mu + sd * z
            serie.append(v)
        brutas[k] = serie

    # mudancas de rotina (nao sao evento de saude): degrau temporario em passos e sono
    for _ in range(cenario.get("rotina", 0)):
        dur, t0 = rng.randint(10, 21), rng.randint(40, n - 25)
        sinal = rng.choice([-1, 1])
        mag = rng.uniform(1.0, 1.5)
        for t in range(t0, min(n, t0 + dur)):
            brutas["steps"][t] += sinal * mag * sds["steps"]
            brutas["sleep_seconds"][t] += -sinal * 0.5 * sds["sleep_seconds"]
            brutas["sleep_score"][t] += -sinal * 0.3 * sds["sleep_score"]

    # limites fisicos e arredondamento
    dias: list[dict[str, float | None]] = []
    for t in range(n):
        d: dict[str, float | None] = {}
        for k in INDICADORES:
            lo, hi = LIMITES[k]
            v = min(hi, max(lo, brutas[k][t]))
            d[k] = round(v, 1) if k == "hrv_last_night" else float(round(v))
        dias.append(d)

    # leituras travadas: repete o valor do dia anterior por 3-6 dias em um indicador
    for _ in range(cenario.get("travados", 0)):
        k = rng.choice(INDICADORES)
        t0, dur = rng.randint(35, n - 8), rng.randint(3, 6)
        base = dias[t0 - 1][k]
        for t in range(t0, t0 + dur):
            dias[t][k] = base
    # valores absurdos isolados
    pa = cenario.get("absurdos", 0.0)
    if pa:
        for t in range(n):
            for k in INDICADORES:
                if rng.random() < pa:
                    dias[t][k] = float(rng.choice(ABSURDOS[k]))
    # ausentes aleatorios por valor e em blocos de dias inteiros
    pm = cenario.get("ausentes_aleatorios", 0.0)
    if pm:
        for t in range(n):
            for k in INDICADORES:
                if rng.random() < pm:
                    dias[t][k] = None
    for _ in range(cenario.get("blocos", 0)):
        t0, dur = rng.randint(30, n - 11), rng.randint(3, 10)
        for t in range(t0, t0 + dur):
            for k in INDICADORES:
                dias[t][k] = None
    return {"dias": dias, "evento": ev, "seed": seed}


def datas(n: int = DIAS) -> list[date]:
    return [INICIO + timedelta(days=i) for i in range(n)]
