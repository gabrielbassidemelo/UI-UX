"""Orquestração: sincronização periódica, relatórios agendados, alertas e backups."""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone

from . import alerts, notify, reports
from .backup import create_backup
from .config import Config
from .db import Database
from .db import utcnow
from .garmin import GarminSource
from .sync import recent_days, sleep_synced, sync_days, today_local

log = logging.getLogger(__name__)


def make_source(cfg: Config, db: Database) -> GarminSource:
    """Fonte de dados conforme [garmin] fonte. A integração não oficial é desativada por padrão."""
    fonte = cfg.get("garmin", "fonte", "oficial")
    if fonte == "oficial":
        from .garmin_official import PersonalApiSource
        return PersonalApiSource(cfg, db)
    if fonte == "nao_oficial":
        try:
            from .garmin import GarminConnectSource
            import garminconnect  # noqa: F401
        except ImportError as e:
            raise RuntimeError('Integração não oficial não instalada (pip install "healthmon[nao-oficial]")') from e
        log.warning("Usando a integração NÃO OFICIAL da Garmin (desativada por padrão).")
        return GarminConnectSource(cfg)
    raise ValueError(f"[garmin] fonte inválida: {fonte}")


def tracked(name: str):
    """Registra cada execução em job_runs (histórico, falhas e recuperação)."""
    def deco(fn):
        def wrapper(self, *a, **kw):
            rid = self.db.insert("job_runs", {"job": name, "started_at": utcnow(), "status": "executando"})
            try:
                out = fn(self, *a, **kw)
                self.db.x("UPDATE job_runs SET finished_at=?, status='ok', detail=? WHERE id=?", (utcnow(), str(out)[:300], rid))
                return out
            except Exception as e:  # noqa: BLE001
                self.db.x("UPDATE job_runs SET finished_at=?, status='erro', detail=? WHERE id=?",
                          (utcnow(), f"{type(e).__name__}: {e}"[:300], rid))
                log.exception("Tarefa %s falhou", name)
                raise
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


