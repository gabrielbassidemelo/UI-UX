"""Base de conhecimento científico local e atualizável, com busca BM25 (SQLite FTS5).

- Conteúdo: metadados e resumo próprio de cada evidência (sem texto protegido por direitos).
- Atualização: edite kb/evidencias.json ou acrescente entradas em $HEALTHMON_HOME/kb_extra.json.
- Verificação: `healthmon kb verificar` consulta o Crossref (API pública; envia apenas o DOI,
  nenhum dado pessoal) e confere título e ano. O resultado fica em kb_verificacao.json.
- Busca semântica por embeddings não foi adotada: com poucas dezenas de registros
  curados, BM25 recupera bem e não exige modelo nem serviço externo.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import Config, write_private_file

KB_FILE = Path(__file__).parent / "kb" / "evidencias.json"
GRADE = {"forte": "Forte (revisões sistemáticas, diretrizes ou ensaios grandes)",
         "moderada": "Moderada (ensaios/meta-análises com limitações)",
         "limitada": "Limitada (poucos estudos, revisões narrativas ou evidência indireta)",
         "regulatório": "Norma regulatória"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


@lru_cache(maxsize=4)
def _load(extra: str | None) -> tuple[dict[str, dict], sqlite3.Connection]:
    entries = {e["chave"]: e for e in json.loads(KB_FILE.read_text(encoding="utf-8"))["evidencias"]}
    if extra and Path(extra).exists():
        for e in json.loads(Path(extra).read_text(encoding="utf-8")).get("evidencias", []):
            if {"chave", "titulo", "autores", "ano"} <= set(e):
                entries[e["chave"]] = {**e, "origem": "usuário"}
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.execute("CREATE VIRTUAL TABLE kb USING fts5(chave UNINDEXED, texto, tokenize='unicode61 remove_diacritics 2')")
    for e in entries.values():
        text = " ".join(str(e.get(k, "")) for k in ("titulo", "resultados", "populacao", "tipo", "limitacoes")) \
               + " " + " ".join(e.get("temas", []))
        con.execute("INSERT INTO kb VALUES(?,?)", (e["chave"], text))
    return entries, con


def entries(cfg: Config | None = None) -> dict[str, dict]:
    return _load(str(cfg.home / "kb_extra.json") if cfg else None)[0]


def verification(cfg: Config | None) -> dict[str, Any]:
    if cfg is None:
        return {}
    f = cfg.home / "kb_verificacao.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


CLASSES = {"verificada": "Verificada na fonte",
           "parcialmente_verificada": "Parcialmente verificada (publicação confirmada; parte do conteúdo não conferida)",
           "nao_verificada": "Não verificada", "inadequada": "Inadequada para a regra"}
ORDER = ["inadequada", "nao_verificada", "parcialmente_verificada", "verificada"]
VERIF_FILE = Path(__file__).parent / "kb" / "verificacao.json"


@lru_cache(maxsize=1)
def _shipped() -> dict[str, Any]:
    """Resultado da verificação feita no desenvolvimento (Crossref + PubMed + leitura dos resumos por modelo de IA).
    Não é auditoria humana. Entradas sem registro contam como não verificadas."""
    if not VERIF_FILE.exists():
        return {"itens": {}}
    return json.loads(VERIF_FILE.read_text(encoding="utf-8"))


def status(key: str) -> dict[str, Any]:
    it = _shipped().get("itens", {}).get(key)
    return it or {"classe": "nao_verificada", "observacao": "Sem registro de verificação."}


def failing(keys: list[str], cfg: Config | None = None) -> list[str]:
    """Referências que NÃO atingem o nível mínimo exigido ([evidencias] nivel_minimo). Regras que dependem
    delas ficam desabilitadas."""
    minimum = (cfg.get("evidencias", "nivel_minimo", "parcialmente_verificada") if cfg else "parcialmente_verificada")
    if minimum not in ORDER:
        minimum = "verificada"
    floor = ORDER.index(minimum)
    return [k for k in dict.fromkeys(keys) if ORDER.index(status(k).get("classe", "nao_verificada")) < floor]


def summary() -> dict[str, Any]:
    ents = entries()
    counts = {c: 0 for c in ORDER}
    for k in ents:
        counts[status(k).get("classe", "nao_verificada")] += 1
    meta = {k: v for k, v in _shipped().items() if k != "itens"}
    return {"total": len(ents), "por_classe": counts, **meta}


def cite(keys: list[str], cfg: Config | None = None) -> list[dict[str, Any]]:
    ents, ver = entries(cfg), verification(cfg)
    out = []
    for k in dict.fromkeys(keys):
        e = ents.get(k)
        if not e:
            continue
        ref = f"{e['autores']} ({e['ano']}). {e['titulo']}. {e['periodico']}."
        out.append({"chave": k, "referencia": ref, "doi": e.get("doi"),
                    "url": f"https://doi.org/{e['doi']}" if e.get("doi") else None, "tipo": e["tipo"],
                    "grau": e["grau"], "resumo": e["resultados"], "limitacoes": e["limitacoes"],
                    "pmid": status(k).get("pmid"), "classe": status(k).get("classe", "nao_verificada"),
                    "classe_texto": CLASSES[status(k).get("classe", "nao_verificada")],
                    "observacao_verificacao": status(k).get("observacao"),
                    "verificado": status(k).get("classe") == "verificada",
                    "conferido_online": bool(ver.get(k, {}).get("ok"))})
    return out


def search(query: str, k: int = 5, cfg: Config | None = None) -> list[dict[str, Any]]:
    ents, con = _load(str(cfg.home / "kb_extra.json") if cfg else None)
    toks = [t for t in re.findall(r"\w+", _norm(query)) if len(t) > 2]
    if not toks:
        return []
    rows = con.execute("SELECT chave, bm25(kb) FROM kb WHERE kb MATCH ? ORDER BY bm25(kb) LIMIT ?",
                       (" OR ".join(f"{t}*" for t in toks), k)).fetchall()
    return [{**cite([r[0]], cfg)[0], "relevancia": round(-r[1], 2)} for r in rows]


def _similar(a: str, b: str) -> float:
    ta, tb = set(re.findall(r"\w+", _norm(a))), set(re.findall(r"\w+", _norm(b)))
    return len(ta & tb) / max(1, len(ta | tb))


def verify(cfg: Config, session: Any = None) -> dict[str, Any]:
    """Confere cada DOI no Crossref. Só o DOI trafega; nenhum dado pessoal é enviado."""
    import requests
    s = session or requests.Session()
    out: dict[str, Any] = {}
    for k, e in entries(cfg).items():
        if not e.get("doi"):
            out[k] = {"ok": False, "motivo": "sem DOI (conferir manualmente)"}
            continue
        try:
            r = s.get(f"https://api.crossref.org/works/{e['doi']}", timeout=20,
                      headers={"User-Agent": "healthmon-kb-check/2.0"})
            if r.status_code != 200:
                out[k] = {"ok": False, "motivo": f"Crossref HTTP {r.status_code}"}
                continue
            m = r.json()["message"]
            title = (m.get("title") or [""])[0]
            year = (m.get("issued", {}).get("date-parts") or [[None]])[0][0]
            sim = _similar(title, e["titulo"])
            ok = sim >= 0.6 and (year is None or abs(int(year) - int(e["ano"])) <= 1)
            out[k] = {"ok": ok, "similaridade_titulo": round(sim, 2), "ano_crossref": year,
                      "titulo_crossref": title[:200]}
        except Exception as ex:  # noqa: BLE001
            out[k] = {"ok": False, "motivo": type(ex).__name__}
    write_private_file(cfg.home / "kb_verificacao.json", json.dumps(out, ensure_ascii=False, indent=1))
    return {"total": len(out), "confirmadas": sum(1 for v in out.values() if v["ok"]),
            "pendentes": [k for k, v in out.items() if not v["ok"]]}
