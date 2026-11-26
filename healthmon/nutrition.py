"""Nutrição: metas, registro de refeições, base de alimentos e sugestão por otimização linear.

Finalidade informativa; não substitui nutricionista. Fundamentos (kb/evidencias.json):
- Proteína: morton2018 (platô médio ~1,6 g/kg/dia para hipertrofia, IC até ~2,2), jager2017,
  helms2014 (déficit). Por refeição ~0,4 g/kg: schoenfeld2018meal.
- Ritmo de perda: 0,5–1% do peso/semana preserva melhor a massa magra (garthe2011, helms2014).
- Superávit conservador para hipertrofia: slater2019. Recomposição: barakat2020.
- Fibras: 14 g/1.000 kcal (iom2005), mínimo de 25 g/dia (who2023carb). Água: efsa2010.
- Baixa disponibilidade energética (< 30 kcal/kg de massa magra/dia): mountjoy2018.
Limites de segurança em SAFETY são constantes e não mudam sozinhos.
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import metabolism as met
from . import profile as prof
from .config import Config
from .db import Database, utcnow

SAFETY = {
    "deficit_max_pct": 25.0,                # do gasto total estimado
    "imc_minimo_para_deficit": 18.5,        # abaixo disso nenhum déficit é proposto
    "deficit_max_idoso_pct": 10.0,          # a partir de 65 anos
    "proteina_min_idoso_g_kg": 1.2,
    "perda_max_pct_semana": 1.0,            # do peso corporal
    "piso_kcal": {"masculino": 1500, "feminino": 1200},   # piso conservador do sistema, sem supervisão
    "superavit_max_kcal": 400,
    "proteina_max_g_kg": 2.5,
    "gordura_min_g_kg": 0.6,
    "ajuste_max_kcal": 200,                 # por revisão
    "mudanca_que_exige_confirmacao_pct": 5.0,
    "disponibilidade_energetica_min": 30.0,  # kcal/kg MLG/dia
}
PROTEIN = {"emagrecimento": (1.8, 2.2, 2.0), "recomposicao": (1.8, 2.2, 2.0), "hipertrofia": (1.6, 2.2, 1.8),
           "forca": (1.6, 2.2, 1.8), "desempenho": (1.4, 2.0, 1.6), "condicionamento": (1.2, 1.8, 1.5),
           "manutencao": (1.2, 1.6, 1.4), "saude": (1.0, 1.6, 1.2)}
MACRO_KCAL = {"protein_g": 4, "carb_g": 4, "fat_g": 9}
TACO_SHA256 = "8c0f7c701eadc9d2afa2711688e3d9523a3a2ad0c1ebcf1fa2fc9e6d08f916ab"   # arquivo conferido em 26/11/2026
TACO_URL = "https://raw.githubusercontent.com/machine-learning-mocha/taco/main/formatados/alimentos.csv"


# ----------------------------------------------------------------------- metas
def compute_targets(db: Database, cfg: Config, day: date | None = None) -> dict[str, Any]:
    from . import safety
    day = day or date.today()
    p = prof.get(db)
    scr = safety.screen(db, cfg, day)
    miss = prof.missing(db, "nutricao")["nutricao"]
    stop = safety.blocked(scr, "metas_nutricionais")
    if miss or stop:
        return {"disponivel": False, "faltam": miss, "bloqueio": stop or scr["bloqueios"].get("deficit"),
                "ocultar_calorias": bool(scr.get("ocultar_calorias"))}
    t = met.tdee(db, cfg, day)
    if not t.get("estimativa"):
        return {"disponivel": False, "faltam": ["dados para estimar o gasto energético"], "bloqueio": None}
    goal, sex = p["objetivo"], p["sexo"]
    from . import kb
    core = ["morton2018", "jager2017"] + (["garthe2011", "helms2014"] if goal == "emagrecimento" else
                                          ["slater2019"] if goal in ("hipertrofia", "forca") else [])
    bad_refs = kb.failing(core + met.REFS["tmb"][:2], cfg)
    if bad_refs:
        return {"disponivel": False, "faltam": [], "evidencia_pendente": bad_refs,
                "bloqueio": "Metas personalizadas desabilitadas: as referências científicas que as sustentam ainda não "
                            "foram verificadas (" + ", ".join(bad_refs) + ")."}
    w, _ = prof.current_weight(db, cfg, day.isoformat())
    tdee, notes, refs = t["estimativa"]["kcal"], list(scr["avisos"]), ["morton2018", "jager2017"]
    imc = prof.bmi(p, w)
    old = bool(scr.get("idoso"))
    no_deficit = safety.blocked(scr, "deficit")
    if no_deficit and goal == "emagrecimento":
        kcal = float(tdee)
        notes.append(no_deficit)
    elif goal == "emagrecimento":
        kcal = tdee * 0.80
        refs += ["garthe2011", "helms2014"]
        if old:
            kcal = tdee * (1 - SAFETY["deficit_max_idoso_pct"] / 100)
            notes.append("65 anos ou mais: déficit limitado a 10% para proteger massa muscular e óssea; perda de peso "
                         "nessa faixa deve ser acompanhada por profissional.")
        if scr["risco_nutricional"]["imc_proximo_do_limite"]:
            kcal = max(kcal, tdee * 0.90)
            notes.append(f"IMC estimado de {imc:.1f}, próximo do limite de baixo peso: déficit limitado a 10%. "
                         "Converse com um profissional antes de continuar perdendo peso.")
        rate = t["peso"].get("pct_semana")
        if rate is not None and t["peso"].get("suficiente"):
            if rate < -SAFETY["perda_max_pct_semana"]:
                kcal = max(kcal, tdee * 0.90)
                notes.append(f"Você está perdendo {abs(rate):.1f}% do peso por semana, acima do limite de 1%: "
                             "o déficit foi reduzido para proteger massa magra e recuperação.")
            elif rate > -0.25 and "adaptativa" in t["vias"]:
                notes.append("A perda está abaixo de 0,25%/semana com registro consistente; o gasto recalibrado "
                             "já reflete isso na meta.")
    elif goal in ("hipertrofia", "forca"):
        kcal = tdee + min(SAFETY["superavit_max_kcal"], tdee * (0.10 if p.get("experiencia") == "iniciante" else 0.05))
        refs.append("slater2019")
    else:
        kcal = float(tdee)
        if goal == "recomposicao":
            refs.append("barakat2020")
    floor = max(SAFETY["piso_kcal"][sex], tdee * (1 - SAFETY["deficit_max_pct"] / 100))
    if kcal < floor:
        kcal = floor
        notes.append("Meta elevada até o piso de segurança (déficit máximo de 25% e mínimo absoluto do sistema).")
    ref_w = w
    h = p["altura_cm"] / 100
    obese = w / h ** 2 >= 30
    if obese:
        ref_w = 25 * h ** 2
        notes.append(f"IMC ≥ 30: proteína e gordura calculadas sobre um peso de referência ({ref_w:.0f} kg, IMC 25) "
                     "para não superestimar; é uma convenção prática.")
    lo, hi, mid = PROTEIN[goal]
    if old:
        mid = max(mid, SAFETY["proteina_min_idoso_g_kg"])
    protein = min(mid, SAFETY["proteina_max_g_kg"]) * ref_w

    # Disponibilidade energética (critério de atletas; não se aplica com IMC ≥ 30). Só usa medidas até o dia.
    ea_info = None
    bf = db.q1("SELECT body_fat_pct FROM body_measurements WHERE body_fat_pct IS NOT NULL AND date<=? "
               "ORDER BY date DESC LIMIT 1", (day.isoformat(),))
    if bf and not obese:
        ffm = w * (1 - bf["body_fat_pct"] / 100)
        ex = db.q1("SELECT AVG(calories_active) v FROM daily_metrics WHERE date BETWEEN ? AND ? AND calories_active IS NOT NULL",
                   ((day - timedelta(days=13)).isoformat(), day.isoformat()))
        if ex and ex["v"]:
            ea = (kcal - ex["v"] * 0.5) / ffm   # convenção: metade das calorias ativas do vestível tratada como exercício
            ea_info = {"kcal_kg_mlg": round(ea, 1), "aproximada": True}
            if ea < SAFETY["disponibilidade_energetica_min"] and kcal < tdee:
                kcal = float(tdee)              # o déficit é efetivamente removido, não só comentado
                notes.append("Disponibilidade energética estimada abaixo de 30 kcal/kg de massa magra: o déficit foi "
                             "removido. Procure orientação profissional antes de reduzir calorias.")
                refs.append("mountjoy2018")
    fat = max(0.25 * kcal / 9, SAFETY["gordura_min_g_kg"] * ref_w)
    carb = max(0.0, (kcal - protein * 4 - fat * 9) / 4)
    if carb < 100:
        notes.append("Carboidrato calculado abaixo de 100 g/dia: reveja com um nutricionista se pretende manter.")
    if scr["risco_nutricional"]["ingestao_muito_baixa"]:
        notes.append("Atenção: a ingestão que você registrou está bem abaixo do seu gasto de repouso estimado há duas "
                     "semanas. Se o registro está completo, isso é pouco para sustentar saúde e treino; converse com "
                     "um profissional.")
    meals = int(p.get("refeicoes_dia", 4))
    fib = [v for ref, v in (("who2023carb", 25.0), ("iom2005", 14 * kcal / 1000)) if not kb.failing([ref], cfg)]
    fiber = round(max(fib)) if fib else None
    pend = kb.failing(["iom2005", "who2023carb", "efsa2010"], cfg)
    if pend:
        notes.append("Sem meta de " + " e ".join(x for x, r in (("fibra por caloria", "iom2005"), ("água", "efsa2010")) if r in pend)
                     + ": a referência correspondente ainda não foi verificada na fonte.") if any(r in pend for r in ("iom2005", "efsa2010")) else None
    out = {
        "disponivel": True, "objetivo": goal, "kcal": round(kcal / 10) * 10, "tdee": t["estimativa"],
        "balanco_kcal": round(kcal - tdee), "proteina_g": round(protein), "proteina_faixa_g_kg": [lo, hi],
        "gordura_g": round(fat), "carboidrato_g": round(carb), "fibra_g": fiber,
        "agua_ml": None if (scr.get("sem_meta_de_agua") or kb.failing(["efsa2010"], cfg)) else round(35 * w / 50) * 50,
        "refeicoes": meals,
        "por_refeicao": {"proteina_g": round(protein / meals), "alvo_g_kg": 0.4,
                         "texto": f"~{round(protein / meals)} g de proteína em cada uma das {meals} refeições"},
        "observacoes": notes + ([t["sub_registro"]["texto"]] if t.get("sub_registro") else []),
        "bloqueio": no_deficit if goal == "emagrecimento" else None, "imc": round(imc, 1) if imc else None,
        "confianca": t["estimativa"]["confianca"],
        "refs": [r for r in refs + ["iom2005", "who2023carb", "efsa2010", "schoenfeld2018meal"] if not kb.failing([r], cfg)],
        "limitacoes": "Metas derivadas de estimativas de gasto com incerteza de centenas de kcal; o ajuste fino vem da "
                      "evolução do seu peso em semanas. Água: ~35 ml/kg é uma regra prática de uso comum, não um valor da "
                      "EFSA (que indica 2,0–2,5 L/dia de água total para adultos); a necessidade sobe com calor e treino.",
    }
    if ea_info:
        out["disponibilidade_energetica"] = ea_info
    return out


def active_targets(db: Database) -> dict[str, Any] | None:
    return db.get_json("meta_nutricional")


def review_targets(db: Database, cfg: Config, day: date | None = None) -> dict[str, Any]:
    """Compara a meta em vigor com a calculada. Mudança > 5% vira proposta que você confirma."""
    new = compute_targets(db, cfg, day)
    cur = active_targets(db)
    if not new.get("disponivel"):
        return {"estado": "indisponivel", "calculada": new, "vigente": cur}
    if cur is None:
        return {"estado": "proposta", "motivo": "Primeira meta: confirme para ativar.", "calculada": new, "vigente": None}
    diff = new["kcal"] - cur["kcal"]
    if abs(diff) / cur["kcal"] * 100 <= SAFETY["mudanca_que_exige_confirmacao_pct"] and new["objetivo"] == cur.get("objetivo"):
        return {"estado": "em_dia", "calculada": new, "vigente": cur}
    step = max(-SAFETY["ajuste_max_kcal"], min(SAFETY["ajuste_max_kcal"], diff))
    if new["objetivo"] == cur.get("objetivo") and step != diff:
        k2 = round((cur["kcal"] + step) / 10) * 10
        new = {**new, "kcal": k2, "balanco_kcal": round(k2 - new["tdee"]["kcal"]),
               "carboidrato_g": max(0, round((k2 - new["proteina_g"] * 4 - new["gordura_g"] * 9) / 4)),
               "observacoes": new["observacoes"] + [f"Ajuste limitado a {SAFETY['ajuste_max_kcal']} kcal por revisão."]}
    return {"estado": "proposta", "motivo": f"A meta calculada difere {diff:+.0f} kcal da vigente.", "calculada": new, "vigente": cur}


def confirm_targets(db: Database, cfg: Config, day: date | None = None) -> dict[str, Any]:
    r = review_targets(db, cfg, day)
    if r["estado"] != "proposta":
        raise ValueError("Não há proposta de meta para confirmar")
    t = {**r["calculada"], "confirmada_em": utcnow()}
    db.set_json("meta_nutricional", t)
    return t


# ------------------------------------------------------------------- alimentos
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _f(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", ".")
    if s in ("", "NA", "*", "-"):
        return None
    if s.lower() == "tr":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def add_food(db: Database, name: str, source: str, kcal: float | None, protein: float | None, carb: float | None,
             fat: float | None, fiber: float | None = None, portion_g: float | None = None,
             price_100g: float | None = None) -> int:
    if kcal is not None and not 0 <= kcal <= 900:
        raise ValueError("kcal por 100 g fora do intervalo físico (0–900)")
    db.insert("foods", {"name": name.strip(), "source": source, "kcal": kcal, "protein_g": protein, "carb_g": carb,
                        "fat_g": fat, "fiber_g": fiber, "portion_g": portion_g, "price_100g": price_100g},
              upsert_on="name,source")
    fid = db.q1("SELECT id FROM foods WHERE name=? AND source=?", (name.strip(), source))["id"]
    db.x("DELETE FROM foods_fts WHERE rowid=?", (fid,))
    db.x("INSERT INTO foods_fts(rowid, name) VALUES(?, ?)", (fid, name.strip()))
    return fid


COLS = {"name": ("descrição dos alimentos", "descricao", "description", "alimento", "nome", "name"),
        "kcal": ("energia..kcal.", "energy_kcal", "energia (kcal)", "kcal", "energy"),
        "protein": ("proteína..g.", "protein_g", "proteina", "proteína (g)", "protein"),
        "fat": ("lipídeos..g.", "lipid_g", "lipideos", "lipídeos (g)", "fat", "gordura"),
        "carb": ("carboidrato..g.", "carbohydrate_g", "carboidrato", "carboidrato (g)", "carbohydrate", "carb"),
        "fiber": ("fibra.alimentar..g.", "fiber_g", "fibra", "fibra alimentar (g)", "fiber")}


def import_foods_csv(db: Database, path: Path, source: str) -> dict[str, int]:
    """Importa tabela de composição (TACO, TBCA, USDA ou CSV próprio), valores por 100 g."""
    text = Path(path).read_text(encoding="utf-8-sig")
    reader = csv.DictReader(text.splitlines(), delimiter=";" if text.split("\n", 1)[0].count(";") > 3 else ",")
    header = {(_norm(h).strip()): h for h in reader.fieldnames or []}
    col = {}
    for key, names in COLS.items():
        col[key] = next((header[_norm(n)] for n in names if _norm(n) in header), None)
    if not col["name"] or not col["kcal"]:
        raise ValueError(f"Colunas de nome/energia não reconhecidas em {list(header.values())[:8]}")
    ok = skip = 0
    for row in reader:
        name, kcal = (row.get(col["name"]) or "").strip(), _f(row.get(col["kcal"]))
        if not name or kcal is None or not 0 <= kcal <= 900:
            skip += 1
            continue
        add_food(db, name, source, kcal, *(_f(row.get(col[k])) if col[k] else None for k in ("protein", "carb", "fat", "fiber")))
        ok += 1
    return {"importados": ok, "ignorados": skip}


def download_taco(db: Database, url: str = TACO_URL) -> dict[str, int]:
    """Baixa a TACO (NEPA/UNICAMP, 4ª ed.) de um espelho público e importa. Não envia nenhum dado seu."""
    import tempfile
    import requests
    import hashlib
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    if url == TACO_URL and hashlib.sha256(r.content).hexdigest() != TACO_SHA256:
        raise RuntimeError("O arquivo da TACO baixado difere do conferido no desenvolvimento; importação cancelada. "
                           "Baixe a tabela da fonte oficial (NEPA/UNICAMP) e use `healthmon alimentos importar`.")
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(r.text)
    try:
        return import_foods_csv(db, Path(f.name), "TACO")
    finally:
        Path(f.name).unlink(missing_ok=True)


def search_foods(db: Database, query: str, limit: int = 8) -> list[dict[str, Any]]:
    tokens = [t for t in re.findall(r"\w+", _norm(query)) if len(t) > 1]
    if not tokens:
        return []
    for expr in (" AND ".join(f"{t}*" for t in tokens), " OR ".join(f"{t}*" for t in tokens)):
        rows = db.q("SELECT f.*, bm25(foods_fts) rank FROM foods_fts JOIN foods f ON f.id=foods_fts.rowid "
                    "WHERE foods_fts MATCH ? ORDER BY rank, length(f.name) LIMIT ?", (expr, limit))
        if rows:
            return rows
    return []


# -------------------------------------------------------------------- refeições
ITEM_RE = re.compile(r"^\s*(?:(\d+(?:[.,]\d+)?)\s*(kg|g|ml|l)\b\s*(?:de\s+)?)?(?:(\d+(?:[.,]\d+)?)\s+)?(.+?)\s*$", re.I)


def parse_meal_text(db: Database, text: str) -> dict[str, Any]:
    """'150 g frango grelhado, 200g arroz cozido, 1 banana' → itens casados com a base de alimentos."""
    items, unmatched = [], []
    for part in re.split(r"[,;\n+]|\be\b", text):
        part = part.strip()
        if not part:
            continue
        m = ITEM_RE.match(part)
        qty, unit, count, name = m.group(1), (m.group(2) or "").lower(), m.group(3), m.group(4)
        found = search_foods(db, name, 1)
        if not found:
            unmatched.append(part)
            continue
        f = found[0]
        if qty:
            grams = float(qty.replace(",", ".")) * (1000 if unit in ("kg", "l") else 1)
            unc = 15.0
        else:
            n = float(count.replace(",", ".")) if count else 1.0
            grams = n * (f["portion_g"] or 100)
            unc = 30.0 if f["portion_g"] else 50.0
        k = grams / 100
        items.append({"texto": part, "alimento": f["name"], "fonte": f["source"], "gramas": round(grams),
                      "kcal": round((f["kcal"] or 0) * k), "protein_g": round((f["protein_g"] or 0) * k, 1),
                      "carb_g": round((f["carb_g"] or 0) * k, 1), "fat_g": round((f["fat_g"] or 0) * k, 1),
                      "fiber_g": round((f["fiber_g"] or 0) * k, 1), "incerteza_pct": unc,
                      "porcao_assumida": not qty})
    tot = {k: round(sum(i[k] for i in items), 1) for k in ("kcal", "protein_g", "carb_g", "fat_g", "fiber_g")}
    kc = tot["kcal"] or 1
    unc = round(sum(i["incerteza_pct"] * i["kcal"] for i in items) / kc, 0) if items else None
    return {"itens": items, "nao_encontrados": unmatched, "total": tot, "incerteza_pct": unc,
            "nota": "Estimativa a partir de tabela de composição; preparo, marca e porção alteram os valores."}


def log_meal(db: Database, day: str, description: str, kcal: float | None = None, protein_g: float | None = None,
             carb_g: float | None = None, fat_g: float | None = None, fiber_g: float | None = None,
             time: str | None = None, method: str = "manual", items: list | None = None,
             uncertainty_pct: float | None = None) -> int:
    if kcal is not None and not 0 <= kcal <= 6000:
        raise ValueError("Calorias de uma refeição fora do intervalo plausível")
    return db.insert("meals", {"date": day, "time": time, "description": description[:500], "kcal": kcal,
                               "protein_g": protein_g, "carb_g": carb_g, "fat_g": fat_g, "fiber_g": fiber_g,
                               "method": method, "uncertainty_pct": uncertainty_pct,
                               "items": json.dumps(items, ensure_ascii=False) if items else None, "created_at": utcnow()})


def log_meal_text(db: Database, day: str, text: str, time: str | None = None) -> dict[str, Any]:
    parsed = parse_meal_text(db, text)
    if not parsed["itens"]:
        raise ValueError("Nenhum alimento reconhecido. Importe uma base de alimentos ou informe os valores manualmente.")
    t = parsed["total"]
    parsed["id"] = log_meal(db, day, text, t["kcal"], t["protein_g"], t["carb_g"], t["fat_g"], t["fiber_g"], time,
                            "texto", parsed["itens"], parsed["incerteza_pct"])
    return parsed


def day_summary(db: Database, cfg: Config, day: date) -> dict[str, Any]:
    meals = db.q("SELECT * FROM meals WHERE date=? ORDER BY COALESCE(time,'99'), id", (day.isoformat(),))
    tot = {k: round(sum(m[k] or 0 for m in meals), 1) for k in ("kcal", "protein_g", "carb_g", "fat_g", "fiber_g")}
    target = active_targets(db)
    ck = db.q1("SELECT water_ml FROM checkins WHERE date=?", (day.isoformat(),))
    out: dict[str, Any] = {"dia": day.isoformat(), "refeicoes": meals, "total": tot, "meta": target,
                           "agua_ml": (ck or {}).get("water_ml")}
    if target:
        out["restante"] = {"kcal": round(target["kcal"] - tot["kcal"]), "proteina_g": round(target["proteina_g"] - tot["protein_g"]),
                           "carboidrato_g": round(target["carboidrato_g"] - tot["carb_g"]),
                           "gordura_g": round(target["gordura_g"] - tot["fat_g"]),
                           "fibra_g": round(target["fibra_g"] - tot["fiber_g"]) if target.get("fibra_g") is not None else None}
    return out


def consistency(db: Database, cfg: Config, end: date, days: int = 28) -> list[str]:
    """Aponta registros provavelmente incompletos — não julga a alimentação."""
    start = end - timedelta(days=days - 1)
    intake = met.daily_intake(db, start.isoformat(), end.isoformat())
    b = met.bmr(db, cfg, end)
    out = []
    if not intake:
        return ["Nenhuma refeição registrada no período."]
    low = [d for d, v in intake.items() if b.get("disponivel") and (v["kcal"] or 0) < 0.6 * b["mifflin"]]
    if low:
        out.append(f"{len(low)} dia(s) com total abaixo de 60% da sua TMB estimada: provavelmente registro incompleto.")
    cov = len([v for v in intake.values() if v["completo"]]) / days
    if cov < 0.7:
        out.append(f"Registro completo em {cov:.0%} dos dias; abaixo de 70% a recalibração do gasto fica desativada.")
    t = met.tdee(db, cfg, end, days)
    if t.get("adaptacao"):
        out.append(t["adaptacao"]["texto"])
    return out


# ------------------------------------------------------- otimização (opcional)
def suggest_day(db: Database, targets: dict[str, Any], food_ids: list[int], max_g: float = 400.0) -> dict[str, Any]:
    """Programação linear: quantidades (g) dos alimentos escolhidos que mais se aproximam das metas,
    com custo como critério secundário quando há preço. Requer scipy (extra 'otimizacao')."""
    try:
        from scipy.optimize import linprog
    except ImportError:
        return {"disponivel": False, "motivo": "Instale o extra de otimização: pip install 'healthmon[otimizacao]'"}
    foods = [f for f in (db.q1("SELECT * FROM foods WHERE id=?", (i,)) for i in food_ids) if f]
    if len(foods) < 3:
        return {"disponivel": False, "motivo": "Escolha ao menos 3 alimentos."}
    n = len(foods)
    goals = [("kcal", targets["kcal"], 1.0), ("protein_g", targets["proteina_g"], 8.0),
             ("carb_g", targets["carboidrato_g"], 2.0), ("fat_g", targets["gordura_g"], 4.0)]
    m = len(goals)
    # variáveis: x (g/100 de cada alimento), desvios positivos e negativos de cada meta
    c = [0.01 * (f["price_100g"] or 0) for f in foods] + [w for _, _, w in goals] * 2
    a_eq, b_eq = [], []
    for j, (key, tgt, _) in enumerate(goals):
        row = [(f[key] or 0) for f in foods] + [0.0] * (2 * m)
        row[n + j], row[n + m + j] = -1.0, 1.0
        a_eq.append(row)
        b_eq.append(tgt)
    bounds = [(0, (30 if (f["fat_g"] or 0) > 60 else max_g) / 100) for f in foods] + [(0, None)] * (2 * m)
    res = linprog(c, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        return {"disponivel": False, "motivo": "Sem solução viável com esses alimentos."}
    plan = [{"alimento": f["name"], "gramas": round(x * 100 / 5) * 5} for f, x in zip(foods, res.x[:n]) if x * 100 >= 5]
    tot = {k: round(sum((f[k] or 0) * x for f, x in zip(foods, res.x[:n]))) for k in ("kcal", "protein_g", "carb_g", "fat_g", "fiber_g")}
    cost = sum((f["price_100g"] or 0) * x for f, x in zip(foods, res.x[:n]))
    return {"disponivel": True, "itens": plan, "total": tot, "custo": round(cost, 2) if cost else None,
            "nota": "Solução matemática para bater as metas com os alimentos escolhidos; distribua nas refeições e "
                    "ajuste ao seu gosto. Não avalia micronutrientes."}
