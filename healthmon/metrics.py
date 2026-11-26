"""Catálogo dos indicadores: rótulo, unidade, direção desejável e categoria.

`bom` indica a direção normalmente associada a melhor recuperação *em relação à sua
própria linha de base* ("alto", "baixo" ou "neutro" quando qualquer desvio é só
informativo). Não são limites clínicos.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    bom: str  # "alto" | "baixo" | "neutro"
    categoria: str
    log: bool = False  # usar log na linha de base (ex.: HRV)
    casas: int = 0


METRICS: dict[str, Metric] = {m.key: m for m in [
    # Cardiovascular
    Metric("resting_hr", "FC de repouso", "bpm", "baixo", "cardio"),
    Metric("min_hr", "FC mínima", "bpm", "neutro", "cardio"),
    Metric("max_hr", "FC máxima", "bpm", "neutro", "cardio"),
    Metric("hrv_last_night", "HRV (média da noite)", "ms", "alto", "cardio", log=True),
    Metric("hrv_weekly_avg", "HRV (média 7 dias, Garmin)", "ms", "alto", "cardio", log=True),
    Metric("vo2max", "VO2 máx. estimado", "ml/kg/min", "alto", "cardio", casas=1),
    # Sono
    Metric("sleep_seconds", "Duração do sono", "h", "alto", "sono", casas=1),
    Metric("deep_seconds", "Sono profundo", "h", "neutro", "sono", casas=1),
    Metric("light_seconds", "Sono leve", "h", "neutro", "sono", casas=1),
    Metric("rem_seconds", "Sono REM", "h", "neutro", "sono", casas=1),
    Metric("awake_seconds", "Acordado durante a noite", "min", "baixo", "sono"),
    Metric("awake_count", "Despertares", "", "baixo", "sono"),
    Metric("sleep_score", "Pontuação do sono", "", "alto", "sono"),
    Metric("sleep_resp_avg", "Respiração no sono", "rpm", "neutro", "sono", casas=1),
    Metric("sleep_spo2_avg", "SpO2 média no sono", "%", "neutro", "sono"),
    Metric("sleep_spo2_low", "SpO2 mínima no sono", "%", "neutro", "sono"),
    Metric("sleep_stress_avg", "Estresse durante o sono", "", "baixo", "sono"),
    Metric("skin_temp_dev", "Temperatura da pele (desvio)", "°C", "neutro", "sono", casas=1),
    # Estresse e energia
    Metric("stress_avg", "Estresse médio", "", "baixo", "estresse"),
    Metric("stress_max", "Estresse máximo", "", "baixo", "estresse"),
    Metric("stress_high_seconds", "Tempo em estresse alto", "min", "baixo", "estresse"),
    Metric("bb_max", "Body Battery máximo", "", "alto", "estresse"),
    Metric("bb_min", "Body Battery mínimo", "", "alto", "estresse"),
    Metric("bb_charged", "Body Battery recarregado", "", "alto", "estresse"),
    Metric("bb_drained", "Body Battery gasto", "", "neutro", "estresse"),
    Metric("resp_waking_avg", "Respiração acordado", "rpm", "neutro", "estresse", casas=1),
    Metric("spo2_avg", "SpO2 média do dia", "%", "neutro", "estresse"),
    # Recuperação
    Metric("training_readiness", "Prontidão para treino", "", "alto", "recuperacao"),
    Metric("recovery_time_h", "Tempo de recuperação", "h", "baixo", "recuperacao"),
    # Atividade
    Metric("steps", "Passos", "", "alto", "atividade"),
    Metric("distance_m", "Distância", "km", "neutro", "atividade", casas=1),
    Metric("calories_total", "Calorias totais", "kcal", "neutro", "atividade"),
    Metric("calories_active", "Calorias ativas", "kcal", "neutro", "atividade"),
    Metric("intensity_minutes", "Minutos de intensidade", "min", "alto", "atividade"),
    Metric("floors", "Andares subidos", "", "neutro", "atividade"),
    Metric("training_load", "Carga de treino (soma)", "", "neutro", "atividade"),
    Metric("activity_count", "Atividades registradas", "", "neutro", "atividade"),
]}

# Indicadores principais usados no relatório diário/semanal
CORE = ["hrv_last_night", "resting_hr", "sleep_score", "sleep_seconds", "bb_max",
        "stress_avg", "training_readiness", "steps"]

CATEGORIAS = {
    "cardio": "Cardiovascular",
    "sono": "Sono",
    "estresse": "Estresse e energia",
    "recuperacao": "Recuperação",
    "atividade": "Atividade",
}


def display_value(key: str, value: float | None) -> float | None:
    """Converte da unidade armazenada para a unidade de exibição."""
    if value is None:
        return None
    m = METRICS[key]
    if m.unit == "h" and key.endswith("seconds"):
        return round(value / 3600, 2)
    if m.unit == "min" and key.endswith("seconds"):
        return round(value / 60, 1)
    if key == "distance_m":
        return round(value / 1000, 2)
    return value


def fmt(key: str, value: float | None) -> str:
    if value is None:
        return "—"
    m = METRICS[key]
    v = display_value(key, value)
    if m.unit == "h":
        h = int(v)
        mi = round((v - h) * 60)
        if mi == 60:
            h, mi = h + 1, 0
        return f"{h}h{mi:02d}"
    s = f"{v:.{m.casas}f}".replace(".", ",")
    if m.casas == 0 and abs(v) >= 1000:
        s = f"{v:,.0f}".replace(",", ".")
    return f"{s} {m.unit}".strip()
