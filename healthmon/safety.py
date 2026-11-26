"""Triagem de segurança centralizada. Nenhum módulo personaliza nada sem passar por aqui.

Princípios:
- Perguntas estruturadas (sim/não) decidem; texto livre só reforça, nunca libera.
- Informação necessária ausente = recomendação correspondente SUSPENSA (com a lista do que falta).
- As regras são código determinístico. A IA generativa não participa das decisões e o texto que
  ela produzir passa por um filtro (ver llm.py) antes de aparecer.
- Nada aqui diagnostica, prescreve ou sugere alterar medicamento: os bloqueios encaminham ao profissional.
- IMC não é o único critério nutricional: entram percentual de gordura, ritmo de perda, ingestão
  registrada, idade e condições informadas.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from typing import Any

from . import profile as prof
from .config import Config
from .db import Database

# pergunta -> texto exibido (todas obrigatórias, para qualquer pessoa)
TRIAGE = {
    "gestante_ou_lactante": "Está grávida, amamentando ou teve parto nos últimos 12 meses?",
    "transtorno_alimentar": "Tem ou já teve transtorno alimentar (anorexia, bulimia, compulsão)?",
    "doenca_cardiovascular": "Tem doença do coração ou dos vasos (pressão alta, arritmia, infarto, insuficiência cardíaca, AVC) ou usa marca-passo?",
    "doenca_renal": "Tem doença nos rins?",
    "doenca_hepatica": "Tem doença no fígado?",
    "diabetes": "Tem diabetes?",
    "usa_insulina_ou_hipoglicemiante": "Usa insulina ou remédio que pode baixar demais a glicose (ex.: glibenclamida, gliclazida, dapagliflozina)?",
    "usa_medicamento_fc": "Usa remédio que altera os batimentos (betabloqueador, verapamil, diltiazem, antiarrítmico)?",
    "usa_anticoagulante": "Usa anticoagulante ou antiagregante (varfarina, rivaroxabana, clopidogrel, AAS diário)?",
    "usa_psiquiatrico_ou_estimulante": "Usa antidepressivo, lítio ou estimulante (ex.: metilfenidato, lisdexanfetamina)?",
    "usa_levotiroxina": "Usa hormônio da tireoide (levotiroxina)?",
    "usa_diuretico": "Usa diurético?",
    "cirurgia_recente": "Fez cirurgia nos últimos 6 meses ou está em reabilitação de lesão?",
    "alergia_leite": "Tem alergia a leite ou intolerância à lactose?",
    "alergia_peixe": "Tem alergia a peixe ou frutos do mar?",
}
SYMPTOMS = {   # triagem do check-in diário -> (texto, urgência)
    "peito": ("Dor ou pressão no peito", True),
    "desmaio": ("Desmaio ou quase desmaio / tontura forte", True),
    "falta_de_ar": ("Falta de ar fora do normal", True),
    "palpitacao": ("Palpitações ou coração disparado em repouso", True),
    "febre": ("Febre ou sensação de doença (gripe, infecção)", False),
    "dor_articular": ("Dor aguda ou inchaço em articulação / lesão nova", False),
}
# Reforço por texto livre (sintomas e observações). Normalizado, sem acentos, com negação tratada.
SYMPTOM_TEXT = {
    "peito": ("dor no peito", "dor toracica", "aperto no peito", "pressao no peito", "dor precordial", "angina"),
    "desmaio": ("desmai", "sincope", "tontura", "tonto", "tonta", "vertigem", "escurecimento da vista"),
    "falta_de_ar": ("falta de ar", "falta d ar", "dispneia", "sem folego", "sufoc"),
    "palpitacao": ("palpita", "coracao disparad", "coracao acelerad", "taquicardia", "arritmia"),
    "febre": ("febre", "febril", "calafrio", "gripe", "gripad", "infeccao", "covid"),
    "dor_articular": ("dor forte", "dor intensa", "dor no joelho", "dor no ombro", "dor na lombar", "dor nas costas", "dor no quadril", "dor no punho", "dor no tornozelo", "dor na coluna", "dor aguda", "inchaco", "inchad", "torci", "entorse", "estalo no", "lesao", "lesion", "distens"),
}
NEGATION = re.compile(r"\b(sem|nao|nenhum|nenhuma|zero)\s+(tenho\s+|tive\s+|senti\s+|sinto\s+)?$")
UNDER18 = "Este sistema é destinado a adultos (18 anos ou mais). Crianças e adolescentes precisam de acompanhamento profissional."


def norm(s: Any) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def symptom_flags(checkin: dict[str, Any] | None) -> dict[str, bool]:
    """Sinais de alerta do dia: respostas estruturadas + texto livre de sintomas E observações."""
    import json
    out = {k: False for k in SYMPTOMS}
    if not checkin:
        return out
    try:
        tri = json.loads(checkin.get("triagem") or "{}")
    except ValueError:
        tri = {}
    for k in SYMPTOMS:
        out[k] = bool(tri.get(k))
    text = norm(f"{checkin.get('symptoms') or ''} . {checkin.get('notes') or ''}")
    for k, terms in SYMPTOM_TEXT.items():
        for t in terms:
            for m in re.finditer(rf"\b{re.escape(t)}", text):
                before = text[max(0, m.start() - 22):m.start()]
                after = text[m.end():m.end() + 12]
                if NEGATION.search(before) or (k == "peito" and after.strip().startswith("do pe")):
                    continue
                out[k] = True
    return out


def triage_answered(checkin: dict[str, Any] | None) -> bool:
    return bool(checkin and checkin.get("triagem"))


def screen(db: Database, cfg: Config, day: date | None = None) -> dict[str, Any]:
    """Resultado da triagem: o que falta, sinalizadores e bloqueios por área (com o motivo)."""
    day = day or date.today()
    p = prof.get(db)
    b: dict[str, str] = {}
    avisos: list[str] = []
    faltam = [k for k in ("sexo", "nascimento", "altura_cm") if k not in p] + [k for k in TRIAGE if k not in p]
    age = prof.age(p, day)
    out: dict[str, Any] = {"faltam": faltam, "idade": age, "bloqueios": b, "avisos": avisos, "flags": {}}

    def block(areas: tuple[str, ...], reason: str) -> None:
        for a in areas:
            b.setdefault(a, reason)
    ALL = ("deficit", "metas_nutricionais", "metas_peso", "treino_plano", "treino_intenso", "suplementos")
    if faltam:
        block(ALL, "Responda a triagem de saúde no perfil antes de receber recomendações personalizadas. Falta: "
              + ", ".join(faltam) + ".")
        return out
    if age is None or age < prof.MIN_AGE:
        block(ALL, UNDER18)
        return out
    f = {k: bool(p.get(k)) for k in TRIAGE}
    f["idoso"] = age >= 65
    f["gestante_ou_lactante"] = f["gestante_ou_lactante"] or prof.pregnant_or_lactating(p)
    out["flags"] = f

    if f["gestante_ou_lactante"]:
        block(("deficit", "metas_nutricionais", "metas_peso", "suplementos", "treino_plano", "treino_intenso"),
              "Gestação, amamentação ou pós-parto: alimentação, suplementos e exercício nessa fase devem ser "
              "orientados pelo obstetra e pelo nutricionista. O sistema segue apenas registrando e descrevendo seus dados.")
    if f["transtorno_alimentar"]:
        block(("deficit", "metas_nutricionais", "metas_peso"),
              "Você informou transtorno alimentar atual ou passado. O sistema não calcula metas de calorias ou de peso "
              "nem mostra contagem calórica nessa situação. O acompanhamento com profissionais de saúde é o caminho seguro.")
        out["ocultar_calorias"] = True
    if f["doenca_renal"] or f["doenca_hepatica"]:
        block(("deficit", "metas_nutricionais", "suplementos"),
              "Com doença renal ou hepática, as quantidades de proteína, líquidos e qualquer suplemento precisam ser "
              "definidas pelo seu médico ou nutricionista. O sistema não calcula essas metas.")
    if f["doenca_cardiovascular"]:
        block(("treino_intenso",), "Com doença cardiovascular informada, o sistema não propõe HIIT nem séries perto da falha. "
                                   "A intensidade deve ser definida com o seu médico.")
        avisos.append("Meta de água e de sódio não se aplica a quem tem restrição hídrica (ex.: insuficiência cardíaca): siga a orientação médica.")
        out["sem_meta_de_agua"] = True
    if f["usa_medicamento_fc"]:
        block(("treino_intenso",), "Você usa medicamento que altera a frequência cardíaca: zonas por batimentos não se aplicam e a "
                                   "intensidade deve ser combinada com o médico.")
        out["zonas_fc_invalidas"] = True
    if f["diabetes"] and f["usa_insulina_ou_hipoglicemiante"]:
        block(("deficit",), "Com insulina ou medicamento que pode causar hipoglicemia, reduzir calorias ou carboidratos muda a "
                            "necessidade do remédio. O sistema não propõe déficit: isso deve ser ajustado com o seu médico.")
        avisos.append("Exercício pode baixar a glicose durante e horas depois do treino. Combine com o seu médico como "
                      "monitorar e o que ter à mão; o sistema não orienta doses.")
    elif f["diabetes"]:
        avisos.append("Diabetes informado: mudanças de alimentação e treino devem ser acompanhadas pelo seu médico.")
    if f["cirurgia_recente"]:
        block(("treino_plano", "treino_intenso"), "Cirurgia recente ou reabilitação: o plano de treino deve vir do seu médico ou "
                                                  "fisioterapeuta. O sistema não gera plano nessa fase.")
    if not p.get("liberacao_medica"):
        block(("treino_intenso",), "Sem liberação médica registrada, o plano fica em intensidade moderada, sem HIIT.")
    if f["idoso"]:
        out["idoso"] = True
        avisos.append("A partir dos 65 anos o sistema usa regras mais conservadoras: sem HIIT automático, mais repetições "
                      "em reserva, déficit menor e proteína mínima mais alta, além de incluir treino de equilíbrio.")

    # --- sinais nutricionais além do IMC ---------------------------------------------------
    w, src = prof.current_weight(db, cfg, day.isoformat())
    out["peso_medido"] = w is not None and "configuração" not in src
    if not out["peso_medido"]:
        block(("deficit", "metas_nutricionais", "metas_peso"),
              "Registre o seu peso real (Composição corporal) antes de receber metas: o sistema não usa peso presumido.")
        return out
    risk = nutrition_risk(db, cfg, day, p, w)
    out["risco_nutricional"] = risk
    if risk["bloqueia_deficit"]:
        block(("deficit", "metas_peso"), " ".join(risk["motivos"]) + " O sistema não propõe déficit nem meta de perda de peso "
              "nessa condição; procure um médico ou nutricionista.")
    return out


def nutrition_risk(db: Database, cfg: Config, day: date, p: dict[str, Any], w: float) -> dict[str, Any]:
    from . import metabolism as met
    reasons: list[str] = []
    imc = prof.bmi(p, w)
    if imc is not None and imc < 18.5:
        reasons.append(f"IMC estimado de {imc:.1f} (baixo peso).")
    bf = db.q1("SELECT body_fat_pct FROM body_measurements WHERE body_fat_pct IS NOT NULL AND date<=? "
               "ORDER BY date DESC LIMIT 1", (day.isoformat(),))
    low_bf = 8 if p.get("sexo") == "masculino" else 15
    if bf and bf["body_fat_pct"] < low_bf:
        reasons.append(f"Percentual de gordura registrado ({bf['body_fat_pct']:.0f}%) já muito baixo.")
    wt = met.weight_trend(db, day, 28)
    if wt.get("suficiente") and wt["pct_semana"] < -1.5:
        reasons.append(f"Perda de peso recente rápida ({abs(wt['pct_semana']):.1f}% por semana).")
    intake = met.daily_intake(db, (day - timedelta(days=13)).isoformat(), day.isoformat())
    full = [v["kcal"] for v in intake.values() if v["n"] >= 2 and v["kcal"]]
    b = met.bmr(db, cfg, day)
    low_intake = bool(b.get("disponivel") and len(full) >= 10 and sum(full) / len(full) < 0.8 * b["mifflin"])
    if low_intake:
        reasons.append("A ingestão registrada nas últimas duas semanas está bem abaixo do gasto de repouso estimado.")
    return {"imc": round(imc, 1) if imc else None, "motivos": reasons, "bloqueia_deficit": bool(reasons),
            "ingestao_muito_baixa": low_intake, "imc_proximo_do_limite": imc is not None and 18.5 <= imc < 20}


def blocked(scr: dict[str, Any], area: str) -> str | None:
    return scr["bloqueios"].get(area)


# ------------------------------------------------------------ filtro de texto gerado por IA
FORBIDDEN_LLM = [
    r"\b(aument|reduz|suspend|interromp|pare de|dobr|troque|substitu)\w*\s+(a|o|sua|seu|as|os)?\s*(dose|medica|remedio|insulina|levotiroxina|anti\w+)",
    r"\b(diagnostic|voce tem|voce esta com|provavel(mente)?\s+\w*\s*(hipo|hiper|diabet|anemia|apneia|depress|arritmia|infec))",
    r"\b(hipotireoidismo|hipertireoidismo|diabetes|anemia|apneia|fibrilacao|depressao|overtraining)\s+(provavel|confirmad)",
    r"\b\d+\s*(mg|mcg|ui|g)\b",                      # doses
    r"\b(hiit|treino intenso|alta intensidade|ate a falha)\b",
    r"\b(jejum|dieta de \d+|corte \d+ ?kcal)\b",
]


def llm_text_allowed(text: str, readiness_level: str | None = None) -> tuple[bool, str]:
    """Rejeita texto de IA com diagnóstico, dose, alteração de medicamento ou incentivo a esforço intenso."""
    t = norm(text)
    for pat in FORBIDDEN_LLM:
        if re.search(pat, t):
            return False, pat
    if readiness_level in ("parar", "recuperacao") and re.search(r"\b(trein|corr|pedal|muscula)\w*", t) \
            and not re.search(r"\b(nao|evite|descans|suspen)\w*", t):
        return False, "incentiva treino em dia de pausa"
    return True, ""
