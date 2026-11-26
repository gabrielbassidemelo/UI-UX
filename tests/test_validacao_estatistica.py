"""Versao reduzida e rapida da validacao por SIMULACAO dos detectores (dados SINTETICOS).

IMPORTANTE: tudo aqui e simulado. Passar nestes testes NAO e validacao com dados reais e NAO
comprova utilidade clinica. As faixas abaixo foram escolhidas a partir do conjunto de
DESENVOLVIMENTO (validacao/avaliar.py, execucao completa) e sao propositalmente largas: servem como
trava de regressao ("o detector mudou de comportamento"), nao como meta de qualidade. Em
particular, o falso alarme do detector persistente "qualquer indicador" (~21% por dia no
desenvolvimento) e ALTO; a faixa so impede que piore. O conjunto de avaliacao reduzido usa as
primeiras sementes do conjunto "avaliacao" (disjunto do de desenvolvimento).

Valores de referencia no desenvolvimento (series realistas, 7 indicadores):
  FA/dia assess persistente qualquer = 0,21 (IC95 0,20-0,22); so HRV = 0,035; alertas = 0,18;
  readiness fora de "normal" sem evento = 0,056;
  perturbacao 2 desvios x 10 dias: sensibilidade persistente = 1,00; so HRV = 0,78;
  readiness mantem "normal" em 15% dos dias do evento (68% com 1 desvio).
"""
from __future__ import annotations

import os
import statistics as st

import pytest

from validacao import avaliar as av
from validacao import simulador as sim

CONJUNTO = os.environ.get("HEALTHMON_VAL_CONJUNTO", "avaliacao")  # "desenvolvimento" so para depurar
N_NULO, N_NULO_CARO, JANELA_NULO = 16, 6, (90, 124)
N_PULSO, N_PULSO_CARO = 14, 6
REALISTA = av.REALISTA
PULSO_M2_D10 = dict(REALISTA, evento=dict(tipo="perturbacao", mag=2.0, dur=10))


@pytest.fixture(scope="module")
def amb():
    av.iniciar_ambiente()  # banco temporario (em /dev/shm quando existe: os upserts ficam ~5x mais rapidos)
    yield av._ENV
    av.limpar_ambiente()


def _rodar(cenario, cid, n, n_caro, janela=None, grupos_extra=True):
    out = []
    for i in range(n):
        t = dict(conjunto=CONJUNTO, grupo="nulo" if "evento" not in cenario else "pulso", nome="t", cid=cid, i=i,
                 caro=i < n_caro, cenario=cenario)
        out.append(av.avaliar_serie(t, janela=janela))
    return out


@pytest.fixture(scope="module")
def nulo(amb):
    return _rodar(REALISTA, 1, N_NULO, N_NULO_CARO, JANELA_NULO)


@pytest.fixture(scope="module")
def pulso(amb):
    return _rodar(PULSO_M2_D10, av._cid_pulso(2.0, 10), N_PULSO, N_PULSO_CARO)


def _taxa(res, det):
    f = [x for r in res if det in r["flags"] for x in r["flags"][det]]
    return sum(f) / len(f)


# ------------------------------------------------------------------ simulador e protocolo
def test_conjuntos_de_sementes_sao_disjuntos():
    ds = {sim.semente("desenvolvimento", c, i) for c in range(60) for i in range(0, 300)}
    av_ = {sim.semente("avaliacao", c, i) for c in range(60) for i in range(0, 300)}
    assert not ds & av_
    with pytest.raises(ValueError):
        sim.semente("avaliacao", 0, sim.PASSO_CENARIO)  # transbordaria para a faixa do cenario seguinte


def test_simulador_e_deterministico_e_plausivel():
    a = sim.gerar_serie(123, dict(REALISTA, rotina=1))
    b = sim.gerar_serie(123, dict(REALISTA, rotina=1))
    assert a == b and len(a["dias"]) == sim.DIAS
    obs = {k: [d[k] for d in a["dias"] if d[k] is not None] for k in sim.INDICADORES}
    # faltas aleatorias e em bloco existem, mas a serie nao esta vazia
    faltas = sum(d[k] is None for d in a["dias"] for k in sim.INDICADORES) / (sim.DIAS * 7)
    assert 0.01 < faltas < 0.20
    # valores tipicos sao fisiologicamente plausiveis (mediana); erros de sensor ficam so nas caudas
    assert 25 < st.median(obs["hrv_last_night"]) < 150
    assert 45 < st.median(obs["resting_hr"]) < 75
    assert 5 * 3600 < st.median(obs["sleep_seconds"]) < 10 * 3600


def test_simulador_correlaciona_indicadores_e_tem_autocorrelacao():
    xs, ys, ac = [], [], []
    for i in range(20):
        d = sim.gerar_serie(sim.semente(CONJUNTO, 0, i), {})["dias"]
        h, r = [x["hrv_last_night"] for x in d], [x["resting_hr"] for x in d]
        xs.append(st.correlation(h, r))
        ac.append(st.correlation(r[:-1], r[1:]))
    assert st.mean(xs) < -0.2          # HRV alta <-> FC de repouso baixa
    assert 0.25 < st.mean(ac) < 0.75   # AR(1) com phi entre 0,3 e 0,7 (mais fator comum)


