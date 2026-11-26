"""Testes de CONTRATO da integração oficial (Garmin Personal API) com respostas SINTÉTICAS.

Usam as classes reais da biblioteca oficial com uma sessão HTTP falsa. Validam caminhos,
cabeçalhos, renovação de token, erros e limites. NÃO validam o formato real das respostas
da Garmin: isso só é possível com `healthmon validar-garmin` no ambiente definitivo.
"""
import base64
import json
import logging
import time
from datetime import date, timedelta

import pytest

from healthmon import alerts
from healthmon.garmin import AuthRequired
from healthmon.garmin_official import (ENDPOINTS, BudgetExceeded, PersonalApiSource, RateLimited,
                                       key_expiry_status, shape)
from healthmon.service import Service, make_source
from healthmon.sync import sync_days

KEY = "chave-sintetica-NAO-REAL-123456"
DAY = "2026-11-20"


def jwt(exp: float) -> str:
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{b({'alg': 'none'})}.{b({'exp': exp})}.assinatura"


class Resp:
    def __init__(self, status=200, payload=None, text=None):
        self.status_code, self.ok = status, 200 <= status < 300
        self._payload = payload
        self.text = text if text is not None else (json.dumps(payload) if payload is not None else "")
        self.content = self.text.encode()
        self.headers = {}

    def json(self):
        return json.loads(self.text)


PAYLOADS = {
    "/usersummary-service/v2/dailysummaries": [{"calendarDate": DAY, "totalSteps": 9100, "totalKilocalories": 2800,
                                                "activeKilocalories": 700, "averageStressLevel": 31}],
    "/sleep-service/sleep/dailySleep": {"dailySleepDTO": {"sleepTimeSeconds": 25200, "deepSleepSeconds": 5000,
                                                          "remSleepSeconds": 5400, "lightSleepSeconds": 14800,
                                                          "sleepScores": {"overall": {"value": 80}}}},
    f"/hrv-service/hrv/{DAY}": {"hrvSummary": {"lastNightAvg": 51, "weeklyAvg": 50, "status": "BALANCED"}},
    "/wellness-service/wellness/dailyHeartRate": {"restingHeartRate": 54, "minHeartRate": 49, "maxHeartRate": 150},
    "/wellness-service/wellness/bodyBattery/reports/daily": [{"date": DAY, "charged": 60, "drained": 55,
                                                             "bodyBatteryValuesArray": [[1, 20], [2, 85], [3, 40]]}],
    f"/usersummary-service/stats/stress/daily/{DAY}/{DAY}": [{"calendarDate": DAY, "values": {"overallStressLevel": 31}}],
    f"/wellness-service/wellness/daily/spo2acclimation/{DAY}": {"averageSpO2": 96},
    f"/metrics-service/metrics/maxmet/daily/{DAY}/{DAY}": [{"generic": {"vo2MaxPreciseValue": 47.2}}],
    "/activitylist-service/activities/search/activities": [{"activityId": 77, "startTimeLocal": f"{DAY} 07:00:00",
                                                           "activityType": {"typeKey": "running"}, "duration": 1800,
                                                           "activityTrainingLoad": 90}],
    f"/weight-service/weight/range/{DAY}/{DAY}": {"dailyWeightSummaries": [{"allWeightMetrics": [{"weight": 87500, "bodyFat": 22.5}]}]},
    f"/usersummary-service/stats/hydration/daily/{DAY}/{DAY}": [{"valueInML": 2300}],
    f"/nutrition-service/food/logs/{DAY}": None,
}


class FakeSession:
    def __init__(self, token_status=200, overrides=None):
        self.posts, self.gets, self.token_status = [], [], token_status
        self.overrides = overrides or {}          # path -> lista de Resp consumida em ordem
        self.exp = time.time() + 24 * 3600

    def post(self, url, headers=None, timeout=None):
        self.posts.append((url, headers))
        if self.token_status != 200:
            return Resp(self.token_status, text="negado")
        return Resp(200, text=jwt(self.exp))

    def get(self, url, headers=None, params=None, timeout=None):
        path = url.split("garmin.com", 1)[1]
        self.gets.append((path, headers, params))
        if self.overrides.get(path):
            return self.overrides[path].pop(0)
        if path not in PAYLOADS:
            return Resp(404, text="nao existe")
        p = PAYLOADS[path]
        return Resp(200, p) if p is not None else Resp(204, text="")

    def close(self):
        pass


