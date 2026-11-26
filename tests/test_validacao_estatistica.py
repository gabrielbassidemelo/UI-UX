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
Apos as correcoes (docs/VALIDACAO_ESTATISTICA.md secao 10; desenvolvimento, escala 0,5): FA/dia alertas = 0,148
  (individual 0,128; composto 0,046); changepoint qualquer dos 7 = 0,129 (so HRV 0,016); tendencia qualquer = 0,126
  (so HRV 0,025). As faixas abaixo foram escolhidas nesses valores, com folga, e seguem sendo travas de regressao.
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
    cf = {sim.semente("confirmacao", c, i) for c in range(60) for i in range(0, 300)}
    assert not ds & av_ and not ds & cf and not av_ & cf
    assert min(cf) >= 9_000_000
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
    # dev: 0,21 / 0,035 / 0,148 (alertas, apos a correcao; era 0,18). Faixas largas: so travam piora grande.
    assert fa_qualquer <= 0.33, f"persistente qualquer indicador: {fa_qualquer:.3f}"
    assert fa_hrv <= 0.12, f"persistente so HRV: {fa_hrv:.3f}"
    assert fa_alerta <= 0.24, f"alertas: {fa_alerta:.3f}"
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
    assert k_al / n_al >= 0.65, f"alertas: {k_al}/{n_al}"   # dev apos correcao: ~0,9 (2 dp); folga larga


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


# ------------------------------------------------------------------ detectores corrigidos
def _store_de(linhas: dict):
    """Store em memoria a partir de {data_iso: {indicador: valor}} (sem banco)."""
    from healthmon import analysis as an
    s_ = an.Store.__new__(an.Store)
    s_.rows = linhas
    s_.start, s_.end = sim.datas()[0], sim.datas()[-1]
    return s_


def _ar1(rng, n, phi):
    import math
    x, out = rng.gauss(0, 1), []
    for _ in range(n + 30):
        x = phi * x + math.sqrt(1 - phi * phi) * rng.gauss(0, 1)
        out.append(x)
    return out[30:]


def test_changepoint_calibrado_sob_ruido_independente(amb):
    """Era xfail(strict) (CUSUM k=0,5 h=5 disparava em ~20% dos dias com ruido i.i.d.). Corrigido: pre-branqueamento,
    k/h recalibrados no desenvolvimento e resultado limitado a mudanca em curso."""
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
    assert hit / tot < 0.05  # criterio razoavel: falso alarme bilateral < 5% por dia sob ruido i.i.d. (dev: ~2%)


def test_changepoint_e_tendencia_calibrados_sob_autocorrelacao():
    """Um indicador (FC de repouso) com AR(1) phi 0,5 e 0,7, sem evento. Antes: CUSUM 64-75% e tendencia ~25-30%
    dos dias (dev). Faixas largas sobre o desenvolvimento apos a correcao (changepoint ~4-8%, tendencia ~3-4%)."""
    import random
    from healthmon import analysis as an, stats
    datas = sim.datas()
    cp = tr = tot = 0
    for phi in (0.5, 0.7):
        rng = random.Random(sim.semente(CONJUNTO, 79, int(phi * 10)))
        for _ in range(12):
            x = _ar1(rng, sim.DIAS, phi)
            st_ = _store_de({datas[t].isoformat(): {"resting_hr": 58 + 2.6 * x[t]} for t in range(sim.DIAS)})
            for t in range(100, 180):
                tot += 1
                cp += stats.changepoint(st_, "resting_hr", datas[t]) is not None
                tr += an.trend(st_, "resting_hr", datas[t]).direcao != "estavel"
    assert cp / tot < 0.15, f"changepoint com autocorrelacao: {cp / tot:.3f}"
    assert tr / tot < 0.15, f"tendencia (bilateral) com autocorrelacao: {tr / tot:.3f}"


def test_changepoint_detecta_degrau_e_so_reporta_mudanca_recente():
    from healthmon import stats
    datas = sim.datas()
    import random
    rng = random.Random(5)
    def serie(t_ini, t_fim):
        return {datas[t].isoformat(): {"resting_hr": 58 + 2.6 * rng.gauss(0, 1) + (6 if t_ini <= t < t_fim else 0)}
                for t in range(sim.DIAS)}
    # degrau de +2,3 dp mantido: detectado e marcado como "alta"
    s1 = _store_de(serie(120, 180))
    r = stats.changepoint(s1, "resting_hr", datas[150])
    assert r and r["direcao"] == "alta" and r["inicio"] >= datas[120].isoformat()
    # a mesma mudanca, terminada ha ~35 dias, NAO e mais reportada (so mudanca recente/em curso)
    s2 = _store_de(serie(100, 115))
    assert stats.changepoint(s2, "resting_hr", datas[155]) is None


