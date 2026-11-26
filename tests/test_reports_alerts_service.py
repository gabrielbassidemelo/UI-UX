from datetime import date, timedelta

from healthmon import alerts, notify, reports
from healthmon.demo import DemoSource
from healthmon.garmin import AuthRequired
from healthmon.llm import narrate, strip_identifiers
from healthmon.service import Service
from healthmon.sync import sync_days

from .conftest import END


def test_all_reports_generate(cfg, demo_db):
    d = reports.daily(cfg, demo_db, END)
    assert "Recuperação" in d.doc.markdown() and "DADOS SIMULADOS" in d.doc.markdown()
    n = reports.nightly(cfg, demo_db, END)
    assert "Atividades do dia" in n.doc.markdown()
    w = reports.weekly(cfg, demo_db, END)
    assert "<svg" in w.doc.render_html() and "Tendências" in w.doc.markdown()
    m = reports.monthly(cfg, demo_db, 2026, 10)
    assert "outubro de 2026" in m.doc.title
    assert len(demo_db.list_reports()) == 4
    assert (cfg.reports_dir / "diario" / f"{END.isoformat()}.md").exists()


def test_daily_flags_missing_sleep(cfg, demo_db):
    day = END + timedelta(days=1)
    demo_db.upsert_daily(day.isoformat(), {"steps": 1000})
    rep = reports.daily(cfg, demo_db, day)
    md = rep.doc.markdown()
    assert "ainda não estava sincronizado" in md
    assert "sleep_seconds" in rep.missing


def test_html_escapes_untrusted_text(cfg, demo_db):
    demo_db.upsert_activity({"activity_id": "x1", "date": END.isoformat(), "start_local": f"{END} 18:00:00",
                             "type": "running", "name": "<script>alert(1)</script>", "duration_s": 600})
    html = reports.nightly(cfg, demo_db, END).doc.render_html()
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_alerts_fire_once_with_cooldown(cfg, demo_db):
    ids = alerts.evaluate(cfg, demo_db, END)
    kinds = {a["kind"] for a in demo_db.alerts()}
    assert ids and {"hrv_queda_persistente", "fc_repouso_aumento", "recuperacao_insuficiente"} <= kinds
    assert alerts.evaluate(cfg, demo_db, END) == []  # cooldown evita repetição


def test_alert_toggle_respected(cfg, demo_db):
    cfg.data["alertas"]["hrv_queda_persistente"] = False
    alerts.evaluate(cfg, demo_db, END)
    assert "hrv_queda_persistente" not in {a["kind"] for a in demo_db.alerts()}


def test_no_alerts_when_stable(cfg, db):
    from healthmon.demo import populate
    populate(db, days=60, end=END, stress_last=0)
    assert alerts.evaluate(cfg, db, END) == []


class FlakySource:
    def __init__(self, fail_endpoint=None, auth=False):
        self.inner, self.fail, self.auth = DemoSource(), fail_endpoint, auth

    def fetch_day(self, day):
        if self.auth:
            raise AuthRequired("expirado")
        out = self.inner.fetch_day(day)
        if self.fail:
            out[self.fail] = ("erro", {"erro": "X"})
        return out


def test_sync_partial_and_auth_error(cfg, db):
    r = sync_days(cfg, db, FlakySource("hrv"), ["2026-11-01"])
    assert r["status"] == "parcial" and db.daily("2026-11-01")["sleep_seconds"]
    r = sync_days(cfg, db, FlakySource(auth=True), ["2026-11-02"])
    assert r["status"] == "erro" and r["erro"] == "autenticacao"
    assert alerts.evaluate_sync(cfg, db, auth_error=True)


class NoSleepSource(DemoSource):
    def fetch_day(self, day):
        out = super().fetch_day(day)
        out["sleep"] = ("vazio", {})
        return out


class FakeScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, fn, trigger, **kw):
        self.jobs.append((fn, trigger, kw))


def test_daily_job_retries_then_reports(cfg, db, monkeypatch):
    sched = FakeScheduler()
    svc = Service(cfg, db, source=NoSleepSource(), scheduler=sched)
    assert svc.job_daily(0) == "retentativa"
    assert sched.jobs[0][2]["kwargs"] == {"attempt": 1}
    assert svc.job_daily(int(cfg.get("relatorios", "retentativa_max"))) == "gerado"
    rep = db.list_reports("diario")[0]
    assert "sleep_seconds" in rep["missing"]


def test_cloud_ai_requires_consent(cfg):
    cfg.data["ia"]["modo"] = "nuvem"
    text, mode = narrate(cfg, "diario", {"x": 1})
    assert text is None and mode == "regras"


def test_strip_identifiers():
    out = strip_identifiers({"a": 1, "email": "x", "nested": [{"activity_id": 3, "b": 2}]})
    assert out == {"a": 1, "nested": [{"b": 2}]}


def test_minimal_notifications_hide_health_data(cfg, demo_db, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda c, s, t, db=None: sent.append((s, t)) or ["telegram"])
    cfg.data["notificacoes"]["canais"] = ["telegram"]
    notify.notify_report(cfg, "diário", "Relatório diário", "HRV 40 ms · sono 5h")
    assert "HRV" not in sent[-1][0] + sent[-1][1]
    alerts.evaluate(cfg, demo_db, END)
    from datetime import datetime
    n = notify.dispatch_alerts(cfg, demo_db, now=datetime(2026, 11, 20, 12, 0, tzinfo=cfg.tz))
    assert n == 2  # limite diário
    assert all("HRV" not in s + t for s, t in sent[1:])


def test_quiet_hours(cfg):
    from datetime import datetime
    assert notify.in_quiet_hours(cfg, datetime(2026, 1, 1, 23, 0, tzinfo=cfg.tz))
    assert notify.in_quiet_hours(cfg, datetime(2026, 1, 1, 6, 30, tzinfo=cfg.tz))
    assert not notify.in_quiet_hours(cfg, datetime(2026, 1, 1, 12, 0, tzinfo=cfg.tz))
