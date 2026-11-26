"""Validação da integração real com a Garmin Connect Personal API.

Roda no servidor, com a sua chave. O relatório gerado contém apenas estados, nomes de campos,
contagens e "igual/diferente": NENHUM valor de saúde. Por isso pode ser compartilhado para ajuste
do leitor. A comparação de valores com o aplicativo Garmin Connect é feita por você, no painel
(Configurações → Conferência com o app) ou no terminal do servidor (`healthmon conferir-garmin`).

Campos esperados abaixo foram extraídos da especificação OpenAPI oficial (v1.0.0, lida em 26/11/2026).
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from .config import Config, write_private_file
from .db import Database, utcnow
from .garmin_official import ENDPOINTS, EXTRA_ENDPOINTS, NOT_IN_PERSONAL_API, PersonalApiSource, key_expiry_status, shape
from .metrics import METRICS, fmt
from .parse import PLAUSIBLE, _day_item, _get, num, parse_official
from .sync import process_day, today_local

EXPECTED = {   # endpoint -> campos que a especificação oficial descreve e que o sistema usa
    "daily_summary": ["calendarDate", "includesWellnessData", "movement.steps.value", "calories.burnedTotal",
                      "calories.burnedActive", "calories.burnedResting", "heartRate.restingValue", "stress.avgLevel",
                      "bodyBattery.maxValue", "bodyBattery.minValue", "respiration.avgValue", "pulseOx.avgValue"],
    "sleep": ["calendarDate", "sleepTimeSeconds", "deepSleepSeconds", "lightSleepSeconds", "remSleepSeconds",
              "awakeSleepSeconds", "sleepStartTimestampLocal", "sleepEndTimestampLocal", "sleepScores.overall.value",
              "avgSleepStress", "averageRespirationValue", "averageSpO2Value"],
    "hrv": ["hrvSummary.lastNightAvg", "hrvSummary.weeklyAvg", "hrvSummary.status", "hrvSummary.baseline.balancedLow"],
    "heart_rate": ["restingHeartRate", "minHeartRate", "maxHeartRate"],
    "body_battery": ["date", "charged", "drained", "bodyBatteryValuesArray"],
    "stress": ["calendarDate", "values.overallStressLevel"],
    "spo2": ["averageSpO2", "lowestSpO2", "avgSleepSpO2"],
    "max_metrics": ["generic.vo2MaxPreciseValue"],
    "weight": ["dailyWeightSummaries"],
    "activities": ["activityId", "startTimeLocal", "duration", "calories", "averageHR"],
}
CORE = ["sleep_seconds", "sleep_score", "hrv_last_night", "resting_hr", "steps", "calories_total", "calories_active",
        "calories_resting", "stress_avg", "bb_max"]
COMPARE = ["steps", "resting_hr", "sleep_seconds", "sleep_score", "hrv_last_night", "stress_avg", "bb_max", "bb_min",
           "calories_total", "calories_active", "calories_resting"]


def _first(payload: Any, day: str) -> dict:
    if isinstance(payload, dict) and "data" not in payload:
        return payload
    return _day_item(payload, day, ("calendarDate", "date")) or (payload[0] if isinstance(payload, list) and payload else {})


def run(cfg: Config, db: Database, source: PersonalApiSource | None = None, history_days: int = 365) -> dict[str, Any]:
    src = source or PersonalApiSource(cfg, db)
    day = (today_local(cfg) - timedelta(days=1)).isoformat()
    rep: dict[str, Any] = {"executado_em": utcnow(), "dia_testado": day, "etapas": {}, "aprovado": False}
    st = rep["etapas"]
    try:
        src._token()
        st["autenticacao"] = "ok"
        src._token(force=True)
        st["renovacao_do_token"] = "ok"
    except Exception as e:  # noqa: BLE001 — só o tipo do erro; a mensagem pode conter detalhes da requisição
        st["autenticacao"] = f"falhou ({type(e).__name__})"
        return _save(cfg, rep)
    try:
        fetched = src.fetch_day(day, extras=True)
    except Exception as e:  # noqa: BLE001
        st["coleta"] = f"falhou ({type(e).__name__})"
        return _save(cfg, rep)
    eps = {}
    for name in {**ENDPOINTS, **EXTRA_ENDPOINTS}:
        status, payload = fetched[name]
        info: dict[str, Any] = {"status": status}
        if status == "ok":
            item = _first(payload, day)
            exp = EXPECTED.get(name, [])
            info["campos_esperados_ausentes"] = [f for f in exp if _get(item, f) is None]
            info["estrutura"] = shape(payload, max_depth=3)
            db.save_raw(day, name, status, payload)
        eps[name] = info
    st["endpoints"] = eps
    raw = {k: v for k, (s_, v) in fetched.items() if s_ == "ok"}
    values = parse_official(raw, day)
    process_day(db, day)
    rep["indicadores_reconhecidos"] = sorted(k for k in values if k in METRICS)
    rep["principais_ausentes"] = [METRICS[k].label for k in CORE if k not in values]
    rep["nao_existem_na_api"] = NOT_IN_PERSONAL_API

    # Unidades: quantos valores brutos caíram fora da faixa plausível (sinal de unidade trocada)
    summ = _first(raw.get("daily_summary"), day)
    units = {}
    w = raw.get("weight") or {}
    wv = [num(m.get("weight")) for s_ in w.get("dailyWeightSummaries") or [] for m in s_.get("allWeightMetrics") or []]
    if wv and wv[0]:
        units["peso"] = "quilogramas (como na especificação)" if wv[0] < 500 else "gramas (convertido para kg)"
    hs = num(_get(summ, "stress.highStressDurationInMillis"))
    tot = num(_get(summ, "stress.totalDurationInMillis"))
    if hs is not None and tot:
        units["duracoes_de_estresse"] = "milissegundos" if tot > 100000 else "INESPERADO: parece segundos"
    sl = raw.get("sleep") or {}
    ts = sl.get("sleepStartTimestampLocal")
    units["horario_do_sono"] = ("texto ISO sem fuso" if isinstance(ts, str) else "milissegundos" if isinstance(ts, (int, float))
                                else "ausente")
    units["fora_da_faixa_plausivel"] = [k for k in PLAUSIBLE if k not in values and _raw_has(raw, summ, k)]
    st["unidades"] = units

    # Fuso horário: o dia da Garmin é o dia local; a hora de dormir deve cair à noite/madrugada
    tz: dict[str, Any] = {"configurado": str(cfg.tz)}
    try:
        prof_ = src.connect().get_user_profile_settings() or {}
        tz["conta_garmin"] = prof_.get("timeZone")
        tz["sistema_de_medidas"] = prof_.get("measurementSystem")
        tz["confere"] = (prof_.get("timeZone") == str(cfg.tz)) if prof_.get("timeZone") else None
    except Exception as e:  # noqa: BLE001
        tz["conta_garmin"] = f"não obtido ({type(e).__name__})"
    start = values.get("sleep_start")
    if start:
        hh = int(start[11:13])
        tz["hora_de_dormir_plausivel"] = hh >= 18 or hh <= 6
    tz["data_do_resumo_confere"] = (str(summ.get("calendarDate", ""))[:10] == day) if summ else None
    st["fuso_horario"] = tz

    # Consistência entre o resumo diário e os endpoints dedicados (sem mostrar valores)
    hr = raw.get("heart_rate") or {}
    bb = _first(raw.get("body_battery"), day)
    stv = (_first(raw.get("stress"), day) or {}).get("values") or {}

    def same(a: Any, b: Any) -> str:
        a, b = num(a), num(b)
        return "sem dado em um dos lados" if a is None or b is None else "igual" if abs(a - b) <= 1 else "diferente"
    st["consistencia"] = {"fc_repouso": same(_get(summ, "heartRate.restingValue"), hr.get("restingHeartRate")),
                          "body_battery_recarregado": same(_get(summ, "bodyBattery.chargedValue"), bb.get("charged")),
                          "estresse_medio": same(_get(summ, "stress.avgLevel"), stv.get("overallStressLevel"))}

    # Histórico disponível: uma chamada de resumo diário cobre até 1 ano
    try:
        d1 = today_local(cfg) - timedelta(days=1)
        d0 = d1 - timedelta(days=history_days - 1)
        hist = src.connect().get_daily_summaries(d0.isoformat(), d1.isoformat()) or {}
        items = hist.get("data") if isinstance(hist, dict) else hist
        with_data = sorted(str(i.get("calendarDate"))[:10] for i in items or []
                           if isinstance(i, dict) and i.get("includesWellnessData") is not False)
        st["historico"] = {"dias_consultados": history_days, "dias_com_dados": len(with_data),
                           "primeiro_dia": with_data[0] if with_data else None}
    except Exception as e:  # noqa: BLE001
        st["historico"] = f"não obtido ({type(e).__name__})"

    rep["validade_da_chave"] = key_expiry_status(cfg)
    bad = [n for n, i in eps.items() if i["status"] == "erro"]
    rep["aprovado"] = not bad and len(rep["principais_ausentes"]) <= 3 and st.get("renovacao_do_token") == "ok"
    rep["motivo"] = ("ok" if rep["aprovado"] else
                     f"endpoints com erro: {bad}; indicadores principais ausentes: {rep['principais_ausentes']}")
    if rep["aprovado"]:
        db.set_setting("garmin_validado_em", utcnow())
        db.set_setting("data_source", "garmin")
    return _save(cfg, rep)


def _raw_has(raw: dict, summ: dict, key: str) -> bool:
    paths = {"resting_hr": "heartRate.restingValue", "steps": "movement.steps.value", "calories_total": "calories.burnedTotal",
             "calories_resting": "calories.burnedResting", "stress_avg": "stress.avgLevel", "bb_max": "bodyBattery.maxValue"}
    return key in paths and _get(summ, paths[key]) is not None


def _save(cfg: Config, rep: dict[str, Any]) -> dict[str, Any]:
    write_private_file(cfg.home / "validacao_garmin.json", json.dumps(rep, ensure_ascii=False, indent=1))
    return rep


def comparison(db: Database, day: date) -> dict[str, Any]:
    """Valores lidos pelo sistema, para VOCÊ comparar com o que o app Garmin Connect mostra no mesmo dia."""
    row = db.daily(day.isoformat()) or {}
    checked = db.get_json("conferencia_app", {})
    return {"dia": day.isoformat(),
            "itens": [{"chave": k, "rotulo": METRICS[k].label, "valor": fmt(k, row.get(k)) if row.get(k) is not None else None,
                       "situacao": checked.get(k, {}).get("situacao")} for k in COMPARE],
            "onde_ver_no_app": "Garmin Connect → Mais → Estatísticas de saúde, na mesma data. Calorias: Resumo do dia.",
            "nota": "Ausente aqui e presente no app indica falha de leitura; ausente nos dois indica que a pulseira não fornece."}


def record_check(db: Database, key: str, ok: bool, day: str) -> None:
    if key not in COMPARE:
        raise ValueError("indicador inválido")
    c = db.get_json("conferencia_app", {})
    c[key] = {"situacao": "confere" if ok else "diverge", "dia": day, "em": utcnow()}
    db.set_json("conferencia_app", c)