def test_trend_detecta_rampa_clara_e_corrige_autocorrelacao():
    from healthmon import analysis as an
    datas = sim.datas()
    s_ = _store_de({datas[t].isoformat(): {"resting_hr": 55.0 + 0.4 * t + (t % 3) * 0.3} for t in range(28)})
    t = an.trend(s_, "resting_hr", datas[27])
    assert t.direcao == "subindo" and t.p is not None and t.p < 0.001
    # o fator de variancia de Mann-Kendall nunca fica < 1 e cresce com autocorrelacao positiva forte
    import random
    rng = random.Random(3)
    xs = list(range(28))
    ruido = _ar1(rng, 28, 0.8)
    assert an._mk_var_factor(ruido, 0.0, xs) >= 1.0


def test_valor_implausivel_ja_gravado_e_descartado_na_carga(amb):
    from healthmon import analysis as an
    db = amb["db"]
    datas = sim.datas()
    dias = [{"hrv_last_night": 55.0, "resting_hr": 58.0, "steps": 8000.0} for _ in range(30)]
    dias[10] = {"hrv_last_night": 400.0, "resting_hr": 200.0, "steps": 0.0}   # HRV/FC impossiveis; 0 passos e valido
    av.gravar(db, dias)
    store = an.Store(db, datas[0], datas[29])
    assert store.value("hrv_last_night", datas[10]) is None
    assert store.value("resting_hr", datas[10]) is None
    assert store.value("steps", datas[10]) == 0.0
    assert len(store.values("hrv_last_night", datas[0], datas[29])) == 29
    # sem o valor absurdo a base nao e inflada: dispersao ~0 cai no piso e o dia 11 e avaliado normalmente
    assert an.assess(store, "hrv_last_night", datas[11]).base.n == 10


def test_alerta_composto_exige_dois_grupos_fisiologicos():
    """Sinais so do grupo 'sono' (3 indicadores ruins ao mesmo tempo) nao bastam para o alerta composto."""
    from healthmon import analysis as an
    datas = sim.datas()
    rng = __import__("random").Random(11)
    base = {"hrv_last_night": 55.0, "resting_hr": 58.0, "sleep_score": 78.0, "sleep_seconds": 26000.0,
            "sleep_stress_avg": 16.0}
    def serie(ruins):
        rows = {}
        for t in range(60):
            d = {k: v * (1 + 0.02 * rng.gauss(0, 1)) for k, v in base.items()}
            if t >= 54:  # ultimos 6 dias com os indicadores 'ruins' muito fora da faixa
                for k, f in ruins.items():
                    d[k] = d[k] * f
            rows[datas[t].isoformat()] = d
        return _store_de(rows)
    so_sono = serie({"sleep_score": 0.7, "sleep_seconds": 0.7, "sleep_stress_avg": 1.5})
    dia = datas[59]
    assert an.recovery(so_sono, dia).persistente                              # regra antiga: so sono ja bastava
    assert not an.recovery(so_sono, dia, grupos_independentes=True).persistente
    dois = serie({"sleep_score": 0.7, "hrv_last_night": 0.6})                # autonomico + sono
    r = an.recovery(dois, dia, grupos_independentes=True)
    assert r.persistente and set(r.grupos) >= {"autonomico", "sono"}


def test_alerta_isolado_exige_5_de_7_dias_mas_persistente_continua_4_de_7():
    from healthmon import analysis as an
    datas = sim.datas()
    ciclo = [56.0, 57.0, 58.0, 59.0, 60.0]
    def serie(n_ruins):
        rows = {}
        for t in range(45):
            v = ciclo[t % 5]
            if t >= 38:                          # ultimos 7 dias: n_ruins com FC alta (z ~ +2,7), resto na mediana
                v = 62.0 if (t - 38) < n_ruins else 58.0
            rows[datas[t].isoformat()] = {"resting_hr": v}
        return _store_de(rows)
    a4 = an.assess(serie(4), "resting_hr", datas[44])
    assert a4.padrao == "persistente" and a4.dias_fora7 == 4        # regra de persistencia do assess preservada
    assert not an.persistente_isolado(a4)
    a5 = an.assess(serie(5), "resting_hr", datas[44])
    assert a5.padrao == "persistente" and an.persistente_isolado(a5)


