from datetime import date, timedelta

from healthmon import analysis as an
from healthmon.parse import parse_activity, parse_day

from .conftest import END


def test_parse_day_fields_and_sentinels():
    raw = {
        "user_summary": {"restingHeartRate": 55, "totalSteps": 9000, "averageStressLevel": -1,
                         "bodyBatteryHighestValue": 80, "moderateIntensityMinutes": 10,
                         "vigorousIntensityMinutes": 5},
        "sleep": {"dailySleepDTO": {"sleepTimeSeconds": 25200, "deepSleepSeconds": 5000,
                                    "sleepScores": {"overall": {"value": 81}},
                                    "sleepStartTimestampLocal": 1764111600000},
                  "avgSkinTempDeviationC": -0.3},
        "hrv": {"hrvSummary": {"lastNightAvg": 50, "weeklyAvg": 48, "status": "BALANCED",
                               "baseline": {"balancedLow": 44, "balancedUpper": 58}}},
        "training_readiness": [{"score": 70, "level": "HIGH", "recoveryTime": 600, "timestamp": "a"}],
        "activities": [{"activityId": 1, "activityTrainingLoad": 80.5}],
    }
    v = parse_day(raw)
    assert v["resting_hr"] == 55 and v["steps"] == 9000
    assert "stress_avg" not in v  # -1 = sem dado
    assert v["intensity_minutes"] == 20
    assert v["sleep_seconds"] == 25200 and v["sleep_score"] == 81
    assert v["skin_temp_dev"] == -0.3
    assert v["hrv_last_night"] == 50 and v["hrv_status"] == "BALANCED"
    assert v["training_readiness"] == 70 and v["recovery_time_h"] == 10
    assert v["training_load"] == 80.5 and v["activity_count"] == 1
    assert v["sleep_start"].startswith("2025-") or v["sleep_start"].startswith("2026-")


def test_parse_empty_is_empty():
    assert parse_day({}) == {}
    assert parse_day({"sleep": {"dailySleepDTO": {"sleepTimeSeconds": None}}}) == {}


def test_parse_activity():
    a = parse_activity({"activityId": 9, "startTimeLocal": "2026-11-20 07:00:00",
                        "activityType": {"typeKey": "running"}, "duration": 1800})
    assert a["activity_id"] == "9" and a["date"] == "2026-11-20" and a["type"] == "running"


def test_persistent_vs_pontual(demo_db):
    store = an.load_store(demo_db, END, 120)
    hrv = an.assess(store, "hrv_last_night", END)
    rhr = an.assess(store, "resting_hr", END)
    assert hrv.padrao == "persistente" and hrv.leitura == "desfavoravel"
    assert rhr.padrao == "persistente" and rhr.sentido == "acima"
    # Período estável da simulação (sem estresse e fora da oscilação lenta)
    early = an.assess(store, "hrv_last_night", END - timedelta(days=30))
    assert early.padrao != "persistente"


def test_single_day_outlier_is_pontual(db):
    for i in range(40):
        d = (date(2026, 1, 1) + timedelta(days=i)).isoformat()
        db.upsert_daily(d, {"resting_hr": 55 + (i % 3) - 1})
    db.upsert_daily("2026-02-10", {"resting_hr": 66})
    store = an.load_store(db, date(2026, 2, 10), 60)
    a = an.assess(store, "resting_hr", date(2026, 2, 10))
    assert a.padrao == "pontual" and a.leitura == "desfavoravel"


def test_recovery_and_trend(demo_db):
    store = an.load_store(demo_db, END, 120)
    rec = an.recovery(store, END)
    assert rec.status == "atencao" and rec.persistente
    t = an.trend(store, "sleep_score", END, 28)
    assert t.n >= 20 and t.direcao in ("descendo", "estavel")


def test_spearman():
    rho, p = an.spearman(list(range(20)), [x * 2 + 1 for x in range(20)])
    assert rho > 0.99 and p < 0.001
    assert an.spearman([1, 2], [1, 2]) is None


def test_correlations_have_sample_size(demo_db):
    store = an.load_store(demo_db, END, 120)
    cs = an.correlations(store, END, 90)
    assert all("n" in c for c in cs)
    assert any(c["rho"] is not None for c in cs)
