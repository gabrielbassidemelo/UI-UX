"""Treinador adaptativo: programa periodizado, progressão e ajuste diário.

Princípios (chaves em kb/evidencias.json):
- Volume semanal por grupo muscular e relação dose-resposta: schoenfeld2017volume.
- Distribuir o volume em 2×/semana por grupo é uma forma prática de organizar o treino, não um requisito
  (schoenfeld2016freq; com volume igual a frequência não mostrou efeito). Faixas de carga: schoenfeld2017load
  (estudos com séries até a falha).
- Intensidade por repetições em reserva (RIR/RPE): zourdos2016; proximidade da falha: refalo2023.
- Modelos de progressão e periodização: acsm2009. Aeróbico: garber2011, bull2020. HIIT: milanovic2015.
- Prevenção de fadiga excessiva: meeusen2013.
- A decisão diária NUNCA usa uma métrica isolada: combina sinais objetivos (vários indicadores
  contra a sua linha de base), percepção subjetiva, sono e carga recente (kiviniemi2007 usou HRV
  para guiar treino, mas aqui HRV é só um dos sinais).

Limites de segurança são constantes do código (SAFETY) e não são alterados automaticamente.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from . import analysis as an
from . import profile as prof
from .config import Config
from .db import Database, utcnow

SAFETY = {
    "max_dias_forca": {"iniciante": 4, "intermediario": 5, "avancado": 6},
    "max_series_grupo_semana": {"iniciante": 12, "intermediario": 18, "avancado": 22},
    "max_hiit_semana": 2,
    "max_aumento_carga_pct": 10.0,        # por progressão, em um exercício
    "max_sessoes_semana": 6,              # força + cardio: sempre sobra 1 dia sem treino estruturado
    "min_dias_descanso_semana": 1,
    "max_minutos_sessao": 120,
}
URGENT = ("Dor ou pressão no peito, desmaio, falta de ar intensa ou palpitações fortes são sinais de alerta: "
          "procure atendimento de urgência agora (SAMU 192). Não treine.")
STOP_SYMPTOMS = ("Interrompa o exercício e procure atendimento se sentir dor ou pressão no peito, falta de ar "
                 "desproporcional, tontura ou desmaio, palpitações incomuns ou dor articular aguda. Dor muscular "
                 "leve é esperada; dor aguda, não.")
READINESS_Z = 1.5
RED_FLAGS = ("dor no peito", "peito", "tontura", "desmaio", "febre", "falta de ar", "palpita")

# chave: (nome, grupo, padrão, equipamentos, evitar-se-limitação, dica técnica)
EX: dict[str, tuple[str, str, str, set[str], set[str], str]] = {
    "agachamento": ("Agachamento com barra", "quadriceps", "agachar", {"academia"}, {"joelho", "lombar"},
                    "Pés na largura dos ombros, tronco firme, desça até onde mantiver a coluna neutra."),
    "leg_press": ("Leg press", "quadriceps", "agachar", {"academia"}, set(),
                  "Não trave os joelhos no topo; mantenha a lombar apoiada."),
    "agachamento_goblet": ("Agachamento goblet", "quadriceps", "agachar", {"academia", "halteres"}, {"joelho"},
                           "Halter junto ao peito, cotovelos por dentro dos joelhos."),
    "agachamento_livre": ("Agachamento com peso corporal", "quadriceps", "agachar", {"academia", "halteres", "peso_corporal"}, set(),
                          "Descida controlada em 2–3 s."),
    "afundo": ("Afundo/passada", "quadriceps", "unilateral", {"academia", "halteres", "peso_corporal"}, {"joelho"},
               "Passo longo, tronco ereto, joelho da frente alinhado ao pé."),
    "cadeira_extensora": ("Cadeira extensora", "quadriceps", "isolado_perna", {"academia"}, set(),
                          "Suba em 1 s, segure, desça em 2–3 s."),
    "terra_romeno": ("Levantamento terra romeno", "posterior", "dobradica", {"academia", "halteres"}, {"lombar"},
                     "Quadril para trás, joelhos levemente flexionados, coluna neutra."),
    "mesa_flexora": ("Mesa/cadeira flexora", "posterior", "dobradica", {"academia"}, set(),
                     "Movimento controlado, sem tirar o quadril do apoio."),
    "elevacao_pelvica": ("Elevação pélvica (hip thrust)", "gluteos", "dobradica", {"academia", "halteres", "peso_corporal"}, set(),
                         "Queixo recolhido, costelas para baixo, pausa de 1 s no topo."),
    "supino": ("Supino reto com barra", "peito", "empurrar_h", {"academia"}, {"ombro"},
               "Escápulas retraídas, barra desce até o meio do peito."),
    "supino_halteres": ("Supino com halteres", "peito", "empurrar_h", {"academia", "halteres"}, set(),
                        "Cotovelos a ~45° do tronco."),
    "flexao": ("Flexão de braços", "peito", "empurrar_h", {"academia", "halteres", "peso_corporal"}, {"punho"},
               "Corpo em linha; use joelhos apoiados ou inclinação se necessário."),
    "supino_inclinado": ("Supino inclinado com halteres", "peito", "empurrar_h", {"academia", "halteres"}, {"ombro"},
                         "Banco a 30°; amplitude confortável para o ombro."),
    "desenvolvimento": ("Desenvolvimento com halteres", "ombros", "empurrar_v", {"academia", "halteres"}, {"ombro"},
                        "Costelas para baixo, sem arquear a lombar."),
    "elevacao_lateral": ("Elevação lateral", "ombros", "isolado_ombro", {"academia", "halteres"}, set(),
                         "Suba até a linha dos ombros, cotovelos levemente flexionados."),
    "pike_flexao": ("Flexão pike", "ombros", "empurrar_v", {"peso_corporal", "halteres", "academia"}, {"ombro", "punho"},
                    "Quadril alto, cabeça desce entre as mãos."),
    "puxada": ("Puxada alta (pulley)", "costas", "puxar_v", {"academia"}, set(),
               "Puxe os cotovelos para baixo, peito aberto."),
    "barra_fixa": ("Barra fixa (ou assistida)", "costas", "puxar_v", {"academia", "peso_corporal"}, {"ombro"},
                   "Inicie o movimento pelas escápulas."),
    "remada_curvada": ("Remada curvada com barra", "costas", "puxar_h", {"academia"}, {"lombar"},
                       "Tronco inclinado, coluna neutra, barra em direção ao umbigo."),
    "remada_halter": ("Remada unilateral com halter", "costas", "puxar_h", {"academia", "halteres"}, set(),
                      "Apoie mão e joelho no banco; puxe o cotovelo para trás."),
    "remada_baixa": ("Remada baixa (polia)", "costas", "puxar_h", {"academia"}, set(),
                     "Tronco estável; não balance."),
    "remada_invertida": ("Remada invertida", "costas", "puxar_h", {"peso_corporal", "academia"}, set(),
                         "Corpo em linha, peito até a barra/mesa."),
    "rosca": ("Rosca direta", "biceps", "isolado_braco", {"academia", "halteres"}, set(),
              "Cotovelos fixos ao lado do corpo."),
    "triceps_polia": ("Tríceps na polia", "triceps", "isolado_braco", {"academia"}, set(),
                      "Cotovelos fixos; estenda completamente."),
    "triceps_frances": ("Tríceps francês com halter", "triceps", "isolado_braco", {"academia", "halteres"}, {"ombro"},
                        "Cotovelos apontando para cima."),
    "mergulho_banco": ("Mergulho no banco", "triceps", "isolado_braco", {"peso_corporal", "halteres", "academia"}, {"ombro"},
                       "Amplitude curta e confortável para o ombro."),
    "panturrilha": ("Elevação de panturrilha", "panturrilhas", "isolado_perna", {"academia", "halteres", "peso_corporal"}, set(),
                    "Amplitude completa, pausa no topo e embaixo."),
    "prancha": ("Prancha", "core", "core", {"academia", "halteres", "peso_corporal"}, set(),
                "Glúteos e abdômen contraídos; 30–60 s."),
    "dead_bug": ("Dead bug", "core", "core", {"academia", "halteres", "peso_corporal"}, set(),
                 "Lombar colada ao chão; movimentos lentos."),
    "abdominal_polia": ("Abdominal na polia", "core", "core", {"academia"}, {"lombar"},
                        "Flexione a coluna, não o quadril."),
}
PATTERN_ORDER = {  # preferência por padrão (do mais ao menos exigente)
    "agachar": ["agachamento", "leg_press", "agachamento_goblet", "agachamento_livre"],
    "unilateral": ["afundo"],
    "dobradica": ["terra_romeno", "elevacao_pelvica", "mesa_flexora"],
    "empurrar_h": ["supino", "supino_halteres", "supino_inclinado", "flexao"],
    "empurrar_v": ["desenvolvimento", "pike_flexao"],
    "puxar_v": ["puxada", "barra_fixa"],
    "puxar_h": ["remada_curvada", "remada_halter", "remada_baixa", "remada_invertida"],
    "isolado_ombro": ["elevacao_lateral"],
    "isolado_braco": ["rosca", "triceps_polia", "triceps_frances", "mergulho_banco"],
    "isolado_perna": ["cadeira_extensora", "panturrilha"],
    "core": ["prancha", "dead_bug", "abdominal_polia"],
}
TEMPLATES: dict[int, list[tuple[str, list[str]]]] = {
    2: [("Corpo inteiro A", ["agachar", "empurrar_h", "puxar_h", "dobradica", "core"]),
        ("Corpo inteiro B", ["dobradica", "empurrar_v", "puxar_v", "unilateral", "core"])],
    3: [("Corpo inteiro A", ["agachar", "empurrar_h", "puxar_h", "isolado_braco", "core"]),
        ("Corpo inteiro B", ["dobradica", "empurrar_v", "puxar_v", "unilateral", "core"]),
        ("Corpo inteiro C", ["agachar", "empurrar_h", "puxar_h", "isolado_ombro", "isolado_perna"])],
    4: [("Superiores A", ["empurrar_h", "puxar_h", "empurrar_v", "puxar_v", "isolado_braco"]),
        ("Inferiores A", ["agachar", "dobradica", "unilateral", "isolado_perna", "core"]),
        ("Superiores B", ["puxar_v", "empurrar_h", "puxar_h", "isolado_ombro", "isolado_braco"]),
        ("Inferiores B", ["dobradica", "agachar", "isolado_perna", "unilateral", "core"])],
}
TEMPLATES[1] = TEMPLATES[2][:1]
TEMPLATES[5] = TEMPLATES[4] + [("Corpo inteiro (ênfase em pontos fracos)", ["empurrar_h", "puxar_h", "agachar", "isolado_ombro", "core"])]
TEMPLATES[6] = TEMPLATES[4] + [("Superiores C", ["empurrar_v", "puxar_h", "isolado_ombro", "isolado_braco", "core"]),
                               ("Inferiores C", ["agachar", "dobradica", "isolado_perna", "core"])]
WEEKDAYS = {1: [0], 2: [0, 3], 3: [0, 2, 4], 4: [0, 1, 3, 4], 5: [0, 1, 2, 4, 5], 6: [0, 1, 2, 3, 4, 5]}
GOAL = {  # reps compostos, reps acessórios, RIR base, descanso compostos/acessórios (s), séries, objetivo fisiológico
    "hipertrofia": ((6, 10), (10, 15), 2, (150, 75), 3, "Tensão mecânica com volume suficiente para hipertrofia"),
    "forca": ((3, 6), (6, 10), 2, (210, 120), 4, "Adaptações neurais e força máxima com cargas altas"),
    "emagrecimento": ((8, 12), (10, 15), 2, (120, 60), 3, "Preservar massa magra durante o déficit calórico"),
    "recomposicao": ((6, 10), (10, 15), 2, (150, 75), 3, "Estímulo de hipertrofia em manutenção calórica"),
    "condicionamento": ((8, 12), (12, 15), 3, (90, 60), 2, "Manter força como base para o trabalho aeróbico"),
    "saude": ((8, 12), (10, 15), 3, (120, 60), 2, "Força funcional e saúde musculoesquelética"),
}
GOAL["manutencao"] = GOAL["saude"]
GOAL["desempenho"] = GOAL["forca"]
MESO = [("Semana 1 — adaptação", 0, 1.0), ("Semana 2 — progressão", -1, 1.0),
        ("Semana 3 — pico de volume", -1, 1.0), ("Semana 4 — deload (redução planejada)", 2, 0.6)]


def pick_exercise(pattern: str, equipment: str, limitations: list[str], used: set[str],
                  week_used: set[str] | None = None) -> tuple[str, list[str]]:
    """Escolhe um exercício compatível com equipamento e limitações. Se nenhum for seguro para a
    limitação informada, o padrão é omitido (não se força um exercício contraindicado)."""
    lim = {x.lower() for x in limitations}
    ok = [k for k in PATTERN_ORDER[pattern] if equipment in EX[k][3] and not (EX[k][4] & lim) and k not in used]
    if not ok:
        return "", []
    choice = next((k for k in ok if k not in (week_used or set())), ok[0])
    return choice, [EX[k][0] for k in ok if k != choice][:2]


def hr_zones(db: Database, cfg: Config, day: date | None = None) -> dict[str, Any]:
    p = prof.get(db)
    a = prof.age(p, day)
    if p.get("fc_max_medida"):
        hmax, src = p["fc_max_medida"], "FC máxima medida"
    elif a is not None:
        hmax, src = 208 - 0.7 * a, "estimativa de Tanaka (208 − 0,7 × idade); o desvio-padrão é de ~10 bpm, então o valor real pode diferir em até ~20 bpm"
    else:
        return {"disponivel": False, "faltam": ["nascimento ou fc_max_medida"]}
    rows = db.q("SELECT resting_hr FROM daily_metrics WHERE resting_hr IS NOT NULL ORDER BY date DESC LIMIT 14")
    if not rows:
        return {"disponivel": False, "faltam": ["FC de repouso medida pela pulseira (nenhum valor presumido é usado)"]}
    rest = sorted(r["resting_hr"] for r in rows)[len(rows) // 2]
    names = [("Z1", .3, .4, "muito leve, recuperação"), ("Z2", .4, .6, "moderado: dá para conversar"),
             ("Z3", .6, .75, "vigoroso sustentável"), ("Z4", .75, .9, "forte, perto do limiar"), ("Z5", .9, 1.0, "máximo")]
    meds = " ".join(str(m).lower() for m in p.get("medicamentos", []))
    warn = ("Você usa medicamento ou dispositivo que altera a frequência cardíaca (betabloqueador, verapamil, diltiazem, "
            "antiarrítmico, marca-passo): zonas por batimentos não se aplicam; guie-se pela percepção de esforço e pela "
            "orientação do seu médico."
            if p.get("usa_medicamento_fc") or any(x in meds for x in ("olol", "betabloq", "carvedilol", "selozok", "concor",
                                                                    "verapamil", "diltiazem", "amiodarona", "marca-passo", "marcapasso")) else None)
    return {"disponivel": True, "fc_max": round(hmax), "fc_repouso": round(rest), "origem": src, "aviso": warn,
            "metodo": "reserva de frequência cardíaca (Karvonen)", "refs": ["tanaka2001", "garber2011"],
            "zonas": [{"zona": n, "de": round(rest + lo * (hmax - rest)), "ate": round(rest + hi * (hmax - rest)),
                       "uso": u} for n, lo, hi, u in names]}


def _cardio(goal: str, experience: str, cleared: bool, strength_days: int) -> list[dict[str, Any]]:
    out = []
    z2 = {"condicionamento": (3, 40), "emagrecimento": (3, 35), "saude": (3, 30), "desempenho": (2, 40)}.get(goal, (2, 25))
    for i in range(z2[0]):
        out.append({"tipo": "Aeróbico Z2", "duracao_min": z2[1], "intensidade": "Zona 2: esforço moderado, dá para conversar",
                    "objetivo": "Base aeróbica e saúde cardiometabólica", "refs": ["garber2011", "bull2020"]})
    out = out[:max(0, SAFETY["max_sessoes_semana"] - strength_days)]
    if out and goal in ("condicionamento", "desempenho") and experience != "iniciante" and cleared and SAFETY["max_hiit_semana"] >= 1:
        out[-1] = {"tipo": "HIIT", "duracao_min": 25, "intensidade": "4–6 × 3–4 min em Z4–Z5, recuperação igual em Z1",
                   "objetivo": "VO2 máx.", "refs": ["milanovic2015"],
                   "condicao": "Somente com recuperação normal; nunca em dias consecutivos."}
    return out


def build_plan(db: Database, cfg: Config, start: date | None = None) -> dict[str, Any]:
    p = prof.get(db)
    miss = prof.missing(db, "treino")["treino"]
    if miss:
        raise ValueError("Complete o perfil antes de gerar o plano: " + ", ".join(miss))
    from . import safety
    scr = safety.screen(db, cfg, start or date.today())
    stop = safety.blocked(scr, "treino_plano")
    if stop:
        raise ValueError(stop)
    from . import kb
    bad_refs = kb.failing(["acsm2009", "schoenfeld2017volume", "zourdos2016", "garber2011", "bull2020"], cfg)
    if bad_refs:
        raise ValueError("Plano personalizado desabilitado: referências científicas ainda não verificadas ("
                         + ", ".join(bad_refs) + ").")
    exp, goal = p["experiencia"], p["objetivo"]
    intense_block = safety.blocked(scr, "treino_intenso")
    cleared = not intense_block and not p.get("condicoes_saude")
    old = bool(scr.get("idoso"))
    notes = list(scr["avisos"])
    if intense_block:
        notes.append(intense_block)
    days = min(p["dias_treino"], SAFETY["max_dias_forca"][exp], 7 - SAFETY["min_dias_descanso_semana"])
    if days < p["dias_treino"]:
        notes.append(f"Dias de força limitados a {days}/semana para o nível {exp} (limite de segurança).")
    if not cleared:
        notes.append("Sem liberação médica registrada ou com condição de saúde informada: plano restrito a "
                     "intensidade moderada, sem HIIT. Faça uma avaliação médica antes de iniciar ou intensificar "
                     "(um questionário de prontidão como o PAR-Q+ ajuda nessa conversa).")
    comp, acc, rir, rest, sets, objetivo = GOAL[goal]
    if not cleared:
        rir = max(rir, 3)
    elif old:
        rir = max(rir, 2)
    minutes = min(p["minutos_sessao"], SAFETY["max_minutos_sessao"])
    max_ex = max(3, min(7, int((minutes - 8) // 8)))
    sessions = []
    week_used: set[str] = set()
    skipped: set[str] = set()
    for name, patterns in TEMPLATES[days]:
        used: set[str] = set()
        items = []
        for pat in patterns + (["isolado_perna", "core"] if "Inferiores" in name else ["isolado_braco", "core"]):
            if len(items) >= max_ex:
                break
            k, alts = pick_exercise(pat, p["equipamento"], p.get("limitacoes", []), used, week_used)
            if not k:
                skipped.add(pat)
                continue
            used.add(k)
            week_used.add(k)
            compound = not pat.startswith("isolado") and pat != "core"
            lo, hi = comp if compound else acc
            items.append({"chave": k, "exercicio": EX[k][0], "grupo": EX[k][1], "series": sets if compound else max(2, sets - 1),
                          "reps": [lo, hi], "rir": rir, "descanso_s": rest[0] if compound else rest[1],
                          "tecnica": EX[k][5], "alternativas": alts})
        dur = 8 + sum(it["series"] * (it["descanso_s"] + 40) for it in items) / 60
        sessions.append({"nome": name, "exercicios": items, "duracao_min": round(dur),
                         "aquecimento": "5–8 min leves + 1–2 séries de aproximação no primeiro exercício",
                         "objetivo": objetivo})
    if skipped & {"empurrar_v", "empurrar_h", "agachar", "dobradica", "puxar_v", "puxar_h", "unilateral"}:
        notes.append("Alguns padrões de movimento foram omitidos por causa das limitações informadas "
                     f"({', '.join(p.get('limitacoes', []))}). Um fisioterapeuta ou professor pode indicar adaptações.")
    # teto de séries semanais por grupo
    cap = SAFETY["max_series_grupo_semana"][exp]
    vol: dict[str, int] = {}
    for s in sessions:
        kept = []
        for it in s["exercicios"]:
            room = cap - vol.get(it["grupo"], 0)
            if room <= 0:
                continue                      # teto semanal do grupo atingido: exercício removido
            it["series"] = min(it["series"], room)
            vol[it["grupo"]] = vol.get(it["grupo"], 0) + it["series"]
            kept.append(it)
        s["exercicios"] = kept
        s["duracao_min"] = round(8 + sum(i["series"] * (i["descanso_s"] + 40) for i in kept) / 60)
    cardio = _cardio(goal, exp, cleared and not old, days)
    start = start or date.today()
    start -= timedelta(days=start.weekday())
    plan = {
        "inicio": start.isoformat(), "objetivo": goal, "experiencia": exp, "dias_forca": days,
        "dias_semana": WEEKDAYS[days], "sessoes": sessions, "cardio": cardio,
        "mesociclo": [{"nome": n, "ajuste_rir": d, "fator_series": f} for n, d, f in MESO],
        "series_semanais_por_grupo": vol, "mobilidade": "5–10 min de mobilidade de quadril, coluna torácica e "
                                                        "ombros ao fim das sessões ou nos dias de descanso.",
        "progressao": "Dupla progressão: primeiro complete o topo da faixa de repetições em todas as séries com "
                      "o RIR alvo; depois aumente a carga em 2,5–5% e volte à base da faixa.",
        "rir_minimo": 3 if not cleared else 2 if old else 0, "seguranca": STOP_SYMPTOMS, "observacoes": notes,
        "equilibrio": ("Inclua 2–3 vezes por semana exercícios de equilíbrio (apoio em uma perna perto de um suporte, "
                       "caminhada em linha, levantar e sentar da cadeira): reduzem o risco de quedas.") if old else None,
        "refs": ["acsm2009", "schoenfeld2017volume", "schoenfeld2016freq", "zourdos2016", "garber2011"],
    }
    return plan


def propose_plan(db: Database, cfg: Config, reason: str = "novo plano", start: date | None = None) -> int:
    """Mudanças de planejamento ficam como 'proposto' até você confirmar."""
    plan = build_plan(db, cfg, start)
    db.x("UPDATE training_plans SET status='recusado' WHERE status='proposto'")
    return db.insert("training_plans", {"created_at": utcnow(), "status": "proposto", "reason": reason,
                                        "config": json.dumps(prof.get(db), ensure_ascii=False),
                                        "plan": json.dumps(plan, ensure_ascii=False)})


def confirm_plan(db: Database, plan_id: int) -> None:
    if not db.q1("SELECT 1 FROM training_plans WHERE id=? AND status='proposto'", (plan_id,)):
        raise ValueError("Plano não encontrado ou já decidido")
    db.x("UPDATE training_plans SET status='arquivado' WHERE status='ativo'")
    db.x("UPDATE training_plans SET status='ativo', confirmed_at=? WHERE id=?", (utcnow(), plan_id))


def get_plan(db: Database, status: str = "ativo") -> dict[str, Any] | None:
    r = db.q1("SELECT * FROM training_plans WHERE status=? ORDER BY id DESC LIMIT 1", (status,))
    if not r:
        return None
    return {"id": r["id"], "status": r["status"], "criado_em": r["created_at"], "motivo": r["reason"],
            **json.loads(r["plan"])}


# ------------------------------------------------------------------ registros
def log_set(db: Database, day: str, exercise: str, reps: float, load_kg: float, rir: float | None = None,
            session: str | None = None) -> int:
    if not (0 < reps <= 100 and 0 <= load_kg <= 600) or (rir is not None and not 0 <= rir <= 10):
        raise ValueError("Valores de série implausíveis")
    n = db.q1("SELECT COUNT(*) n FROM workout_sets WHERE date=? AND exercise=?", (day, exercise))["n"]
    return db.insert("workout_sets", {"date": day, "session": session, "exercise": exercise, "set_no": n + 1,
                                      "reps": reps, "load_kg": load_kg, "rir": rir, "created_at": utcnow()})


def log_session(db: Database, day: str, session: str, kind: str, duration_min: float | None, rpe: float | None,
                time_of_day: str | None = None, notes: str | None = None) -> int:
    if rpe is not None and not 1 <= rpe <= 10:
        raise ValueError("RPE deve estar entre 1 e 10")
    return db.insert("workout_sessions", {"date": day, "session": session, "kind": kind, "duration_min": duration_min,
                                          "rpe": rpe, "time_of_day": time_of_day, "notes": notes, "created_at": utcnow()})


def e1rm(load: float, reps: float) -> float:
    return load * (1 + reps / 30)      # Epley; estimativa, menos confiável acima de ~10 repetições


def progression(db: Database, exercise_key: str, reps_range: list[int], target_rir: int) -> dict[str, Any]:
    name = EX[exercise_key][0] if exercise_key in EX else exercise_key
    last = db.q1("SELECT date FROM workout_sets WHERE exercise IN (?,?) ORDER BY date DESC LIMIT 1", (exercise_key, name))
    if not last:
        return {"acao": "definir", "texto": f"Sem registro: escolha uma carga que permita {reps_range[0]}–{reps_range[1]} "
                                            f"repetições terminando com {target_rir} de reserva."}
    sets = db.q("SELECT reps, load_kg, rir FROM workout_sets WHERE exercise IN (?,?) AND date=? ORDER BY set_no",
                (exercise_key, name, last["date"]))
    load = max(s["load_kg"] for s in sets)
    lo, hi = reps_range
    rirs = [s["rir"] for s in sets if s["rir"] is not None]
    easy = not rirs or min(rirs) >= target_rir
    if all(s["reps"] >= hi for s in sets) and easy:
        inc = max(1.0, round(load * 0.025 * 2) / 2) if load else 0
        inc = min(inc, load * SAFETY["max_aumento_carga_pct"] / 100) if load else 0
        return {"acao": "aumentar", "carga_kg": round(load + inc, 1), "ultima": load, "data": last["date"],
                "texto": f"Você completou o topo da faixa em todas as séries: suba para {load + inc:.1f} kg e volte a {lo} repetições."}
    if any(s["reps"] < lo for s in sets):
        return {"acao": "reduzir", "carga_kg": round(load * 0.95, 1), "ultima": load, "data": last["date"],
                "texto": f"Séries abaixo de {lo} repetições: reduza ~5% ({load * 0.95:.1f} kg) e reconstrua."}
    return {"acao": "manter", "carga_kg": load, "ultima": load, "data": last["date"],
            "texto": f"Mantenha {load:.1f} kg e tente somar repetições até {hi} em todas as séries."}


def exercise_history(db: Database, exercise: str, limit: int = 60) -> list[dict[str, Any]]:
    rows = db.q("SELECT date, MAX(load_kg) carga, MAX(load_kg*(1+reps/30.0)) e1rm, SUM(reps*load_kg) volume "
                "FROM workout_sets WHERE exercise=? GROUP BY date ORDER BY date DESC LIMIT ?", (exercise, limit))
    return list(reversed(rows))


# ------------------------------------------------------------ decisão diária
def readiness(db: Database, cfg: Config, day: date) -> dict[str, Any]:
    """Combina sinais independentes; nenhum deles decide sozinho."""
    w, mn = int(cfg.get("analise", "janela_base_dias", 28)), int(cfg.get("analise", "minimo_dias_base", 10))
    store = an.load_store(db, day, w + 60)
    rec = an.recovery(store, day, w, mn)
    signals, score, sources = [], 0, 0
    # Sinais objetivos INDEPENDENTES. Body Battery e prontidão do Garmin são índices compostos de HRV e sono
    # e por isso não entram na conta (evita contar a mesma informação duas ou três vezes). Um grupo só conta
    # se o desvio do dia for ≥ 1,5 desvio da sua base ou se o padrão for persistente.
    groups = {"HRV": ["hrv_last_night"], "FC de repouso": ["resting_hr"], "Sono": ["sleep_score", "sleep_seconds"],
              "Estresse durante o sono": ["sleep_stress_avg"]}
    bad, seen = [], 0
    for label, keys in groups.items():
        hit = False
        for k in keys:
            a_ = an.assess(store, k, day, w, mn)
            if a_.z is None or not a_.base.confiavel:
                continue
            seen += 1
            if a_.leitura == "desfavoravel" and (abs(a_.z) >= READINESS_Z or a_.padrao == "persistente"):
                hit = True
        if hit:
            bad.append(label)
    if seen:
        sources += 1
        if len(bad) >= 3 or {"HRV", "FC de repouso"} <= set(bad):     # HRV baixa junto com FC de repouso alta pesa mais
            score += 2
            signals.append(f"Indicadores objetivos: {len(bad)} grupos independentes fora do seu padrão ({', '.join(bad)})")
        elif len(bad) == 2:
            score += 1
            signals.append(f"Indicadores objetivos: 2 grupos fora do seu padrão ({', '.join(bad)})")
        elif len(bad) == 1:
            signals.append(f"Um único indicador fora do padrão ({bad[0]}): isoladamente não altera o treino")
        if rec.persistente and len(bad) >= 2:
            score += 1
            signals.append("Padrão desfavorável persistente (3 dos últimos 4 dias)")
    ck = db.q1("SELECT * FROM checkins WHERE date=?", (day.isoformat(),))
    red = False
    if ck:
        sources += 1
        pr = ck.get("perceived_recovery")
        if pr is not None:
            if pr <= 4:
                score += 2
                signals.append(f"Você relatou recuperação baixa ({pr:.0f}/10)")
            elif pr <= 6:
                score += 1
                signals.append(f"Você relatou recuperação mediana ({pr:.0f}/10)")
            else:
                signals.append(f"Você relatou boa recuperação ({pr:.0f}/10)")
        if (ck.get("soreness") or 0) >= 4:
            score += 1
            signals.append("Dor muscular alta relatada")
    from . import safety
    flags = safety.symptom_flags(ck)
    red = any(flags.values())
    urgent = any(flags[k] for k, (_, urg) in safety.SYMPTOMS.items() if urg)
    answered = safety.triage_answered(ck)
    if red:
        signals.insert(0, "Sintomas de alerta relatados hoje: " + ", ".join(safety.SYMPTOMS[k][0] for k, v in flags.items() if v))
    sleep = store.value("sleep_seconds", day)
    if sleep is not None and "Sono" not in bad:        # não contar o sono duas vezes
        if sleep < 6 * 3600:
            score += 1
            signals.append(f"Sono curto na última noite ({sleep / 3600:.1f} h)")
    load7 = sum(v for _, v in store.values("training_load", day - timedelta(days=6), day))
    load28 = sum(v for _, v in store.values("training_load", day - timedelta(days=27), day)) / 4
    if load28 > 0 and load7 / load28 > 1.5:
        score += 1
        signals.append(f"Carga dos últimos 7 dias {load7 / load28:.1f}× a sua média semanal (sinal auxiliar)")
    if red:
        level = "parar"
    elif score >= 4:
        level = "recuperacao"
    elif score >= 2:
        level = "reduzido"
    else:
        level = "normal"
    conf = "alta" if sources >= 2 and seen >= 3 else "média" if sources >= 1 else "baixa"
    return {"nivel": level, "pontos": score, "sinais": signals, "confianca": conf, "fontes": sources,
            "tem_checkin": bool(ck), "triagem_respondida": answered, "sintomas": [k for k, v in flags.items() if v],
            "urgente": urgent, "recuperacao_objetiva": rec.status,
            "regra": "0–1 ponto: treino normal; 2–3: volume/intensidade reduzidos; ≥4: recuperação ativa ou descanso; "
                     "sintomas de alerta: não treinar. Contam 4 grupos objetivos independentes (HRV, FC de repouso, sono, "
                     "estresse no sono), a sua percepção, sono curto e pico de carga; nenhum decide sozinho. "
                     "É uma regra de decisão transparente, não validada em estudo.",
            "refs": ["kiviniemi2007", "meeusen2013", "zourdos2016"]}


def todays_session(db: Database, cfg: Config, day: date) -> dict[str, Any]:
    plan = get_plan(db)
    ready = readiness(db, cfg, day)
    out: dict[str, Any] = {"dia": day.isoformat(), "prontidao": ready, "seguranca": STOP_SYMPTOMS}
    if ready["nivel"] == "parar":
        if ready["urgente"]:
            txt = URGENT
        elif "febre" in ready["sintomas"]:
            txt = ("Com febre ou sinais de infecção, não treine: descanse e hidrate-se. Volte de forma gradual quando os "
                   "sintomas passarem; procure avaliação profissional se piorar ou não melhorar em poucos dias.")
        else:
            txt = ("Dor aguda, inchaço ou lesão nova: não treine a região afetada hoje. Se a dor for forte, houver inchaço "
                   "importante ou não melhorar, procure avaliação profissional antes de retomar.")
        out.update(tipo="descanso", titulo="Não treine hoje", texto=txt)
        return out
    # A triagem é reavaliada TODO dia: um plano confirmado antes de um bloqueio deixa de valer.
    from . import safety
    scr = safety.screen(db, cfg, day)
    stop = safety.blocked(scr, "treino_plano")
    if stop:
        out.update(tipo="bloqueado", titulo="Plano de treino suspenso", texto=stop, bloqueio=stop)
        return out
    no_intense = safety.blocked(scr, "treino_intenso")
    if not plan:
        out.update(tipo="sem_plano", titulo="Sem plano de treino ativo",
                   texto="Complete o perfil e confirme um plano em Treino.")
        return out
    start = date.fromisoformat(plan["inicio"])
    week = max(0, (day - start).days // 7) % 4
    meso = plan["mesociclo"][week]
    out["semana"] = meso["nome"]
    if day.weekday() not in plan["dias_semana"]:
        cardio = plan["cardio"]
        rest_days = [d_ for d_ in range(7) if d_ not in plan["dias_semana"]]
        idx = rest_days.index(day.weekday())
        if idx < len(cardio) and ready["nivel"] != "recuperacao":
            c = dict(cardio[idx])
            if c["tipo"] == "HIIT" and (ready["nivel"] != "normal" or not ready["triagem_respondida"] or no_intense):
                c = {"tipo": "Aeróbico Z2", "duracao_min": 30, "intensidade": "Zona 2: esforço moderado", "objetivo": "Base aeróbica",
                     "refs": ["garber2011"], "ajuste": (no_intense if no_intense else
                                                       "HIIT trocado por Z2: recuperação abaixo do ideal." if ready["nivel"] != "normal"
                                                       else "HIIT só é liberado depois que você responde a triagem de sintomas do dia (check-in).")}
            out.update(tipo="cardio", titulo=c["tipo"], cardio=c)
        else:
            out.update(tipo="descanso", titulo="Descanso ou recuperação ativa",
                       texto="Caminhada leve (Z1, 20–30 min) e mobilidade.")
        return out
    session = json.loads(json.dumps(plan["sessoes"][plan["dias_semana"].index(day.weekday()) % len(plan["sessoes"])]))
    factor, rir_adj, changes = meso["fator_series"], meso["ajuste_rir"], []
    from . import calibration
    stage_ok = calibration.stage(db, day)["permite"]["progressao_de_carga"]
    if not ready["triagem_respondida"]:
        changes.append("Responda a triagem de sintomas no check-in de hoje: sem ela o sistema não sugere aumento de carga nem HIIT.")
    elif not stage_ok:
        changes.append("Período de calibração: o sistema ainda não sugere aumento de carga; registre as cargas usadas.")
    if no_intense:
        rir_adj = max(rir_adj, 0)
        changes.append(no_intense + " Hoje: sem aumento de carga e com pelo menos 3 repetições de reserva.")
    if ready["nivel"] == "recuperacao":
        out.update(tipo="recuperacao", titulo="Recuperação ativa no lugar de " + session["nome"],
                   texto="Vários sinais independentes indicam recuperação insuficiente: troque por caminhada leve "
                         "(Z1, 20–30 min) e mobilidade, ou descanse. A sessão planejada fica para o próximo dia.")
        return out
    if ready["nivel"] == "reduzido":
        if factor < 1:      # semana de deload já reduz volume e intensidade: não acumular reduções
            changes.append("Semana de deload: a redução planejada já cobre os sinais de recuperação incompleta.")
        else:
            factor, rir_adj = 0.65, rir_adj + 1
            changes.append("Volume reduzido em ~35% e 1 repetição a mais de reserva (sinais de recuperação incompleta).")
    for it in session["exercicios"]:
        it["series"] = max(2 if it["series"] >= 3 else 1, round(it["series"] * factor))
        it["rir"] = max(plan.get("rir_minimo", 0), 3 if no_intense else 0, min(5, it["rir"] + rir_adj))
        can_progress = ready["nivel"] == "normal" and factor >= 1 and ready["triagem_respondida"] and stage_ok and not no_intense
        it["progressao"] = (progression(db, it["chave"], it["reps"], it["rir"]) if can_progress
                            else {"acao": "manter", "texto": "Hoje não é dia de aumentar carga: repita a última carga."})
    session["duracao_min"] = round(8 + sum(i["series"] * (i["descanso_s"] + 40) for i in session["exercicios"]) / 60)
    out.update(tipo="forca", titulo=session["nome"], sessao=session, ajustes=changes)
    return out


def weekly_summary(db: Database, end: date) -> dict[str, Any]:
    start = end - timedelta(days=6)
    sets = db.q("SELECT exercise, COUNT(*) series, SUM(reps*load_kg) volume FROM workout_sets WHERE date BETWEEN ? AND ? "
                "GROUP BY exercise", (start.isoformat(), end.isoformat()))
    ses = db.q("SELECT * FROM workout_sessions WHERE date BETWEEN ? AND ? ORDER BY date", (start.isoformat(), end.isoformat()))
    by_group: dict[str, int] = {}
    inv = {v[0]: v[1] for v in EX.values()} | {k: v[1] for k, v in EX.items()}
    for s in sets:
        g = inv.get(s["exercise"], "outros")
        by_group[g] = by_group.get(g, 0) + s["series"]
    return {"periodo": [start.isoformat(), end.isoformat()], "sessoes": ses, "series_por_grupo": by_group,
            "volume_total_kg": round(sum(s["volume"] or 0 for s in sets))}