def test_changepoint_e_tendencia_mantem_formato_de_saida():
    from healthmon import stats
    datas = sim.datas()
    import random
    rng = random.Random(9)
    s_ = _store_de({datas[t].isoformat(): {"resting_hr": 58 + 2.6 * rng.gauss(0, 1) + (8 if t >= 120 else 0)}
                    for t in range(sim.DIAS)})
    r = stats.changepoint(s_, "resting_hr", datas[145])
    assert r is None or set(r) == {"direcao", "inicio", "detectada"}
    assert r is not None and r["direcao"] == "alta"


# ------------------------------------------------------------------ calculo a mao e negativos (SIMULADO / sintetico)
def test_mann_kendall_confere_com_calculo_a_mao():
    """Serie sintetica 50..59 (10 dias): S = 45, var = 10*9*25/18 = 125, z = (45-1)/sqrt(125) = 3,9354,
    p bilateral ~ 8,3e-5; inclinacao 1/dia = 7/semana; variacao = 100*1*9/54,5 = 16,51%. Serie perfeitamente
    linear: residuos iguais, fator de autocorrelacao = 1 (nao altera a variancia)."""
    import math
    from healthmon import analysis as an
    datas = sim.datas()
    s_ = _store_de({datas[t].isoformat(): {"resting_hr": 50.0 + t} for t in range(10)})
    t = an.trend(s_, "resting_hr", datas[9])
    z = 44 / math.sqrt(125)
    p_mao = 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))
    assert abs(t.p - p_mao) < 1e-9 and 8.0e-5 < t.p < 8.6e-5
    assert abs(t.inclinacao_semana - 7.0) < 1e-9
    assert abs(t.variacao_pct - 100 * 9 / 54.5) < 1e-6
    assert t.direcao == "subindo"


def test_valor_absurdo_isolado_nao_dispara_changepoint_nem_tendencia():
    """Negativo: serie estavel (i.i.d.) com UM valor impossivel (FC 200, HRV 400) no ultimo dia nao pode gerar
    mudanca de nivel nem tendencia; o valor e descartado antes das estatisticas."""
    import random
    from healthmon import analysis as an, stats
    datas = sim.datas()
    rng = random.Random(sim.semente(CONJUNTO, 80, 0))
    rows = {datas[t].isoformat(): {"resting_hr": round(58 + 2.0 * rng.gauss(0, 1), 1),
                                   "hrv_last_night": round(55 + 6.0 * rng.gauss(0, 1), 1)} for t in range(70)}
    rows[datas[69].isoformat()] = {"resting_hr": 200.0, "hrv_last_night": 400.0}
    s_ = _store_de(rows)
    for k in ("resting_hr", "hrv_last_night"):
        assert s_.value(k, datas[69]) is None
        assert stats.changepoint(s_, k, datas[69]) is None
        assert an.trend(s_, k, datas[69]).direcao == "estavel"


def test_changepoint_com_lacunas_nao_quebra_e_nao_alarma_em_serie_estavel():
    """Entrada incompleta: 30% dos dias ausentes e blocos de 5 dias sem dado, ruido i.i.d. sintetico. Sem excecao,
    sem alarme (a pre-branqueamento usa so pares de dias consecutivos) e dados insuficientes devolvem None."""
    import random
    from healthmon import stats
    datas = sim.datas()
    rng = random.Random(sim.semente(CONJUNTO, 81, 0))
    rows = {}
    for t in range(sim.DIAS):
        if rng.random() < 0.30 or 100 <= t < 105 or 140 <= t < 145:
            continue
        rows[datas[t].isoformat()] = {"resting_hr": 58 + 2.6 * rng.gauss(0, 1)}
    s_ = _store_de(rows)
    achados = sum(stats.changepoint(s_, "resting_hr", datas[t]) is not None for t in range(120, 180))
    assert achados / 60 < 0.15
    assert stats.changepoint(_store_de({}), "resting_hr", datas[100]) is None
    poucos = _store_de({datas[t].isoformat(): {"resting_hr": 58.0 + t} for t in range(0, 40, 4)})   # 10 pontos, < 35
    assert stats.changepoint(poucos, "resting_hr", datas[39]) is None
