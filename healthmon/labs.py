"""Exames laboratoriais: registro estruturado preservando valor, unidade, data e a referência
DO LABORATÓRIO. O sistema não usa faixas próprias, não diagnostica e não sugere tratamento:
só aponta resultados fora da referência informada e tendências, para levar ao médico.

Os arquivos originais (PDF/imagem) ficam em $HEALTHMON_HOME/exames (permissão 600) e nunca
saem da máquina. A extração automática de PDF não está implementada: os valores entram por
formulário ou CSV (colunas: data, analito, valor, unidade, ref_min, ref_max, laboratorio).
"""
from __future__ import annotations

import csv
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import analysis as an
from .config import Config, ensure_private_dir, write_private_file
from .db import Database, utcnow

ALIASES = {
    "hemoglobina": ["hemoglobina", "hb"], "hematocrito": ["hematocrito"], "leucocitos": ["leucocitos"],
    "plaquetas": ["plaquetas"], "glicemia": ["glicemia", "glicose"], "hba1c": ["hemoglobina glicada", "hba1c", "a1c"],
    "insulina": ["insulina"], "colesterol_total": ["colesterol total"], "ldl": ["ldl"], "hdl": ["hdl"],
    "triglicerideos": ["triglicerideos", "triglicerides"], "testosterona": ["testosterona"], "cortisol": ["cortisol"],
    "vitamina_d": ["vitamina d", "25-oh", "25 oh", "25-hidroxi"], "vitamina_b12": ["b12", "cobalamina"],
    "ferritina": ["ferritina"], "tsh": ["tsh"], "t3": ["t3"], "t4": ["t4"], "tgo": ["tgo", "ast"], "tgp": ["tgp", "alt"],
    "ggt": ["ggt", "gama gt", "gama-gt"], "creatinina": ["creatinina"], "ureia": ["ureia"], "acido_urico": ["acido urico"],
    "pcr": ["pcr", "proteina c reativa"],
}
CONTEXT = {  # contexto informativo (não é interpretação clínica)
    "creatinina": "Creatina suplementar e massa muscular elevada aumentam a creatinina sem indicar lesão renal; informe o médico.",
    "tgo": "Treino intenso nos dias anteriores à coleta pode elevar TGO/TGP e CK de forma transitória.",
    "tgp": "Treino intenso nos dias anteriores à coleta pode elevar TGO/TGP de forma transitória.",
    "cortisol": "Varia com horário da coleta, sono e estresse agudo.",
    "testosterona": "Varia com horário da coleta, sono recente e déficit calórico.",
    "glicemia": "Depende do jejum e do horário; resultados isolados têm variação.",
    "ferritina": "Eleva-se em inflamação e após exercício intenso.",
}


def _n(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s).lower().strip())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def analyte_key(name: str) -> str:
    n = _n(name)
    best = None                                   # o alias mais longo vence: "hemoglobina glicada" ≠ "hemoglobina"
    for key, names in ALIASES.items():
        for a in names:
            if re.search(rf"(^|[^a-z0-9]){re.escape(a)}($|[^a-z0-9])", n) and (best is None or len(a) > len(best[1])):
                best = (key, a)
    if best:
        return best[0]
    return re.sub(r"[^a-z0-9]+", "_", n).strip("_")[:40]


