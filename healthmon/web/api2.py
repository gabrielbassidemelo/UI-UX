"""Rotas da fase 2 (perfil, metabolismo, treino, nutrição, suplementos, exames, aprendizado).

Todas passam pelo middleware de sessão e CSRF de app.py. Erros de validação viram HTTP 400.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, timedelta
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .. import analysis as an
from .. import backup, calibration, garmin_validation, kb, labs, learning, offsite, safety
from .. import metabolism as met, nutrition as nut, planner, profile as prof
from .. import stats, supplements as sup, training as tr
from ..config import Config
from ..db import Database, utcnow
from ..garmin_official import NOT_IN_PERSONAL_API, key_expiry_status
from ..metrics import METRICS
from ..sync import today_local

URGENT_TEXT = ("Sintoma de alerta marcado: procure atendimento de urgência agora (SAMU 192). Não treine e não use este "
               "painel para decidir o que fazer.")
_URL_RE = re.compile(r"https?://\S+|/p/[^/\s\"']+", re.I)
_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/)(?:[\w.\-~]+[/\\])+[\w.\-~]*")


def scrub(text: Any) -> str | None:
    """Remove URLs, tokens de PAR e caminhos absolutos de mensagens de erro antes de sair pela API."""
    if text is None:
        return None
    return _PATH_RE.sub("[caminho]", _URL_RE.sub("[url]", str(text)))[:300]


def register(app: FastAPI, cfg: Config, db: Database, ref_day: Callable[[], date]) -> None:
    def today() -> date:
        return today_local(cfg)

    def guard(fn):
        try:
            return fn()
        except (ValueError, KeyError, TypeError, OverflowError) as e:
            raise HTTPException(400, str(e)) from e

    def num(b: dict, k: str, lo: float, hi: float, required: bool = False) -> float | None:
        """Número finito dentro de [lo, hi]; vazio vira None. Rejeita inf, nan e valores absurdos."""
        v = b.get(k)
        if v in (None, ""):
            if required:
                raise HTTPException(400, f"{k}: obrigatório")
            return None
        try:
            f = float(str(v).replace(",", "."))
        except (ValueError, OverflowError) as e:
            raise HTTPException(400, f"{k}: número inválido") from e
        if not math.isfinite(f) or not lo <= f <= hi:
            raise HTTPException(400, f"{k}: fora do intervalo {lo:g}–{hi:g}")
        return f

    async def body(request: Request) -> dict[str, Any]:
        try:
            b = await request.json()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, "JSON inválido") from e
        if not isinstance(b, dict):
            raise HTTPException(400, "Corpo deve ser um objeto JSON")
        return b

    def day_arg(v: str | None) -> date:
        return guard(lambda: date.fromisoformat(v)) if v else today()

    # ------------------------------------------------------------------ início
    @app.get("/api/home")
    def home():
        d = today()
        plan = planner.daily_plan(db, cfg, d)
        store = an.load_store(db, d, 60)
        trend = []
        for i in range(6, -1, -1):
            dd = d - timedelta(days=i)
            trend.append({"dia": dd.isoformat(), "status": an.recovery(store, dd).status if store.raw(dd) else "sem_dado"})
        nutr = nut.day_summary(db, cfg, d)
        blk = safety.screen(db, cfg, d)["bloqueios"]
        if blk.get("metas_nutricionais") or blk.get("metas_peso") or nut.review_targets(db, cfg, d)["estado"] == "indisponivel":
            # meta bloqueada/indisponível: nunca mostrar a meta antiga como se estivesse valendo
            nutr = {**nutr, "meta": None, "meta_bloqueada": True}
            nutr.pop("restante", None)
        return {**plan, "tendencia_recuperacao": trend, "nutricao": nutr,
                "ultima_sincronizacao": db.last_successful_sync(),
                "alertas": [a for a in db.alerts(10) if not a["acknowledged"]],
                "objetivos": planner.goals_progress(db, d), "demo": db.get_setting("data_source") == "demo"}

    # ------------------------------------------------------------------ perfil
    @app.get("/api/perfil")
    def perfil():
        return {"perfil": prof.get(db), "faltam": prof.missing(db),
                "campos": {k: {"descricao": v[2], "tipo": v[0].__name__,
                               "opcoes": sorted(v[1]) if isinstance(v[1], set) else None} for k, v in prof.FIELDS.items()},
                "objetivos": prof.GOALS, "experiencias": prof.EXPERIENCE, "equipamentos": prof.EQUIPMENT}

    @app.post("/api/perfil")
    async def perfil_post(request: Request):
        b = await body(request)
        return {"perfil": guard(lambda: prof.update(db, b)), "faltam": prof.missing(db)}

    ADHERENCE = {"sim": 1.0, "parcial": 0.5, "nao": 0.0, "não": 0.0}
    SCALE_1_10 = ("perceived_recovery",)
    SCALE_1_5 = ("energy", "soreness", "mood", "sleep_quality", "fatigue", "perceived_stress")

    def whole(b: dict, k: str, lo: int, hi: int) -> int | None:
        """Inteiro em [lo, hi]; vazio/ausente = None (nunca zero). Rejeita decimais, texto e booleanos."""
        v = b.get(k)
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        if isinstance(v, bool):
            raise HTTPException(400, f"{k}: use um número inteiro de {lo} a {hi}")
        try:
            f = float(str(v).replace(",", ".").strip())
        except (ValueError, OverflowError) as e:
            raise HTTPException(400, f"{k}: número inválido") from e
        if not math.isfinite(f) or f != int(f):
            raise HTTPException(400, f"{k}: use um número inteiro de {lo} a {hi}")
        if not lo <= int(f) <= hi:
            raise HTTPException(400, f"{k}: fora de {lo}–{hi}")
        return int(f)

    def parse_triagem(v: Any) -> str | None:
        """Triagem de sintomas do dia: objeto com TODAS as chaves de safety.SYMPTOMS, valores booleanos.
        Chaves desconhecidas, faltantes ou valores não booleanos são rejeitados (não respondido não vira 'não')."""
        if v is None or v == "":
            return None
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError as e:
                raise HTTPException(400, "triagem: JSON inválido") from e
        if not isinstance(v, dict):
            raise HTTPException(400, "triagem: deve ser um objeto {sintoma: true/false}")
        unknown = sorted(set(v) - set(safety.SYMPTOMS))
        if unknown:
            raise HTTPException(400, "triagem: chave(s) desconhecida(s): " + ", ".join(str(x)[:30] for x in unknown[:5]))
        absent = [k for k in safety.SYMPTOMS if k not in v]
        if absent:
            raise HTTPException(400, "triagem: responda todos os sintomas; faltam: " + ", ".join(absent))
        if any(not isinstance(x, bool) for x in v.values()):
            raise HTTPException(400, "triagem: cada resposta deve ser verdadeiro ou falso")
        return json.dumps({k: v[k] for k in safety.SYMPTOMS}, ensure_ascii=False)

    @app.post("/api/checkin")
    async def checkin(request: Request):
        b = await body(request)
        d = day_arg(b.get("dia")).isoformat()
        row: dict[str, Any] = {"date": d, "updated_at": utcnow()}
        for k in SCALE_1_10:
            v = whole(b, k, 1, 10)
            if v is not None:
                row[k] = v
        for k in SCALE_1_5:
            v = whole(b, k, 1, 5)
            if v is not None:
                row[k] = v
        for k, (lo, hi) in {"caffeine_mg": (0, 1500), "alcohol_units": (0, 30), "water_ml": (0, 10000)}.items():
            if b.get(k) not in (None, ""):
                v = guard(lambda: float(b[k]))
                if not math.isfinite(v) or not lo <= v <= hi:
                    raise HTTPException(400, f"{k}: fora de {lo}–{hi}")
                row[k] = v
        if b.get("adherence") not in (None, ""):
            key = str(b["adherence"]).strip().lower()
            if key not in ADHERENCE:
                raise HTTPException(400, "adherence: use sim, parcial ou nao")
            row["adherence"] = ADHERENCE[key]
        tri = parse_triagem(b.get("triagem"))
        if tri is not None:
            row["triagem"] = tri
        for k, n in (("caffeine_last_time", 5), ("symptoms", 300), ("notes", 500)):
            if b.get(k):
                row[k] = str(b[k])[:n]
        db.insert("checkins", row, upsert_on="date")
        ready = tr.readiness(db, cfg, date.fromisoformat(d))
        return {"ok": True, "prontidao": ready, "urgente": bool(ready.get("urgente")),
                "aviso_urgente": URGENT_TEXT if ready.get("urgente") else None}

    @app.get("/api/checkin")
    def checkin_get(dia: str | None = None):
        return db.q1("SELECT * FROM checkins WHERE date=?", (day_arg(dia).isoformat(),)) or {}

    # ------------------------------------------------- composição e metabolismo
    @app.post("/api/peso")
    async def peso(request: Request):
        b = await body(request)
        guard(lambda: prof.record_weight(db, day_arg(b.get("dia")).isoformat(), num(b, "peso_kg", 30, 300, True),
                                         num(b, "gordura_pct", 3, 60), num(b, "musculo_kg", 10, 150), num(b, "cintura_cm", 40, 250)))
        return {"ok": True}

    @app.get("/api/composicao")
    def composicao(days: int = 90):
        days = max(7, min(days, 3650))
        d = today()
        rows = prof.weights(db, (d - timedelta(days=days - 1)).isoformat(), d.isoformat())
        series: list[float | None] = [None] * days
        for r in rows:
            series[(date.fromisoformat(r["date"]) - (d - timedelta(days=days - 1))).days] = r["weight_kg"]
        smooth = stats.ewma(series, 7)
        wt = met.weight_trend(db, d, min(days, 42))
        return {"medidas": rows, "datas": [(d - timedelta(days=days - 1 - i)).isoformat() for i in range(days)],
                "peso": series, "peso_suavizado": [round(v, 2) if v is not None else None for v in smooth],
                "tendencia": {k: v for k, v in wt.items() if not k.startswith("_")},
                "nota": "A linha suavizada (média exponencial de 7 dias) reduz o efeito de água e conteúdo intestinal. "
                        "Percentual de gordura por bioimpedância tem erro de vários pontos; acompanhe a tendência."}

    @app.get("/api/metabolismo")
    def metabolismo(days: int = 28):
        d = today()
        t = met.tdee(db, cfg, d, max(21, min(days, 90)))
        t["peso"] = {k: v for k, v in t.get("peso", {}).items() if not k.startswith("_")}
        p = prof.get(db)
        strategies = met.strategies(p.get("objetivo", "saude"))
        for s in strategies:
            s["evidencias"] = kb.cite(s.pop("refs"), cfg)
        return {**t, "evidencias": kb.cite(met.REFS["tmb"] + met.REFS["tdee"] + met.REFS["adaptacao"], cfg),
                "estrategias": strategies,
                "aviso": "Valores estimados por equações, dados do vestível e seus registros. Não são medições clínicas "
                         "(calorimetria indireta ou água duplamente marcada)."}

    # ------------------------------------------------------------------ treino
    @app.get("/api/treino")
    def treino():
        d = today()
        stop = safety.blocked(safety.screen(db, cfg, d), "treino_plano")
        hoje = tr.todays_session(db, cfg, d)
        ativo, proposto = tr.get_plan(db), tr.get_plan(db, "proposto")
        if stop:   # plano antigo não é exibido enquanto a triagem o bloqueia
            ativo = proposto = None
            if hoje.get("tipo") != "descanso":
                hoje = {"dia": hoje["dia"], "prontidao": hoje["prontidao"], "tipo": "bloqueado", "titulo": "Treino personalizado suspenso",
                        "texto": stop}
        return {"ativo": ativo, "proposto": proposto, "hoje": hoje, "bloqueio": stop,
                "zonas": tr.hr_zones(db, cfg, d), "semana": tr.weekly_summary(db, d), "faltam": prof.missing(db, "treino")["treino"],
                "limites": tr.SAFETY, "evidencias": kb.cite(["acsm2009", "schoenfeld2017volume", "schoenfeld2016freq",
                                                              "zourdos2016", "refalo2023", "garber2011", "bull2020", "meeusen2013"], cfg)}

    @app.post("/api/treino/propor")
    async def treino_propor(request: Request):
        b = await body(request)
        return {"id": guard(lambda: tr.propose_plan(db, cfg, str(b.get("motivo", "solicitado no dashboard"))[:200]))}

    @app.post("/api/treino/confirmar/{pid}")
    def treino_confirmar(pid: int):
        guard(lambda: tr.confirm_plan(db, pid))
        learning.record_decision(db, today().isoformat(), "plano", "Plano de treino confirmado", status="aceita")
        return {"ok": True}

    @app.post("/api/treino/serie")
    async def treino_serie(request: Request):
        b = await body(request)
        rid = guard(lambda: tr.log_set(db, day_arg(b.get("dia")).isoformat(), str(b["exercicio"])[:80], num(b, "reps", 1, 100, True),
                                       num(b, "carga_kg", 0, 600, True), num(b, "rir", 0, 10),
                                       str(b.get("sessao", ""))[:60] or None))
        return {"id": rid}

    @app.post("/api/treino/sessao")
    async def treino_sessao(request: Request):
        b = await body(request)
        rid = guard(lambda: tr.log_session(db, day_arg(b.get("dia")).isoformat(), str(b.get("sessao", ""))[:60],
                                           str(b.get("tipo", "forca"))[:20],
                                           num(b, "duracao_min", 1, 600), num(b, "rpe", 1, 10),
                                           str(b.get("periodo", ""))[:10] or None, str(b.get("notas", ""))[:300] or None))
        if b.get("seguiu_recomendacao") is not None:
            dec = db.q1("SELECT id FROM decisions WHERE date=? AND area='treino' ORDER BY id DESC LIMIT 1",
                        (day_arg(b.get("dia")).isoformat(),))
            if dec:
                learning.set_followed(db, dec["id"], bool(b["seguiu_recomendacao"]))
        return {"id": rid}

    @app.get("/api/treino/historico")
    def treino_hist(exercicio: str):
        return {"exercicio": exercicio, "historico": tr.exercise_history(db, exercicio[:80]),
                "series": db.q("SELECT date, set_no, reps, load_kg, rir FROM workout_sets WHERE exercise=? ORDER BY date DESC, set_no LIMIT 40",
                               (exercicio[:80],)),
                "nota": "1RM estimado pela fórmula de Epley; é uma estimativa, menos confiável acima de ~10 repetições."}

    # ---------------------------------------------------------------- nutrição
    @app.get("/api/nutricao")
    def nutricao(dia: str | None = None):
        d = day_arg(dia)
        rev = nut.review_targets(db, cfg, d)
        refs = (rev.get("calculada") or {}).get("refs") or ["morton2018", "jager2017"]
        resumo = nut.day_summary(db, cfg, d)
        if rev["estado"] == "indisponivel":   # sem metas antigas quando a meta está bloqueada ou indisponível
            resumo = {**resumo, "meta": None, "meta_bloqueada": True}
            resumo.pop("restante", None)
            rev = {**rev, "vigente": None}
        return {"resumo": resumo, "revisao": rev, "consistencia": nut.consistency(db, cfg, d),
                "alimentos_na_base": db.q1("SELECT COUNT(*) n FROM foods")["n"], "limites": nut.SAFETY,
                "evidencias": kb.cite(refs + ["garthe2011", "helms2014", "mountjoy2018"], cfg),
                "foto": {"disponivel": False, "motivo": "Estimativa por fotografia não está implementada. Mesmo com modelos "
                         "de visão, o erro de porção e de ingredientes ocultos (óleo, molhos) costuma ser grande; por isso "
                         "ela só será oferecida como opcional, com autorização separada e incerteza explícita."}}

    @app.post("/api/nutricao/confirmar")
    def nutricao_confirmar():
        t = guard(lambda: nut.confirm_targets(db, cfg, today()))
        learning.record_decision(db, today().isoformat(), "nutricao", f"Meta nutricional confirmada: {t['kcal']} kcal", status="aceita")
        return t

    @app.get("/api/alimentos")
    def alimentos(q: str):
        return nut.search_foods(db, q[:80], 12)

    @app.post("/api/refeicoes/previa")
    async def refeicao_previa(request: Request):
        b = await body(request)
        return nut.parse_meal_text(db, str(b.get("texto", ""))[:600])

    @app.post("/api/refeicoes")
    async def refeicao(request: Request):
        b = await body(request)
        d = day_arg(b.get("dia")).isoformat()
        if b.get("texto"):
            return guard(lambda: nut.log_meal_text(db, d, str(b["texto"])[:600], str(b.get("hora", ""))[:5] or None))
        hora = str(b.get("hora", ""))[:5]
        if hora and not re.fullmatch(r"\d{2}:\d{2}", hora):
            raise HTTPException(400, "hora: use HH:MM")
        rid = guard(lambda: nut.log_meal(db, d, str(b.get("descricao", "Refeição"))[:300], num(b, "kcal", 0, 6000),
                                         num(b, "proteina_g", 0, 500), num(b, "carboidrato_g", 0, 1500), num(b, "gordura_g", 0, 600),
                                         num(b, "fibra_g", 0, 200), hora or None))
        return {"id": rid}

    @app.delete("/api/refeicoes/{mid}")
    def refeicao_del(mid: int):
        db.x("DELETE FROM meals WHERE id=?", (mid,))
        return {"ok": True}

    @app.post("/api/nutricao/sugerir")
    async def sugerir(request: Request):
        b = await body(request)
        t = nut.effective_targets(db, cfg, today())
        if not t:
            raise HTTPException(400, "Não há meta nutricional em vigor (não confirmada ou suspensa pela triagem de saúde).")
        ids = guard(lambda: [int(i) for i in b.get("alimentos", [])][:25])
        return nut.suggest_day(db, t, ids)

    # ------------------------------------------------------------- suplementos
    @app.get("/api/suplementos")
    def suplementos():
        r = sup.evaluate(db, cfg, today())
        for it in r.get("itens", []):
            it["evidencias"] = kb.cite(it.pop("refs"), cfg)
        if r.get("refs_gerais"):
            r["evidencias_gerais"] = kb.cite(r.pop("refs_gerais"), cfg)
        return r

    @app.get("/api/suplementos/consulta")
    def sup_consulta(nome: str):
        return sup.check_substance(nome[:80]) or {"recusado": False, "resultados": kb.search(nome[:80], 4, cfg)}

    # ------------------------------------------------------------------ exames
    @app.get("/api/exames")
    def exames():
        return labs.overview(db)

    @app.post("/api/exames")
    async def exames_post(request: Request):
        b = await body(request)
        rid = guard(lambda: labs.add_result(db, str(b["data"]), str(b["analito"]), b.get("valor"), str(b.get("unidade", "")),
                                            b.get("ref_min"), b.get("ref_max"), str(b.get("laboratorio", "")),
                                            str(b.get("ref_texto", ""))))
        return {"id": rid, "contexto": labs.context_at(db, str(b["data"]))}

    @app.delete("/api/exames/{lid}")
    def exames_del(lid: int):
        db.x("DELETE FROM labs WHERE id=?", (lid,))
        return {"ok": True}

    @app.post("/api/exames/arquivo")
    async def exames_arquivo(request: Request):
        name = request.headers.get("x-arquivo", "exame.pdf")
        data = await request.body()
        return {"arquivo": guard(lambda: labs.store_original(cfg, name, data)),
                "nota": "Arquivo guardado apenas neste servidor. Digite os valores no formulário; a leitura automática não está implementada."}

    # --------------------------------------------------------------- objetivos
    @app.get("/api/objetivos")
    def objetivos():
        return {"objetivos": planner.goals_progress(db, today()), "metricas": planner.GOAL_METRICS}

    @app.post("/api/objetivos")
    async def objetivos_post(request: Request):
        b = await body(request)
        return guard(lambda: planner.add_goal(db, str(b["metrica"]), num(b, "alvo", 0, 100000, True), str(b["prazo"]), today(), cfg))

    @app.delete("/api/objetivos/{gid}")
    def objetivos_del(gid: int):
        planner.remove_goal(db, gid)
        return {"ok": True}

    # ------------------------------------------------------------- aprendizado
    @app.get("/api/feedback")
    def feedback_view(dia: str | None = None):
        """Relato diário × pulseira e avaliação observacional das recomendações. Somente leitura: não altera regras."""
        from .. import feedback
        d = day_arg(dia)
        return {"cruzamento": feedback.cross(db, d), "recomendacoes": feedback.recommendation_effect(db, d)}

    @app.get("/api/aprendizado")
    def aprendizado():
        d = ref_day()
        exps = learning.list_experiments(db)
        for e in exps:
            if e["dias_registrados"] >= 2 * learning.MIN_BLOCKS * e["block_days"]:
                e["analise"] = {k: v for k, v in learning.analyze_experiment(db, e["id"]).items() if k != "experimento"}
        return {"padroes": learning.patterns(db, d), "experimentos": exps, "fatores": learning.SAFE_FACTORS,
                "desfechos": {k: METRICS[k].label for k in learning.OUTCOMES}, "decisoes": learning.decisions(db),
                "resultado_recomendacoes": learning.recommendation_outcomes(db, d), "hoje": learning.today_assignment(db, today())}

    @app.post("/api/experimentos")
    async def exp_post(request: Request):
        b = await body(request)
        return {"id": guard(lambda: learning.create_experiment(db, str(b["fator"]), str(b["desfecho"]), today() + timedelta(days=1),
                                                               int(b.get("dias", 28)), int(b.get("bloco", 2))))}

    @app.post("/api/experimentos/{eid}/dia")
    async def exp_dia(eid: int, request: Request):
        b = await body(request)
        guard(lambda: learning.record_day(db, eid, day_arg(b.get("dia")).isoformat(), bool(b.get("seguiu")), str(b.get("confundidores", ""))))
        return {"ok": True}

    @app.post("/api/experimentos/{eid}/encerrar")
    def exp_fim(eid: int):
        db.x("UPDATE experiments SET status='encerrado' WHERE id=?", (eid,))
        return {"ok": True}

    @app.post("/api/decisoes/{did}")
    async def decisao(did: int, request: Request):
        b = await body(request)
        learning.set_followed(db, did, bool(b.get("seguiu")))
        return {"ok": True}

    # ------------------------------------------------------- estatística e base
    @app.get("/api/estatistica")
    def estatistica(key: str):
        if key not in METRICS:
            raise HTTPException(400, "métrica inválida")
        d = ref_day()
        store = an.load_store(db, d, 200)
        return {"metrica": METRICS[key].label, "bases": stats.multi_baseline(store, key, d),
                "dia_da_semana": stats.weekday_effect(store, key, d), "mudanca": stats.changepoint(store, key, d),
                "previsao": stats.backtest_metric(store, key, d),
                "nota": "Backtesting 1 passo à frente, sem usar dados futuros. Um modelo só substitui a previsão ingênua se "
                        "reduzir o erro em 5% ou mais."}

    @app.get("/api/kb")
    def kb_search(q: str = ""):
        if q:
            return {"resultados": kb.search(q[:120], 8, cfg)}
        ents = kb.entries(cfg)
        return {"total": len(ents), "verificadas": sum(1 for v in kb.verification(cfg).values() if v.get("ok")),
                "graus": kb.GRADE, "resultados": kb.cite(list(ents), cfg)}

    # ------------------------------------------------------- fase 3: segurança e transparência
    @app.get("/api/calibracao")
    def calibracao(dia: str | None = None):
        st = calibration.stage(db, day_arg(dia))
        st["suspenso"] = [k for k, v in st["permite"].items() if not v]
        return st

    @app.get("/api/evidencias/resumo")
    def evidencias_resumo():
        return {**kb.summary(), "classes": kb.CLASSES,
                "aviso": "Verificação feita por modelos de IA em bases públicas; não é auditoria humana independente"}

    @app.get("/api/garmin/conferencia")
    def garmin_conf(dia: str | None = None):
        return garmin_validation.comparison(db, day_arg(dia) if dia else ref_day())

    @app.post("/api/garmin/conferencia")
    async def garmin_conf_post(request: Request):
        b = await body(request)
        if not isinstance(b.get("ok"), bool):
            raise HTTPException(400, "ok: use true (igual) ou false (diferente)")
        if not isinstance(b.get("chave"), str):
            raise HTTPException(400, "chave: obrigatória")
        d = day_arg(b.get("dia")).isoformat()
        guard(lambda: garmin_validation.record_check(db, b["chave"], b["ok"], d))
        return {"ok": True}

    @app.get("/api/backup/status")
    def backup_status():
        st = offsite.status(cfg)
        err = st.get("ultimo_erro")
        if isinstance(err, dict):
            err = {"quando": err.get("quando"), "mensagem": scrub(err.get("mensagem")), "arquivo": err.get("arquivo")}
        elif err is not None:
            err = {"mensagem": scrub(err)}
        externo = {"configurado": bool(st.get("configurado")), "ultimo_envio_ok": st.get("ultimo_envio_ok"),
                   "ultimo_arquivo": st.get("ultimo_arquivo"), "idade_horas": st.get("idade_horas"),
                   "ultimo_erro": err, "falhas_consecutivas": st.get("falhas_consecutivas", 0),
                   "pendente": bool(st.get("pendente")), "atrasado": bool(st.get("atrasado")), "alerta": bool(st.get("alerta"))}
        local: dict[str, Any] = {"existe": False}
        try:
            f = backup.latest_backup(cfg)
            if f:
                local = {"existe": True, "arquivo": f.name, "tamanho_bytes": f.stat().st_size, "formato": "HMBK1"}
                h = backup.read_header(f)
                if h:
                    local.update({"formato": f"HMBK{h.get('v', 2)}", "criado_utc": h.get("criado_utc"),
                                  "linhas_daily_metrics": h.get("linhas_daily_metrics"),
                                  "esquema_versao": (h.get("esquema") or {}).get("user_version")})
        except Exception:  # noqa: BLE001 — arquivo ilegível: informa só que não foi possível ler
            local = {**local, "existe": local.get("existe", False), "erro": "não foi possível ler o cabeçalho do último backup local"}
        return {"externo": externo, "local": local}

    @app.get("/api/backup/baixar")
    def backup_baixar():
        f = backup.latest_backup(cfg)
        if not f:
            raise HTTPException(404, "Nenhum backup local encontrado.")
        return Response(f.read_bytes(), media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{f.name}"', "Cache-Control": "no-store"})

    @app.get("/api/seguranca/triagem")
    def seguranca_triagem():
        p = prof.get(db)
        return {"perguntas": [{"chave": k, "texto": t, "resposta": p.get(k)} for k, t in safety.TRIAGE.items()],
                "sintomas": [{"chave": k, "texto": t, "urgente": u} for k, (t, u) in safety.SYMPTOMS.items()],
                "limitacoes_opcoes": list(prof.LIMITATIONS), "limitacoes": p.get("limitacoes"),
                "faltam": [k for k in ("sexo", "nascimento", "altura_cm") if k not in p] + [k for k in safety.TRIAGE if k not in p],
                "faltam_por_area": prof.missing(db), "aviso_urgente": URGENT_TEXT}

    # ------------------------------------------------------------------ sistema
    @app.get("/api/sistema")
    def sistema():
        d = ref_day()
        store = an.load_store(db, d, 100)
        return {"fonte": cfg.get("garmin", "fonte", "oficial"), "chave": key_expiry_status(cfg),
                "nao_disponiveis_na_api_oficial": NOT_IN_PERSONAL_API, "qualidade": stats.data_quality(store, d),
                "execucoes": db.q("SELECT job, started_at, finished_at, status, detail FROM job_runs ORDER BY id DESC LIMIT 25"),
                "falhas_7d": db.q1("SELECT COUNT(*) n FROM job_runs WHERE status!='ok' AND status!='executando' AND started_at>=?",
                                   ((d - timedelta(days=7)).isoformat(),))["n"],
                "push_inscritos": db.q1("SELECT COUNT(*) n FROM push_subscriptions")["n"],
                "requisicoes_garmin_hoje": db.get_setting("garmin_req_count", "—"),
                "dados_reais_validados": db.get_setting("garmin_validado_em"),
                "ia_modo": cfg.get("ia", "modo")}

    @app.get("/api/push/chave")
    def push_key():
        from ..push import vapid_keys
        return {"publica": vapid_keys(cfg)["public"]}

    @app.post("/api/push/inscrever")
    async def push_sub(request: Request):
        from ..push import subscribe
        b = await body(request)
        guard(lambda: subscribe(db, b))
        return {"ok": True}

    @app.post("/api/push/teste")
    def push_test():
        from ..push import send_push
        try:
            n = send_push(cfg, db, "healthmon", "Notificações ativadas neste aparelho.")
            ativo = "push" in (cfg.get("notificacoes", "canais", ["push"]) or [])
            return {"enviados": n, "canal_push_ativo": ativo,
                    "aviso": None if ativo else "O canal push está desligado em [notificacoes] canais: relatórios e alertas automáticos NÃO serão enviados."}
        except Exception as e:  # noqa: BLE001 — mensagem genérica: detalhes de rede não vão para o cliente
            raise HTTPException(503, "Push indisponível: ative as notificações neste aparelho e confira o servidor.") from e