# ------------------------------------------------------------------ ausencia de vazamento de futuro
def test_flags_nao_dependem_de_dados_futuros(amb):
    """Mudar radicalmente os dias POSTERIORES a um dia t nao pode alterar o estado dos detectores em t."""
    from healthmon import analysis as an
    db, cfg = amb["db"], amb["cfg"]
    serie = sim.gerar_serie(sim.semente(CONJUNTO, 77, 0), REALISTA)
    dias = sim.datas()
    t = 120
    grupos = {"assess", "tendencia", "changepoint", "base", "alertas", "readiness"}
    av.gravar(db, serie["dias"])
    antes = av.flags_dia(an.Store(db, dias[0], dias[179]), db, cfg, dias[t], grupos)
    futuro = [dict(d) for d in serie["dias"]]
    for k in range(t + 1, 180):  # futuro catastrofico: valores absurdos em tudo
        futuro[k] = {"hrv_last_night": 5.0, "resting_hr": 120.0, "sleep_score": 10.0, "sleep_seconds": 7200.0,
                     "sleep_stress_avg": 70.0, "stress_avg": 90.0, "steps": 0.0}
    av.gravar(db, futuro)
    depois = av.flags_dia(an.Store(db, dias[0], dias[179]), db, cfg, dias[t], grupos)
    assert antes == depois


# ------------------------------------------------------------------ desempenho em faixas largas
def test_falso_alarme_sem_evento_dentro_da_faixa(nulo):
    fa_qualquer = _taxa(nulo, "assess_persistente")
    fa_hrv = _taxa(nulo, "assess_persistente_hrv")
    fa_alerta = _taxa(nulo, "alertas")
    # dev: 0,21 / 0,035 / 0,18 (series realistas). Faixas largas: so travam piora grande.
    assert fa_qualquer <= 0.33, f"persistente qualquer indicador: {fa_qualquer:.3f}"
    assert fa_hrv <= 0.12, f"persistente so HRV: {fa_hrv:.3f}"
    assert fa_alerta <= 0.30, f"alertas: {fa_alerta:.3f}"
    # o detector nao pode estar mudo (um detector que nunca dispara tambem seria um defeito)
    assert fa_qualquer > 0.02


def test_readiness_nao_reduz_treino_sem_evento_alem_da_faixa(nulo):
    rs = [r for r in nulo if "readiness_nao_normal" in r["flags"]]
    assert rs
    taxa = _taxa(rs, "readiness_nao_normal")
    assert taxa <= 0.18, f"readiness fora de 'normal' sem evento: {taxa:.3f} (dev 0,056)"
    niveis = {x for r in rs for x in r["flags"]["_nivel"]}
    assert niveis <= {"normal", "reduzido", "recuperacao"}  # sem check-in nunca deve haver "parar"


def test_sensibilidade_a_perturbacao_de_2_desvios_por_10_dias(pulso):
    k = sum(any(r["flags"]["assess_persistente"][:r["dur"]]) for r in pulso)
    k_hrv = sum(any(r["flags"]["assess_persistente_hrv"][:r["dur"]]) for r in pulso)
    k_al = sum(any(r["flags"]["alertas"][:r["dur"]]) for r in pulso if "alertas" in r["flags"])
    n_al = sum("alertas" in r["flags"] for r in pulso)
    n = len(pulso)
    # dev: 1,00 (qualquer), 0,78 (so HRV), alertas 1,00 -> pisos largos
    assert k / n >= 0.75, f"persistente qualquer: {k}/{n}"
    assert k_hrv / n >= 0.30, f"persistente so HRV: {k_hrv}/{n}"
    assert k_al / n_al >= 0.70, f"alertas: {k_al}/{n_al}"


def test_readiness_nao_mantem_treino_normal_durante_perturbacao_forte(pulso):
    rs = [r for r in pulso if "_nivel" in r["flags"]]
    assert rs
    normais = sum(x == "normal" for r in rs for x in r["flags"]["_nivel"][:r["dur"]])
    dias = sum(r["dur"] for r in rs)
    frac = normais / dias
    # dev (2 desvios, 3-10 d): 0,28 dos dias com 'normal'; so 10 d: 0,15. Teto largo.
    assert frac <= 0.50, f"readiness manteve 'normal' em {frac:.2f} dos dias de perturbacao forte"


def test_atraso_de_deteccao_e_curto_para_perturbacao_forte(pulso):
    atrasos = [r["flags"]["assess_persistente"].index(1) for r in pulso if any(r["flags"]["assess_persistente"])]
    assert atrasos and st.median(atrasos) <= 3  # dev: mediana 1 dia


# ------------------------------------------------------------------ defeito conhecido (xfail estrito)
@pytest.mark.xfail(strict=True, reason=(
    "DEFEITO do produto (healthmon.stats.changepoint): CUSUM com k=0,5 e h=5 calibrado para ruido "
    "independente, mas nas series diarias reais/simuladas (AR(1)) 'encontra' mudanca em ~64% dos dias sem "
    "evento (phi=0,5) e ja em ~20% com ruido i.i.d.; o resultado tambem persiste por toda a janela de 60 dias. "
    "Teste NAO ajustado; remova o xfail quando o defeito for corrigido."))
def test_changepoint_calibrado_sob_ruido_independente(amb):
    import math
    import random
    from healthmon import analysis as an, stats
    db = amb["db"]
    rng = random.Random(sim.semente(CONJUNTO, 78, 0))
    hit = tot = 0
    datas = sim.datas()
    for _ in range(6):
        dias = [{"resting_hr": round(58 + 2.6 * rng.gauss(0, 1), 1)} for _ in range(sim.DIAS)]
        av.gravar(db, dias)
        store = an.Store(db, datas[0], datas[179])
        for t in range(150, 180):
            tot += 1
            hit += stats.changepoint(store, "resting_hr", datas[t]) is not None
    assert hit / tot < 0.05  # criterio razoavel: falso alarme bilateral < 5% por dia sob ruido i.i.d.