class Service:
    def __init__(self, cfg: Config, db: Database, source: GarminSource | None = None, scheduler=None):
        self.cfg, self.db = cfg, db
        self._source = source
        self.scheduler = scheduler
        self._lock = threading.Lock()

    @property
    def source(self) -> GarminSource:
        if self._source is None:
            self._source = make_source(self.cfg, self.db)
        return self._source

    def pending_days(self, n: int) -> list[str]:
        """Sincronização incremental: pula dias antigos que já têm todos os endpoints gravados."""
        days = recent_days(self.cfg, n)
        done = {r["date"] for r in self.db.q(
            "SELECT date FROM raw_payloads WHERE status IN ('ok','vazio','indisponivel') GROUP BY date HAVING COUNT(*)>=5")}
        fresh = set(days[-3:])
        return [d for d in days if d in fresh or d not in done]

    # ------------------------------------------------------------- sync
    def sync(self, days: list[str] | None = None) -> dict:
        with self._lock:
            days = days or recent_days(self.cfg)
            try:
                src = self.source
            except Exception as e:  # noqa: BLE001 — fonte mal configurada não derruba o serviço
                res = {"status": "erro", "erro": "configuracao", "detalhe": str(e), "dias": {}}
                sid = self.db.log_sync_start(days)
                self.db.log_sync_end(sid, "erro", f"configuracao: {e}")
            else:
                res = sync_days(self.cfg, self.db, src, days, retries=1 if hasattr(src, "stats") else 3)
            log.info("Sincronização %s: %s", res["status"], list(res.get("dias", {})))
            from . import profile
            profile.import_garmin_weights(self.db)
            alerts.evaluate_sync(self.cfg, self.db, auth_error=res.get("erro") == "autenticacao")
            if res["status"] != "erro":
                alerts.evaluate(self.cfg, self.db, date.fromisoformat(days[-1]))
            notify.dispatch_alerts(self.cfg, self.db)
            return res

    # --------------------------------------------------------- relatórios
    @tracked("relatorio_diario")
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
        notify.notify_report(self.cfg, "diário", rep.doc.title, rep.summary, self.db)
        return "gerado"

    @tracked("relatorio_noturno")
    def job_nightly(self) -> str:
        today = today_local(self.cfg)
        self.sync(recent_days(self.cfg, 1, today))
        rep = reports.nightly(self.cfg, self.db, today)
        notify.notify_report(self.cfg, "noturno", rep.doc.title, rep.summary, self.db)
        return "gerado"

    @tracked("relatorio_semanal")
    def job_weekly(self) -> str:
        today = today_local(self.cfg)
        rep = reports.weekly(self.cfg, self.db, today)
        notify.notify_report(self.cfg, "semanal", rep.doc.title, rep.summary, self.db)
        return "gerado"

    @tracked("relatorio_mensal")
    def job_monthly(self) -> str:
        today = today_local(self.cfg)
        prev = today.replace(day=1) - timedelta(days=1)
        rep = reports.monthly(self.cfg, self.db, prev.year, prev.month)
        notify.notify_report(self.cfg, "mensal", rep.doc.title, rep.summary, self.db)
        return "gerado"

    @tracked("sincronizacao")
    def job_sync(self) -> str:
        return self.sync()["status"]

    def _backup_alert(self, kind: str, title: str, msg: str, evidence: dict) -> None:
        last = self.db.last_alert(kind)
        if last and datetime.now(timezone.utc) - datetime.fromisoformat(last["created_at"]) < timedelta(hours=20):
            return
        self.db.add_alert(today_local(self.cfg).isoformat(), kind, "atencao", title, msg, evidence)

    @tracked("backup")
    def job_backup(self) -> str:
        """Backup diário cifrado (RPO de 24 h): cópia consistente, verificação e envio para fora da VM.
        Qualquer falha vira alerta; nenhuma falha é silenciosa."""
        from . import offsite
        from .backup import verify_backup
        try:
            p = create_backup(self.cfg)
            verify_backup(self.cfg, p)      # estrutura e checksum (HMBK2) ou restauração de teste (HMBK1)
        except Exception as e:  # noqa: BLE001
            self._backup_alert("falha_backup", "Backup diário falhou",
                               "O backup local de hoje não foi criado ou não passou na verificação. Seus dados continuam "
                               "no servidor, mas sem cópia nova. Veja Configurações › Backup.", {"erro": type(e).__name__})
            notify.dispatch_alerts(self.cfg, self.db)
            raise
        out = f"{p.name} verificado"
        if offsite.configured(self.cfg):
            r = offsite.sync_latest(self.cfg)
            out += f"; externo: {r.get('status')}"
            self._offsite_check()
        else:
            self._backup_alert("backup_externo_ausente", "Backup externo não configurado",
                               "Os backups estão só no próprio servidor. Se a máquina for perdida, o histórico se perde. "
                               "Configure o envio ao Object Storage (docs/RECUPERACAO_DESASTRES.md).", {})
        notify.dispatch_alerts(self.cfg, self.db)
        return out

    def _offsite_check(self) -> dict:
        from . import offsite
        st = offsite.status(self.cfg)
        if st.get("alerta"):
            self._backup_alert("falha_backup_externo", "Backup externo com falha ou atrasado",
                               f"Falhas consecutivas: {st.get('falhas_consecutivas')}; último envio com sucesso: "
                               f"{st.get('ultimo_envio_ok') or 'nunca'}. O objetivo é nunca ficar mais de 24 h sem cópia externa.",
                               {k: st.get(k) for k in ("falhas_consecutivas", "idade_horas", "atrasado")})
        return st

    @tracked("backup_externo")
    def job_offsite_retry(self) -> str:
        """Reenvia o último backup se o envio anterior falhou ou está pendente."""
        from . import offsite
        if not offsite.configured(self.cfg):
            return "desativado"
        st = offsite.status(self.cfg)
        r = offsite.sync_latest(self.cfg) if (st.get("pendente") or st.get("ultimo_erro") or st.get("atrasado")) else {"status": "em_dia"}
        self._offsite_check()
        notify.dispatch_alerts(self.cfg, self.db)
        return str(r.get("status"))

    @tracked("retencao")
    def job_retention(self) -> str:
        raw = int(self.cfg.get("retencao", "brutos_dias", 400))
        runs = int(self.cfg.get("retencao", "execucoes_dias", 90))
        today = today_local(self.cfg)
        a = self.db.x("DELETE FROM raw_payloads WHERE date<?", ((today - timedelta(days=raw)).isoformat(),))
        self.db.x("DELETE FROM job_runs WHERE started_at<?", ((today - timedelta(days=runs)).isoformat(),))
        self.db.x("DELETE FROM sync_log WHERE started_at<?", ((today - timedelta(days=runs)).isoformat(),))
        return "ok"

    def catch_up(self) -> list[str]:
        """Ao iniciar (ex.: servidor reiniciado), gera relatórios cujo horário passou e não foram gerados."""
        now = datetime.now(self.cfg.tz)
        today = now.date().isoformat()
        done = []
        # tarefas que ficaram 'executando' por queda do processo
        self.db.x("UPDATE job_runs SET status='interrompida', finished_at=? WHERE status='executando'", (utcnow(),))

        def has(kind: str) -> bool:
            return bool(self.db.q1("SELECT 1 FROM reports WHERE kind=? AND period_end=?", (kind, today)))

        def past(key: str, default: str) -> bool:
            h, m = self.cfg.get("relatorios", key, default).split(":")
            return (now.hour, now.minute) >= (int(h), int(m))
        try:
            if past("diario", "08:00") and not has("diario"):
                self.job_daily(attempt=int(self.cfg.get("relatorios", "retentativa_max", 4)))
                done.append("diario")
            if past("noturno", "22:00") and not has("noturno"):
                self.job_nightly()
                done.append("noturno")
        except Exception:  # noqa: BLE001 — já registrado em job_runs
            pass
        return done

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
        scheduler.add_job(self.job_sync, "cron", hour=hours, minute=40, timezone=tz, id="sync",
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
        scheduler.add_job(self.job_offsite_retry, "cron", hour="9,15,21", minute=20, timezone=tz, id="offsite-retry",
                          misfire_grace_time=3 * 3600, coalesce=True)
        scheduler.add_job(self.job_retention, "cron", day_of_week="mon", hour=4, minute=10, timezone=tz, id="retention",
                          misfire_grace_time=24 * 3600, coalesce=True)
        scheduler.add_job(lambda: notify.dispatch_alerts(self.cfg, self.db), "cron", minute="*/30",
                          timezone=tz, id="dispatch", coalesce=True)
