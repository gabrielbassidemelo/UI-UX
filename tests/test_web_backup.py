import sqlite3

import pytest
from fastapi.testclient import TestClient

from healthmon.auth import set_password, verify_password, hash_password
from healthmon.backup import create_backup, decrypt_backup, restore_backup
from healthmon.web.app import create_app

H = {"X-HM": "1"}


@pytest.fixture
def client(cfg, demo_db):
    set_password(cfg, "senha-forte-123")
    return TestClient(create_app(cfg, demo_db, sync_fn=lambda: None))


def test_password_hash():
    h = hash_password("abc12345678")
    assert verify_password("abc12345678", h) and not verify_password("x", h)


def test_requires_auth(client):
    assert client.get("/api/overview").status_code == 401
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/healthz").status_code == 200


def test_login_flow_and_endpoints(client):
    assert client.post("/api/login", json={"senha": "errada"}, headers=H).status_code == 401
    r = client.post("/api/login", json={"senha": "senha-forte-123"}, headers=H)
    assert r.status_code == 200
    ck = r.headers["set-cookie"].lower()
    assert "httponly" in ck and "samesite=strict" in ck
    for path in ["/api/meta", "/api/overview", "/api/series?keys=hrv_last_night,resting_hr&days=30",
                 "/api/compare?days=7", "/api/insights?days=30", "/api/activities?days=30",
                 "/api/alerts", "/api/reports", "/api/status", "/"]:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    s = client.get("/api/series?keys=hrv_last_night&days=30").json()
    assert len(s["datas"]) == 30 and "hrv_last_night__mm7" in s["series"]
    assert client.get("/api/meta").json()["demo"] is True
    assert client.post("/api/sync", headers=H).json()["ok"] is True


def test_csrf_header_required(client):
    client.post("/api/login", json={"senha": "senha-forte-123"}, headers=H)
    assert client.post("/api/sync").status_code == 403


def test_login_throttle(client):
    for _ in range(5):
        client.post("/api/login", json={"senha": "x"}, headers=H)
    assert client.post("/api/login", json={"senha": "senha-forte-123"}, headers=H).status_code == 429


def test_backup_roundtrip(cfg, demo_db, monkeypatch):
    p = create_backup(cfg)
    assert p.read_bytes()[:5] == b"HMBK1"
    assert b"SQLite" not in p.read_bytes()  # conteúdo cifrado
    assert (p.stat().st_mode & 0o077) == 0
    data = decrypt_backup(cfg, p)
    assert data.startswith(b"SQLite format 3")
    with demo_db.conn() as c:
        c.execute("DELETE FROM daily_metrics")
    restore_backup(cfg, p)
    n = sqlite3.connect(cfg.db_path).execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0]
    assert n == 90
    monkeypatch.setenv("HEALTHMON_BACKUP_PASSPHRASE", "outra-frase-errada-xyz")
    with pytest.raises(Exception):
        decrypt_backup(cfg, p)
