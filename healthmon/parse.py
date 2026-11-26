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


def find_dict(obj: Any, keys: tuple[str, ...], depth: int = 0) -> dict | None:
    """Primeiro dicionário (em qualquer nível) que contém alguma das chaves indicadas."""
    if depth > 5:
        return None
    if isinstance(obj, dict):
        if any(k in obj for k in keys):
            return obj
        for v in obj.values():
            r = find_dict(v, keys, depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj[:60]:
            r = find_dict(v, keys, depth + 1)
            if r:
                return r
    return None


def _iso_local(v: Any) -> str | None:
    """Carimbo local da Garmin → 'AAAA-MM-DDTHH:MM'. A API oficial usa texto ISO sem fuso; a antiga, ms."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return _ts_local(v)
    s_ = str(v).replace(" ", "T")
    return s_[:16] if len(s_) >= 16 and s_[4] == "-" else None


def _day_item(payload: Any, day: str | None, date_keys: tuple[str, ...]) -> dict | None:
    """Em listas por intervalo, escolhe o item do dia pedido (nunca o de outro dia)."""
    items = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), list) else payload
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return None
    cands = [i for i in items if isinstance(i, dict)]
    if day:
        for i in cands:
            if any(str(i.get(k, ""))[:10] == day for k in date_keys):
                return i
        return cands[0] if len(cands) == 1 and not any(i.get(k) for i in cands for k in date_keys) else None
    return cands[0] if cands else None


def parse_official(raw: dict[str, Any], day: str | None = None) -> dict[str, Any]:
    """Leitura conforme a especificação OpenAPI oficial da Garmin Connect Personal API (v1.0.0).

    Regras: campo ausente ou nulo vira ausência (None), nunca zero. Se o resumo diário disser que
    não há dados de bem-estar (`includesWellnessData` falso), passos, calorias, estresse e afins do
    dia são tratados como ausentes, mesmo que venham zerados."""
    out: dict[str, Any] = {}
    s = _day_item(raw.get("daily_summary"), day, ("calendarDate",)) or {}
    wellness = s.get("includesWellnessData") is not False
    if s and wellness:
        g = lambda path: num(_get(s, path))  # noqa: E731
        out.update({
            "steps": g("movement.steps.value"), "steps_goal": g("movement.steps.goal"),
            "distance_m": g("movement.steps.distanceInMeters"), "floors": g("movement.floorsAscended.value"),
            "calories_total": g("calories.burnedTotal"), "calories_active": g("calories.burnedActive"),
            "calories_resting": g("calories.burnedResting"),
            "resting_hr": g("heartRate.restingValue"), "min_hr": g("heartRate.minValue"), "max_hr": g("heartRate.maxValue"),
            "stress_avg": g("stress.avgLevel"), "stress_max": g("stress.maxLevel"),
            "bb_max": g("bodyBattery.maxValue"), "bb_min": g("bodyBattery.minValue"),
            "bb_charged": g("bodyBattery.chargedValue"), "bb_drained": g("bodyBattery.drainedValue"),
            "resp_avg": g("respiration.avgValue"), "spo2_avg": g("pulseOx.avgValue"),
            "water_ml": g("hydration.consumedInFractionalMl"),
        })
        for key, path in (("stress_high_seconds", "stress.highStressDurationInMillis"),
                          ("stress_rest_seconds", "stress.restDurationInMillis")):
            v = g(path)
            out[key] = v / 1000 if v is not None else None          # a especificação dá durações em ms
        mod, vig = g("intensityMinutes.moderate"), g("intensityMinutes.vigorous")
        if mod is not None or vig is not None:
            out["intensity_minutes"] = (mod or 0) + 2 * (vig or 0)
        # Sem tempo de uso registrado, zeros de movimento/calorias significam "pulseira não usada"
        if (g("wellnessChronology.totalDurationInMillis") or 0) == 0 and not out.get("steps"):
            for k in ("steps", "distance_m", "floors", "calories_active", "intensity_minutes"):
                out.pop(k, None)

    sl = _day_item(raw.get("sleep"), day, ("calendarDate",)) or {}
    if isinstance(sl.get("dailySleepDTO"), dict):                    # formato antigo embrulhado
        sl = sl["dailySleepDTO"]
    if num(sl.get("sleepTimeSeconds")):
        out.update({
            "sleep_seconds": num(sl.get("sleepTimeSeconds")), "deep_seconds": num(sl.get("deepSleepSeconds")),
            "light_seconds": num(sl.get("lightSleepSeconds")), "rem_seconds": num(sl.get("remSleepSeconds")),
            "awake_seconds": num(sl.get("awakeSleepSeconds")), "awake_count": num(sl.get("awakeCount")),
            "sleep_score": num(pick(sl, "sleepScores.overall.value")),
            "sleep_resp_avg": num(sl.get("averageRespirationValue")),
            "sleep_spo2_avg": num(sl.get("averageSpO2Value")), "sleep_spo2_low": num(sl.get("lowestSpO2Value")),
            "sleep_stress_avg": num(sl.get("avgSleepStress")),
            "sleep_start": _iso_local(sl.get("sleepStartTimestampLocal")),
            "sleep_end": _iso_local(sl.get("sleepEndTimestampLocal")),
        })
        if sl.get("deviceRemCapable") is False:
            out["rem_seconds"] = None                                 # aparelho não mede REM: ausência, não zero

    hrv = _day_item(raw.get("hrv"), day, ("calendarDate",)) or {}
    hs = hrv.get("hrvSummary") if isinstance(hrv.get("hrvSummary"), dict) else hrv
    if hs:
        out["hrv_last_night"] = num(hs.get("lastNightAvg"))
        out["hrv_weekly_avg"] = num(hs.get("weeklyAvg"))
        out["hrv_status"] = hs.get("status")
        out["hrv_baseline_low"] = num(pick(hs, "baseline.balancedLow"))
        out["hrv_baseline_high"] = num(pick(hs, "baseline.balancedUpper"))

    # Endpoints dedicados (usados na validação e como complemento quando o resumo não traz o campo)
    hr = raw.get("heart_rate") if isinstance(raw.get("heart_rate"), dict) else {}
    for k, f in (("resting_hr", "restingHeartRate"), ("min_hr", "minHeartRate"), ("max_hr", "maxHeartRate")):
        if out.get(k) is None:
            out[k] = num(hr.get(f))
    bb = _day_item(raw.get("body_battery"), day, ("date",)) or {}
    if out.get("bb_charged") is None:
        out["bb_charged"], out["bb_drained"] = num(bb.get("charged")), num(bb.get("drained"))
    if out.get("bb_max") is None and bb.get("bodyBatteryValuesArray"):
        idx = next((d_.get("bodyBatteryValueDescriptorIndex") for d_ in bb.get("bodyBatteryValueDescriptorDTOList") or []
                    if d_.get("bodyBatteryValueDescriptorKey") == "bodyBatteryLevel"), None)
        vals = [num(r[idx]) for r in bb["bodyBatteryValuesArray"]
                if idx is not None and isinstance(r, list) and len(r) > idx and num(r[idx]) is not None]
        if vals:
            out["bb_max"], out["bb_min"] = max(vals), min(vals)
    st = (_day_item(raw.get("stress"), day, ("calendarDate",)) or {}).get("values") or {}
    if out.get("stress_avg") is None:
        out["stress_avg"] = num(st.get("overallStressLevel"))
    if out.get("stress_high_seconds") is None:
        out["stress_high_seconds"] = num(st.get("highStressDuration"))          # este endpoint usa segundos
    sp = raw.get("spo2") if isinstance(raw.get("spo2"), dict) else {}
    if out.get("spo2_avg") is None:
        out["spo2_avg"] = num(sp.get("averageSpO2"))
    if out.get("sleep_spo2_avg") is None:
        out["sleep_spo2_avg"] = num(sp.get("avgSleepSpO2"))

    mm = _day_item(raw.get("max_metrics"), None, ()) or {}
    gen = mm.get("generic") if isinstance(mm.get("generic"), dict) else {}
    out["vo2max"] = num(gen.get("vo2MaxPreciseValue")) or num(gen.get("vo2MaxValue"))

    ws = raw.get("weight") if isinstance(raw.get("weight"), dict) else {}
    for summ in ws.get("dailyWeightSummaries") or []:
        if day and str(summ.get("summaryDate", ""))[:10] != day:
            continue
        ms = [m for m in summ.get("allWeightMetrics") or [] if isinstance(m, dict) and num(m.get("weight"))]
        if ms:
            m = ms[-1]
            wv = num(m["weight"])
            out["weight_kg"] = round(wv / 1000, 2) if wv > 500 else wv   # especificação: kg; API antiga: gramas
            out["body_fat_pct"] = num(m.get("bodyFat"))
            mu = num(m.get("muscleMass"))
            out["muscle_kg"] = (round(mu / 1000, 2) if mu and mu > 500 else mu)

    acts = raw.get("activities")
    if isinstance(acts, list):
        loads = [num(a.get("activityTrainingLoad")) for a in acts if isinstance(a, dict)]
        out["activity_count"] = float(len(acts))
        known = [x for x in loads if x is not None]
        out["training_load"] = round(sum(known), 1) if known else (0.0 if not acts else None)
    elif "activities" in raw and acts is None:
        out["activity_count"], out["training_load"] = 0.0, 0.0       # consulta respondeu "nenhuma atividade"

    # Faixas plausíveis: valor fora da faixa é erro de leitura/sensor e vira ausência
    for k, (lo, hi) in PLAUSIBLE.items():
        if out.get(k) is not None and not lo <= out[k] <= hi:
            out[k] = None
    return {k: v for k, v in out.items() if v is not None}


PLAUSIBLE = {"resting_hr": (25, 130), "min_hr": (20, 150), "max_hr": (40, 240), "hrv_last_night": (3, 300),
             "hrv_weekly_avg": (3, 300), "sleep_seconds": (600, 64800), "sleep_score": (1, 100), "stress_avg": (1, 100),
             "stress_max": (1, 100), "bb_max": (1, 100), "bb_min": (1, 100), "spo2_avg": (60, 100),
             "sleep_spo2_avg": (60, 100), "sleep_spo2_low": (50, 100), "resp_avg": (4, 40), "sleep_resp_avg": (4, 40),
             "steps": (0, 150000), "calories_total": (500, 15000), "calories_resting": (500, 6000),
             "calories_active": (0, 12000), "weight_kg": (30, 300), "body_fat_pct": (3, 60), "vo2max": (10, 100)}


def nutrition_summary(raw: dict[str, Any]) -> dict[str, float] | None:
    """Totais do dia registrados no Garmin Connect (se você usa o registro alimentar de lá)."""
    n = (raw.get("food_logs") or {}).get("dailyNutritionContent") if isinstance(raw.get("food_logs"), dict) else None
    if not isinstance(n, dict) or not num(n.get("calories")):
        return None
    return {"kcal": num(n.get("calories")), "protein_g": num(n.get("protein")), "carb_g": num(n.get("carbs")),
            "fat_g": num(n.get("fat"))}


def parse_day(raw: dict[str, Any], day: str | None = None) -> dict[str, Any]:
    if "daily_summary" in raw or "heart_rate" in raw:        # fonte oficial
        return parse_official(raw, day)
    out: dict[str, Any] = {}
    official = False
    s = raw.get("user_summary") or find_dict(
        raw.get("daily_summary"), ("totalSteps", "totalKilocalories", "restingHeartRate")) or {}
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
    if not isinstance(sl, dict):
        sl = {"dailySleepDTO": find_dict(sl, ("sleepTimeSeconds",)) or {}}
    dto = sl.get("dailySleepDTO") or (sl if "sleepTimeSeconds" in sl else {})
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

    hrv = find_dict(raw.get("hrv"), ("lastNightAvg", "weeklyAvg")) or {}
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

    mm = find_dict(raw.get("max_metrics"), ("vo2MaxPreciseValue", "vo2MaxValue"))
    if mm:
        out["vo2max"] = num(pick(mm, "vo2MaxPreciseValue", "vo2MaxValue"))

    acts = raw.get("activities")
    if isinstance(acts, dict):   # alguns endpoints embrulham a lista
        acts = next((v for v in acts.values() if isinstance(v, list)), [])
    if "activities" in raw and acts is None:
        acts = []
    if isinstance(acts, list):
        loads = [num(a.get("activityTrainingLoad")) for a in acts if isinstance(a, dict)]
        out["activity_count"] = float(len(acts))
        known = [x for x in loads if x is not None]
        out["training_load"] = round(sum(known), 1) if known else (0.0 if not acts else None)

    if official:
        for k, v in parse_official_extras(raw).items():
            if out.get(k) is None:
                out[k] = v
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
