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


def summary_v2(day=DAY, wellness=True, steps=9100):
    """Resumo diário SINTÉTICO no formato UserDailySummaryV2DTO da especificação oficial."""
    return {"calendarDate": day, "includesWellnessData": wellness, "includesActivityData": True,
            "wellnessChronology": {"totalDurationInMillis": 80000000 if wellness else 0},
            "movement": {"steps": {"goal": 8000, "value": steps, "distanceInMeters": 7000.5}, "floorsAscended": {"value": 6}},
            "calories": {"burnedResting": 1900, "burnedActive": 700, "burnedTotal": 2600},
            "heartRate": {"minValue": 49, "maxValue": 150, "restingValue": 54},
            "intensityMinutes": {"moderate": 20, "vigorous": 10},
            "stress": {"avgLevel": 31, "maxLevel": 88, "highStressDurationInMillis": 1800000, "restDurationInMillis": 30000000,
                       "totalDurationInMillis": 60000000},
            "bodyBattery": {"minValue": 20, "maxValue": 85, "chargedValue": 60, "drainedValue": 55},
            "hydration": {"consumedInFractionalMl": 2300.0}, "respiration": {"avgValue": 14}, "pulseOx": {"avgValue": 96}}


SLEEP = {"calendarDate": DAY, "sleepTimeSeconds": 25200, "deepSleepSeconds": 5000, "remSleepSeconds": 5400,
         "lightSleepSeconds": 14800, "awakeSleepSeconds": 900, "awakeCount": 2, "avgSleepStress": 16.0,
         "averageRespirationValue": 13.5, "averageSpO2Value": 95.0, "lowestSpO2Value": 89,
         "sleepStartTimestampLocal": "2026-11-19T23:10:00.0", "sleepEndTimestampLocal": "2026-11-20T06:25:00.0",
         "sleepScores": {"overall": {"value": 80, "qualifierKey": "GOOD"}}, "deviceRemCapable": True}
HRV = {"lastNightAvg": 51, "weeklyAvg": 50, "status": "BALANCED", "calendarDate": DAY, "baseline": {"balancedLow": 44, "balancedUpper": 58}}
ACT = {"activityId": 77, "startTimeLocal": f"{DAY} 07:00:00", "activityType": {"typeKey": "running"}, "duration": 1800.0,
       "calories": 320.0, "averageHR": 140.0, "activityTrainingLoad": 90.0}