@pytest.fixture
def src(cfg, db, monkeypatch):
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", KEY)
    s = PersonalApiSource(cfg, db, session=FakeSession(), sleep=lambda x: None)
    return s


def test_official_is_default_and_unofficial_is_opt_in(cfg, db):
    assert cfg.get("garmin", "fonte") == "oficial"
    assert isinstance(make_source(cfg, db), PersonalApiSource)
    cfg.data["garmin"]["fonte"] = "qualquer"
    with pytest.raises(ValueError):
        make_source(cfg, db)


def test_auth_header_endpoints_and_token_cache(src, cfg):
    out = src.fetch_day(DAY)
    s = src.session
    assert set(out) == set(ENDPOINTS)
    assert s.posts[0][0].endswith("/personalapitoken-service/personal-api/personal-api-key/token")
    assert s.posts[0][1]["Authorization"] == f"ApiKey {KEY}"
    assert all(h["Authorization"].startswith("Bearer ") for _, h, _ in s.gets)
    assert len(s.posts) == 1                              # um token para todas as chamadas
    assert out["food_logs"][0] == "vazio" and out["sleep"][0] == "ok"
    cache = cfg.token_dir / "garmin-personal-bearer.json"
    assert cache.exists() and (cache.stat().st_mode & 0o077) == 0
    PersonalApiSource(cfg, None, session=s, sleep=lambda x: None).fetch_day(DAY)
    assert len(s.posts) == 1                              # segunda instância reutiliza o cache


def test_token_renewed_when_near_expiry(cfg, db, monkeypatch):
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", KEY)
    s = FakeSession()
    s.exp = time.time() + 600                             # expira em 10 min (< janela de 1 h)
    PersonalApiSource(cfg, db, session=s, sleep=lambda x: None).fetch_day(DAY)
    PersonalApiSource(cfg, db, session=s, sleep=lambda x: None).fetch_day(DAY)
    assert len(s.posts) == 2


def test_401_forces_one_refresh_then_auth_required(cfg, db, monkeypatch):
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", KEY)
    path = "/sleep-service/sleep/dailySleep"
    s = FakeSession(overrides={path: [Resp(401, text="revogado")]})
    out = PersonalApiSource(cfg, db, session=s, sleep=lambda x: None).fetch_day(DAY)
    assert out["sleep"][0] == "ok" and len(s.posts) == 2  # renovou e repetiu
    s2 = FakeSession(overrides={path: [Resp(401, text="x"), Resp(401, text="x")]})
    with pytest.raises(AuthRequired):
        PersonalApiSource(cfg, db, session=s2, sleep=lambda x: None).fetch_day(DAY)


def test_missing_or_rejected_key(cfg, db, monkeypatch):
    monkeypatch.delenv("GARMIN_PERSONAL_API_KEY", raising=False)
    with pytest.raises(AuthRequired):
        PersonalApiSource(cfg, db, session=FakeSession(), sleep=lambda x: None).fetch_day(DAY)
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", KEY)
    with pytest.raises(AuthRequired):
        PersonalApiSource(cfg, db, session=FakeSession(token_status=401), sleep=lambda x: None).fetch_day(DAY)
    res = sync_days(cfg, db, PersonalApiSource(cfg, db, session=FakeSession(token_status=403), sleep=lambda x: None), [DAY])
    assert res["erro"] == "autenticacao"
    assert alerts.evaluate_sync(cfg, db, auth_error=True)


def test_backoff_on_5xx_and_statuses(cfg, db, monkeypatch):
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", KEY)
    waits = []
    s = FakeSession(overrides={"/sleep-service/sleep/dailySleep": [Resp(503, text="x"), Resp(502, text="x")],
                               f"/hrv-service/hrv/{DAY}": [Resp(404, text="x")],
                               "/wellness-service/wellness/dailyHeartRate": [Resp(200, text="<html>mudou</html>")]})
    src = PersonalApiSource(cfg, db, session=s, sleep=waits.append)
    out = src.fetch_day(DAY)
    assert out["sleep"][0] == "ok" and src.stats["retentativas"] == 2
    backoffs = [w for w in waits if w > 1.5]
    assert len(backoffs) == 2 and backoffs[1] > backoffs[0]          # exponencial
    assert out["hrv"][0] == "indisponivel"
    assert out["heart_rate"][0] == "erro"                             # API mudou: não quebra o resto
    assert out["daily_summary"][0] == "ok"