def _num(v: Any) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    import math
    try:
        f = float(str(v).strip().replace(".", "").replace(",", ".") if re.match(r"^\d{1,3}(\.\d{3})+(,\d+)?$", str(v).strip())
                  else str(v).strip().replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(f) or abs(f) > 1e9:
        raise ValueError("Valor numérico fora do intervalo aceito")
    return f


def add_result(db: Database, day: str, analyte: str, value: Any, unit: str = "", ref_low: Any = None,
               ref_high: Any = None, lab: str = "", ref_text: str = "", source_file: str = "") -> int:
    date.fromisoformat(day)
    if not analyte.strip():
        raise ValueError("Informe o nome do exame")
    v = _num(value)
    return db.insert("labs", {"date": day, "lab": lab[:80], "analyte": analyte.strip()[:80], "analyte_key": analyte_key(analyte),
                              "value": v, "value_text": None if v is not None else str(value)[:60], "unit": unit.strip()[:30],
                              "ref_low": _num(ref_low), "ref_high": _num(ref_high), "ref_text": ref_text[:120],
                              "source_file": source_file, "created_at": utcnow()})


def import_csv(db: Database, path: Path) -> int:
    text = Path(path).read_text(encoding="utf-8-sig")
    rd = csv.DictReader(text.splitlines(), delimiter=";" if text.split("\n", 1)[0].count(";") >= 2 else ",")
    n = 0
    for r in rd:
        r = {_n(k): v for k, v in r.items() if k}
        add_result(db, r.get("data", ""), r.get("analito") or r.get("exame", ""), r.get("valor"), r.get("unidade", ""),
                   r.get("ref_min"), r.get("ref_max"), r.get("laboratorio", ""), r.get("referencia", ""))
        n += 1
    return n


def store_original(cfg: Config, filename: str, content: bytes) -> str:
    """Guarda o documento original localmente (600). Não processa nem envia."""
    if len(content) > 15 * 1024 * 1024:
        raise ValueError("Arquivo acima de 15 MB")
    ext = Path(filename).suffix.lower()
    if ext not in (".pdf", ".png", ".jpg", ".jpeg", ".heic", ".csv"):
        raise ValueError("Formato não aceito (use PDF, imagem ou CSV)")
    d = cfg.home / "exames"
    ensure_private_dir(d)
    import secrets as _secrets
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(filename).name)[:80]
    name = f"{date.today().isoformat()}_{_secrets.token_hex(3)}_{safe}"   # sufixo aleatório: nunca sobrescreve
    write_private_file(d / name, content)
    return name


def flag(r: dict[str, Any]) -> str:
    if r["value"] is None or (r["ref_low"] is None and r["ref_high"] is None):
        return "sem_referencia"
    if r["ref_low"] is not None and r["value"] < r["ref_low"]:
        return "abaixo"
    if r["ref_high"] is not None and r["value"] > r["ref_high"]:
        return "acima"
    return "dentro"


def overview(db: Database) -> dict[str, Any]:
    rows = db.q("SELECT * FROM labs ORDER BY analyte_key, date")
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["analyte_key"], []).append(r)
    items, attention = [], []
    for key, hist in by.items():
        last = hist[-1]
        f = flag(last)
        trend = None
        nums = [h for h in hist if h["value"] is not None and h["unit"] == last["unit"]]
        if len(nums) >= 2 and nums[-2]["value"]:
            ch = 100 * (nums[-1]["value"] - nums[-2]["value"]) / abs(nums[-2]["value"])
            trend = {"variacao_pct": round(ch, 1), "desde": nums[-2]["date"], "n": len(nums)}
        item = {"chave": key, "analito": last["analyte"], "data": last["date"], "valor": last["value"],
                "valor_texto": last["value_text"], "unidade": last["unit"], "ref_min": last["ref_low"],
                "ref_max": last["ref_high"], "ref_texto": last["ref_text"], "laboratorio": last["lab"],
                "situacao": f, "tendencia": trend, "contexto": CONTEXT.get(key),
                "historico": [{"data": h["date"], "valor": h["value"], "unidade": h["unit"]} for h in hist]}
        items.append(item)
        if f in ("abaixo", "acima"):
            attention.append(f"{last['analyte']} ({last['date']}): {last['value']:g} {last['unit']} — {f} da referência do "
                             f"laboratório. Leve este resultado ao seu médico.")
    return {"itens": items, "atencao": attention,
            "aviso": "Comparação apenas com a referência informada pelo próprio laboratório. Não é diagnóstico; "
                     "valores isolados fora da faixa são comuns e precisam de interpretação médica."}


def context_at(db: Database, day: str, days: int = 28) -> dict[str, Any]:
    """Contexto de sono, recuperação e treino nas semanas anteriores a uma coleta (descritivo)."""
    end = date.fromisoformat(day)
    store = an.load_store(db, end, days + 5)
    out = {}
    for k in ("sleep_seconds", "hrv_last_night", "resting_hr", "stress_avg", "training_load", "steps"):
        st_ = an.period_stats(store, k, end - timedelta(days=days - 1), end)
        if st_["n"]:
            out[k] = st_
    return {"data": day, "janela_dias": days, "indicadores": out,
            "nota": "Descrição do período anterior à coleta, para conversar com o médico; não estabelece causa."}
