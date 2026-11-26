"""Gerador de dados SIMULADOS para demonstração e testes (marcados como demo no banco).

Usa o mesmo formato JSON que o Garmin Connect devolve, para exercitar o pipeline
completo (raw → parse → análise). Nunca misture com o banco real.
"""
from __future__ import annotations

import math
import random
import zlib
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .db import Database
from .sync import process_day


def _ms_local(d: date, hour: float) -> int:
    dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(hours=hour)
    return int(dt.timestamp() * 1000)


class DemoSource:
    """Fonte sintética com o mesmo contrato de GarminConnectSource.fetch_day."""

    def __init__(self, seed: int = 7, stress_period: tuple[date, date] | None = None):
        self.rng = random.Random(seed)
        self.stress_period = stress_period

    def fetch_day(self, day: str) -> dict[str, tuple[str, Any]]:
        d = date.fromisoformat(day)
        rng = random.Random(zlib.crc32(day.encode()))
        bad = bool(self.stress_period and self.stress_period[0] <= d <= self.stress_period[1])
        wk = d.weekday()
        trains = wk in (0, 2, 4, 5)
        load = rng.uniform(60, 140) if trains else 0
        sleep_h = rng.gauss(7.2 if not bad else 6.1, 0.45) - (0.3 if wk == 4 else 0)
        hrv = rng.gauss(52 if not bad else 41, 4) + 2 * math.sin(d.toordinal() / 9)
        rhr = rng.gauss(54 if not bad else 59, 1.4)
        stress = rng.gauss(28 if not bad else 41, 5)
        score = max(30, min(98, 60 + (sleep_h - 6) * 12 + rng.gauss(0, 5) - (8 if bad else 0)))
        sleep_s = sleep_h * 3600
        deep, rem = sleep_s * rng.uniform(.16, .22), sleep_s * rng.uniform(.18, .25)
        bed = rng.gauss(23.3, 0.5)
        steps = int(rng.gauss(9000 if trains else 6500, 1800))
        acts = []
        if trains:
            acts.append({"activityId": int(d.strftime("%Y%m%d")) * 10 + 1,
                         "activityName": "Corrida" if wk in (0, 4) else "Musculação",
                         "activityType": {"typeKey": "running" if wk in (0, 4) else "strength_training"},
                         "startTimeLocal": f"{day} 07:10:00", "duration": rng.uniform(2400, 3900),
                         "distance": rng.uniform(5000, 9000) if wk in (0, 4) else 0,
                         "calories": rng.uniform(350, 650), "averageHR": rng.uniform(130, 150),
                         "maxHR": rng.uniform(160, 178), "aerobicTrainingEffect": rng.uniform(2.2, 3.8),
                         "anaerobicTrainingEffect": rng.uniform(0.5, 2.0), "activityTrainingLoad": load})
        return {
            "user_summary": ("ok", {
                "totalSteps": steps, "dailyStepGoal": 8000, "totalDistanceMeters": steps * 0.78,
                "totalKilocalories": 2350 + steps * 0.04 + (450 if trains else 0),
                "activeKilocalories": 300 + steps * 0.04 + (450 if trains else 0),
                "restingHeartRate": round(rhr), "minHeartRate": round(rhr) - 4, "maxHeartRate": 165 if trains else 128,
                "averageStressLevel": round(stress), "maxStressLevel": min(99, round(stress + 45)),
                "highStressDuration": max(0, (stress - 20) * 120), "restStressDuration": 30000,
                "bodyBatteryHighestValue": round(max(20, min(100, 55 + score * 0.4 - (10 if bad else 0)))),
                "bodyBatteryLowestValue": round(rng.uniform(8, 25)), "bodyBatteryChargedValue": round(rng.uniform(45, 75)),
                "bodyBatteryDrainedValue": round(rng.uniform(50, 80)), "averageSpo2": round(rng.gauss(96, 0.8)),
                "avgWakingRespirationValue": round(rng.gauss(14.5, 0.5), 1),
                "moderateIntensityMinutes": round(rng.uniform(10, 40)), "vigorousIntensityMinutes": round(load / 6),
                "floorsAscended": round(rng.uniform(2, 12)),
            }),
            "sleep": ("ok", {"dailySleepDTO": {
                "sleepTimeSeconds": round(sleep_s), "deepSleepSeconds": round(deep), "remSleepSeconds": round(rem),
                "lightSleepSeconds": round(sleep_s - deep - rem), "awakeSleepSeconds": round(rng.uniform(600, 2400)),
                "awakeCount": rng.randint(0, 4), "sleepScores": {"overall": {"value": round(score)}},
                "averageRespirationValue": round(rng.gauss(13.8, 0.4), 1), "averageSpO2Value": round(rng.gauss(95, 1)),
                "lowestSpO2Value": round(rng.gauss(89, 2)), "avgSleepStress": round(rng.gauss(16 if not bad else 24, 3)),
                "sleepStartTimestampLocal": _ms_local(d - timedelta(days=1), bed),
                "sleepEndTimestampLocal": _ms_local(d - timedelta(days=1), bed + sleep_h + 0.4)},
                "avgSkinTempDeviationC": round(rng.gauss(0, 0.25), 2)}),
            "hrv": ("ok", {"hrvSummary": {"lastNightAvg": round(hrv), "weeklyAvg": round(hrv + rng.gauss(0, 1.5)),
                                          "status": "UNBALANCED" if bad else "BALANCED",
                                          "baseline": {"balancedLow": 45, "balancedUpper": 60}}}),
            "training_readiness": ("ok", [{"score": round(max(5, min(100, 40 + (hrv - 40) * 2 + (score - 70) * 0.5))),
                                           "level": "MODERATE", "recoveryTime": round(load * 10),
                                           "timestamp": f"{day}T07:00:00"}]),
            "respiration": ("ok", {}), "spo2": ("ok", {}),
            "max_metrics": ("ok", [{"generic": {"vo2MaxPreciseValue": 47.5 + d.toordinal() % 30 / 100}}]),
            "activities": ("ok" if acts else "vazio", acts),
        }


def populate(db: Database, days: int = 120, end: date | None = None, stress_last: int = 6) -> None:
    end = end or date.today()
    src = DemoSource(stress_period=(end - timedelta(days=stress_last - 1), end) if stress_last else None)
    db.set_setting("data_source", "demo")
    for i in range(days - 1, -1, -1):
        d = (end - timedelta(days=i)).isoformat()
        for ep, (status, payload) in src.fetch_day(d).items():
            db.save_raw(d, ep, status, payload)
        process_day(db, d)
