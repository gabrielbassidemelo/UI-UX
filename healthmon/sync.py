"""Sincronização: busca dados brutos, normaliza e grava; inventário de disponibilidade."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

from .config import Config
from .db import Database, utcnow
from .garmin import AuthRequired, GarminSource
from .metrics import METRICS
from .parse import parse_activity, parse_day

log = logging.getLogger(__name__)


def today_local(cfg: Config) -> date:
    return datetime.now(cfg.tz).date()


def process_day(db: Database, day: str) -> dict[str, Any]:
    raw = db.raw_for_day(day)
    values = parse_day(raw, day)
    if values:
        db.upsert_daily(day, values)
    from .parse import nutrition_summary
    ns = nutrition_summary(raw)
    if ns:      # registro alimentar feito no Garmin Connect: entra como uma refeição-resumo, sem duplicar as suas
        db.x("DELETE FROM meals WHERE date=? AND method='garmin'", (day,))
        if not db.q1("SELECT 1 FROM meals WHERE date=?", (day,)):
            db.insert("meals", {"date": day, "description": "Total registrado no Garmin Connect", **ns,
                                "method": "garmin", "uncertainty_pct": 20.0, "created_at": utcnow()})
    acts = raw.get("activities") or []
    if isinstance(acts, dict):
        acts = next((v for v in acts.values() if isinstance(v, list)), [])
    for a in acts:
        if isinstance(a, dict):
            pa = parse_activity(a)
            if pa:
                import json
                pa["raw"] = json.dumps(a, ensure_ascii=False)
                pa["date"] = pa["date"] or day
                db.upsert_activity(pa)
    return values


def sync_days(cfg: Config, db: Database, source: GarminSource, days: list[str],
              retries: int = 3) -> dict[str, Any]:
    sid = db.log_sync_start(days)
    result: dict[str, Any] = {"dias": {}, "status": "ok"}
    try:
        for day in days:
            attempt = 0
            while True:
                try:
                    fetched = source.fetch_day(day)
                    break
                except AuthRequired:
                    raise
                except Exception as e:  # noqa: BLE001
                    if type(e).__name__ in ("RateLimited", "BudgetExceeded"):
                        raise                       # não insistir contra limite de requisições
                    attempt += 1
                    if attempt > retries:
                        raise
                    wait = min(300, 20 * 2 ** attempt)
                    log.warning("Falha ao sincronizar %s (%s). Nova tentativa em %ss",
                                day, type(e).__name__, wait)
                    time.sleep(wait)
            for endpoint, (status, payload) in fetched.items():
                db.save_raw(day, endpoint, status, payload)
            values = process_day(db, day)
            errs = [k for k, (s, _) in fetched.items() if s == "erro"]
            if errs:
                result["status"] = "parcial"
            result["dias"][day] = {"metricas": len(values), "erros": errs}
        db.log_sync_end(sid, result["status"], str(result["dias"]))
    except AuthRequired as e:
        db.log_sync_end(sid, "erro", f"autenticacao: {e}")
        result.update(status="erro", erro="autenticacao", detalhe=str(e))
    except Exception as e:  # noqa: BLE001
        db.log_sync_end(sid, "erro", f"{type(e).__name__}: {e}")
        result.update(status="erro", erro=type(e).__name__, detalhe=str(e)[:300])
    return result


def sync_range(cfg: Config, db: Database, source: Any, start: str, end: str) -> dict[str, Any]:
    """Importa histórico por intervalo (fonte oficial). Idempotente: regravar um dia não duplica nada."""
    sid = db.log_sync_start([start, end])
    try:
        fetched = source.fetch_range(start, end)
        n = 0
        for day, eps in fetched.items():
            for endpoint, (status, payload) in eps.items():
                db.save_raw(day, endpoint, status, payload)
            if process_day(db, day):
                n += 1
        db.log_sync_end(sid, "ok", f"{len(fetched)} dias, {n} com dados")
        return {"status": "ok", "dias": len(fetched), "dias_com_dados": n}
    except AuthRequired as e:
        db.log_sync_end(sid, "erro", f"autenticacao: {e}")
        return {"status": "erro", "erro": "autenticacao", "detalhe": str(e)}
    except Exception as e:  # noqa: BLE001
        db.log_sync_end(sid, "erro", f"{type(e).__name__}: {e}")
        return {"status": "erro", "erro": type(e).__name__, "detalhe": str(e)[:300]}


def recent_days(cfg: Config, n: int | None = None, ref: date | None = None) -> list[str]:
    n = n if n is not None else int(cfg.get("coleta", "dias_reprocessar", 2))
    ref = ref or today_local(cfg)
    return [(ref - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def availability(db: Database, days: int = 30, ref: date | None = None) -> dict[str, Any]:
    """Inventário empírico: quais indicadores a SUA conta/dispositivo realmente fornece."""
    ref = ref or date.today()
    start = (ref - timedelta(days=days - 1)).isoformat()
    rows = db.daily_range(start, ref.isoformat())
    total = len(rows)
    out = {}
    for key, m in METRICS.items():
        n = sum(1 for r in rows if r.get(key) is not None)
        if total == 0:
            st = "sem dados"
        elif n == 0:
            st = "indisponível"
        elif n / total >= 0.6:
            st = "disponível"
        else:
            st = "parcial"
        out[key] = {"rotulo": m.label, "dias_com_dado": n, "dias": total, "status": st}
    return out


def sleep_synced(db: Database, day: str) -> bool:
    r = db.daily(day)
    return bool(r and r.get("sleep_seconds"))
