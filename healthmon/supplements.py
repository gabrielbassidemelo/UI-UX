"""Inteligência de suplementação: fichas baseadas em evidência + regras verificáveis.

Regras de desenho:
- Nenhuma sugestão nasce de métrica do Garmin. As regras usam objetivo, alimentação registrada,
  hábitos, condições de saúde, medicamentos e exames informados por você.
- Sem perfil de saúde preenchido (condições, medicamentos, restrições), não há avaliação individual.
- O sistema pode (e costuma) concluir que um suplemento é desnecessário.
- Deficiências não são diagnosticadas aqui; exames fora da referência são assunto para o médico.
- Hormônios, anabolizantes, pró-hormônios, SARMs, estimulantes proibidos e "queimadores" são recusados.
- Doses são as faixas ESTUDADAS na literatura, não prescrição.
"""
from __future__ import annotations

import unicodedata
from datetime import date, timedelta
from typing import Any

from . import metabolism as met
from . import nutrition as nut
from . import profile as prof
from .config import Config
from .db import Database

BANNED = {
    "anaboliz": "esteroides anabolizantes", "testosterona": "hormônios", "oxandrolona": "esteroides anabolizantes",
    "stanozolol": "esteroides anabolizantes", "trembolona": "esteroides anabolizantes", "nandrolona": "esteroides anabolizantes",
    "gh ": "hormônio do crescimento", "hgh": "hormônio do crescimento", "sarm": "SARMs", "ostarine": "SARMs",
    "clembuterol": "estimulante de uso veterinário", "clenbuterol": "estimulante de uso veterinário",
    "efedrina": "estimulante de risco cardiovascular", "dmaa": "estimulante proibido", "dnp": "desacoplador letal",
    "dinitrofenol": "desacoplador letal", "sibutramina": "medicamento controlado", "t3": "hormônio tireoidiano",
    "t4": "hormônio tireoidiano", "diuretic": "diuréticos", "insulina": "hormônios", "pro-hormon": "pró-hormônios",
}