def test_rate_limit_opens_circuit_breaker(cfg, db, monkeypatch):
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", KEY)
    s = FakeSession(overrides={"/sleep-service/sleep/dailySleep": [Resp(429, text="devagar")]})
    src = PersonalApiSource(cfg, db, session=s, sleep=lambda x: None)
    with pytest.raises(RateLimited):
        src.fetch_day(DAY)
    n = len(s.gets)
    with pytest.raises(RateLimited):                                  # bloqueado: nenhuma nova requisição
        src.fetch_day(DAY)
    assert len(s.gets) == n
    res = sync_days(cfg, db, src, [DAY], retries=3)
    assert res["status"] == "erro" and len(s.gets) == n               # sync não insiste


def test_daily_budget(cfg, db, monkeypatch):
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", KEY)
    cfg.data["garmin"]["max_requisicoes_dia"] = 5
    s = FakeSession()
    with pytest.raises(BudgetExceeded):
        PersonalApiSource(cfg, db, session=s, sleep=lambda x: None).fetch_day(DAY)
    assert len(s.gets) == 5


def test_sync_parses_and_never_duplicates(cfg, db, src):
    for _ in range(2):
        res = sync_days(cfg, db, src, [DAY])
        assert res["status"] == "ok"
    r = db.daily(DAY)
    assert (r["steps"], r["sleep_score"], r["hrv_last_night"], r["resting_hr"]) == (9100, 80, 51, 54)
    assert (r["bb_max"], r["bb_min"], r["bb_charged"], r["vo2max"]) == (85, 20, 60, 47.2)
    assert r["weight_kg"] == 87.5 and r["body_fat_pct"] == 22.5 and r["water_ml"] == 2300
    assert db.q1("SELECT COUNT(*) n FROM daily_metrics")["n"] == 1
    assert db.q1("SELECT COUNT(*) n FROM activities")["n"] == 1
    assert db.q1("SELECT COUNT(*) n FROM raw_payloads WHERE date=?", (DAY,))["n"] == len(ENDPOINTS)


def test_incremental_sync_skips_complete_old_days(cfg, db, src, monkeypatch):
    svc = Service(cfg, db, source=src)
    monkeypatch.setattr("healthmon.service.recent_days", lambda c, n=None, ref=None: [
        (date(2026, 11, 20) - timedelta(days=i)).isoformat() for i in range(9, -1, -1)])
    old = "2026-11-12"
    for ep in ENDPOINTS:
        db.save_raw(old, ep, "ok", {})
    pend = svc.pending_days(10)
    assert old not in pend and "2026-11-20" in pend and len(pend) == 9


def test_key_never_in_database_or_logs(cfg, db, src, caplog):
    caplog.set_level(logging.DEBUG)
    sync_days(cfg, db, src, [DAY])
    assert KEY not in caplog.text
    for f in cfg.home.rglob("*"):
        if f.is_file() and f.name != "secrets.env":
            assert KEY.encode() not in f.read_bytes(), f.name


def test_key_expiry_levels_and_alert(cfg, db):
    today = date(2026, 11, 20)
    assert key_expiry_status(cfg, today)["nivel"] == "desconhecido"
    for delta, level in ((40, "ok"), (10, "aviso"), (2, "critico"), (-1, "expirada")):
        cfg.data["garmin"]["chave_expira_em"] = (today + timedelta(days=delta)).isoformat()
        assert key_expiry_status(cfg, today)["nivel"] == level
    cfg.data["garmin"]["chave_expira_em"] = (date.today() + timedelta(days=2)).isoformat()
    assert alerts.evaluate_key_expiry(cfg, db)
    assert alerts.evaluate_key_expiry(cfg, db) is None                # sem repetição


def test_shape_has_no_values():
    s = json.dumps(shape({"restingHeartRate": 54, "nested": [{"score": 80, "nome": "segredo"}]}))
    assert "54" not in s and "80" not in s and "segredo" not in s and "restingHeartRate" in s
