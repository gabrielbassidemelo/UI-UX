"""Orquestração: sincronização periódica, relatórios agendados, alertas e backups."""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta

from . import alerts, notify, reports
from .backup import create_backup
from .config import Config
from .db import Database
from .garmin import GarminConnectSource, GarminSource
from .sync import recent_days, sleep_synced, sync_days, today_local

log = logging.getLogger(__name__)


class Service:
    def __init__(self, cfg: Config, db: Database, source: GarminSource | None = None, scheduler=None):
        self.cfg, self.db = cfg, db
        self.source = source or GarminConnectSource(cfg)
        self.scheduler = scheduler
        self._lock = threading.Lock()

    # ------------------------------------------------------------- sync
    def sync(self, days: list[str] | None = None) -> dict:
        with self._lock:
            days = days or recent_days(self.cfg)
            res = sync_days(self.cfg, self.db, self.source, days)
            log.info("Sincronização %s: %s", res["status"], list(res.get("dias", {})))
            alerts.evaluate_sync(self.cfg, self.db, auth_error=res.get("erro") == "autenticacao")
            if res["status"] != "erro":
                alerts.evaluate(self.cfg, self.db, date.fromisoformat(days[-1]))
            notify.dispatch_alerts(self.cfg, self.db)
            return res

    # --------------------------------------------------------- relatórios
    def job_daily(self, attempt: int = 0) -> str:
        today = today_local(self.cfg)
        res = self.sync(recent_days(self.cfg, 2, today))
        max_try = int(self.cfg.get("relatorios", "retentativa_max", 4))
        if not sleep_synced(self.db, today.isoformat()) and attempt < max_try:
            delay = int(self.cfg.get("relatorios", "retentativa_intervalo_min", 30))
            log.info("Sono de %s ainda não sincronizado; nova tentativa em %s min (%s/%s)",
                     today, delay, attempt + 1, max_try)
            if self.scheduler is not None:
                self.scheduler.add_job(self.job_daily, "date",
                                       run_date=datetime.now(self.cfg.tz) + timedelta(minutes=delay),
                                       kwargs={"attempt": attempt + 1}, id=f"daily-retry-{attempt + 1}",
                                       replace_existing=True)
                return "retentativa"
        rep = reports.daily(self.cfg, self.db, today, sync_failed=res["status"] == "erro")
        notify.notify_report(self.cfg, "diário", rep.doc.title, rep.summary)
        return "gerado"

    def job_nightly(self) -> str:
        today = today_local(self.cfg)
        self.sync(recent_days(self.cfg, 1, today))
        rep = reports.nightly(self.cfg, self.db, today)
        notify.notify_report(self.cfg, "noturno", rep.doc.title, rep.summary)
        return "gerado"

    def job_weekly(self) -> str:
        today = today_local(self.cfg)
        rep = reports.weekly(self.cfg, self.db, today)
        notify.notify_report(self.cfg, "semanal", rep.doc.title, rep.summary)
        return "gerado"

    def job_monthly(self) -> str:
        today = today_local(self.cfg)
        prev = today.replace(day=1) - timedelta(days=1)
        rep = reports.monthly(self.cfg, self.db, prev.year, prev.month)
        notify.notify_report(self.cfg, "mensal", rep.doc.title, rep.summary)
        return "gerado"

    def job_backup(self) -> None:
        try:
            p = create_backup(self.cfg)
            log.info("Backup criado: %s", p.name)
        except Exception as e:  # noqa: BLE001
            log.error("Backup falhou: %s", e)

    # ----------------------------------------------------------- agenda
    def schedule(self, scheduler) -> None:
        self.scheduler = scheduler
        tz = self.cfg.tz
        g = self.cfg.get

        def hm(s: str) -> tuple[int, int]:
            h, m = s.split(":")
            return int(h), int(m)

        interval = int(g("coleta", "intervalo_min", 120))
        hours = ",".join(str(h) for h in range(5, 24, max(1, interval // 60)))
        scheduler.add_job(self.sync, "cron", hour=hours, minute=40, timezone=tz, id="sync",
                          max_instances=1, coalesce=True, misfire_grace_time=1800)
        h, m = hm(g("relatorios", "diario", "08:00"))
        scheduler.add_job(self.job_daily, "cron", hour=h, minute=m, timezone=tz, id="daily",
                          misfire_grace_time=3 * 3600, coalesce=True)
        h, m = hm(g("relatorios", "noturno", "22:00"))
        scheduler.add_job(self.job_nightly, "cron", hour=h, minute=m, timezone=tz, id="nightly",
                          misfire_grace_time=2 * 3600, coalesce=True)
        h, m = hm(g("relatorios", "semanal_hora", "19:00"))
        scheduler.add_job(self.job_weekly, "cron", day_of_week=g("relatorios", "semanal_dia", "sun"),
                          hour=h, minute=m, timezone=tz, id="weekly", misfire_grace_time=12 * 3600, coalesce=True)
        h, m = hm(g("relatorios", "mensal_hora", "09:00"))
        scheduler.add_job(self.job_monthly, "cron", day=int(g("relatorios", "mensal_dia", 1)), hour=h,
                          minute=m, timezone=tz, id="monthly", misfire_grace_time=24 * 3600, coalesce=True)
        h, m = hm(g("backup", "hora", "03:30"))
        scheduler.add_job(self.job_backup, "cron", hour=h, minute=m, timezone=tz, id="backup",
                          misfire_grace_time=6 * 3600, coalesce=True)
        scheduler.add_job(lambda: notify.dispatch_alerts(self.cfg, self.db), "cron", minute="*/30",
                          timezone=tz, id="dispatch", coalesce=True)