def _n(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def check_substance(name: str) -> dict[str, Any] | None:
    n = _n(name) + " "
    for k, why in BANNED.items():
        if k in n:
            return {"recusado": True, "motivo": f"'{name}' se enquadra em {why}. O sistema não orienta o uso dessas "
                    "substâncias nem altera medicamentos; isso é decisão médica."}
    return None


# Fichas. dose = faixas estudadas; interacoes = {termo no medicamento: explicação}
SHEETS: dict[str, dict[str, Any]] = {
    "creatina": {
        "nome": "Creatina monoidratada", "finalidade": "Desempenho em esforços intensos e curtos; ganho de força e massa magra com treino de força.",
        "populacao": "Adultos saudáveis em treino de força ou de alta intensidade.", "evidencia": "forte",
        "beneficios": "Aumentos pequenos a moderados de força, potência e massa magra quando combinada ao treino.",
        "dose": "3–5 g/dia de forma contínua (ou ~0,03 g/kg/dia). Fase de carga de ~20 g/dia por 5–7 dias é opcional.",
        "horario": "Indiferente; a regularidade diária é o que importa.",
        "adversos": "Ganho de 1–2 kg de água intracelular nas primeiras semanas; desconforto gastrointestinal em doses altas.",
        "contra": {"renal": "Doença renal ou função renal reduzida: só com liberação médica.", "rim": "Doença renal: só com liberação médica."},
        "interacoes": {"anti-inflamat": "Uso crônico de anti-inflamatórios: converse com o médico (função renal).",
                       "ibuprofeno": "Uso crônico de anti-inflamatórios: converse com o médico (função renal).",
                       "diuretic": "Diuréticos: avaliação médica antes."},
        "sono": "Sem efeito conhecido relevante sobre o sono.",
        "custo": "Alto custo-benefício: barata e com efeito consistente para quem treina força.",
        "profissional": "Recomendável avisar o médico; eleva a creatinina sérica sem indicar lesão renal, o que pode confundir exames.",
        "refs": ["kreider2017", "antonio2021", "maughan2018"]},
    "whey": {
        "nome": "Whey protein (ou outra proteína em pó)", "finalidade": "Praticidade para atingir a meta diária de proteína.",
        "populacao": "Adultos em treino de força.", "evidencia": "forte",
        "beneficios": "O benefício vem de atingir a proteína total do dia; não há vantagem sobre proteína de alimentos em quantidade equivalente.",
        "dose": "20–40 g por porção (≈0,25–0,4 g/kg), conforme a lacuna em relação à meta diária.",
        "horario": "Qualquer refeição; o total diário importa mais que o horário.",
        "adversos": "Desconforto gastrointestinal, sobretudo em intolerância à lactose (concentrado).",
        "contra": {"renal": "Doença renal: a ingestão de proteína deve ser definida pelo médico/nutricionista.",
                   "alergia a leite": "Alergia à proteína do leite: não usar.", "aplv": "Alergia à proteína do leite: não usar."},
        "interacoes": {"levodopa": "Proteína interfere na absorção de levodopa; siga a orientação médica de horários."},
        "sono": "Sem efeito relevante.", "custo": "Razoável como conveniência; desnecessário se a alimentação já fornece proteína suficiente.",
        "profissional": "Não é necessário em pessoas saudáveis; nutricionista ajuda a ajustar a alimentação primeiro.",
        "refs": ["morton2018", "jager2017"]},
    "cafeina": {
        "nome": "Cafeína", "finalidade": "Desempenho agudo (resistência, força, atenção).", "populacao": "Adultos saudáveis.", "evidencia": "forte",
        "beneficios": "Melhora pequena a moderada de desempenho em muitos indivíduos; resposta bastante variável.",
        "dose": "3–6 mg/kg cerca de 60 min antes do treino nos estudos; doses de ~2–3 mg/kg já mostram efeito. "
                "Referência de segurança (EFSA): até 400 mg/dia e 200 mg por dose em adultos saudáveis.",
        "horario": "Evitar nas 6–8 horas antes de dormir.",
        "adversos": "Ansiedade, taquicardia, tremor, refluxo e piora do sono.",
        "contra": {"arritmia": "Arritmia: só com liberação médica.", "hipertens": "Hipertensão: converse com o médico.",
                   "ansiedade": "Transtorno de ansiedade: pode piorar os sintomas.", "gesta": "Gestação: limite menor, definido pelo obstetra.",
                   "refluxo": "Refluxo: pode piorar.", "insonia": "Insônia: tende a piorar."},
        "interacoes": {"metilfenidato": "Soma de efeito estimulante.", "lisdexanfetamina": "Soma de efeito estimulante.",
                       "venvanse": "Soma de efeito estimulante.", "ritalina": "Soma de efeito estimulante.",
                       "teofilina": "Aumenta efeitos adversos.", "ciprofloxacino": "Reduz a eliminação da cafeína.",
                       "clozapina": "Cafeína altera níveis do medicamento.", "litio": "Mudanças no consumo alteram níveis de lítio."},
        "sono": "Prejudica o sono mesmo quando tomada 6 horas antes de deitar.",
        "custo": "Baixo custo (café também conta); o limite é a tolerância e o sono.",
        "profissional": "Necessária se houver condição cardiovascular, ansiedade, gestação ou uso de estimulantes.",
        "refs": ["guest2021", "efsa2015caffeine", "drake2013", "clark2017"]},
    "beta-alanina": {
        "nome": "Beta-alanina", "finalidade": "Tamponamento em esforços intensos de 1–4 minutos.", "populacao": "Praticantes de esforços intermitentes/alta intensidade.",
        "evidencia": "moderada", "beneficios": "Melhora pequena em esforços de alta intensidade com 1–4 min; pouco efeito em força máxima ou treinos longos leves.",
        "dose": "4–6 g/dia, divididos em doses de até ~1,6 g, por no mínimo 2–4 semanas.",
        "horario": "Indiferente; efeito é crônico.", "adversos": "Parestesia (formigamento) transitória, reduzida ao fracionar a dose.",
        "contra": {}, "interacoes": {}, "sono": "Sem efeito conhecido.",
        "custo": "Baixo a moderado custo-benefício: efeito pequeno e específico.",
        "profissional": "Não é essencial em pessoas saudáveis.", "refs": ["trexler2015", "maughan2018"]},
    "omega-3": {
        "nome": "Ômega-3 (EPA/DHA)", "finalidade": "Complementar a ingestão quando há pouco consumo de peixe; redução de triglicerídeos sob orientação médica.",
        "populacao": "Adultos; ensaios grandes em prevenção cardiovascular.", "evidencia": "moderada",
        "beneficios": "Reduz triglicerídeos; em prevenção primária, pouco ou nenhum efeito em eventos cardiovasculares e mortalidade.",
        "dose": "Cerca de 1 g/dia de EPA+DHA nos ensaios de prevenção; 2–4 g/dia é uso médico para triglicerídeos.",
        "horario": "Com refeições.", "adversos": "Eructação, desconforto gastrointestinal; doses mais altas associadas a maior risco de fibrilação atrial.",
        "contra": {"fibrila": "Histórico de fibrilação atrial: só com orientação do cardiologista.", "arritmia": "Arritmia: só com orientação do cardiologista."},
        "interacoes": {"varfarina": "Anticoagulante: avaliação médica (risco de sangramento).", "marevan": "Anticoagulante: avaliação médica.",
                       "rivaroxabana": "Anticoagulante: avaliação médica.", "xarelto": "Anticoagulante: avaliação médica.",
                       "apixabana": "Anticoagulante: avaliação médica.", "eliquis": "Anticoagulante: avaliação médica.",
                       "clopidogrel": "Antiagregante: avaliação médica.", "aas": "Antiagregante: avaliação médica."},
        "sono": "Sem efeito consistente.", "custo": "Baixo para a população geral; comer peixe 1–2 vezes por semana é a primeira opção.",
        "profissional": "Recomendada antes de doses acima de 1 g/dia ou com medicamentos anticoagulantes.",
        "refs": ["abdelhamid2020", "manson2019n3", "gencer2021"]},
    "magnesio": {
        "nome": "Magnésio", "finalidade": "Corrigir ingestão insuficiente; uso para sono tem evidência fraca.", "populacao": "Estudos de sono sobretudo em idosos com insônia.",
        "evidencia": "limitada", "beneficios": "Pequena melhora possível na latência do sono em idosos com insônia (evidência de baixa qualidade).",
        "dose": "Estudos usam 200–400 mg/dia de magnésio elementar; o limite superior tolerável para suplementos é 350 mg/dia.",
        "horario": "À noite nos estudos de sono; longe de certos medicamentos (ver interações).",
        "adversos": "Diarreia e desconforto abdominal (dose-dependente).",
        "contra": {"renal": "Doença renal: risco de acúmulo; não usar sem médico.", "rim": "Doença renal: não usar sem médico."},
        "interacoes": {"levotiroxina": "Separar por pelo menos 4 horas.", "puran": "Separar por pelo menos 4 horas (levotiroxina).",
                       "alendronato": "Reduz absorção do bisfosfonato; separar horários.", "ciprofloxacino": "Reduz absorção do antibiótico.",
                       "levofloxacino": "Reduz absorção do antibiótico.", "doxiciclina": "Reduz absorção do antibiótico.",
                       "tetraciclina": "Reduz absorção do antibiótico."},
        "sono": "Efeito incerto e pequeno.", "custo": "Baixo: alimentos (leguminosas, castanhas, folhas, integrais) suprem a maioria das pessoas.",
        "profissional": "Recomendada se houver doença renal ou uso dos medicamentos listados.", "refs": ["mah2021", "iom1997"]},
    "vitamina d": {
        "nome": "Vitamina D", "finalidade": "Corrigir deficiência confirmada por exame (25-OH-vitamina D).", "populacao": "Pessoas com deficiência ou em grupos de risco.",
        "evidencia": "forte", "beneficios": "Em pessoas sem deficiência, suplementar não reduziu câncer nem eventos cardiovasculares (ensaio VITAL). O benefício está em corrigir deficiência.",
        "dose": "Referência dietética: 600 UI/dia; limite superior tolerável: 4.000 UI/dia. Dose de correção é definida pelo médico conforme o exame.",
        "horario": "Com refeição que contenha gordura.", "adversos": "Em excesso: hipercalcemia, cálculo renal.",
        "contra": {"hipercalcemia": "Hipercalcemia: não usar.", "sarcoidose": "Sarcoidose: só com médico.", "calculo renal": "Cálculo renal: avaliação médica."},
        "interacoes": {"hidroclorotiazida": "Tiazídicos elevam o cálcio; avaliação médica.", "clortalidona": "Tiazídicos elevam o cálcio; avaliação médica.",
                       "digoxina": "Hipercalcemia aumenta toxicidade da digoxina."},
        "sono": "Sem efeito estabelecido.", "custo": "Alto quando há deficiência comprovada; baixo sem exame.",
        "profissional": "Necessária: a indicação depende de exame de sangue.", "refs": ["manson2019", "holick2011", "iom2011"]},
    "eletrolitos": {
        "nome": "Eletrólitos (sódio e outros)", "finalidade": "Repor perdas de suor em exercícios prolongados.", "populacao": "Exercício acima de ~60–90 min ou em calor intenso.",
        "evidencia": "moderada", "beneficios": "Ajuda a manter a hidratação e reduzir risco de hiponatremia em esforços longos; sem benefício em treinos curtos.",
        "dose": "Bebidas com cerca de 20–30 mmol/L de sódio (≈0,5–0,7 g/L) durante exercícios longos com muito suor.",
        "horario": "Durante e após sessões longas.", "adversos": "Excesso de sódio em quem não precisa.",
        "contra": {"hipertens": "Hipertensão: sódio extra só com orientação.", "renal": "Doença renal: só com orientação médica.",
                   "insuficiencia cardiaca": "Insuficiência cardíaca: só com orientação médica."},
        "interacoes": {"espironolactona": "Poupador de potássio: evitar suplementos com potássio.", "losartana": "Evitar potássio extra sem orientação.",
                       "enalapril": "Evitar potássio extra sem orientação."},
        "sono": "Sem efeito.", "custo": "Baixo fora de sessões longas ou muito calor: água e alimentação bastam.",
        "profissional": "Necessária com hipertensão, doença renal ou cardíaca.", "refs": ["sawka2007", "thomas2016"]},
}


def _avg_intake(db: Database, end: date, days: int = 14) -> dict[str, Any]:
    it = met.daily_intake(db, (end - timedelta(days=days - 1)).isoformat(), end.isoformat())
    full = [v for v in it.values() if v["completo"]]
    if len(full) < 5:
        return {"n": len(full)}
    return {"n": len(full), "protein_g": sum(v["protein_g"] or 0 for v in full) / len(full)}


def evaluate(db: Database, cfg: Config, day: date | None = None) -> dict[str, Any]:
    day = day or date.today()
    p = prof.get(db)
    miss = prof.missing(db, "suplementacao")["suplementacao"]
    if miss:
        return {"disponivel": False, "faltam": miss,
                "texto": "Antes de qualquer avaliação individual, informe (mesmo que seja 'nenhum'): " + ", ".join(miss) + "."}
    meds = [_n(m) for m in p.get("medicamentos", [])]
    conds = [_n(c) for c in p.get("condicoes_saude", [])]
    diet = " ".join(_n(r) for r in p.get("restricoes_alimentares", []))
    goal = p["objetivo"]
    intake, target = _avg_intake(db, day), nut.active_targets(db)
    w, _ = prof.current_weight(db, cfg, day.isoformat())
    in_use = {r["key"]: r for r in db.q("SELECT * FROM user_supplements WHERE status='em_uso'")}
    out = []
    for key, s in SHEETS.items():
        contra = [txt for term, txt in s["contra"].items() if any(term in c for c in conds)]
        inter = [f"{m}: {txt}" for term, txt in s["interacoes"].items() for m in meds if term in m]
        status, why = "desnecessario", ""
        if key == "creatina":
            if goal in ("hipertrofia", "forca", "recomposicao", "desempenho", "emagrecimento"):
                status, why = "potencialmente_relevante", "Seu objetivo envolve treino de força, contexto em que a creatina tem a evidência mais consistente."
            else:
                why = "Seu objetivo atual não depende de força/potência; o benefício esperado é pequeno."
        elif key == "whey":
            if "protein_g" not in intake or not target:
                status, why = "dados_insuficientes", "Registre a alimentação por pelo menos 5 dias completos e confirme a meta nutricional para avaliar se falta proteína."
            elif intake["protein_g"] >= 0.9 * target["proteina_g"]:
                why = f"Sua ingestão média de proteína ({intake['protein_g']:.0f} g/dia) já atinge a meta ({target['proteina_g']} g) com alimentos."
            else:
                status = "potencialmente_relevante"
                why = (f"Sua ingestão média ({intake['protein_g']:.0f} g/dia em {intake['n']} dias registrados) está abaixo da meta "
                       f"({target['proteina_g']} g). Ajustar as refeições é a primeira opção; proteína em pó é conveniência.")
        elif key == "cafeina":
            hab = p.get("cafeina_habitual_mg")
            if hab is not None and w and hab >= 400:
                status, why = "inadequado", f"Você já consome cerca de {hab:.0f} mg/dia, no limite de referência de segurança; acrescentar não é indicado."
            elif goal in ("desempenho", "condicionamento", "forca"):
                status, why = "potencialmente_relevante", "Pode ajudar em sessões-chave, se você tolera bem e toma longe do horário de dormir."
            else:
                why = "Sem necessidade para o seu objetivo; o café habitual já fornece cafeína."
        elif key == "beta-alanina":
            if goal in ("desempenho", "condicionamento"):
                status, why = "potencialmente_relevante", "Só faz sentido se você realiza esforços intensos de 1–4 minutos (tiros, lutas, remo)."
            else:
                why = "O efeito é restrito a esforços intensos de 1–4 minutos; não se aplica ao seu objetivo."
        elif key == "omega-3":
            if "vegan" in diet or "vegetarian" in diet or "nao come peixe" in diet or "sem peixe" in diet:
                status, why = "requer_avaliacao", "Você informou não consumir peixe; vale discutir com nutricionista/médico (há opções de DHA de algas)."
            else:
                why = "Em prevenção primária os ensaios mostram pouco ou nenhum benefício; consumir peixe 1–2 vezes por semana é preferível."
        elif key == "magnesio":
            why = ("A evidência para sono é fraca, e dados de sono do relógio não indicam deficiência de magnésio. "
                   "Alimentos ricos em magnésio são a primeira opção.")
        elif key == "vitamina d":
            lab = db.q1("SELECT value, unit, ref_low, date FROM labs WHERE analyte_key='vitamina_d' ORDER BY date DESC LIMIT 1")
            if not lab:
                status, why = "dados_insuficientes", "A indicação depende de exame de sangue (25-OH-vitamina D). Dados do relógio não avaliam vitamina D."
            elif lab["ref_low"] is not None and lab["value"] is not None and lab["value"] < lab["ref_low"]:
                status, why = "requer_avaliacao", (f"Seu exame de {lab['date']} ({lab['value']:g} {lab['unit'] or ''}) está abaixo da referência do "
                                                   "laboratório. Leve ao médico: a dose de correção é definida por ele.")
            else:
                why = f"Seu exame de {lab['date']} está dentro da referência do laboratório; suplementar não trouxe benefício em ensaios com pessoas sem deficiência."
        elif key == "eletrolitos":
            long_ = db.q1("SELECT COUNT(*) n FROM activities WHERE date>=? AND duration_s>=5400", ((day - timedelta(days=27)).isoformat(),))
            if long_ and long_["n"] >= 2:
                status, why = "potencialmente_relevante", f"Você fez {long_['n']} atividades com mais de 90 minutos nas últimas 4 semanas; nelas a reposição de sódio pode ajudar."
            else:
                why = "Seus treinos recentes têm menos de 90 minutos; água e alimentação normal são suficientes."
        if contra:
            status = "inadequado"
            why = "Contraindicação a partir do que você informou. " + why
        elif inter and status == "potencialmente_relevante":
            status = "requer_avaliacao"
            why = "Há possível interação com medicamento informado. " + why
        out.append({"chave": key, **{k: s[k] for k in ("nome", "finalidade", "populacao", "evidencia", "beneficios", "dose", "horario",
                                                      "adversos", "sono", "custo", "profissional", "refs")},
                    "status": status, "justificativa": why, "contraindicacoes_aplicaveis": contra,
                    "contraindicacoes_gerais": sorted(set(s["contra"].values())), "interacoes_aplicaveis": inter,
                    "interacoes_gerais": sorted(set(s["interacoes"].values())), "em_uso": key in in_use})
    order = {"inadequado": 0, "requer_avaliacao": 1, "potencialmente_relevante": 2, "dados_insuficientes": 3, "desnecessario": 4}
    out.sort(key=lambda x: order[x["status"]])
    return {"disponivel": True, "itens": out,
            "aviso": "Avaliação informativa baseada no que você informou. Interações listadas não são exaustivas: confirme com "
                     "médico ou farmacêutico. Suplementos devem estar regularizados na Anvisa (RDC 243/2018 e IN 28/2018). "
                     "Nenhum suplemento é necessário para ter resultado.",
            "refs_gerais": ["maughan2018", "anvisa2018"]}
