"""Rotas da fase 2 (perfil, metabolismo, treino, nutrição, suplementos, exames, aprendizado).

Todas passam pelo middleware de sessão e CSRF de app.py. Erros de validação viram HTTP 400.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request

from .. import analysis as an
from .. import kb, labs, learning, metabolism as met, nutrition as nut, planner, profile as prof
from .. import stats, supplements as sup, training as tr
from ..config import Config
from ..db import Database, utcnow
from ..garmin_official import NOT_IN_PERSONAL_API, key_expiry_status
from ..metrics import METRICS
from ..sync import today_local


def register(app: FastAPI, cfg: Config, db: Database, ref_day: Callable[[], date]) -> None:
    def today() -> date:
        return today_local(cfg)

    def guard(fn):
        try:
            return fn()
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, str(e)) from e

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
        return {**plan, "tendencia_recuperacao": trend, "nutricao": nut.day_summary(db, cfg, d),
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

    @app.post("/api/checkin")
    async def checkin(request: Request):
        b = await body(request)
        d = day_arg(b.get("dia")).isoformat()
        row: dict[str, Any] = {"date": d, "updated_at": utcnow()}
        limits = {"perceived_recovery": (1, 10), "energy": (1, 5), "soreness": (1, 5), "mood": (1, 5), "sleep_quality": (1, 5),
                  "caffeine_mg": (0, 1500), "alcohol_units": (0, 30), "water_ml": (0, 10000)}
        for k, (lo, hi) in limits.items():
            if b.get(k) not in (None, ""):
                v = guard(lambda: float(b[k]))
                if not lo <= v <= hi:
                    raise HTTPException(400, f"{k}: fora de {lo}–{hi}")
                row[k] = v
        for k, n in (("caffeine_last_time", 5), ("symptoms", 300), ("notes", 500)):
            if b.get(k):
                row[k] = str(b[k])[:n]
        db.insert("checkins", row, upsert_on="date")
        return {"ok": True, "prontidao": tr.readiness(db, cfg, date.fromisoformat(d))}

    @app.get("/api/checkin")
    def checkin_get(dia: str | None = None):
        return db.q1("SELECT * FROM checkins WHERE date=?", (day_arg(dia).isoformat(),)) or {}

    # ------------------------------------------------- composição e metabolismo
    @app.post("/api/peso")
    async def peso(request: Request):
        b = await body(request)
        guard(lambda: prof.record_weight(db, day_arg(b.get("dia")).isoformat(), float(b["peso_kg"]),
                                         *(float(b[k]) if b.get(k) not in (None, "") else None
                                           for k in ("gordura_pct", "musculo_kg", "cintura_cm"))))
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
        return {"ativo": tr.get_plan(db), "proposto": tr.get_plan(db, "proposto"), "hoje": tr.todays_session(db, cfg, d),
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
        rid = guard(lambda: tr.log_set(db, day_arg(b.get("dia")).isoformat(), str(b["exercicio"])[:80], float(b["reps"]),
                                       float(b["carga_kg"]), float(b["rir"]) if b.get("rir") not in (None, "") else None,
                                       str(b.get("sessao", ""))[:60] or None))
        return {"id": rid}

    @app.post("/api/treino/sessao")
    async def treino_sessao(request: Request):
        b = await body(request)
        rid = guard(lambda: tr.log_session(db, day_arg(b.get("dia")).isoformat(), str(b.get("sessao", ""))[:60],
                                           str(b.get("tipo", "forca"))[:20],
                                           float(b["duracao_min"]) if b.get("duracao_min") else None,
                                           float(b["rpe"]) if b.get("rpe") else None,
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
        return {"resumo": nut.day_summary(db, cfg, d), "revisao": rev, "consistencia": nut.consistency(db, cfg, d),
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
        f = lambda k: float(b[k]) if b.get(k) not in (None, "") else None  # noqa: E731
        rid = guard(lambda: nut.log_meal(db, d, str(b.get("descricao", "Refeição"))[:300], f("kcal"), f("proteina_g"),
                                         f("carboidrato_g"), f("gordura_g"), f("fibra_g"), str(b.get("hora", ""))[:5] or None))
        return {"id": rid}

    @app.delete("/api/refeicoes/{mid}")
    def refeicao_del(mid: int):
        db.x("DELETE FROM meals WHERE id=?", (mid,))
        return {"ok": True}

    @app.post("/api/nutricao/sugerir")
    async def sugerir(request: Request):
        b = await body(request)
        t = nut.active_targets(db)
        if not t:
            raise HTTPException(400, "Confirme uma meta nutricional primeiro")
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
        return guard(lambda: planner.add_goal(db, str(b["metrica"]), float(b["alvo"]), str(b["prazo"]), today()))

    @app.delete("/api/objetivos/{gid}")
    def objetivos_del(gid: int):
        planner.remove_goal(db, gid)
        return {"ok": True}

    # ------------------------------------------------------------- aprendizado
    @app.get("/api/aprendizado")
    def aprendizado():
        d = ref_day()
        exps = learning.list_experiments(db)
        for e in exps:
            if e["dias_registrados"] >= 2 * learning.MIN_PER_ARM:
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
            return {"enviados": send_push(cfg, db, "healthmon", "Notificações ativadas neste aparelho.")}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, f"Push indisponível: {e}") from e
