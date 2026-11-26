"""Perfil individual: dados que o Garmin não fornece e que você informa (dashboard ou CLI)."""
from __future__ import annotations

from datetime import date
from typing import Any

from .config import Config
from .db import Database

GOALS = {
    "emagrecimento": "Emagrecimento (redução de gordura)",
    "hipertrofia": "Hipertrofia (ganho de massa muscular)",
    "forca": "Ganho de força",
    "recomposicao": "Recomposição corporal",
    "manutencao": "Manutenção de peso",
    "desempenho": "Desempenho esportivo",
    "condicionamento": "Condicionamento cardiorrespiratório",
    "saude": "Saúde geral e longevidade",
}
EXPERIENCE = {"iniciante": "Iniciante (< 1 ano de treino regular)",
              "intermediario": "Intermediário (1–3 anos)", "avancado": "Avançado (> 3 anos)"}
EQUIPMENT = {"academia": "Academia completa", "halteres": "Casa com halteres/elásticos",
             "peso_corporal": "Somente peso corporal"}

# campo -> (tipo, validação, descrição)
FIELDS: dict[str, tuple[type, Any, str]] = {
    "sexo": (str, {"masculino", "feminino"}, "Sexo biológico (usado nas equações metabólicas)"),
    "nascimento": (str, "date", "Data de nascimento (AAAA-MM-DD)"),
    "altura_cm": (float, (120, 230), "Altura em cm"),
    "objetivo": (str, set(GOALS), "Objetivo principal"),
    "experiencia": (str, set(EXPERIENCE), "Experiência de treino"),
    "dias_treino": (int, (1, 7), "Dias disponíveis por semana"),
    "minutos_sessao": (int, (20, 150), "Minutos por sessão"),
    "equipamento": (str, set(EQUIPMENT), "Equipamento disponível"),
    "limitacoes": (list, None, "Lesões/limitações (ex.: ombro, joelho, lombar)"),
    "condicoes_saude": (list, None, "Condições de saúde diagnosticadas"),
    "medicamentos": (list, None, "Medicamentos em uso"),
    "liberacao_medica": (bool, None, "Tem liberação médica para exercício intenso?"),
    "gestante_ou_lactante": (bool, None, "Está grávida, amamentando ou teve parto nos últimos 12 meses?"),
    "transtorno_alimentar": (bool, None, "Tem ou já teve transtorno alimentar?"),
    "doenca_cardiovascular": (bool, None, "Doença cardiovascular ou marca-passo?"),
    "doenca_renal": (bool, None, "Doença nos rins?"),
    "doenca_hepatica": (bool, None, "Doença no fígado?"),
    "diabetes": (bool, None, "Diabetes?"),
    "usa_insulina_ou_hipoglicemiante": (bool, None, "Usa insulina ou hipoglicemiante?"),
    "usa_medicamento_fc": (bool, None, "Usa remédio que altera os batimentos?"),
    "usa_anticoagulante": (bool, None, "Usa anticoagulante ou antiagregante?"),
    "usa_psiquiatrico_ou_estimulante": (bool, None, "Usa antidepressivo, lítio ou estimulante?"),
    "usa_levotiroxina": (bool, None, "Usa levotiroxina?"),
    "usa_diuretico": (bool, None, "Usa diurético?"),
    "cirurgia_recente": (bool, None, "Cirurgia nos últimos 6 meses ou reabilitação?"),
    "alergia_leite": (bool, None, "Alergia a leite ou intolerância à lactose?"),
    "alergia_peixe": (bool, None, "Alergia a peixe ou frutos do mar?"),
    "restricoes_alimentares": (list, None, "Restrições/preferências alimentares"),
    "refeicoes_dia": (int, (2, 8), "Refeições por dia"),
    "fc_max_medida": (float, (120, 230), "FC máxima medida em teste (opcional)"),
    "cafeina_habitual_mg": (float, (0, 1500), "Cafeína habitual (mg/dia)"),
    "orcamento_alimentar_dia": (float, (0, 1000), "Orçamento alimentar diário em R$ (opcional)"),
}
MIN_AGE = 18
PREGNANCY_TERMS = ("gestante", "gestacao", "gravid", "gravd", "lactante", "lactacao", "amament", "pos-parto", "pos parto",
                   "puerper", "pregnan")


