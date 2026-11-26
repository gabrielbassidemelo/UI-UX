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
    "hormonio do crescimento": "hormônio do crescimento", "somatropina": "hormônio do crescimento", "ostarin": "SARMs",
    "ligandrol": "SARMs", "cardarine": "SARMs", "yohimb": "estimulante de risco cardiovascular", "ioimb": "estimulante de risco cardiovascular",
    "efedra": "estimulante de risco cardiovascular", "ma huang": "estimulante de risco cardiovascular",
    "sinefrina": "estimulante de risco cardiovascular", "termogen": "'queimadores' com estimulantes", "queimador": "'queimadores' com estimulantes",
    "laxante": "uso de laxantes para emagrecer", "liraglutida": "medicamento de prescrição", "semaglutida": "medicamento de prescrição",
    "ozempic": "medicamento de prescrição", "anfetamina": "medicamento controlado", "oxandrin": "esteroides anabolizantes",
    "durateston": "esteroides anabolizantes", "deca": "esteroides anabolizantes", "boldenona": "esteroides anabolizantes",
}


def _n(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


SYNONYMS = {  # termo da ficha -> formas equivalentes (casadas no INÍCIO de palavra)
    "renal": ("renal", "rim", "rins", "drc", "irc", "nefropat", "nefrit", "dialise", "hemodialise", "transplante renal", "insuficiencia renal"),
    "rim": (),
    "arritmia": ("arritmia", "taquicardia", "fibrila", "palpita", "extrassistol", "fa", "icc", "insuficiencia cardiaca", "infarto",
                 "iam", "marca passo", "marcapasso", "cardiopat", "coronari"),
    "fibrila": ("fibrila", "flutter"),
    "hipertens": ("hipertens", "pressao alta", "has"),
    "ansiedade": ("ansiedade", "tag", "panico", "transtorno de ansiedade"),
    "refluxo": ("refluxo", "drge", "gastrite", "ulcera"),
    "gesta": ("gesta", "gravid", "lacta", "amament"),
    "insonia": ("insonia",),
    "alergia a leite": ("alergia a leite", "alergia ao leite", "aplv", "alergia a proteina do leite"),
    "aplv": (),
}


def term_matches(term: str, text: str) -> bool:
    """Casa o termo (e sinônimos) apenas no início de palavra: 'rim' não casa com 'primária'."""
    import re as _re
    forms = SYNONYMS.get(term, (term,))
    text = _re.sub(r"[^a-z0-9]+", " ", text)
    return any(_re.search(rf"(?<![a-z0-9]){_re.escape(f)}" + (r"(?![a-z0-9])" if len(f) <= 4 else ""), text) for f in forms)


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
        "dose": "3–5 g/dia de forma contínua. Fase de carga de ~0,3 g/kg/dia por 5–7 dias é opcional.",
        "horario": "Indiferente; a regularidade diária é o que importa.",
        "adversos": "Retenção hídrica inicial de cerca de 0,5–1,0 L com a fase de carga; desconforto gastrointestinal em doses altas.",
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
                "Referência de segurança (EFSA): até 400 mg/dia e 200 mg por dose em adultos saudáveis; em pessoas "
                "mais pesadas, 3–6 mg/kg ultrapassa esse teto por dose, e o sistema usa o teto.",
        "horario": "Evitar nas 6–8 horas antes de dormir.",
        "adversos": "Ansiedade, taquicardia, tremor, refluxo e piora do sono.",
        "contra": {"arritmia": "Arritmia: só com liberação médica.", "hipertens": "Hipertensão: converse com o médico.",
                   "ansiedade": "Transtorno de ansiedade: pode piorar os sintomas.", "gesta": "Gestação: limite menor, definido pelo obstetra.",
                   "refluxo": "Refluxo: pode piorar.", "insonia": "Insônia: tende a piorar."},
        "interacoes": {"metilfenidato": "Soma de efeito estimulante.", "lisdexanfetamina": "Soma de efeito estimulante.",
                       "venvanse": "Soma de efeito estimulante.", "ritalina": "Soma de efeito estimulante.",
                       "teofilina": "Aumenta efeitos adversos.", "ciprofloxacino": "Reduz a eliminação da cafeína.",
                       "clozapina": "Cafeína altera níveis do medicamento.", "litio": "Mudanças no consumo alteram níveis de lítio."},
        "sono": "Pode prejudicar o sono; em um ensaio, 400 mg tomados 6 horas antes de deitar reduziram o sono em mais de 1 hora.",
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
        "horario": "Com refeições.", "adversos": "Eructação, desconforto gastrointestinal; maior risco de fibrilação atrial em meta-análise de ensaios, presente já com até 1 g/dia e maior acima disso.",
        "contra": {"fibrila": "Histórico de fibrilação atrial: só com orientação do cardiologista.", "arritmia": "Arritmia: só com orientação do cardiologista."},
        "interacoes": {"varfarina": "Anticoagulante: avaliação médica (risco de sangramento).", "marevan": "Anticoagulante: avaliação médica.",
                       "rivaroxabana": "Anticoagulante: avaliação médica.", "xarelto": "Anticoagulante: avaliação médica.",
                       "apixabana": "Anticoagulante: avaliação médica.", "eliquis": "Anticoagulante: avaliação médica.",
                       "clopidogrel": "Antiagregante: avaliação médica.", "aas": "Antiagregante: avaliação médica."},
        "sono": "Sem efeito consistente.", "custo": "Baixo para a população geral; comer peixe 1–2 vezes por semana é a primeira opção.",
        "profissional": "Recomendada antes de doses acima de 1 g/dia ou com medicamentos anticoagulantes.",
        "refs": ["abdelhamid2020", "manson2019n3", "gencer2021"]},
    "magnesio": {
        "nome": "Magnésio", "finalidade": "Corrigir ingestão insuficiente; uso para sono tem evidência fraca.", "populacao": "Três ensaios pequenos, somente em idosos com insônia; não se aplica a adultos jovens saudáveis.",
        "evidencia": "limitada", "beneficios": "Em idosos com insônia, redução de cerca de 17 min na latência do sono; tempo total de sono sem diferença "
                      "(evidência de baixa a muito baixa qualidade). Nunca é sugerido a partir de dados de sono do relógio.",
        "dose": "Os ensaios em idosos com insônia usaram 320–729 mg/dia de magnésio elementar, em geral ACIMA do limite superior "
                "tolerável de 350 mg/dia para suplementos. O sistema não sugere dose; uso só com orientação profissional.",
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
        "evidencia": "forte", "beneficios": "Em adultos mais velhos não selecionados por nível de vitamina D, suplementar não reduziu câncer nem eventos "
                      "cardiovasculares (ensaio VITAL). O benefício esperado está em corrigir deficiência confirmada.",
        "dose": "Referência dietética: 600 UI/dia (800 UI/dia acima de 70 anos); limite superior tolerável: 4.000 UI/dia. Dose de correção é definida pelo médico conforme o exame.",
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
    from . import safety
    scr = safety.screen(db, cfg, day)
    stop = safety.blocked(scr, "suplementos")
    if stop:
        return {"disponivel": False, "faltam": [], "texto": stop}
    fl = scr["flags"]
    meds = [_n(m) for m in p.get("medicamentos", [])]
    conds = [_n(c) for c in p.get("condicoes_saude", [])]
    diet = " ".join(_n(r) for r in p.get("restricoes_alimentares", []))
    goal = p["objetivo"]
    intake, target = _avg_intake(db, day), nut.effective_targets(db, cfg, day)
    w, _ = prof.current_weight(db, cfg, day.isoformat())
    in_use = {r["key"]: r for r in db.q("SELECT * FROM user_supplements WHERE status='em_uso'")}
    out = []
    for key, s in SHEETS.items():
        health_txt = conds + [diet]            # alergias costumam ser informadas em restrições alimentares
        contra = list(dict.fromkeys(txt for term, txt in s["contra"].items() if any(term_matches(term, c) for c in health_txt)))
        inter = [f"{m}: {txt}" for term, txt in s["interacoes"].items() for m in meds if term_matches(term, m)]
        # Triagem estruturada (decide); o texto livre acima só acrescenta
        if key == "cafeina":
            if fl["doenca_cardiovascular"] or fl["usa_medicamento_fc"]:
                contra.append("Doença cardiovascular ou medicamento que altera os batimentos: cafeína extra só com liberação médica.")
            if fl["usa_psiquiatrico_ou_estimulante"]:
                inter.append("Antidepressivo, lítio ou estimulante em uso: há interações relevantes (alguns elevam muito a cafeína "
                             "no sangue; estimulantes somam efeito). Confirme com médico ou farmacêutico.")
        if key == "omega-3":
            if fl["alergia_peixe"]:
                contra.append("Alergia a peixe ou frutos do mar: não usar óleo de peixe; opções de algas só com orientação.")
            if fl["usa_anticoagulante"]:
                inter.append("Anticoagulante ou antiagregante em uso: avaliação médica antes (risco de sangramento).")
        if key == "whey" and fl["alergia_leite"]:
            contra.append("Alergia a leite ou intolerância à lactose informada: whey concentrado não é adequado; alternativas "
                          "dependem do tipo de alergia e devem ser vistas com profissional.")
        if key == "magnesio" and fl["usa_levotiroxina"]:
            inter.append("Levotiroxina em uso: magnésio reduz a absorção; separar por pelo menos 4 horas, conforme orientação médica.")
        if key in ("creatina", "eletrolitos") and fl["usa_diuretico"]:
            inter.append("Diurético em uso: avaliação médica antes (hidratação, função renal e eletrólitos).")
        if key == "eletrolitos" and fl["doenca_cardiovascular"]:
            contra.append("Doença cardiovascular: sódio e potássio extras só com orientação médica.")
        if key == "vitamina d" and fl["usa_diuretico"]:
            inter.append("Alguns diuréticos (tiazídicos) elevam o cálcio; avaliação médica antes de vitamina D.")
        contra, inter = list(dict.fromkeys(contra)), list(dict.fromkeys(inter))
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
            today_ck = db.q1("SELECT caffeine_mg FROM checkins WHERE date=?", (day.isoformat(),))
            if hab is not None and today_ck and today_ck["caffeine_mg"] is not None:
                hab = max(hab, today_ck["caffeine_mg"])       # vale o maior entre o habitual e o já consumido hoje
            room = None if hab is None else 400 - hab
            if room is not None and room < 100:
                status, why = "inadequado", (f"Você já consome cerca de {hab:.0f} mg/dia; somar uma dose pré-treino passaria da "
                                             "referência de segurança de 400 mg/dia. Acrescentar não é indicado.")
            elif hab is None:
                status, why = "dados_insuficientes", "Informe no perfil a sua cafeína habitual (mg/dia) para avaliar a margem de segurança."
            elif goal in ("desempenho", "condicionamento", "forca"):
                dose = min(200, room, 3 * w) if w else min(200, room)
                status, why = "potencialmente_relevante", (
                    f"Pode ajudar em sessões-chave, se você tolera bem. Para o seu caso, a dose única fica limitada a cerca de "
                    f"{dose:.0f} mg (teto de 200 mg por dose e de 400 mg/dia somando os {hab:.0f} mg habituais), longe do horário de dormir.")
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
            lab = db.q1("SELECT value, unit, ref_low, ref_high, date FROM labs WHERE analyte_key='vitamina_d' ORDER BY date DESC LIMIT 1")
            old = lab and (day - date.fromisoformat(lab["date"])).days > 365
            desc = f"{lab['value']:g} {lab['unit'] or ''}".strip() if lab and lab["value"] is not None else ""
            if not lab or lab["value"] is None:
                status, why = "dados_insuficientes", "A indicação depende de exame de sangue (25-OH-vitamina D). Dados do relógio não avaliam vitamina D."
            elif old:
                status, why = "dados_insuficientes", f"Seu último exame ({lab['date']}, {desc}) tem mais de 12 meses e não reflete a situação atual. Converse com o médico sobre repetir."
            elif lab["ref_low"] is None and lab["ref_high"] is None:
                status, why = "requer_avaliacao", f"Seu exame de {lab['date']} ({desc}) foi registrado sem a faixa de referência do laboratório; o sistema não o interpreta. Leve ao médico."
            elif lab["ref_low"] is not None and lab["value"] < lab["ref_low"]:
                status, why = "requer_avaliacao", (f"Seu exame de {lab['date']} ({desc}) está abaixo da referência do "
                                                   "laboratório. Leve ao médico: a dose de correção é definida por ele.")
            elif lab["ref_high"] is not None and lab["value"] > lab["ref_high"]:
                status, why = "inadequado", f"Seu exame de {lab['date']} ({desc}) está acima da referência do laboratório: não suplemente e procure o médico."
            else:
                why = f"Seu exame de {lab['date']} está dentro da referência do laboratório; suplementar não trouxe benefício em ensaios com pessoas sem deficiência."
        elif key == "eletrolitos":
            long_ = db.q1("SELECT COUNT(*) n FROM workout_sessions WHERE date>=? AND duration_min>=90", ((day - timedelta(days=27)).isoformat(),))
            if long_ and long_["n"] >= 2:
                status, why = "potencialmente_relevante", f"Você registrou {long_['n']} sessões com 90 minutos ou mais nas últimas 4 semanas; nelas a reposição de sódio pode ajudar."
            else:
                why = "Você não registrou sessões de 90 minutos ou mais; água e alimentação normal são suficientes."
        if contra:
            status = "inadequado"
            why = "Contraindicação a partir do que você informou. " + why
        elif inter and status == "potencialmente_relevante":
            status = "requer_avaliacao"
            why = "Há possível interação com medicamento informado. " + why
        from . import kb
        bad_refs = kb.failing(s["refs"], cfg)
        if bad_refs and not contra:      # contraindicação informada prevalece; sem evidência verificada não há avaliação individual deste item
            status = "evidencia_nao_verificada"
            why = ("Avaliação individual desabilitada: a evidência que sustenta esta ficha ainda não foi verificada ("
                   + ", ".join(bad_refs) + ").")
        out.append({"chave": key, **{k: s[k] for k in ("nome", "finalidade", "populacao", "evidencia", "beneficios", "dose", "horario",
                                                      "adversos", "sono", "custo", "profissional", "refs")},
                    "status": status, "justificativa": why, "contraindicacoes_aplicaveis": contra,
                    "contraindicacoes_gerais": sorted(set(s["contra"].values())), "interacoes_aplicaveis": inter,
                    "interacoes_gerais": sorted(set(s["interacoes"].values())), "em_uso": key in in_use})
    order = {"inadequado": 0, "requer_avaliacao": 1, "potencialmente_relevante": 2, "dados_insuficientes": 3,
             "desnecessario": 4, "evidencia_nao_verificada": 5}
    out.sort(key=lambda x: order[x["status"]])
    return {"disponivel": True, "itens": out,
            "aviso": "Avaliação informativa baseada no que você informou. Interações listadas não são exaustivas: confirme com "
                     "médico ou farmacêutico. Confira no portal da Anvisa se o produto está regularizado (o sistema não verificou as normas vigentes). "
                     "Nenhum suplemento é necessário para ter resultado.",
            "refs_gerais": ["maughan2018"]}
