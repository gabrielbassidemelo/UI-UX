"""Período de calibração individual: o que o sistema pode recomendar em cada estágio.

O estágio depende de QUANTOS dias com dados válidos existem (histórico importado conta) e da
cobertura recente, não apenas do calendário. Os limites de 7, 28 e 90 dias são parâmetros
operacionais iniciais, não garantias de validade científica.

| Estágio        | Dias válidos | O que o sistema faz |
|----------------|--------------|---------------------|
| observacao     | < 7          | Coleta, checagem de consistência, relatórios descritivos, orientações gerais de baixo risco |
| linha_de_base  | 7 a 27       | Constrói a base; padrões preliminares com confiança explícita; ajustes só conservadores (reduzir, nunca intensificar) |
| personalizado  | 28 a 89      | Ajuste de treino, avaliação de recuperação, tendências; calibração metabólica se houver peso e alimentação suficientes |
| longitudinal   | ≥ 90         | Comparações longas, avaliação das estratégias e da eficácia das recomendações |
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .db import Database

STAGES = [("observacao", 0), ("linha_de_base", 7), ("personalizado", 28), ("longitudinal", 90)]
LABEL = {"observacao": "Observação (primeiros 7 dias de dados)", "linha_de_base": "Construção da linha de base (8 a 28 dias)",
         "personalizado": "Recomendações personalizadas (após 28 dias)", "longitudinal": "Análise longitudinal (após 90 dias)"}
CORE = ("sleep_seconds", "resting_hr", "hrv_last_night", "steps")


def valid_days(db: Database, day: date, window: int = 120) -> int:
    """Dia válido = pelo menos 2 dos 4 indicadores centrais presentes."""
    rows = db.q(f"SELECT {', '.join(CORE)} FROM daily_metrics WHERE date BETWEEN ? AND ?",
                ((day - timedelta(days=window - 1)).isoformat(), day.isoformat()))
    return sum(1 for r in rows if sum(r[k] is not None for k in CORE) >= 2)


def stage(db: Database, day: date) -> dict[str, Any]:
    n = valid_days(db, day)
    recent = valid_days(db, day, 14)
    name = [s for s, need in STAGES if n >= need][-1]
    note = None
    if name in ("personalizado", "longitudinal") and recent < 8:
        name, note = "linha_de_base", ("Há histórico, mas menos de 8 dias válidos nas últimas duas semanas: as recomendações "
                                      "voltam ao modo conservador até a coleta se regularizar.")
    idx = [s for s, _ in STAGES].index(name)
    nxt = STAGES[idx + 1] if idx + 1 < len(STAGES) else None
    return {
        "estagio": name, "rotulo": LABEL[name], "dias_validos": n, "dias_validos_14d": recent, "observacao": note,
        "proximo": {"estagio": nxt[0], "faltam_dias": max(0, nxt[1] - n)} if nxt else None,
        "permite": {
            "relatorios_descritivos": True,
            "orientacoes_gerais": True,
            "alertas_de_tendencia": idx >= 1,
            "reduzir_treino_por_recuperacao": idx >= 1,     # direção conservadora
            "progressao_de_carga": idx >= 2,
            "calibracao_metabolica": idx >= 2,
            "analise_de_tendencias": idx >= 2,
            "comparacoes_longitudinais": idx >= 3,
            "avaliacao_das_recomendacoes": idx >= 3,
        },
        "nota": "Estágios são parâmetros operacionais iniciais; dependem de quantidade, qualidade e representatividade dos dados.",
    }