def pregnant_or_lactating(p: dict[str, Any]) -> bool:
    import unicodedata
    txt = " ".join(str(c) for c in p.get("condicoes_saude", []))
    txt = "".join(c for c in unicodedata.normalize("NFD", txt.lower()) if unicodedata.category(c) != "Mn")
    return bool(p.get("gestante_ou_lactante")) or any(t in txt for t in PREGNANCY_TERMS)


def bmi(p: dict[str, Any], weight_kg: float | None) -> float | None:
    if not weight_kg or not p.get("altura_cm"):
        return None
    return weight_kg / (p["altura_cm"] / 100) ** 2


REQUIRED = {   # sexo, nascimento e altura valem para todas as áreas, assim como a triagem (safety.TRIAGE)
    "metabolismo": ["sexo", "nascimento", "altura_cm"],
    "treino": ["sexo", "nascimento", "altura_cm", "objetivo", "experiencia", "dias_treino", "minutos_sessao", "equipamento",
               "limitacoes", "liberacao_medica"],
    "nutricao": ["sexo", "nascimento", "altura_cm", "objetivo"],
    "suplementacao": ["sexo", "nascimento", "altura_cm", "objetivo", "condicoes_saude", "medicamentos", "restricoes_alimentares",
                      "cafeina_habitual_mg"],
}


LIMITATIONS = {   # seleção fechada por região; sinônimos comuns são convertidos
    "ombro": ("ombro", "manguito", "supraespinh", "clavicula", "luxacao do ombro"),
    "joelho": ("joelho", "menisco", "lca", "lcp", "ligamento cruzado", "patel", "condromalacia"),
    "lombar": ("lombar", "lombalgia", "hernia", "coluna", "ciatic", "disco"),
    "punho": ("punho", "tunel do carpo", "mao"),
    "quadril": ("quadril", "bursite trocant", "impacto femoroacetabular", "virilha"),
}


def normalize_limitations(items: list[str]) -> list[str]:
    import unicodedata
    out: list[str] = []
    for it in items:
        t = "".join(c for c in unicodedata.normalize("NFD", str(it).lower()) if unicodedata.category(c) != "Mn")
        hit = [region for region, terms in LIMITATIONS.items() if any(term in t for term in terms)]
        if not hit:
            raise ValueError(f"Limitação não reconhecida: '{it}'. Escolha entre {', '.join(LIMITATIONS)}; para outras "
                             "situações (ex.: pós-operatório), marque 'cirurgia recente ou reabilitação' na triagem.")
        out += [h for h in hit if h not in out]
    return out


def validate(field: str, value: Any) -> Any:
    if field not in FIELDS:
        raise ValueError(f"Campo desconhecido: {field}")
    typ, rule, _ = FIELDS[field]
    if field == "limitacoes":
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]
        return normalize_limitations(list(value))
    if typ is list:
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]
        if not isinstance(value, list) or len(value) > 30:
            raise ValueError(f"{field}: informe uma lista")
        return [str(v)[:80] for v in value]
    if typ is bool:
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "sim", "true", "s")
        return bool(value)
    if typ in (int, float):
        import math
        fv = float(value)
        if not math.isfinite(fv):
            raise ValueError(f"{field}: número inválido")
        v = typ(fv)
        if not (rule[0] <= v <= rule[1]):
            raise ValueError(f"{field}: valor fora do intervalo plausível {rule[0]}–{rule[1]}")
        return v
    value = str(value).strip().lower()
    if rule == "date":
        d = date.fromisoformat(value)
        t = date.today()
        if not (date(1920, 1, 1) <= d <= t):
            raise ValueError("nascimento: data implausível")
        if t.year - d.year - ((t.month, t.day) < (d.month, d.day)) < MIN_AGE:
            raise ValueError(f"Este sistema é destinado a adultos (≥ {MIN_AGE} anos); crianças e adolescentes "
                             "precisam de acompanhamento profissional.")
        return d.isoformat()
    if value not in rule:
        raise ValueError(f"{field}: use um de {sorted(rule)}")
    return value


