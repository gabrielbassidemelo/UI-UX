"""Plano diário: responde às perguntas do dia juntando os módulos, sempre com
o que fazer, por quê, dados usados, evidência, grau de confiança e limitações."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import analysis as an
from . import insights as ins
from . import kb, learning, metabolism as met, nutrition as nut, profile as prof, stats, training as tr
from .config import Config
from .db import Database
from .metrics import fmt

GOAL_METRICS = {
    "peso": ("Peso", "kg"), "gordura": ("Gordura corporal", "%"), "cintura": ("Cintura", "cm"),
    "vo2max": ("VO2 máx. estimado", "ml/kg/min"), "passos": ("Passos por dia (média 7 dias)", ""),
    "sono": ("Sono (média 7 dias)", "h"), "fc_repouso": ("FC de repouso (média 7 dias)", "bpm"),
}


def _metric_value(db: Database, metric: str, day: date) -> float | None:
    d7 = (day - timedelta(days=6)).isoformat()
    if metric in ("peso", "gordura", "cintura"):
        col = {"peso": "weight_kg", "gordura": "body_fat_pct", "cintura": "waist_cm"}[metric]
        r = db.q1(f"SELECT AVG({col}) v FROM (SELECT {col} FROM body_measurements WHERE {col} IS NOT NULL AND date<=? "
                  "ORDER BY date DESC LIMIT 3)", (day.isoformat(),))
        return r["v"] if r else None
    if metric.startswith("e1rm:"):
        r = db.q1("SELECT MAX(load_kg*(1+reps/30.0)) v FROM workout_sets WHERE exercise=? AND date>=?",
                  (metric[5:], (day - timedelta(days=27)).isoformat()))
        return r["v"] if r else None
    col, scale = {"vo2max": ("vo2max", 1), "passos": ("steps", 1), "sono": ("sleep_seconds", 1 / 3600),
                  "fc_repouso": ("resting_hr", 1)}[metric]
    r = db.q1(f"SELECT AVG({col}) v FROM daily_metrics WHERE {col} IS NOT NULL AND date BETWEEN ? AND ?", (d7, day.isoformat()))
    return r["v"] * scale if r and r["v"] is not None else None


def add_goal(db: Database, metric: str, target: float, deadline: str, day: date | None = None) -> dict[str, Any]:
    if metric not in GOAL_METRICS and not metric.startswith("e1rm:"):
        raise ValueError(f"Métrica de objetivo inválida; use {list(GOAL_METRICS)} ou e1rm:<exercício>")
    day = day or date.today()
    dl = date.fromisoformat(deadline)
    if dl <= day:
        raise ValueError("O prazo precisa ser no futuro")
    start = _metric_value(db, metric, day)
    if metric == "peso" and start:
        weeks = (dl - day).days / 7
        if (start - target) / start * 100 / weeks > nut.SAFETY["perda_max_pct_semana"]:
            raise ValueError("Meta exigiria perder mais de 1% do peso por semana, acima do limite de segurança. "
                             "Escolha um prazo maior ou uma meta menor.")
    goals = db.get_json("objetivos", [])
    g = {"id": max([x["id"] for x in goals], default=0) + 1, "metrica": metric, "alvo": float(target), "prazo": deadline,
         "inicio": day.isoformat(), "valor_inicial": start}
    db.set_json("objetivos", goals + [g])
    return g


def remove_goal(db: Database, gid: int) -> None:
    db.set_json("objetivos", [g for g in db.get_json("objetivos", []) if g["id"] != gid])


def goals_progress(db: Database, day: date | None = None) -> list[dict[str, Any]]:
    day = day or date.today()
    out = []
    for g in db.get_json("objetivos", []):
        cur = _metric_value(db, g["metrica"], day)
        label, unit = GOAL_METRICS.get(g["metrica"], (f"1RM estimado — {g['metrica'][5:]}", "kg"))
        item = {**g, "rotulo": label, "unidade": unit, "atual": round(cur, 2) if cur is not None else None}
        s0 = g.get("valor_inicial")
        if cur is not None and s0 is not None and g["alvo"] != s0:
            pct = 100 * (cur - s0) / (g["alvo"] - s0)
            total = (date.fromisoformat(g["prazo"]) - date.fromisoformat(g["inicio"])).days or 1
            elapsed = 100 * (day - date.fromisoformat(g["inicio"])).days / total
            item.update(progresso_pct=round(max(-100, min(150, pct))), tempo_decorrido_pct=round(min(100, elapsed)),
                        situacao="no ritmo" if pct >= elapsed - 10 else "abaixo do ritmo" if elapsed >= 15 else "início")
        else:
            item["situacao"] = "sem dados suficientes"
        out.append(item)
    return out


def _item(q: str, what: str, why: str, data: list[str], refs: list[str], conf: str, limits: str,
          cfg: Config | None = None) -> dict[str, Any]:
    return {"pergunta": q, "o_que_fazer": what, "por_que": why, "dados": data, "evidencias": kb.cite(refs, cfg),
            "confianca": conf, "limitacoes": limits}


def daily_plan(db: Database, cfg: Config, day: date | None = None, record: bool = True) -> dict[str, Any]:
    day = day or date.today()
    w, mn = int(cfg.get("analise", "janela_base_dias", 28)), int(cfg.get("analise", "minimo_dias_base", 10))
    snap = an.day_snapshot(db, day, w, mn)
    store = an.load_store(db, day, 100)
    dq = stats.data_quality(store, day)
    ready = tr.readiness(db, cfg, day)
    session = tr.todays_session(db, cfg, day)
    av = snap["avaliacoes"]
    items = []
    low_data = "Poucos dados recentes: leia com cautela. " if dq["nivel"] != "boa" else ""

    # 1. recuperação
    rec = snap["recuperacao"]
    data = [ins._desc(av[k]) for k in an.RECOVERY_KEYS if k in av][:6]
    if not ready["tem_checkin"]:
        data.append("Sem check-in subjetivo hoje (registre em 'Hoje' para aumentar a confiança).")
    items.append(_item("Como está minha recuperação hoje?", ins.STATUS_TXT[rec["status"]] + ".",
                       "; ".join(ready["sinais"]) or "Nenhum sinal desfavorável nos indicadores disponíveis.",
                       data, ["plews2013", "meeusen2013"], ready["confianca"],
                       low_data + "Comparação com a sua própria linha de base; vestíveis estimam, não medem clinicamente.", cfg))
    # 2–4. treino
    if session["tipo"] == "forca":
        s = session["sessao"]
        what = f"{s['nome']} (~{s['duracao_min']} min): " + "; ".join(
            f"{e['exercicio']} {e['series']}×{e['reps'][0]}–{e['reps'][1]} (RIR {e['rir']})" for e in s["exercicios"])
        inten = f"Repetições em reserva conforme cada exercício; {session.get('semana', '')}. " + " ".join(session.get("ajustes", []))
    elif session["tipo"] == "cardio":
        c = session["cardio"]
        what, inten = f"{c['tipo']} por {c['duracao_min']} min", c["intensidade"] + (". " + c["ajuste"] if c.get("ajuste") else "")
    else:
        what, inten = session["titulo"] + ". " + session.get("texto", ""), "Leve ou nenhuma."
    prio = {"forca": "Musculação", "cardio": "Cardio", "recuperacao": "Recuperação", "descanso": "Recuperação",
            "sem_plano": "Defina um plano"}[session["tipo"]]
    items.append(_item("Qual treino é mais adequado e com que intensidade?", f"Prioridade de hoje: {prio}. {what}",
                       f"Intensidade: {inten} Regra aplicada: {ready['regra']}", ready["sinais"] or ["Sem sinais desfavoráveis."],
                       ready["refs"] + ["acsm2009"], ready["confianca"],
                       "O ajuste considera vários sinais e a sua percepção; se você se sentir diferente do indicado, a sua "
                       "percepção e o bom senso prevalecem. " + tr.STOP_SYMPTOMS, cfg))
    # 5. nutrição
    tgt = nut.active_targets(db)
    rev = nut.review_targets(db, cfg, day)
    if tgt:
        items.append(_item("Qual é minha meta nutricional?",
                           f"{tgt['kcal']} kcal · proteína {tgt['proteina_g']} g · carboidrato {tgt['carboidrato_g']} g · "
                           f"gordura {tgt['gordura_g']} g · fibra {tgt['fibra_g']} g · água ~{tgt['agua_ml']} ml",
                           f"Objetivo '{prof.GOALS.get(tgt['objetivo'], tgt['objetivo'])}' sobre um gasto estimado de "
                           f"{tgt['tdee']['kcal']} kcal ({tgt['tdee']['origem']})."
                           + (" Há uma proposta de ajuste aguardando sua confirmação." if rev["estado"] == "proposta" else ""),
                           [f"Gasto estimado: {tgt['tdee']['intervalo'][0]}–{tgt['tdee']['intervalo'][1]} kcal"] + tgt.get("observacoes", []),
                           tgt["refs"][:4], tgt["confianca"], tgt["limitacoes"], cfg))
    else:
        miss = rev["calculada"].get("faltam") if rev["estado"] == "indisponivel" else None
        items.append(_item("Qual é minha meta nutricional?",
                           "Ainda não há meta confirmada." + (f" Complete o perfil: {', '.join(miss)}." if miss else " Revise e confirme a proposta em Nutrição."),
                           "Metas só entram em vigor depois da sua confirmação.", [], [], "baixa", "", cfg))
    # 6. disposição
    tips = ins.day_suggestions(snap)
    items.append(_item("Como posso melhorar minha disposição?", tips[0] if tips else "Mantenha a rotina.",
                       " ".join(tips[1:3]), [ins._desc(av[k]) for k in ("sleep_seconds", "stress_avg", "bb_max") if k in av],
                       ["watson2015", "bull2020"], "média", "Sugestões gerais de rotina, não tratamento.", cfg))
    # 7. atenção
    pts = ins.attention_points(snap)
    items.append(_item("Algum indicador merece atenção?",
                       "; ".join(p["texto"] for p in pts[:4]) or "Nenhum indicador relevante fora do seu padrão.",
                       "Indicadores persistentes pesam mais que desvios de um dia.", [f"Qualidade dos dados: {dq['pontuacao']}/100 ({dq['nivel']})"],
                       ["plews2013"], "média" if dq["nivel"] == "boa" else "baixa",
                       "Alterações persistentes ou acompanhadas de sintomas devem ser avaliadas por profissional de saúde.", cfg))
    # 8. sono
    night = ins.night_suggestions(snap, snap["regularidade_sono"])
    ck = db.q1("SELECT caffeine_last_time, caffeine_mg FROM checkins WHERE date=?", (day.isoformat(),))
    if ck and ck.get("caffeine_last_time") and ck["caffeine_last_time"] >= "15:00":
        night.insert(0, f"Você registrou cafeína às {ck['caffeine_last_time']}; cafeína até 6 horas antes de deitar reduz o sono.")
    items.append(_item("O que fazer para dormir melhor?", night[0], " ".join(night[1:3]),
                       [ins._desc(av[k]) for k in ("sleep_score", "sleep_seconds", "sleep_stress_avg") if k in av],
                       ["watson2015", "drake2013", "clark2017"], "média", "Medidas gerais de higiene do sono.", cfg))
    # 9. objetivos
    gp = goals_progress(db, day)
    if gp:
        txt = "; ".join(f"{g['rotulo']}: {g['atual'] if g['atual'] is not None else '—'} → {g['alvo']:g} {g['unidade']} "
                        f"({g.get('progresso_pct', '—')}% do caminho, {g['situacao']})" for g in gp)
    else:
        txt = "Nenhum objetivo cadastrado. Cadastre em Evolução para acompanhar o progresso."
    wt = met.weight_trend(db, day)
    items.append(_item("Estou evoluindo em direção aos meus objetivos?", txt,
                       (f"Tendência de peso: {wt['kg_semana']:+.2f} kg/semana (IC95% {wt['kg_semana_ic'][0]:+.2f} a {wt['kg_semana_ic'][1]:+.2f})."
                        if wt.get("suficiente") else "Tendência de peso indisponível: " + wt.get("motivo", "sem pesagens.")),
                       [f"{wt['n']} pesagem(ns) em {wt['dias']} dias"], ["hall2011"], "média" if wt.get("suficiente") else "baixa",
                       "Peso oscila 1–2 kg por água, sal, glicogênio e conteúdo intestinal; olhe a tendência de semanas.", cfg))

    plan = {"dia": day.isoformat(), "qualidade_dados": dq, "prontidao": ready, "sessao": session, "itens": items,
            "experimento": learning.today_assignment(db, day),
            "proximas_acoes": _next_actions(db, cfg, day, session, rev, ready)}
    if record:
        learning.record_decision(db, day.isoformat(), "treino", session["titulo"], what[:900], "; ".join(ready["sinais"]),
                                 ready["confianca"], ",".join(ready["refs"]))
    return plan


def _next_actions(db: Database, cfg: Config, day: date, session: dict, rev: dict, ready: dict) -> list[str]:
    acts = []
    miss = {a: f for a, f in prof.missing(db).items() if f}
    if miss:
        acts.append("Completar o perfil (" + ", ".join(sorted({x for f in miss.values() for x in f})) + ").")
    if not ready["tem_checkin"]:
        acts.append("Fazer o check-in de hoje (30 segundos): como você se sente, cafeína, água.")
    if tr.get_plan(db, "proposto"):
        acts.append("Revisar e confirmar o plano de treino proposto.")
    elif not tr.get_plan(db) and not miss.get("treino"):
        acts.append("Gerar um plano de treino.")
    if rev["estado"] == "proposta":
        acts.append("Revisar a proposta de meta nutricional.")
    last_w = db.q1("SELECT MAX(date) d FROM body_measurements")
    if not last_w or not last_w["d"] or (day - date.fromisoformat(last_w["d"])).days >= 4:
        acts.append("Registrar o peso (de manhã, em jejum, 3 ou mais vezes por semana).")
    if not db.q1("SELECT 1 FROM meals WHERE date=?", (day.isoformat(),)):
        acts.append("Registrar as refeições de hoje.")
    if session["tipo"] == "forca":
        acts.append("Após o treino, registrar cargas e repetições.")
    return acts[:6]
