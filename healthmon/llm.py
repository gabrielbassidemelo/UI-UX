"""Camada opcional de linguagem natural sobre a análise determinística.

Modos (config [ia].modo):
- "regras": nenhum modelo; o texto vem do motor de regras (padrão).
- "local":  Ollama na própria máquina (http://127.0.0.1:11434). Nada sai da máquina.
- "nuvem":  Claude (Anthropic). Só funciona após consentimento explícito gravado
            (`healthmon ia-nuvem consentir`). Envia APENAS o JSON agregado mostrado por
            `healthmon ia-nuvem previa`: datas, médias/valores diários dos indicadores,
            comparações com a linha de base e a lista de sinais. Nunca envia nome,
            e-mail, tokens, identificadores do dispositivo, localização/GPS ou dados brutos.

O modelo só redige um comentário; os números do relatório vêm sempre do motor local.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import requests

from .config import Config, write_private_file

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Você redige comentários curtos, em português do Brasil, sobre dados de um
dispositivo vestível (Garmin) de uma pessoa adulta, comparados ao histórico dela mesma.
Regras obrigatórias:
- Use somente os números e fatos do JSON fornecido; não invente valores nem métricas.
- Não faça diagnóstico, não cite doenças como provável causa, não prescreva medicamentos.
- Não trate associações como causalidade (use "costuma acompanhar", "está associado").
- Lembre que medições de vestíveis têm limitações e não são exames clínicos.
- Diferencie alterações pontuais (um dia) de tendências persistentes.
- Sugestões apenas de rotina, descanso, sono e intensidade de treino, de forma prudente.
- Se houver sinais persistentes desfavoráveis ou dados ausentes, diga isso com clareza.
- No máximo 6 frases ou 5 tópicos curtos. Sem títulos."""

CONSENT_SCOPE = ("Envio à Anthropic (API Claude) do JSON agregado de cada relatório: datas, "
                 "valores e médias dos indicadores, comparações com a linha de base, tendências, "
                 "associações e sinais. Sem nome, e-mail, tokens, IDs de dispositivo ou GPS.")


def strip_identifiers(facts: dict[str, Any]) -> dict[str, Any]:
    """Defesa extra: remove campos que nunca devem sair da máquina."""
    banned = {"nome", "email", "token", "device_id", "activity_id", "raw", "gps", "lat", "lon",
              "name", "user", "displayName"}

    def clean(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if k not in banned}
        if isinstance(o, list):
            return [clean(v) for v in o]
        return o
    return clean(facts)


def consent_status(cfg: Config) -> dict[str, Any] | None:
    if not cfg.consent_file.exists():
        return None
    try:
        return json.loads(cfg.consent_file.read_text(encoding="utf-8"))
    except ValueError:
        return None


def grant_consent(cfg: Config) -> dict[str, Any]:
    rec = {"concedido_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "provedor": "Anthropic", "modelo": cfg.get("ia", "nuvem_modelo"), "escopo": CONSENT_SCOPE}
    write_private_file(cfg.consent_file, json.dumps(rec, ensure_ascii=False, indent=2))
    return rec


def revoke_consent(cfg: Config) -> None:
    if cfg.consent_file.exists():
        cfg.consent_file.unlink()


def _user_prompt(kind: str, facts: dict[str, Any]) -> str:
    return (f"Tipo de relatório: {kind}.\nDados (JSON):\n"
            f"{json.dumps(facts, ensure_ascii=False, default=str)}\n\n"
            "Escreva o comentário.")


def _ollama(cfg: Config, kind: str, facts: dict[str, Any]) -> str:
    from urllib.parse import urlsplit
    url = cfg.get("ia", "ollama_url", "http://127.0.0.1:11434").rstrip("/")
    if (urlsplit(url).hostname or "") not in ("127.0.0.1", "localhost", "::1", "ollama"):
        raise RuntimeError("ollama_url aponta para outra máquina; a modalidade local só aceita o próprio servidor")
    r = requests.post(f"{url}/api/chat", timeout=180, json={
        "model": cfg.get("ia", "ollama_modelo"),
        "stream": False,
        "options": {"temperature": 0.3},
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": _user_prompt(kind, facts)}],
    })
    r.raise_for_status()
    return r.json()["message"]["content"].strip()


def _claude(cfg: Config, kind: str, facts: dict[str, Any]) -> str:
    import anthropic  # dependência opcional: pip install "healthmon[cloud-ai]"
    key = cfg.secret("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY não configurada em secrets.env")
    client = anthropic.Anthropic(api_key=key)
    response = client.beta.messages.create(
        model=cfg.get("ia", "nuvem_modelo", "claude-opus-5-5"),
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _user_prompt(kind, facts)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("O modelo recusou a solicitação")
    return "".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()


def narrate(cfg: Config, kind: str, facts: dict[str, Any]) -> tuple[str | None, str]:
    """Retorna (texto ou None, modo efetivamente usado).

    A IA só redige. Ela não é chamada em dia de pausa por sintomas, e o texto que produzir é descartado
    se contiver diagnóstico, dose, alteração de medicamento ou incentivo a esforço intenso (safety.llm_text_allowed)."""
    from . import safety
    mode = cfg.get("ia", "modo", "regras")
    level = (facts.get("prontidao") or {}).get("nivel") if isinstance(facts.get("prontidao"), dict) else None
    if mode == "regras" or level == "parar":
        return None, "regras"
    facts = strip_identifiers(facts)
    try:
        if mode == "local":
            text, used = _ollama(cfg, kind, facts), "local"
        elif mode == "nuvem":
            if not consent_status(cfg):
                log.warning("IA em nuvem configurada mas SEM consentimento: usando regras locais.")
                return None, "regras"
            text, used = _claude(cfg, kind, facts), "nuvem"
        else:
            return None, "regras"
    except Exception as e:  # noqa: BLE001 — IA é opcional; relatório segue sem ela
        log.warning("IA (%s) indisponível: %s", mode, type(e).__name__)
        return None, "regras"
    ok, why = safety.llm_text_allowed(text or "", level)
    if not ok:
        log.warning("Comentário da IA descartado pelo filtro de segurança.")
        return None, "regras"
    return text, used