def get(db: Database) -> dict[str, Any]:
    return db.get_json("perfil", {}) or {}


def update(db: Database, changes: dict[str, Any]) -> dict[str, Any]:
    p = get(db)
    for k, v in changes.items():
        if k not in FIELDS:
            continue
        if v is None or v == "":
            p.pop(k, None)
        else:
            p[k] = validate(k, v)
    db.set_json("perfil", p)
    return p


def age(p: dict[str, Any], today: date | None = None) -> int | None:
    if not p.get("nascimento"):
        return None
    b, t = date.fromisoformat(p["nascimento"]), today or date.today()
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def missing(db: Database, area: str | None = None) -> dict[str, list[str]]:
    p = get(db)
    areas = [area] if area else list(REQUIRED)
    from .safety import TRIAGE
    out = {a: [f for f in REQUIRED[a] if f not in p] for a in areas}
    for a in out:
        if a != "metabolismo":                    # a triagem é obrigatória para qualquer pessoa
            out[a] += [k for k in TRIAGE if k not in p and k not in out[a]]
    return out


def record_weight(db: Database, day: str, weight_kg: float, body_fat_pct: float | None = None,
                  muscle_kg: float | None = None, waist_cm: float | None = None,
                  source: str = "manual") -> None:
    if not (30 <= weight_kg <= 300):
        raise ValueError("Peso fora do intervalo plausível (30–300 kg)")
    if body_fat_pct is not None and not (3 <= body_fat_pct <= 60):
        raise ValueError("Percentual de gordura fora do intervalo plausível (3–60%)")
    row = {"date": day, "weight_kg": weight_kg, "source": source}
    for k, v in (("body_fat_pct", body_fat_pct), ("muscle_kg", muscle_kg), ("waist_cm", waist_cm)):
        if v is not None:
            row[k] = v
    db.insert("body_measurements", row, upsert_on="date")


def import_garmin_weights(db: Database) -> int:
    """Copia pesagens vindas da Garmin (balança/registro no app) sem sobrescrever registros manuais."""
    rows = db.q("SELECT date, weight_kg, body_fat_pct, muscle_kg FROM daily_metrics WHERE weight_kg IS NOT NULL")
    n = 0
    for r in rows:
        if not db.q1("SELECT 1 FROM body_measurements WHERE date=? AND source='manual'", (r["date"],)):
            db.insert("body_measurements", {**r, "source": "garmin"}, upsert_on="date")
            n += 1
    return n


def weights(db: Database, start: str, end: str) -> list[dict[str, Any]]:
    return db.q("SELECT * FROM body_measurements WHERE date BETWEEN ? AND ? ORDER BY date", (start, end))


def current_weight(db: Database, cfg: Config, day: str | None = None) -> tuple[float | None, str]:
    """Peso de referência: média das pesagens dos últimos 7 dias; senão a última. Sem pesagem não há peso (nunca um valor presumido)."""
    day = day or date.today().isoformat()
    rows = db.q("SELECT date, weight_kg FROM body_measurements WHERE date<=? AND weight_kg IS NOT NULL "
                "ORDER BY date DESC LIMIT 7", (day,))
    recent = [r["weight_kg"] for r in rows
              if (date.fromisoformat(day) - date.fromisoformat(r["date"])).days <= 7]
    if recent:
        return round(sum(recent) / len(recent), 2), f"média de {len(recent)} pesagem(ns) dos últimos 7 dias"
    if rows:
        return rows[0]["weight_kg"], f"última pesagem ({rows[0]['date']})"
    return None, "sem dado"          # peso presumido da configuração não é mais aceito: só pesagens registradas
