"""Avaliador por SIMULACAO dos detectores estatisticos do healthmon (dados SINTETICOS).

Uso:
    python -m validacao.avaliar --conjunto desenvolvimento --saida /caminho/dev.json
    python -m validacao.avaliar --conjunto avaliacao --saida validacao/resultados.json

Os dados entram no sistema por Database.upsert_daily num banco temporario; os detectores sao
chamados exatamente como o produto os chama (assess, trend, changepoint, alerts.evaluate,
training.readiness). Nenhum limiar de producao e alterado. Protocolo: o conjunto de
"desenvolvimento" serve para depurar este codigo e escolher as faixas dos testes; o conjunto de
"avaliacao" (sementes disjuntas) e rodado UMA vez ao final.

Tudo e SIMULACAO: nao ha validacao com dados reais nem comprovacao de utilidade clinica.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import statistics as st
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from . import simulador as sim
from .simulador import INDICADORES

HRV = "hrv_last_night"
SISTEMA = ["assess_persistente", "assess_persistente_hrv", "assess_pontual", "tendencia",
           "changepoint", "alertas", "readiness_nao_normal"]
BASES = ["z2", "z2_hrv", "w7v28", "w7v28_hrv"]
TODOS = SISTEMA + BASES
GRUPOS_DE = {"assess_persistente": "assess", "assess_persistente_hrv": "assess", "assess_pontual": "assess",
             "tendencia": "tendencia", "changepoint": "changepoint", "alertas": "alertas",
             "readiness_nao_normal": "readiness", "z2": "base", "z2_hrv": "base", "w7v28": "base",
             "w7v28_hrv": "base"}
CAROS = {"alertas", "readiness"}  # grupos caros (leem o banco a cada dia)

# ----------------------------------------------------------------------------- cenarios
REALISTA = dict(ausentes_aleatorios=0.04, blocos=1, absurdos=0.01, travados=1)
CENARIOS_NULOS = {  # sem evento de saude
    "nulo": (0, dict()),
    "realista": (1, dict(REALISTA)),
    "rotina": (2, dict(REALISTA, rotina=2)),
    "extremos": (3, dict(cauda_pesada=True, absurdos=0.03, ausentes_aleatorios=0.04, travados=2)),
}
MAGS = [0.5, 1.0, 1.5, 2.0]
DURS = [3, 5, 7, 10]
# tamanhos (series por cenario/celula) e quantas dessas rodam tambem os detectores caros
TAMANHOS = {"nulo": (200, 60), "realista": (200, 60), "rotina": (120, 40), "extremos": (120, 40),
            "pulso": (40, 20), "degrau": (80, 30), "deriva": (80, 30)}
EVAL_NULO = (90, 179)  # dias avaliados nas series sem evento (3 blocos de 30 dias)


def _cid_pulso(m: float, d: int) -> int:
    return 10 + MAGS.index(m) * 4 + DURS.index(d)


def lista_tarefas(conjunto: str, escala: float = 1.0) -> list[dict]:
    def n(nome: str) -> tuple[int, int]:
        a, b = TAMANHOS[nome]
        return max(1, round(a * escala)), max(1, round(b * escala))
    tarefas = []
    for nome, (cid, cen) in CENARIOS_NULOS.items():
        tot, caro = n(nome)
        for i in range(tot):
            tarefas.append(dict(conjunto=conjunto, grupo="nulo", nome=nome, cid=cid, i=i, caro=i < caro,
                                cenario=cen))
    for m in MAGS:
        for d in DURS:
            tot, caro = n("pulso")
            for i in range(tot):
                cen = dict(REALISTA, evento=dict(tipo="perturbacao", mag=m, dur=d))
                tarefas.append(dict(conjunto=conjunto, grupo="pulso", nome=f"pulso_M{m}_D{d}",
                                    cid=_cid_pulso(m, d), i=i, caro=i < caro, cenario=cen, mag=m, dur=d))
    for base, tipo, off in (("degrau", "degrau", 30), ("deriva", "deriva", 40)):
        for m in MAGS:
            tot, caro = n(base)
            for i in range(tot):
                cen = dict(REALISTA, evento=dict(tipo=tipo, mag=m))
                tarefas.append(dict(conjunto=conjunto, grupo=base, nome=f"{base}_M{m}",
                                    cid=off + MAGS.index(m), i=i, caro=i < caro, cenario=cen, mag=m))
    return tarefas


# ----------------------------------------------------------------------------- ambiente
_ENV: dict = {}


def _dir_base() -> str:
    return "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else tempfile.gettempdir()


def iniciar_ambiente(base_dir: str | None = None) -> None:
    """Cria cfg e banco temporarios (uma vez por processo)."""
    from healthmon.config import init_home, load_config
    from healthmon.db import Database
    tmp = tempfile.mkdtemp(prefix="healthmon_val_", dir=base_dir or os.environ.get("HEALTHMON_VAL_BASE") or _dir_base())
    home = init_home(Path(tmp) / "home")
    cfg = load_config(home)
    cfg.data.setdefault("notificacoes", {})["cooldown_horas"] = 0  # cada dia e avaliado de forma independente
    _ENV.update(tmp=tmp, cfg=cfg, db=Database(cfg.db_path))


def limpar_ambiente() -> None:
    if _ENV.get("tmp"):
        shutil.rmtree(_ENV["tmp"], ignore_errors=True)
        _ENV.clear()


def gravar(db, dias: list[dict]) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM daily_metrics")
        c.execute("DELETE FROM alerts")
    for t, d in enumerate(sim.datas(len(dias))):
        vals = {k: v for k, v in dias[t].items() if v is not None}
        if vals:
            db.upsert_daily(d.isoformat(), vals)


# ----------------------------------------------------------------------------- detectores
def _adverso(key: str) -> int:
    from healthmon.metrics import METRICS
    return -1 if METRICS[key].bom == "alto" else +1


def _tf(key: str, v: float) -> float:
    from healthmon.metrics import METRICS
    return math.log(v) if METRICS[key].log and v > 0 else v


def _z2(store, key: str, day: date) -> int:
    """Referencia (a): z do dia contra media/desvio dos 28 dias anteriores; adverso e z >= 2."""
    v = store.value(key, day)
    if v is None:
        return 0
    ref = [_tf(key, x) for _, x in store.values(key, day - timedelta(days=28), day - timedelta(days=1))]
    if len(ref) < 10:
        return 0
    sd = st.stdev(ref)
    if sd == 0:
        return 0
    return int(_adverso(key) * (_tf(key, v) - st.mean(ref)) / sd >= 2.0)


def _w7v28(store, key: str, day: date) -> int:
    """Referencia (b): media de 7 dias contra media dos 28 dias anteriores a esses 7; adverso e >= 1 desvio."""
    r7 = [_tf(key, x) for _, x in store.values(key, day - timedelta(days=6), day)]
    r28 = [_tf(key, x) for _, x in store.values(key, day - timedelta(days=34), day - timedelta(days=7))]
    if len(r7) < 4 or len(r28) < 10:
        return 0
    sd = st.stdev(r28)
    if sd == 0:
        return 0
    return int(_adverso(key) * (st.mean(r7) - st.mean(r28)) / sd >= 1.0)


def flags_dia(store, db, cfg, day: date, grupos: set[str]) -> dict:
    """Estado (0/1) de cada detector no dia `day`, usando apenas dados ate `day`."""
    from healthmon import alerts, analysis as an, stats, training
    from healthmon.metrics import METRICS
    out: dict = {}
    if "assess" in grupos:
        per = pon = per_h = 0
        for k in INDICADORES:
            a = an.assess(store, k, day)
            if a.base.confiavel and a.leitura == "desfavoravel":
                if a.padrao == "persistente":
                    per = 1
                    per_h |= int(k == HRV)
                elif a.padrao == "pontual":
                    pon = 1
        out.update(assess_persistente=per, assess_persistente_hrv=per_h, assess_pontual=pon)
    if "tendencia" in grupos:
        f = 0
        for k in INDICADORES:
            d = an.trend(store, k, day).direcao
            bom = METRICS[k].bom
            if (d == "descendo" and bom == "alto") or (d == "subindo" and bom == "baixo"):
                f = 1
        out["tendencia"] = f
    if "changepoint" in grupos:
        f = 0
        for k in INDICADORES:
            r = stats.changepoint(store, k, day)
            if r and ((r["direcao"] == "queda") == (METRICS[k].bom == "alto")):
                f = 1
        out["changepoint"] = f
    if "base" in grupos:
        out["z2"] = int(any(_z2(store, k, day) for k in INDICADORES))
        out["z2_hrv"] = _z2(store, HRV, day)
        out["w7v28"] = int(any(_w7v28(store, k, day) for k in INDICADORES))
        out["w7v28_hrv"] = _w7v28(store, HRV, day)
    if "alertas" in grupos:
        out["alertas"] = int(len(alerts.evaluate(cfg, db, day)) > 0)
        with db.conn() as c:
            c.execute("DELETE FROM alerts")
    if "readiness" in grupos:
        nivel = training.readiness(db, cfg, day)["nivel"]
        out["readiness_nao_normal"] = int(nivel != "normal")
        out["_nivel"] = nivel
    return out


def avaliar_serie(tarefa: dict, grupos: set[str] | None = None, janela: tuple[int, int] | None = None) -> dict:
    """Gera a serie simulada, grava no banco temporario e calcula os estados diarios na janela de interesse."""
    from healthmon import analysis as an
    if not _ENV:
        iniciar_ambiente()
    db, cfg = _ENV["db"], _ENV["cfg"]
    serie = sim.gerar_serie(sim.semente(tarefa["conjunto"], tarefa["cid"], tarefa["i"]), tarefa["cenario"])
    gravar(db, serie["dias"])
    ev = serie["evento"]
    g = tarefa["grupo"]
    if g == "nulo":
        lo, hi = EVAL_NULO
    elif g == "pulso":
        lo, hi = ev["ini"], ev["ini"] + ev["dur"] + 6
    elif g == "degrau":
        lo, hi = ev["ini"] - 14, ev["ini"] + 45
    else:
        lo, hi = ev["ini"] - 7, ev["ini"] + 74
    if janela:  # usado so pelos testes reduzidos
        lo, hi = janela
    if grupos is None:
        grupos = {"assess", "tendencia", "changepoint", "base"} | (CAROS if tarefa["caro"] else set())
    datas = sim.datas()
    store = an.Store(db, datas[0], datas[hi])
    flags: dict[str, list] = {}
    for t in range(lo, hi + 1):
        for k, v in flags_dia(store, db, cfg, datas[t], grupos).items():
            flags.setdefault(k, []).append(v)
    return dict(nome=tarefa["nome"], grupo=g, i=tarefa["i"], caro=tarefa["caro"], lo=lo, hi=hi,
                ini=ev["ini"] if ev else None, dur=ev.get("dur") if ev else None,
                mag=ev["mag"] if ev else None, flags=flags)


# ----------------------------------------------------------------------------- estatistica
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return p, max(0.0, c - h), min(1.0, c + h)


def boot_razao(pares: list[tuple[float, float]], reps: int = 500, seed: int = 12345) -> list[float]:
    """IC95% bootstrap por conglomerado (series) para a razao soma(a)/soma(b)."""
    rng = random.Random(seed)
    n = len(pares)
    est = []
    for _ in range(reps):
        sa = sb = 0.0
        for _ in range(n):
            a, b = pares[rng.randrange(n)]
            sa += a
            sb += b
        if sb:
            est.append(sa / sb)
    est.sort()
    if not est:
        return [float("nan")] * 3
    tot_a, tot_b = sum(a for a, _ in pares), sum(b for _, b in pares)
    return [tot_a / tot_b if tot_b else float("nan"), est[int(0.025 * len(est))], est[int(0.975 * len(est)) - 1]]


def _r(x, nd=4):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, nd)


def _ic(v: list[float]) -> dict:
    return {"est": _r(v[0]), "ic95": [_r(v[1]), _r(v[2])]}


def agregar_nulo(res: list[dict], reps: int = 500) -> dict:
    """Falsos alarmes, estabilidade (virada de estado) e erros de readiness em series sem evento."""
    out = {"n_series": len(res)}
    dets = sorted({k for r in res for k in r["flags"] if not k.startswith("_")})
    for det in dets:
        rs = [r["flags"][det] for r in res if det in r["flags"]]
        dia = [(sum(f), len(f)) for f in rs]
        # blocos de 30 dias: alguma ativacao no bloco ("falso alarme em 30 dias")
        b30 = [(int(any(f[j:j + 30])), 1) for f in rs for j in range(0, len(f) - 29, 30)]
        flip = [(sum(a != b for a, b in zip(f, f[1:])), len(f) - 1) for f in rs]
        episodios = [(sum(1 for a, b in zip([0] + f, f) if b and not a), len(f) / 30) for f in rs]
        # chance de ativacao em janelas de D dias SEM evento (base para ler a sensibilidade a eventos de D dias)
        janelas = {}
        for D in DURS:
            w = [(sum(int(any(f[j:j + D])) for j in range(len(f) - D + 1)), len(f) - D + 1) for f in rs]
            janelas[f"{D}d"] = _ic(boot_razao(w, reps))
        out[det] = {"n_series": len(rs), "n_dias": sum(n for _, n in dia),
                    "falso_alarme_em_janela_sem_evento": janelas,
                    "falso_alarme_por_dia": _ic(boot_razao(dia, reps)),
                    "especificidade_dia": None,
                    "falso_alarme_por_30d": _ic(boot_razao(b30, reps)),
                    "viradas_de_estado_por_dia": _ic(boot_razao(flip, reps)),
                    "episodios_por_30d": _ic(boot_razao(episodios, reps))}
        fa = out[det]["falso_alarme_por_dia"]
        out[det]["especificidade_dia"] = {"est": _r(1 - fa["est"]),
                                          "ic95": [_r(1 - fa["ic95"][1]), _r(1 - fa["ic95"][0])]}
    niv = [x for r in res for x in r["flags"].get("_nivel", [])]
    if niv:
        out["readiness_niveis"] = {k: _r(niv.count(k) / len(niv)) for k in sorted(set(niv))}
    return out


def _mediana_atraso(atrasos: list[int]) -> dict:
    if not atrasos:
        return {"mediana": None, "q1": None, "q3": None, "n": 0}
    q = st.quantiles(atrasos, n=4, method="inclusive") if len(atrasos) > 1 else [atrasos[0]] * 3
    return {"mediana": st.median(atrasos), "q1": q[0], "q3": q[2], "n": len(atrasos)}


def _sens(det: str, rs: list[dict], janela) -> dict:
    """Sensibilidade e atraso: janela(r) -> (inicio_idx, fim_idx_exclusivo) dentro do vetor de flags."""
    rs = [r for r in rs if det in r["flags"]]
    k, atrasos = 0, []
    for r in rs:
        a, b = janela(r)
        f = r["flags"][det][a:b]
        if any(f):
            k += 1
            atrasos.append(f.index(1))
    p, lo, hi = wilson(k, len(rs))
    return {"n": len(rs), "detectados": k, "sensibilidade": _r(p), "ic95": [_r(lo), _r(hi)],
            "atraso_dias": _mediana_atraso(atrasos)}


def agregar_pulso(res: list[dict]) -> dict:
    out = {}
    dets = sorted({k for r in res for k in r["flags"] if not k.startswith("_")})
    for det in dets:
        d = {"durante_evento": _sens(det, res, lambda r: (0, r["dur"])),
             "ate_7d_apos_fim": _sens(det, res, lambda r: (0, r["dur"] + 7))}
        out[det] = d
    # readiness: manter "normal" durante a perturbacao (recomendacao incorreta)
    rr = [r for r in res if "_nivel" in r["flags"]]
    if rr:
        dias_normal = [(sum(x == "normal" for x in r["flags"]["_nivel"][:r["dur"]]), r["dur"]) for r in rr]
        dias_normal3 = [(sum(x == "normal" for x in r["flags"]["_nivel"][2:r["dur"]]), max(0, r["dur"] - 2))
                        for r in rr]
        nunca = sum(all(x == "normal" for x in r["flags"]["_nivel"][:r["dur"]]) for r in rr)
        p, lo, hi = wilson(nunca, len(rr))
        out["readiness_normal_durante_evento"] = {
            "n_series": len(rr),
            "fracao_dias_normal": _ic(boot_razao(dias_normal)),
            "fracao_dias_normal_a_partir_do_3o_dia": _ic(boot_razao(dias_normal3)),
            "series_sem_nenhum_dia_reduzido": {"k": nunca, "est": _r(p), "ic95": [_r(lo), _r(hi)]}}
    return out


def agregar_persistente(res: list[dict], pre: int, janela_curta: int) -> dict:
    out = {}
    dets = sorted({k for r in res for k in r["flags"] if not k.startswith("_")})
    for det in dets:
        rs = [r for r in res if det in r["flags"]]
        pre_p = [(sum(r["flags"][det][:pre]), pre) for r in rs] if pre else []
        d = {"ate_fim_da_janela": _sens(det, rs, lambda r: (pre, len(r["flags"][det]))),
             f"em_{janela_curta}d": _sens(det, rs, lambda r: (pre, pre + janela_curta))}
        if pre_p:
            d["falso_alarme_por_dia_pre_evento"] = _ic(boot_razao(pre_p))
        out[det] = d
    rr = [r for r in res if "_nivel" in r["flags"]]
    if rr:
        k = sum(any(x != "normal" for x in r["flags"]["_nivel"][pre:]) for r in rr)
        p, lo, hi = wilson(k, len(rr))
        out["readiness_reduziu_alguma_vez"] = {"n": len(rr), "est": _r(p), "ic95": [_r(lo), _r(hi)]}
    return out


def comparar(nulo: dict, pulso_por_mag: dict, degrau: dict, deriva: dict) -> list[dict]:
    """Compara cada detector do sistema com as duas referencias simples (mesmas definicoes de evento).

    'pior' = a referencia tem sensibilidade >= e falso alarme/dia <= (estimativas pontuais), com ao menos um
    estritamente melhor; 'melhor' = o inverso; 'compromisso' = um ganha em sensibilidade e o outro em FA.
    `distinguivel` = os IC95% nao se sobrepoem na dimensao em que a referencia ganha (Wilson / bootstrap).
    `acaso` = chance media de ativacao do SISTEMA em janelas sem evento (media das duracoes 3,5,7,10 dias).
    """
    nr = nulo["realista"]
    linhas = []
    eventos = []
    for m in MAGS:
        eventos.append((f"perturbacao M={m} (duracoes 3-10 d)", pulso_por_mag[str(m)], "durante_evento"))
    eventos.append(("degrau abrupto M=1.0 (detectado em 14 d)", degrau["1.0"], "em_14d"))
    eventos.append(("deriva 60 d M=1.0 (detectada em 30 d)", deriva["1.0"], "em_30d"))
    for rotulo, tab, chave in eventos:
        for sis in SISTEMA:
            for ref in BASES:
                if sis.endswith("_hrv") != ref.endswith("_hrv"):
                    continue
                try:
                    a, b = tab[sis][chave], tab[ref][chave]
                except KeyError:
                    continue
                fs, fr = nr[sis]["falso_alarme_por_dia"], nr[ref]["falso_alarme_por_dia"]
                s_s, s_r = a["sensibilidade"], b["sensibilidade"]
                if s_r >= s_s and fr["est"] <= fs["est"] and (s_r > s_s or fr["est"] < fs["est"]):
                    v = "sistema_pior"
                elif s_s >= s_r and fs["est"] <= fr["est"] and (s_s > s_r or fs["est"] < fr["est"]):
                    v = "sistema_melhor"
                elif s_s == s_r and fs["est"] == fr["est"]:
                    v = "empate"
                else:
                    v = "compromisso"
                dist = None
                if v == "sistema_pior":
                    dist = bool(b["ic95"][0] > a["ic95"][1] or fs["ic95"][0] > fr["ic95"][1])
                elif v == "sistema_melhor":
                    dist = bool(a["ic95"][0] > b["ic95"][1] or fr["ic95"][0] > fs["ic95"][1])
                acaso = st.mean(nr[sis]["falso_alarme_em_janela_sem_evento"][f"{D}d"]["est"] for D in DURS)
                linhas.append({"evento": rotulo, "sistema": sis, "referencia": ref,
                               "sens_sistema": s_s, "sens_referencia": s_r,
                               "atraso_med_sistema": a["atraso_dias"]["mediana"],
                               "atraso_med_referencia": b["atraso_dias"]["mediana"],
                               "fa_dia_sistema": fs["est"], "fa_dia_referencia": fr["est"],
                               "acaso_sistema_janela_sem_evento": _r(acaso),
                               "veredito": v, "distinguivel": dist})
    return linhas


# ----------------------------------------------------------------------------- execucao
def _worker(tarefa: dict) -> dict:
    return avaliar_serie(tarefa)


def executar(conjunto: str, escala: float = 1.0, workers: int = 4, reps: int = 500) -> dict:
    t0 = time.time()
    tarefas = lista_tarefas(conjunto, escala)
    base = tempfile.mkdtemp(prefix="healthmon_val_run_", dir=_dir_base())
    os.environ["HEALTHMON_VAL_BASE"] = base  # os processos filhos criam seus bancos aqui dentro
    try:
        if workers > 1:
            with ProcessPoolExecutor(workers) as ex:
                res = list(ex.map(_worker, tarefas, chunksize=8))
        else:
            iniciar_ambiente()
            res = [avaliar_serie(t) for t in tarefas]
            limpar_ambiente()
    finally:
        os.environ.pop("HEALTHMON_VAL_BASE", None)
        shutil.rmtree(base, ignore_errors=True)
    por_nome: dict[str, list[dict]] = {}
    for r in res:
        por_nome.setdefault(r["nome"], []).append(r)
    nulos = {n: agregar_nulo(por_nome[n], reps) for n in CENARIOS_NULOS}
    pulso_cel, pulso_mag, pulso_dur = {}, {}, {}
    for m in MAGS:
        for d in DURS:
            pulso_cel[f"M{m}_D{d}"] = agregar_pulso(por_nome[f"pulso_M{m}_D{d}"])
        pulso_mag[str(m)] = agregar_pulso([r for d in DURS for r in por_nome[f"pulso_M{m}_D{d}"]])
    for d in DURS:
        pulso_dur[str(d)] = agregar_pulso([r for m in MAGS for r in por_nome[f"pulso_M{m}_D{d}"]])
    degrau = {str(m): agregar_persistente(por_nome[f"degrau_M{m}"], 14, 14) for m in MAGS}
    deriva = {str(m): agregar_persistente(por_nome[f"deriva_M{m}"], 7, 30) for m in MAGS}
    out = {
        "meta": {
            "aviso": "SIMULACAO com dados sinteticos. Nao e validacao com dados reais nem prova de utilidade clinica.",
            "conjunto": conjunto, "escala": escala, "dias_por_serie": sim.DIAS,
            "sementes": {"base": sim.SEMENTE_BASE[conjunto], "passo_cenario": sim.PASSO_CENARIO},
            "tamanhos_(series,series_com_detectores_caros)": TAMANHOS,
            "n_series_total": len(res), "workers": workers, "bootstrap_reps": reps,
            "segundos": round(time.time() - t0, 1),
            "ic": "Wilson para sensibilidades (series independentes); bootstrap por serie (conglomerado) para taxas por dia/30d",
        },
        "nulos": nulos,
        "pulso_por_celula": pulso_cel, "pulso_por_magnitude": pulso_mag, "pulso_por_duracao": pulso_dur,
        "degrau": degrau, "deriva": deriva,
    }
    out["comparacoes"] = comparar(nulos, pulso_mag, degrau, deriva)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--conjunto", choices=sorted(sim.SEMENTE_BASE), required=True)
    ap.add_argument("--saida", required=True)
    ap.add_argument("--escala", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    out = executar(a.conjunto, a.escala, a.workers)
    Path(a.saida).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"ok: {out['meta']['n_series_total']} series em {out['meta']['segundos']} s -> {a.saida}")


if __name__ == "__main__":
    main()
