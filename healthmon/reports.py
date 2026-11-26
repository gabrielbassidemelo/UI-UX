"""Geração dos relatórios diário, noturno, semanal e mensal (Markdown + HTML)."""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape
from typing import Any

from . import analysis as an
from . import insights as ins
from .charts import line_chart
from .config import Config, write_private_file
from .db import Database
from .llm import narrate
from .metrics import CORE, METRICS, display_value, fmt

DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]


def br(dd: date) -> str:
    return f"{DIAS[dd.weekday()]}, {dd.strftime('%d/%m/%Y')}"


# ------------------------------------------------------------------ documento
@dataclass
class Doc:
    title: str
    blocks: list[tuple[str, Any]] = field(default_factory=list)

    def h(self, t: str): self.blocks.append(("h", t))
    def p(self, t: str): self.blocks.append(("p", t))
    def ul(self, items: list[str]):
        if items:
            self.blocks.append(("ul", items))
    def table(self, head: list[str], rows: list[list[str]]):
        if rows:
            self.blocks.append(("table", (head, rows)))
    def html(self, raw: str, md_alt: str = ""): self.blocks.append(("html", (raw, md_alt)))
    def note(self, t: str): self.blocks.append(("note", t))

    def markdown(self) -> str:
        out = [f"# {self.title}", ""]
        for kind, v in self.blocks:
            if kind == "h":
                out += [f"## {v}", ""]
            elif kind == "p":
                out += [v, ""]
            elif kind == "note":
                out += [f"> {v}", ""]
            elif kind == "ul":
                out += [f"- {i}" for i in v] + [""]
            elif kind == "table":
                head, rows = v
                out += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                out += ["| " + " | ".join(r) + " |" for r in rows] + [""]
            elif kind == "html" and v[1]:
                out += [v[1], ""]
        return "\n".join(out).strip() + "\n"

    def render_html(self) -> str:
        out = [f"<h1>{escape(self.title)}</h1>"]
        for kind, v in self.blocks:
            if kind == "h":
                out.append(f"<h2>{escape(v)}</h2>")
            elif kind == "p":
                out.append(f"<p>{escape(v)}</p>")
            elif kind == "note":
                out.append(f'<p class="note">{escape(v)}</p>')
            elif kind == "ul":
                out.append("<ul>" + "".join(f"<li>{escape(i)}</li>" for i in v) + "</ul>")
            elif kind == "table":
                head, rows = v
                out.append('<div class="tbl"><table><thead><tr>' + "".join(f"<th>{escape(h)}</th>" for h in head)
                           + "</tr></thead><tbody>" + "".join(
                               "<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in rows)
                           + "</tbody></table></div>")
            elif kind == "html":
                out.append(v[0])
        return "\n".join(out)


def _cmp_row(a: dict[str, Any]) -> list[str]:
    k = a["metrica"]
    val = fmt(k, ins._raw(k, a["valor"]))
    med = fmt(k, ins._raw(k, a["mediana"])) if a.get("mediana") is not None else "—"
    if a["sentido"] == "sem_base":
        status = "histórico insuficiente"
    else:
        arrow = {"acima": "↑ acima", "abaixo": "↓ abaixo", "dentro": "= dentro"}[a["sentido"]]
        mark = {"favoravel": " ✓", "desfavoravel": " ⚠", "neutro": ""}[a["leitura"]]
        pad = {"persistente": " · vários dias", "pontual": " · pontual"}.get(a["padrao"], "")
        status = f"{arrow} da faixa{mark}{pad}"
        if not a["base_confiavel"]:
            status += f" (base com {a['base_dias']} dias)"
    return [a["rotulo"], val, med, status]


def _missing_labels(keys: list[str]) -> list[str]:
    return [METRICS[k].label for k in keys]


@dataclass
class Report:
    kind: str
    start: date
    end: date
    doc: Doc
    summary: str
    missing: list[str]
    facts: dict[str, Any]
    ai_mode: str = "regras"


PREVIEW_ONLY = False        # True: monta os fatos sem chamar IA nem gravar (prévia de consentimento)


def _finish(cfg: Config, db: Database, rep: Report) -> Report:
    if PREVIEW_ONLY:
        return rep
    text, mode = narrate(cfg, rep.kind, rep.facts)
    rep.ai_mode = mode
    if text:
        label = "IA local (Ollama)" if mode == "local" else "IA em nuvem (Claude, com seu consentimento)"
        rep.doc.h(f"Comentário — {label}")
        rep.doc.p(text)
    rep.doc.note(ins.AVISO)
    if db.get_setting("data_source") == "demo":
        rep.doc.blocks.insert(0, ("note", "DADOS SIMULADOS — demonstração; não são dados reais."))
    md, html = rep.doc.markdown(), rep.doc.render_html()
    db.save_report(rep.kind, rep.start.isoformat(), rep.end.isoformat(), rep.doc.title,
                   rep.summary, md, html, rep.missing, rep.ai_mode)
    out = cfg.reports_dir / rep.kind / f"{rep.end.isoformat()}.md"
    write_private_file(out, md)
    return rep


def _window(cfg: Config) -> tuple[int, int]:
    return int(cfg.get("analise", "janela_base_dias", 28)), int(cfg.get("analise", "minimo_dias_base", 10))


# -------------------------------------------------------------------- diário
def daily(cfg: Config, db: Database, day: date, sync_failed: bool = False) -> Report:
    w, mn = _window(cfg)
    snap = an.day_snapshot(db, day, w, mn)
    av = snap["avaliacoes"]
    rec = snap["recuperacao"]
    doc = Doc(f"Relatório diário — {br(day)}")
    missing = list(snap["ausentes"])
    if "sleep_seconds" in missing or "sleep_score" in missing:
        doc.note("O sono da última noite ainda não estava sincronizado no momento deste relatório"
                 + (" (houve falha de sincronização)." if sync_failed else ".")
                 + " Abra o app Garmin Connect no iPhone com a pulseira por perto para forçar a sincronização.")
    if missing:
        doc.p("Indicadores ausentes hoje: " + ", ".join(_missing_labels(missing)) + ".")

    doc.h("Recuperação")
    doc.p(ins.STATUS_TXT[rec["status"]] + ".")
    if rec["sinais"]:
        doc.p("Sinais desfavoráveis: " + ", ".join(rec["sinais"]) + ".")
    if rec["persistente"]:
        doc.p("Esse conjunto de sinais se repete em pelo menos 3 dos últimos 4 dias (padrão persistente).")

    doc.h("Noite anterior")
    sleep_keys = ["sleep_seconds", "sleep_score", "deep_seconds", "light_seconds", "rem_seconds",
                  "awake_seconds", "awake_count", "sleep_resp_avg", "sleep_spo2_avg", "sleep_spo2_low",
                  "sleep_stress_avg", "skin_temp_dev"]
    doc.table(["Indicador", "Hoje", "Sua mediana", "Comparação"],
              [_cmp_row(av[k]) for k in sleep_keys if k in av])
    r = db.daily(day.isoformat()) or {}
    if r.get("sleep_start") and r.get("sleep_end"):
        doc.p(f"Dormiu às {r['sleep_start'][11:16]} e acordou às {r['sleep_end'][11:16]}.")
    if r.get("sleep_seconds") and all(r.get(k) for k in ("deep_seconds", "rem_seconds", "light_seconds")):
        tot = r["deep_seconds"] + r["rem_seconds"] + r["light_seconds"]
        doc.p(f"Distribuição das fases: profundo {100*r['deep_seconds']/tot:.0f}%, "
              f"leve {100*r['light_seconds']/tot:.0f}%, REM {100*r['rem_seconds']/tot:.0f}%.")

    doc.h("HRV, frequência cardíaca e energia")
    keys = ["hrv_last_night", "hrv_weekly_avg", "resting_hr", "bb_max", "training_readiness",
            "recovery_time_h", "vo2max"]
    doc.table(["Indicador", "Hoje", "Sua mediana", "Comparação"], [_cmp_row(av[k]) for k in keys if k in av])
    if r.get("hrv_status"):
        doc.p(f"Status de HRV informado pela Garmin: {r['hrv_status']}.")

    pts = ins.attention_points(snap)
    doc.h("Merece atenção")
    doc.ul([p["texto"] for p in pts] or ["Nenhum indicador relevante fora do seu padrão."])
    pos = ins.positives(snap)
    if pos:
        doc.h("Pontos positivos")
        doc.ul(pos)
    doc.h("Sugestões para hoje")
    try:
        from . import training
        ready = training.readiness(db, cfg, day)
    except Exception:  # noqa: BLE001 — sem perfil/plano o relatório segue descritivo
        ready = None
    tips = ins.day_suggestions(snap, ready)
    doc.ul(tips)
    from . import calibration
    stg = calibration.stage(db, day)
    doc.p(f"Estágio de calibração: {stg['rotulo']} — {stg['dias_validos']} dias com dados válidos."
          + (" Recomendações personalizadas ainda suspensas; por enquanto o relatório é descritivo." if not stg["permite"]["progressao_de_carga"] else ""))

    try:
        from . import planner
        plan = planner.daily_plan(db, cfg, day)
        doc.h("Plano do dia")
        doc.table(["Pergunta", "Recomendação", "Confiança"],
                  [[i["pergunta"], i["o_que_fazer"], i["confianca"]] for i in plan["itens"][1:5]])
        if plan["proximas_acoes"]:
            doc.h("Próximas ações")
            doc.ul(plan["proximas_acoes"])
        doc.p(f"Qualidade dos dados nos últimos 14 dias: {plan['qualidade_dados']['pontuacao']}/100 "
              f"({plan['qualidade_dados']['nivel']}). Justificativas e referências completas no dashboard.")
    except Exception as e:  # noqa: BLE001 — o relatório base nunca depende dos módulos novos
        import logging
        logging.getLogger(__name__).warning("Plano do dia indisponível: %s", type(e).__name__)
    summary = ins.STATUS_TXT[rec["status"]]
    if "sleep_seconds" in av:
        summary += f" · sono {fmt('sleep_seconds', r.get('sleep_seconds'))}"
    if "hrv_last_night" in av:
        summary += f" · HRV {fmt('hrv_last_night', r.get('hrv_last_night'))}"
    if pts:
        summary += f" · {len(pts)} ponto(s) de atenção"
    facts = {"dia": day.isoformat(), "recuperacao": rec, "avaliacoes": av, "ausentes": missing,
             "pontos_atencao": [p["texto"] for p in pts], "sugestoes_motor": tips,
             "prontidao": {"nivel": (ready or {}).get("nivel")}}       # a IA não é chamada em dia de pausa
    return _finish(cfg, db, Report("diario", day, day, doc, summary, missing, facts))


# ------------------------------------------------------------------- noturno
def nightly(cfg: Config, db: Database, day: date) -> Report:
    w, mn = _window(cfg)
    snap = an.day_snapshot(db, day, w, mn)
    av = snap["avaliacoes"]
    r = db.daily(day.isoformat()) or {}
    doc = Doc(f"Relatório noturno — {br(day)}")
    missing = [k for k in ("steps", "stress_avg", "calories_total") if r.get(k) is None]
    if missing:
        doc.note("Parte dos dados de hoje ainda não sincronizou: " + ", ".join(_missing_labels(missing))
                 + ". Os valores podem estar incompletos.")
    doc.h("Atividades do dia")
    acts = db.activities_range(day.isoformat(), day.isoformat())
    if acts:
        rows = []
        for a in acts:
            dur = f"{int((a['duration_s'] or 0)//60)} min"
            dist = f"{(a['distance_m'] or 0)/1000:.2f} km".replace(".", ",") if a["distance_m"] else "—"
            fc = f"{a['avg_hr']:.0f}/{a['max_hr']:.0f}" if a["avg_hr"] and a["max_hr"] else "—"
            te = f"{a['aerobic_te']:.1f}".replace(".", ",") if a["aerobic_te"] else "—"
            rows.append([a["start_local"][11:16] if a["start_local"] else "", a["name"] or a["type"] or "",
                         dur, dist, fc, f"{a['calories'] or 0:.0f}", te])
        doc.table(["Início", "Atividade", "Duração", "Distância", "FC méd/máx", "kcal", "Efeito aeróbico"], rows)
    else:
        doc.p("Nenhuma atividade registrada hoje.")
    keys = ["steps", "distance_m", "intensity_minutes", "floors", "calories_total", "calories_active",
            "training_load"]
    doc.table(["Indicador", "Hoje", "Sua mediana", "Comparação"], [_cmp_row(av[k]) for k in keys if k in av])
    doc.h("Estresse e energia")
    keys = ["stress_avg", "stress_max", "stress_high_seconds", "bb_max", "bb_min", "bb_charged", "bb_drained"]
    doc.table(["Indicador", "Hoje", "Sua mediana", "Comparação"], [_cmp_row(av[k]) for k in keys if k in av])
    if r.get("calories_total") is not None:
        cal = f"Gasto energético estimado pela Garmin: {fmt('calories_total', r['calories_total'])}"
        if r.get("calories_active") is not None:
            cal += f" (ativas: {fmt('calories_active', r['calories_active'])})"
        doc.p(cal + ". Estimativas de vestíveis podem ter erro considerável.")
    reg = snap["regularidade_sono"]
    doc.h("Para uma boa noite de sono")
    tips = ins.night_suggestions(snap, reg)
    doc.ul(tips)
    summary = f"Passos {fmt('steps', r.get('steps'))} · estresse médio {fmt('stress_avg', r.get('stress_avg'))}"
    if acts:
        summary += f" · {len(acts)} atividade(s)"
    facts = {"dia": day.isoformat(), "avaliacoes": {k: av[k] for k in av if METRICS[k].categoria in ("atividade", "estresse")},
             "atividades": [{k: a[k] for k in ("type", "duration_s", "distance_m", "calories", "avg_hr", "training_load")} for a in acts],
             "regularidade_sono": reg, "sugestoes_motor": tips}
    return _finish(cfg, db, Report("noturno", day, day, doc, summary, missing, facts))


# ------------------------------------------------------------------ períodos
def _period_report(cfg: Config, db: Database, kind: str, start: date, end: date,
                   prev: tuple[date, date], title: str, trend_days: int, charts: bool) -> Report:
    w, mn = _window(cfg)
    store = an.load_store(db, end, 400)
    keys = [k for k in METRICS if k not in ("activity_count",)]
    comp = [c for c in an.compare_periods(store, keys, (start, end), prev) if c["atual"]["n"] > 0]
    doc = Doc(title)
    days_total = (end - start).days + 1
    have = len([1 for i in range(days_total) if store.raw(start + timedelta(days=i))])
    missing = []
    if have < days_total:
        missing.append(f"{days_total - have} dia(s) sem dados no período")
        doc.note(f"{days_total - have} de {days_total} dias do período não têm dados sincronizados.")

    doc.h("Resumo e comparação com o período anterior")
    rows = []
    for c in comp:
        if c["metrica"] not in CORE and kind == "semanal" and c["metrica"] not in (
                "deep_seconds", "rem_seconds", "intensity_minutes", "training_load", "calories_total"):
            continue
        k = c["metrica"]
        a = c["atual"]["media"]
        b = c["anterior"]["media"]
        conv = (lambda v: ins._raw(k, v))
        mark = {"favoravel": " ✓", "desfavoravel": " ⚠", "neutro": ""}[c["leitura"]]
        rows.append([c["rotulo"], fmt(k, conv(a)), fmt(k, conv(b)) if b is not None else "—",
                     (f"{c['variacao_pct']:+.0f}%{mark}" if c["variacao_pct"] is not None else "—")])
    doc.table(["Indicador (média)", "Este período", "Anterior", "Variação"], rows)

    # recuperação dia a dia
    dist = {"boa": 0, "moderada": 0, "atencao": 0, "indeterminada": 0}
    for i in range(days_total):
        dd = start + timedelta(days=i)
        if store.raw(dd):
            dist[an.recovery(store, dd, w, mn).status] += 1
    doc.h("Recuperação no período")
    doc.p(f"Dias com recuperação dentro do padrão: {dist['boa']}; moderada: {dist['moderada']}; "
          f"com sinais de recuperação insuficiente: {dist['atencao']}; indeterminados: {dist['indeterminada']}.")

    trends = [an.trend(store, k, end, trend_days) for k in CORE + ["deep_seconds", "rem_seconds", "sleep_resp_avg"]]
    tsent = [s for s in (ins.trend_sentence(t.as_dict()) for t in trends) if s]
    doc.h(f"Tendências (últimos {trend_days} dias)")
    doc.ul(tsent or ["Nenhuma tendência estatisticamente consistente; indicadores estáveis."])

    cors = an.correlations(store, end, 90)
    csent = [s for s in (ins.correlation_sentence(c) for c in cors) if s]
    doc.h("Possíveis associações (últimos 90 dias)")
    doc.ul(csent or ["Nenhuma associação moderada ou forte identificada (ou dados ainda insuficientes)."])
    doc.note("Associações descrevem como os indicadores variaram juntos no seu histórico; não provam causa e efeito.")

    reg = an.sleep_regularity(store, end, min(days_total, 30))
    habits = []
    if reg:
        habits.append(f"Horário mediano de dormir {reg['horario_mediano']}, variação de ±{reg['desvio_min']} min.")
    acts = db.activities_range(start.isoformat(), end.isoformat())
    act_days = len({a["date"] for a in acts})
    habits.append(f"{len(acts)} atividade(s) registradas em {act_days} dia(s) diferentes.")
    sl = an.period_stats(store, "sleep_seconds", start, end)
    if sl["n"]:
        short = sum(1 for _, v in store.values("sleep_seconds", start, end) if v < 7 * 3600)
        habits.append(f"Sono médio {fmt('sleep_seconds', sl['media'] * 3600)}; {short} de {sl['n']} noites "
                      "abaixo de 7 h (referência geral de consenso para adultos).")
    doc.h("Hábitos")
    doc.ul(habits)

    if charts:
        doc.h("Gráficos")
        chart_html = []
        for k in CORE:
            pts = [(dd, display_value(k, v)) for dd, v in store.values(k, start, end)]
            prevp = [(dd, display_value(k, v)) for dd, v in store.values(k, *prev)]
            b = an.baseline(store, k, start, w, mn)
            band = tuple(display_value(k, x) for x in b.faixa) if b.faixa else None
            chart_html.append(line_chart(f"{METRICS[k].label} ({METRICS[k].unit})".replace(" ()", ""),
                                         pts, METRICS[k].unit, band, prevp))
        doc.html('<div class="charts">' + "".join(chart_html) + "</div>",
                 "_Gráficos disponíveis na versão HTML/dashboard._")

    recs = _period_recommendations(comp, dist, trends, reg, kind)
    doc.h("Recomendações para o próximo período")
    doc.ul(recs)
    good = sum(1 for c in comp if c["leitura"] == "favoravel")
    bad = sum(1 for c in comp if c["leitura"] == "desfavoravel")
    summary = f"{good} indicador(es) melhoraram e {bad} pioraram vs. período anterior · {dist['atencao']} dia(s) com recuperação insuficiente"
    facts = {"periodo": [start.isoformat(), end.isoformat()], "comparacao": comp, "recuperacao_dias": dist,
             "tendencias": [t.as_dict() for t in trends], "associacoes": cors, "habitos": habits,
             "recomendacoes_motor": recs}
    return _finish(cfg, db, Report(kind, start, end, doc, summary, missing, facts))


def _period_recommendations(comp, dist, trends, reg, kind) -> list[str]:
    recs = []
    by = {c["metrica"]: c for c in comp}
    tr = {t.key: t for t in trends}
    if dist["atencao"] >= 3:
        recs.append("Vários dias com sinais de recuperação insuficiente: planeje uma semana com menos "
                    "volume/intensidade (por exemplo, reduzir 20–40% da carga) e priorize o sono.")
    elif dist["boa"] >= max(4, dist["moderada"] + dist["atencao"]):
        recs.append("Recuperação predominantemente dentro do padrão: há margem para manter ou progredir "
                    "gradualmente a carga de treino, se esse for seu objetivo.")
    if by.get("sleep_seconds", {}).get("leitura") == "desfavoravel" or (
            tr.get("sleep_seconds") and tr["sleep_seconds"].direcao == "descendo"):
        recs.append("A duração do sono caiu: defina um horário-alvo para deitar e proteja a última hora "
                    "antes de dormir.")
    if reg and reg["desvio_min"] >= 60:
        recs.append("Reduzir a variação do horário de dormir (inclusive fins de semana) é uma meta concreta "
                    "para o próximo período.")
    if by.get("stress_avg", {}).get("leitura") == "desfavoravel":
        recs.append("Estresse médio subiu: inclua pausas programadas e alguma atividade leve ao ar livre nos dias mais cheios.")
    if tr.get("hrv_last_night") and tr["hrv_last_night"].direcao == "descendo":
        recs.append("HRV em queda consistente: revise carga de treino, consumo de álcool e sono; se persistir "
                    "sem explicação ou com sintomas, converse com um profissional de saúde.")
    if tr.get("resting_hr") and tr["resting_hr"].direcao == "subindo":
        recs.append("FC de repouso em alta consistente: observe sono, hidratação, calor e carga; se persistir "
                    "com sintomas, procure avaliação profissional.")
    if by.get("steps", {}).get("leitura") == "desfavoravel":
        recs.append("Atividade diária menor que no período anterior: metas pequenas (ex.: +1.000 passos/dia) são mais sustentáveis.")
    if not recs:
        recs.append("Indicadores estáveis: manter a rotina atual de sono, atividade e recuperação.")
    return recs


def weekly(cfg: Config, db: Database, end: date) -> Report:
    start = end - timedelta(days=6)
    prev = (start - timedelta(days=7), start - timedelta(days=1))
    return _period_report(cfg, db, "semanal", start, end, prev,
                          f"Relatório semanal — {start.strftime('%d/%m')} a {end.strftime('%d/%m/%Y')}",
                          28, charts=True)


def monthly(cfg: Config, db: Database, year: int, month: int) -> Report:
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    pend = start - timedelta(days=1)
    pstart = date(pend.year, pend.month, 1)
    return _period_report(cfg, db, "mensal", start, end, (pstart, pend),
                          f"Relatório mensal — {MESES[month - 1]} de {year}",
                          (end - start).days + 1, charts=True)
