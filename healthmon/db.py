"""Banco SQLite local (modo WAL)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .metrics import METRICS

METRIC_COLUMNS = list(METRICS.keys())
EXTRA_DAILY_COLUMNS = {
    "hrv_status": "TEXT",
    "hrv_baseline_low": "REAL",
    "hrv_baseline_high": "REAL",
    "sleep_start": "TEXT",
    "sleep_end": "TEXT",
    "readiness_level": "TEXT",
    "steps_goal": "REAL",
    "stress_rest_seconds": "REAL",
    "weight_kg": "REAL",
    "body_fat_pct": "REAL",
    "muscle_kg": "REAL",
    "water_ml": "REAL",
}

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS daily_metrics (
    date TEXT PRIMARY KEY,
    {", ".join(f"{c} REAL" for c in METRIC_COLUMNS)},
    {", ".join(f"{c} {t}" for c, t in EXTRA_DAILY_COLUMNS.items())},
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS raw_payloads (
    date TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL,          -- ok | vazio | erro | indisponivel
    payload TEXT,
    PRIMARY KEY (date, endpoint)
);
CREATE TABLE IF NOT EXISTS activities (
    activity_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    start_local TEXT,
    type TEXT,
    name TEXT,
    duration_s REAL,
    distance_m REAL,
    calories REAL,
    avg_hr REAL,
    max_hr REAL,
    aerobic_te REAL,
    anaerobic_te REAL,
    training_load REAL,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_act_date ON activities(date);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,            -- diario | noturno | semanal | mensal
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    markdown TEXT NOT NULL,
    html TEXT,
    missing TEXT,                  -- JSON: dados ausentes
    ai_mode TEXT,
    UNIQUE(kind, period_start, period_end)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    date TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,        -- info | atencao
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence TEXT,
    notified_at TEXT,
    acknowledged INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alert_kind ON alerts(kind, created_at);
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,          -- ok | parcial | erro
    dates TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    user_agent TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS body_measurements (
    date TEXT PRIMARY KEY, weight_kg REAL, body_fat_pct REAL, muscle_kg REAL, waist_cm REAL,
    source TEXT NOT NULL DEFAULT 'manual', note TEXT);
CREATE TABLE IF NOT EXISTS foods (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, source TEXT NOT NULL,
    kcal REAL, protein_g REAL, carb_g REAL, fat_g REAL, fiber_g REAL,   -- por 100 g
    portion_g REAL, price_100g REAL, UNIQUE(name, source));
CREATE VIRTUAL TABLE IF NOT EXISTS foods_fts USING fts5(name, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, time TEXT, description TEXT NOT NULL,
    kcal REAL, protein_g REAL, carb_g REAL, fat_g REAL, fiber_g REAL,
    method TEXT NOT NULL DEFAULT 'texto',       -- texto | alimentos | manual | foto | garmin
    uncertainty_pct REAL, items TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(date);
CREATE TABLE IF NOT EXISTS checkins (
    date TEXT PRIMARY KEY, perceived_recovery REAL, energy REAL, soreness REAL, mood REAL,
    sleep_quality REAL, caffeine_mg REAL, caffeine_last_time TEXT, alcohol_units REAL,
    water_ml REAL, symptoms TEXT, notes TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS training_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
    status TEXT NOT NULL,                       -- proposto | ativo | arquivado | recusado
    reason TEXT, config TEXT NOT NULL, plan TEXT NOT NULL, confirmed_at TEXT);
CREATE TABLE IF NOT EXISTS workout_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, session TEXT, exercise TEXT NOT NULL,
    set_no INTEGER, reps REAL, load_kg REAL, rir REAL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_sets_ex ON workout_sets(exercise, date);
CREATE TABLE IF NOT EXISTS workout_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, session TEXT, kind TEXT,
    duration_min REAL, rpe REAL, time_of_day TEXT, notes TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS labs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, lab TEXT, analyte TEXT NOT NULL,
    analyte_key TEXT, value REAL, value_text TEXT, unit TEXT, ref_low REAL, ref_high REAL,
    ref_text TEXT, source_file TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_labs ON labs(analyte_key, date);
CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, factor TEXT NOT NULL,
    condition_a TEXT NOT NULL, condition_b TEXT NOT NULL, outcome TEXT NOT NULL, lag_days INTEGER DEFAULT 0,
    block_days INTEGER DEFAULT 1, start_date TEXT NOT NULL, planned_days INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ativo', seed INTEGER, notes TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS experiment_days (
    experiment_id INTEGER NOT NULL, date TEXT NOT NULL, assigned TEXT NOT NULL,
    followed INTEGER, confounders TEXT, PRIMARY KEY (experiment_id, date));
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, date TEXT NOT NULL,
    area TEXT NOT NULL, title TEXT NOT NULL, detail TEXT, rationale TEXT, confidence TEXT,
    refs TEXT, status TEXT NOT NULL DEFAULT 'pendente',   -- pendente | aceita | recusada | automatica
    followed INTEGER, outcome TEXT, UNIQUE(date, area, title));
CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT, status TEXT NOT NULL, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_jobs ON job_runs(job, started_at);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS user_supplements (
    key TEXT PRIMARY KEY, dose TEXT, since TEXT, status TEXT NOT NULL DEFAULT 'em_uso', notes TEXT);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)
            c.executescript(SCHEMA_V2)
            self._migrate(c)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _migrate(self, c: sqlite3.Connection) -> None:
        cols = {r[1] for r in c.execute("PRAGMA table_info(daily_metrics)")}
        wanted = {**{m: "REAL" for m in METRIC_COLUMNS}, **EXTRA_DAILY_COLUMNS}
        for col, typ in wanted.items():
            if col not in cols:
                c.execute(f"ALTER TABLE daily_metrics ADD COLUMN {col} {typ}")
        ck = {r[1] for r in c.execute("PRAGMA table_info(checkins)")}
        for col, typ in (("fatigue", "REAL"), ("perceived_stress", "REAL"), ("triagem", "TEXT"), ("adherence", "REAL")):
            if col not in ck:
                c.execute(f"ALTER TABLE checkins ADD COLUMN {col} {typ}")

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA secure_delete=ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # --- acesso genérico (módulos da fase 2) ---------------------------
    def q(self, sql: str, args: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, tuple(args))]

    def q1(self, sql: str, args: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args: Iterable[Any] = ()) -> int:
        with self.conn() as c:
            return c.execute(sql, tuple(args)).lastrowid

    def insert(self, table: str, row: dict[str, Any], upsert_on: str | None = None) -> int:
        cols = list(row)
        sql = f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' for _ in cols)})"
        if upsert_on:
            keys = [k.strip() for k in upsert_on.split(",")]
            upd = ",".join(f"{k}=excluded.{k}" for k in cols if k not in keys)
            sql += f" ON CONFLICT({upsert_on}) DO UPDATE SET {upd}" if upd else f" ON CONFLICT({upsert_on}) DO NOTHING"
        return self.x(sql, [row[k] for k in cols])

    def get_json(self, key: str, default: Any = None) -> Any:
        v = self.get_setting(key)
        return json.loads(v) if v else default

    def set_json(self, key: str, value: Any) -> None:
        self.set_setting(key, json.dumps(value, ensure_ascii=False))

    # --- settings ------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.conn() as c:
            r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # --- raw -----------------------------------------------------------
    def save_raw(self, day: str, endpoint: str, status: str, payload: Any) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO raw_payloads(date,endpoint,fetched_at,status,payload) VALUES(?,?,?,?,?) "
                "ON CONFLICT(date,endpoint) DO UPDATE SET fetched_at=excluded.fetched_at, "
                "status=excluded.status, payload=excluded.payload",
                (day, endpoint, utcnow(), status,
                 json.dumps(payload, ensure_ascii=False) if payload is not None else None),
            )

    def raw_for_day(self, day: str) -> dict[str, Any]:
        with self.conn() as c:
            rows = c.execute("SELECT endpoint,status,payload FROM raw_payloads WHERE date=?",
                             (day,)).fetchall()
        return {r["endpoint"]: json.loads(r["payload"]) if r["payload"] else None
                for r in rows if r["status"] == "ok"}

    def raw_status(self, since: str) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute("SELECT endpoint,status,COUNT(*) n FROM raw_payloads WHERE date>=? "
                             "GROUP BY endpoint,status", (since,)).fetchall()

    def raw_days(self) -> list[str]:
        with self.conn() as c:
            return [r[0] for r in c.execute("SELECT DISTINCT date FROM raw_payloads ORDER BY date")]

    # --- daily ---------------------------------------------------------
    def upsert_daily(self, day: str, values: dict[str, Any]) -> None:
        allowed = set(METRIC_COLUMNS) | set(EXTRA_DAILY_COLUMNS)
        vals = {k: v for k, v in values.items() if k in allowed}
        vals["updated_at"] = utcnow()
        cols = ["date", *vals.keys()]
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{k}=excluded.{k}" for k in vals)
        with self.conn() as c:
            c.execute(f"INSERT INTO daily_metrics({','.join(cols)}) VALUES({placeholders}) "
                      f"ON CONFLICT(date) DO UPDATE SET {updates}", [day, *vals.values()])

    def daily_range(self, start: str, end: str) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM daily_metrics WHERE date BETWEEN ? AND ? ORDER BY date",
                             (start, end)).fetchall()
        return [dict(r) for r in rows]

    def daily(self, day: str) -> dict[str, Any] | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM daily_metrics WHERE date=?", (day,)).fetchone()
        return dict(r) if r else None

    def first_last_day(self) -> tuple[str | None, str | None]:
        with self.conn() as c:
            r = c.execute("SELECT MIN(date), MAX(date) FROM daily_metrics").fetchone()
        return r[0], r[1]

    # --- activities ----------------------------------------------------
    def upsert_activity(self, a: dict[str, Any]) -> None:
        cols = list(a.keys())
        with self.conn() as c:
            c.execute(
                f"INSERT INTO activities({','.join(cols)}) VALUES({','.join('?' for _ in cols)}) "
                f"ON CONFLICT(activity_id) DO UPDATE SET "
                + ",".join(f"{k}=excluded.{k}" for k in cols if k != "activity_id"),
                list(a.values()),
            )

    def activities_range(self, start: str, end: str) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT activity_id,date,start_local,type,name,duration_s,distance_m,"
                             "calories,avg_hr,max_hr,aerobic_te,anaerobic_te,training_load "
                             "FROM activities WHERE date BETWEEN ? AND ? ORDER BY start_local",
                             (start, end)).fetchall()
        return [dict(r) for r in rows]

    # --- reports -------------------------------------------------------
    def save_report(self, kind: str, start: str, end: str, title: str, summary: str,
                    markdown: str, html: str, missing: Iterable[str], ai_mode: str) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO reports(kind,period_start,period_end,created_at,title,summary,markdown,html,missing,ai_mode) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(kind,period_start,period_end) DO UPDATE SET "
                "created_at=excluded.created_at,title=excluded.title,summary=excluded.summary,"
                "markdown=excluded.markdown,html=excluded.html,missing=excluded.missing,ai_mode=excluded.ai_mode "
                "RETURNING id",
                (kind, start, end, utcnow(), title, summary, markdown, html,
                 json.dumps(list(missing), ensure_ascii=False), ai_mode))
            return cur.fetchone()[0]

    def list_reports(self, kind: str | None = None, limit: int = 60) -> list[dict[str, Any]]:
        q = "SELECT id,kind,period_start,period_end,created_at,title,summary,missing FROM reports"
        args: list[Any] = []
        if kind:
            q += " WHERE kind=?"
            args.append(kind)
        q += " ORDER BY period_end DESC, id DESC LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    def get_report(self, rid: int) -> dict[str, Any] | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None

    # --- alerts --------------------------------------------------------
    def add_alert(self, day: str, kind: str, severity: str, title: str, message: str,
                  evidence: dict | None = None) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO alerts(created_at,date,kind,severity,title,message,evidence) VALUES(?,?,?,?,?,?,?)",
                (utcnow(), day, kind, severity, title, message,
                 json.dumps(evidence or {}, ensure_ascii=False)))
            return cur.lastrowid

    def last_alert(self, kind: str) -> dict[str, Any] | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM alerts WHERE kind=? ORDER BY created_at DESC LIMIT 1",
                          (kind,)).fetchone()
        return dict(r) if r else None

    def alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,))]

    def pending_alerts(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM alerts WHERE notified_at IS NULL ORDER BY created_at")]

    def mark_alert_notified(self, aid: int) -> None:
        with self.conn() as c:
            c.execute("UPDATE alerts SET notified_at=? WHERE id=?", (utcnow(), aid))

    def count_notified_since(self, iso: str) -> int:
        with self.conn() as c:
            return c.execute("SELECT COUNT(*) FROM alerts WHERE notified_at>=? AND kind!='falha_sincronizacao' AND kind NOT LIKE 'chave_garmin%'",
                             (iso,)).fetchone()[0]

    def ack_alert(self, aid: int) -> None:
        with self.conn() as c:
            c.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (aid,))

    # --- sync log ------------------------------------------------------
    def log_sync_start(self, dates: list[str]) -> int:
        with self.conn() as c:
            return c.execute("INSERT INTO sync_log(started_at,status,dates) VALUES(?,?,?)",
                             (utcnow(), "executando", ",".join(dates))).lastrowid

    def log_sync_end(self, sid: int, status: str, detail: str = "") -> None:
        with self.conn() as c:
            c.execute("UPDATE sync_log SET finished_at=?, status=?, detail=? WHERE id=?",
                      (utcnow(), status, detail[:4000], sid))

    def sync_history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,))]

    def last_successful_sync(self) -> str | None:
        with self.conn() as c:
            r = c.execute("SELECT MAX(finished_at) FROM sync_log WHERE status IN ('ok','parcial')").fetchone()
        return r[0]


def iso(d: date) -> str:
    return d.isoformat()
