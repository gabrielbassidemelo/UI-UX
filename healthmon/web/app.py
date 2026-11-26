"""Dashboard web (FastAPI) — single-user, autenticado, sem recursos externos (CSP 'self')."""
from __future__ import annotations

import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import analysis as an
from .. import auth
from ..config import Config
from ..db import Database
from ..insights import AVISO, STATUS_TXT, attention_points, correlation_sentence, trend_sentence
from ..llm import consent_status
from ..metrics import CATEGORIAS, CORE, METRICS, display_value
from ..sync import availability, today_local

STATIC = Path(__file__).parent / "static"
COOKIE = "hm_session"
PUBLIC = {"/login", "/api/login", "/healthz", "/manifest.webmanifest", "/sw.js"}


def body_limit(path: str) -> int:
    return 16 * 1024 * 1024 if path == "/api/exames/arquivo" else 64 * 1024


class BodyLimit:
    """Conta os bytes recebidos de fato (inclusive em envio 'chunked', sem Content-Length) e
    interrompe a requisição ao passar do limite."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit, seen, over = body_limit(scope["path"]), 0, False

        async def limited():
            nonlocal seen, over
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    over = True
                    raise _TooLarge()               # interrompe a leitura: o restante não é carregado
            return msg
        started = False

        async def tracked(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
                if over:                            # o framework converteu a interrupção em erro genérico
                    msg = {**msg, "status": 413}
            await send(msg)
        try:
            await self.app(scope, limited, tracked)
        except _TooLarge:
            if not started:
                await JSONResponse({"erro": "corpo da requisição grande demais"}, status_code=413)(scope, receive, send)


class _TooLarge(Exception):
    pass


def create_app(cfg: Config, db: Database, sync_fn: Callable[[], Any] | None = None) -> FastAPI:
    app = FastAPI(title="healthmon", docs_url=None, redoc_url=None, openapi_url=None)
    throttle = auth.LoginThrottle()
    https = bool(cfg.get("dashboard", "https", False))
    session_days = int(cfg.get("dashboard", "sessao_dias", 30))
    sync_lock = threading.Lock()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        public = path in PUBLIC or path.startswith("/static/")
        if not public and not auth.valid_session(db, request.cookies.get(COOKIE)):
            if path.startswith("/api/"):
                return JSONResponse({"erro": "não autenticado"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        if request.method not in ("GET", "HEAD") and request.headers.get("x-hm") != "1":
            return JSONResponse({"erro": "requisição recusada"}, status_code=403)
        try:
            if int(request.headers.get("content-length") or 0) > body_limit(path):
                return JSONResponse({"erro": "corpo da requisição grande demais"}, status_code=413)
        except ValueError:
            return JSONResponse({"erro": "content-length inválido"}, status_code=400)
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        if https:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ----------------------------------------------------------- páginas
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/login")
    def login_page():
        return FileResponse(STATIC / "login.html")

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js")
    def sw():
        return FileResponse(STATIC / "sw.js", media_type="application/javascript")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # --------------------------------------------------------------- auth
    @app.post("/api/login")
    def login(request: Request, response: Response, payload: Any = Body(default=None)):
        # Função síncrona de propósito: roda fora do laço de eventos, então o scrypt não trava o servidor.
        stored = auth.stored_hash(cfg)
        if not stored:
            raise HTTPException(503, "Senha do dashboard não definida. Rode `healthmon senha-dashboard`.")
        pw = str(payload.get("senha", "")) if isinstance(payload, dict) else ""
        if len(pw) > 256:
            raise HTTPException(400, "Senha inválida.")
        ok = throttle.attempt(lambda: auth.verify_password(pw, stored))
        if ok is None:
            raise HTTPException(429, "Muitas tentativas. Aguarde 15 minutos.")
        if not ok:
            raise HTTPException(401, "Senha incorreta.")
        token = auth.create_session(db, session_days, request.headers.get("user-agent", ""))
        response.set_cookie(COOKIE, token, max_age=session_days * 86400, httponly=True,
                            samesite="strict", secure=https, path="/")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        auth.end_session(db, request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    # ---------------------------------------------------------------- dados
    def ref_day() -> date:
        _, last = db.first_last_day()
        t = today_local(cfg)
        return min(t, date.fromisoformat(last)) if last else t

    def window() -> tuple[int, int]:
        return int(cfg.get("analise", "janela_base_dias", 28)), int(cfg.get("analise", "minimo_dias_base", 10))

    @app.get("/api/meta")
    def meta():
        return {
            "metricas": {k: {"rotulo": m.label, "unidade": m.unit, "bom": m.bom, "categoria": m.categoria,
                             "casas": m.casas} for k, m in METRICS.items()},
            "categorias": CATEGORIAS, "principais": CORE,
            "demo": db.get_setting("data_source") == "demo", "aviso": AVISO,
        }

    @app.get("/api/overview")
    def overview():
        day = ref_day()
        w, mn = window()
        snap = an.day_snapshot(db, day, w, mn)
        rec = snap["recuperacao"]
        return {
            "dia": day.isoformat(),
            "recuperacao": {**rec, "texto": STATUS_TXT[rec["status"]]},
            "avaliacoes": snap["avaliacoes"],
            "atencao": attention_points(snap),
            "alertas": [a for a in db.alerts(10) if not a["acknowledged"]],
            "ultima_sincronizacao": db.last_successful_sync(),
            "ultimo_relatorio": (db.list_reports(limit=1) or [None])[0],
        }

    @app.get("/api/series")
    def series(keys: str, days: int = 30):
        days = max(7, min(days, 3650))
        end = ref_day()
        start = end - timedelta(days=days - 1)
        w, mn = window()
        store = an.load_store(db, end, days + w + 5)
        wanted = [k for k in keys.split(",") if k in METRICS][:8]
        dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
        out: dict[str, Any] = {"datas": dates, "series": {}, "base": {}}
        for k in wanted:
            out["series"][k] = [display_value(k, store.value(k, date.fromisoformat(d_))) for d_ in dates]
            b = an.baseline(store, k, end + timedelta(days=1), w, mn)
            if b.faixa:
                out["base"][k] = {"mediana": display_value(k, b.mediana),
                                  "faixa": [display_value(k, x) for x in b.faixa], "n": b.n}
            # média móvel de 7 dias
            vals = out["series"][k]
            mm = []
            for i in range(len(vals)):
                win = [v for v in vals[max(0, i - 6): i + 1] if v is not None]
                mm.append(round(sum(win) / len(win), 2) if len(win) >= 4 else None)
            out["series"][k + "__mm7"] = mm
        return out

    @app.get("/api/compare")
    def compare(days: int = 7):
        days = max(1, min(days, 365))
        end = ref_day()
        a = (end - timedelta(days=days - 1), end)
        b = (a[0] - timedelta(days=days), a[0] - timedelta(days=1))
        store = an.load_store(db, end, 2 * days + 5)
        rows = [c for c in an.compare_periods(store, list(METRICS), a, b) if c["atual"]["n"]]
        return {"atual": [x.isoformat() for x in a], "anterior": [x.isoformat() for x in b], "linhas": rows}

    @app.get("/api/insights")
    def insights_(days: int = 30):
        days = max(14, min(days, 365))
        end = ref_day()
        store = an.load_store(db, end, max(days, 90) + 5)
        trends = [an.trend(store, k, end, days).as_dict() for k in METRICS]
        cors = an.correlations(store, end, max(days, 30))
        return {
            "tendencias": trends,
            "frases_tendencia": [s for s in (trend_sentence(t) for t in trends) if s],
            "associacoes": cors,
            "frases_associacao": [s for s in (correlation_sentence(c) for c in cors) if s],
            "regularidade_sono": an.sleep_regularity(store, end, min(days, 30)),
        }

    @app.get("/api/activities")
    def activities(days: int = 30):
        days = max(1, min(days, 3650))
        end = ref_day()
        return db.activities_range((end - timedelta(days=days - 1)).isoformat(), end.isoformat())

    @app.get("/api/alerts")
    def alerts():
        return db.alerts(100)

    @app.post("/api/alerts/{aid}/ack")
    def ack(aid: int):
        db.ack_alert(aid)
        return {"ok": True}

    @app.get("/api/reports")
    def reports(kind: str | None = None):
        return db.list_reports(kind, 120)

    @app.get("/api/reports/{rid}")
    def report(rid: int):
        r = db.get_report(rid)
        if not r:
            raise HTTPException(404)
        return r

    @app.get("/api/status")
    def status():
        return {
            "sincronizacoes": db.sync_history(15),
            "disponibilidade": availability(db, 30, ref_day()),
            "ia_modo": cfg.get("ia", "modo"),
            "ia_nuvem_consentimento": consent_status(cfg),
            "notificacoes": cfg.get("notificacoes", "canais", []),
            "nivel_conteudo": cfg.get("notificacoes", "nivel_conteudo"),
            "periodo": db.first_last_day(),
        }

    @app.post("/api/sync")
    def trigger_sync():
        if sync_fn is None:
            raise HTTPException(503, "Sincronização indisponível neste modo.")
        if not sync_lock.acquire(blocking=False):
            return {"ok": False, "mensagem": "Sincronização já em andamento."}

        def run():
            try:
                sync_fn()
            finally:
                sync_lock.release()
        threading.Thread(target=run, daemon=True).start()
        return {"ok": True, "mensagem": "Sincronização iniciada."}

    from .api2 import register
    register(app, cfg, db, ref_day)
    app.add_middleware(BodyLimit)
    return app
