"""Normalização das respostas do Garmin Connect para a tabela daily_metrics.

O formato das respostas não é documentado oficialmente; por isso cada campo é lido
de forma defensiva (vários caminhos possíveis) e o JSON bruto é sempre guardado em
raw_payloads, permitindo reprocessar (`healthmon reprocessar`) se o formato mudar.
Nada é inventado: campo ausente vira NULL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

NONNEG = {
    "stress_avg", "stress_max", "bb_max", "bb_min", "bb_charged", "bb_drained", "resting_hr",
    "min_hr", "max_hr", "sleep_score", "spo2_avg", "sleep_spo2_avg", "sleep_spo2_low",
    "sleep_stress_avg", "resp_waking_avg", "sleep_resp_avg", "hrv_last_night", "hrv_weekly_avg",
    "training_readiness", "steps", "awake_count",
}


def _get(d: Any, path: str) -> Any:
    cur = d
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def pick(d: Any, *paths: str) -> Any:
    for p in paths:
        v = _get(d, p)
        if v is not None:
            return v
    return None


def num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def find_keys(d: Any, predicate, depth: int = 0) -> list[tuple[str, Any]]:
    """Busca recursiva por chaves (usada para campos de nome variável, ex. temperatura da pele)."""
    out: list[tuple[str, Any]] = []
    if depth > 6:
        return out
    if isinstance(d, dict):
        for k, v in d.items():
            if predicate(k) and not isinstance(v, (dict, list)):
                out.append((k, v))
            out.extend(find_keys(v, predicate, depth + 1))
    elif isinstance(d, list):
        for v in d[:50]:
            out.extend(find_keys(v, predicate, depth + 1))
    return out


def _ts_local(v: Any) -> str | None:
    """Timestamps *Local do Garmin vêm em ms 'como se fossem UTC' representando hora local."""
    f = num(v)
    if f is None:
        return None
    return datetime.fromtimestamp(f / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M")


def parse_day(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    s = raw.get("user_summary") or {}
    out.update({
        "resting_hr": num(pick(s, "restingHeartRate")),
        "min_hr": num(pick(s, "minHeartRate")),
        "max_hr": num(pick(s, "maxHeartRate")),
        "steps": num(pick(s, "totalSteps")),
        "steps_goal": num(pick(s, "dailyStepGoal")),
        "distance_m": num(pick(s, "totalDistanceMeters")),
        "calories_total": num(pick(s, "totalKilocalories")),
        "calories_active": num(pick(s, "activeKilocalories")),
        "floors": num(pick(s, "floorsAscended")),
        "stress_avg": num(pick(s, "averageStressLevel")),
        "stress_max": num(pick(s, "maxStressLevel")),
        "stress_high_seconds": num(pick(s, "highStressDuration")),
        "stress_rest_seconds": num(pick(s, "restStressDuration")),
        "bb_max": num(pick(s, "bodyBatteryHighestValue")),
        "bb_min": num(pick(s, "bodyBatteryLowestValue")),
        "bb_charged": num(pick(s, "bodyBatteryChargedValue")),
        "bb_drained": num(pick(s, "bodyBatteryDrainedValue")),
        "spo2_avg": num(pick(s, "averageSpo2")),
        "resp_waking_avg": num(pick(s, "avgWakingRespirationValue")),
    })
    mod, vig = num(pick(s, "moderateIntensityMinutes")), num(pick(s, "vigorousIntensityMinutes"))
    if mod is not None or vig is not None:
        # Convenção da OMS/Garmin: minutos vigorosos contam em dobro
        out["intensity_minutes"] = (mod or 0) + 2 * (vig or 0)

    sl = raw.get("sleep") or {}
    dto = sl.get("dailySleepDTO") or {}
    if dto and num(dto.get("sleepTimeSeconds")):
        out.update({
            "sleep_seconds": num(dto.get("sleepTimeSeconds")),
            "deep_seconds": num(dto.get("deepSleepSeconds")),
            "light_seconds": num(dto.get("lightSleepSeconds")),
            "rem_seconds": num(dto.get("remSleepSeconds")),
            "awake_seconds": num(dto.get("awakeSleepSeconds")),
            "awake_count": num(dto.get("awakeCount")),
            "sleep_score": num(pick(dto, "sleepScores.overall.value")),
            "sleep_resp_avg": num(dto.get("averageRespirationValue")),
            "sleep_spo2_avg": num(dto.get("averageSpO2Value")),
            "sleep_spo2_low": num(dto.get("lowestSpO2Value")),
            "sleep_stress_avg": num(dto.get("avgSleepStress")),
            "sleep_start": _ts_local(dto.get("sleepStartTimestampLocal")),
            "sleep_end": _ts_local(dto.get("sleepEndTimestampLocal")),
        })
        skin = [num(v) for k, v in find_keys(
            sl, lambda k: "skintemp" in k.lower() and ("dev" in k.lower() or "avg" in k.lower()))]
        skin = [v for v in skin if v is not None and -10 < v < 10]
        if skin:
            out["skin_temp_dev"] = skin[0]
        if out.get("resting_hr") is None:
            out["resting_hr"] = num(sl.get("restingHeartRate"))
        if num(sl.get("avgOvernightHrv")):
            out["hrv_last_night"] = num(sl.get("avgOvernightHrv"))

    hrv = (raw.get("hrv") or {}).get("hrvSummary") or {}
    if hrv:
        out["hrv_last_night"] = num(hrv.get("lastNightAvg")) or out.get("hrv_last_night")
        out["hrv_weekly_avg"] = num(hrv.get("weeklyAvg"))
        out["hrv_status"] = hrv.get("status")
        out["hrv_baseline_low"] = num(pick(hrv, "baseline.balancedLow"))
        out["hrv_baseline_high"] = num(pick(hrv, "baseline.balancedUpper"))

    tr = raw.get("training_readiness")
    if isinstance(tr, dict):
        tr = [tr]
    if isinstance(tr, list) and tr:
        entries = [e for e in tr if isinstance(e, dict) and num(e.get("score")) is not None]
        if entries:
            e = sorted(entries, key=lambda x: str(x.get("timestamp") or x.get("timestampLocal") or ""))[-1]
            out["training_readiness"] = num(e.get("score"))
            out["readiness_level"] = e.get("level")
            rt = num(e.get("recoveryTime"))
            if rt is not None:
                out["recovery_time_h"] = round(rt / 60, 1)  # campo em minutos

    resp = raw.get("respiration") or {}
    if out.get("resp_waking_avg") is None:
        out["resp_waking_avg"] = num(resp.get("avgWakingRespirationValue"))
    if out.get("sleep_resp_avg") is None:
        out["sleep_resp_avg"] = num(resp.get("avgSleepRespirationValue"))

    spo2 = raw.get("spo2") or {}
    if out.get("spo2_avg") is None:
        out["spo2_avg"] = num(spo2.get("averageSpO2"))
    if out.get("sleep_spo2_avg") is None:
        out["sleep_spo2_avg"] = num(spo2.get("avgSleepSpO2"))

    mm = raw.get("max_metrics")
    if isinstance(mm, list) and mm:
        mm = mm[0]
    if isinstance(mm, dict):
        out["vo2max"] = num(pick(mm, "generic.vo2MaxPreciseValue", "generic.vo2MaxValue"))

    acts = raw.get("activities")
    if "activities" in raw and acts is None:
        acts = []
    if isinstance(acts, list):
        loads = [num(a.get("activityTrainingLoad")) for a in acts if isinstance(a, dict)]
        out["activity_count"] = float(len(acts))
        known = [x for x in loads if x is not None]
        out["training_load"] = round(sum(known), 1) if known else (0.0 if not acts else None)

    for k in NONNEG:
        if out.get(k) is not None and out[k] < 0:
            out[k] = None  # Garmin usa -1/-2 como "sem dado"
    return {k: v for k, v in out.items() if v is not None}


def parse_activity(a: dict[str, Any]) -> dict[str, Any] | None:
    aid = a.get("activityId")
    if aid is None:
        return None
    start = a.get("startTimeLocal") or ""
    return {
        "activity_id": str(aid),
        "date": start[:10],
        "start_local": start,
        "type": pick(a, "activityType.typeKey"),
        "name": a.get("activityName"),
        "duration_s": num(a.get("duration")),
        "distance_m": num(a.get("distance")),
        "calories": num(a.get("calories")),
        "avg_hr": num(a.get("averageHR")),
        "max_hr": num(a.get("maxHR")),
        "aerobic_te": num(a.get("aerobicTrainingEffect")),
        "anaerobic_te": num(a.get("anaerobicTrainingEffect")),
        "training_load": num(a.get("activityTrainingLoad")),
    }