PAYLOADS = {
    "/usersummary-service/v2/dailysummaries": {"data": [summary_v2()]},
    "/sleep-service/sleep/dailySleep": SLEEP,
    "/sleep-service/sleep/dailySleepSummariesByDate": [SLEEP],
    f"/hrv-service/hrv/{DAY}": {"userProfilePk": 1, "hrvSummary": HRV},
    "/wellness-service/wellness/dailyHeartRate": {"restingHeartRate": 54, "minHeartRate": 49, "maxHeartRate": 150},
    "/wellness-service/wellness/bodyBattery/reports/daily": [{"date": DAY, "charged": 60, "drained": 55,
        "bodyBatteryValueDescriptorDTOList": [{"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "timestamp"},
                                              {"bodyBatteryValueDescriptorIndex": 1, "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"}],
        "bodyBatteryValuesArray": [[1, 20], [2, 85], [3, 40]]}],
    f"/usersummary-service/stats/stress/daily/{DAY}/{DAY}": [{"calendarDate": DAY, "values": {"overallStressLevel": 31, "highStressDuration": 1800}}],
    f"/wellness-service/wellness/daily/spo2acclimation/{DAY}": {"averageSpO2": 96.0, "lowestSpO2": 89, "avgSleepSpO2": 95.0},
    f"/metrics-service/metrics/maxmet/daily/{DAY}/{DAY}": [{"generic": {"calendarDate": DAY, "vo2MaxPreciseValue": 47.2}}],
    "/activitylist-service/activities/search/activities": [ACT],
    f"/weight-service/weight/range/{DAY}/{DAY}": {"dailyWeightSummaries": [{"summaryDate": DAY, "allWeightMetrics": [{"weight": 87.5, "bodyFat": 22.5}]}]},
    f"/usersummary-service/stats/hydration/daily/{DAY}/{DAY}": [{"calendarDate": DAY, "valueInML": 2300.0}],
    f"/nutrition-service/food/logs/{DAY}": None,
    "/userprofile-service/userprofile/settings": {"timeZone": "America/Sao_Paulo", "measurementSystem": "metric"},
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
    from healthmon.garmin_official import EXTRA_ENDPOINTS
    full = src.fetch_day(DAY, extras=True)
    assert set(full) == set(ENDPOINTS) | set(EXTRA_ENDPOINTS) and full["heart_rate"][0] == "ok"
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
    assert src.fetch_day(DAY, extras=True)["heart_rate"][0] == "erro"   # API mudou: não quebra o resto
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
    cfg.data["garmin"]["max_requisicoes_dia"] = 4
    s = FakeSession()
    with pytest.raises(BudgetExceeded):
        PersonalApiSource(cfg, db, session=s, sleep=lambda x: None).fetch_day(DAY)
    assert len(s.gets) == 4


def test_sync_parses_and_never_duplicates(cfg, db, src):
    for _ in range(2):
        res = sync_days(cfg, db, src, [DAY])
        assert res["status"] == "ok"
    r = db.daily(DAY)
    assert (r["steps"], r["sleep_score"], r["hrv_last_night"], r["resting_hr"]) == (9100, 80, 51, 54)
    assert (r["bb_max"], r["bb_min"], r["bb_charged"], r["vo2max"]) == (85, 20, 60, 47.2)
    assert r["weight_kg"] == 87.5 and r["body_fat_pct"] == 22.5 and r["water_ml"] == 2300
    assert (r["calories_total"], r["calories_active"], r["calories_resting"]) == (2600, 700, 1900)
    assert r["stress_high_seconds"] == 1800 and r["intensity_minutes"] == 40 and r["resp_avg"] == 14
    assert (r["sleep_start"], r["sleep_end"]) == ("2026-11-19T23:10", "2026-11-20T06:25")
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


# ------------------------------------------------- fase 3: especificação oficial, ausência × zero, validação
def test_missing_is_never_zero():
    from healthmon.parse import parse_official
    v = parse_official({"daily_summary": {"data": [summary_v2(wellness=False, steps=0)]}}, DAY)
    assert "steps" not in v and "calories_total" not in v and "stress_avg" not in v      # pulseira sem uso: ausente, não zero
    v = parse_official({"daily_summary": {"data": [{"calendarDate": DAY, "includesWellnessData": True,
                                                    "wellnessChronology": {"totalDurationInMillis": 5000000},
                                                    "movement": {"steps": {"value": 0}}, "heartRate": {"restingValue": None}}]}}, DAY)
    assert v["steps"] == 0 and "resting_hr" not in v                                     # zero medido é zero; nulo é ausente
    v = parse_official({"sleep": {**SLEEP, "deviceRemCapable": False, "remSleepSeconds": 0}}, DAY)
    assert "rem_seconds" not in v and v["deep_seconds"] == 5000
    assert parse_official({"sleep": {"calendarDate": DAY, "sleepTimeSeconds": None}}, DAY) == {}
    assert "hrv_last_night" not in parse_official({"hrv": {"hrvSummary": {"lastNightAvg": None, "status": "NONE"}}}, DAY)


def test_range_item_of_another_day_is_not_used_and_units_are_checked():
    from healthmon.parse import parse_official
    other = summary_v2(day="2026-11-19", steps=1)
    assert "steps" not in parse_official({"daily_summary": {"data": [other]}}, DAY)
    grams = {"weight": {"dailyWeightSummaries": [{"summaryDate": DAY, "allWeightMetrics": [{"weight": 87500}]}]}}
    assert parse_official(grams, DAY)["weight_kg"] == 87.5                               # API antiga em gramas
    absurd = summary_v2(); absurd["heartRate"]["restingValue"] = 540
    assert "resting_hr" not in parse_official({"daily_summary": {"data": [absurd]}}, DAY)  # erro de unidade/sensor vira ausência


def test_range_backfill_uses_few_requests_and_is_idempotent(cfg, db, src):
    from healthmon.sync import sync_range
    for _ in range(2):
        res = sync_range(cfg, db, src, "2026-11-01", DAY)
    assert res["status"] == "ok" and res["dias"] == 20 and res["dias_com_dados"] == 1
    assert db.q1("SELECT COUNT(*) n FROM daily_metrics")["n"] == 1 and db.daily(DAY)["steps"] == 9100
    assert db.q1("SELECT COUNT(*) n FROM activities")["n"] == 1
    assert len(src.session.gets) <= 12                                                   # 2 execuções × ~6 chamadas para 20 dias
    assert db.daily("2026-11-05") is None                                                # dia sem dado não vira linha de zeros


def test_real_validation_procedure_reports_no_health_values(cfg, db, src, monkeypatch):
    import json as _json
    from datetime import date as _date
    from healthmon import garmin_validation as gv
    monkeypatch.setattr(gv, "today_local", lambda c: _date(2026, 11, 21))
    rep = gv.run(cfg, db, src, history_days=30)
    assert rep["aprovado"] and rep["etapas"]["autenticacao"] == "ok" and rep["etapas"]["renovacao_do_token"] == "ok"
    assert len(src.session.posts) == 2                                                   # token obtido e renovado
    assert rep["principais_ausentes"] == [] and "calories_resting" in rep["indicadores_reconhecidos"]
    assert rep["etapas"]["fuso_horario"]["confere"] is True and rep["etapas"]["fuso_horario"]["hora_de_dormir_plausivel"]
    assert rep["etapas"]["consistencia"]["fc_repouso"] == "igual"
    assert rep["etapas"]["unidades"]["peso"].startswith("quilogramas") and rep["etapas"]["unidades"]["duracoes_de_estresse"] == "milissegundos"
    assert rep["etapas"]["historico"]["dias_com_dados"] == 1
    txt = _json.dumps(rep)
    for secret_or_value in (KEY, "9100", "25200", "87.5", "2600"):
        assert secret_or_value not in txt
    assert db.get_setting("garmin_validado_em")
    c = gv.comparison(db, _date(2026, 11, 20))
    assert next(i for i in c["itens"] if i["chave"] == "steps")["valor"] == "9.100"
    gv.record_check(db, "steps", True, DAY)
    assert gv.comparison(db, _date(2026, 11, 20))["itens"][0]["situacao"] == "confere"


def test_validation_fails_closed_on_bad_key_or_unknown_format(cfg, db, monkeypatch):
    from healthmon import garmin_validation as gv
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", KEY)
    rep = gv.run(cfg, db, PersonalApiSource(cfg, db, session=FakeSession(token_status=401), sleep=lambda x: None))
    assert not rep["aprovado"] and "falhou" in rep["etapas"]["autenticacao"] and not db.get_setting("garmin_validado_em")
    weird = FakeSession(overrides={"/usersummary-service/v2/dailysummaries": [Resp(200, {"formato": "novo"})],
                                   "/sleep-service/sleep/dailySleep": [Resp(200, {"x": 1})], f"/hrv-service/hrv/{DAY}": [Resp(200, {"y": 1})]})
    monkeypatch.setattr(gv, "today_local", lambda c: __import__("datetime").date(2026, 11, 21))
    rep = gv.run(cfg, db, PersonalApiSource(cfg, db, session=weird, sleep=lambda x: None), history_days=5)
    assert not rep["aprovado"] and "Passos" in rep["principais_ausentes"] and not db.get_setting("garmin_validado_em")


def test_garmin_food_log_imported_once_without_duplicating_manual_meals(cfg, db):
    from healthmon.sync import process_day
    db.save_raw(DAY, "food_logs", "ok", {"dailyNutritionContent": {"calories": 2100, "protein": 150.0, "carbs": 220.0, "fat": 60.0}})
    process_day(db, DAY); process_day(db, DAY)
    assert db.q("SELECT kcal, method FROM meals WHERE date=?", (DAY,)) == [{"kcal": 2100.0, "method": "garmin"}]
    db.x("DELETE FROM meals")
    db.insert("meals", {"date": DAY, "description": "minha", "kcal": 500, "method": "manual", "created_at": "x"})
    process_day(db, DAY)
    assert db.q1("SELECT COUNT(*) n FROM meals WHERE date=?", (DAY,))["n"] == 1
