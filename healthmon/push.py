"""Notificações push da web (PWA no iPhone, iOS 16.4+ com o app na Tela de Início).

O conteúdo é cifrado de ponta a ponta entre este servidor e o seu aparelho (RFC 8291); o
serviço de push da Apple só vê o endereço de entrega. Mesmo assim, o texto enviado é
genérico por padrão ("há um novo relatório"), sem números de saúde.
Requer o extra `push` (pywebpush) e o dashboard servido por HTTPS.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .config import Config, write_private_file
from .db import Database, utcnow

log = logging.getLogger(__name__)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def vapid_keys(cfg: Config) -> dict[str, str]:
    f = cfg.home / "vapid.json"
    if f.exists():
        return json.loads(f.read_text())
    key = ec.generate_private_key(ec.SECP256R1())
    priv = _b64(key.private_numbers().private_value.to_bytes(32, "big"))
    pub = _b64(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    data = {"private": priv, "public": pub}
    write_private_file(f, json.dumps(data))
    return data


PUSH_HOSTS = ("web.push.apple.com", "fcm.googleapis.com", "updates.push.services.mozilla.com")
PUSH_SUFFIXES = (".push.apple.com", ".notify.windows.com")


def allowed_endpoint(ep: str) -> bool:
    """Só serviços de push conhecidos: o servidor nunca envia requisições a endereços arbitrários (anti-SSRF)."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(ep)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return (u.scheme == "https" and u.port in (None, 443) and not u.username
            and (host in PUSH_HOSTS or host.endswith(PUSH_SUFFIXES)))


def subscribe(db: Database, sub: dict[str, Any]) -> None:
    ep, keys = str(sub.get("endpoint", "")), sub.get("keys") or {}
    if not allowed_endpoint(ep) or len(ep) > 1000 or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("Inscrição de push inválida ou de serviço não reconhecido")
    db.insert("push_subscriptions", {"endpoint": ep, "p256dh": str(keys["p256dh"])[:200], "auth": str(keys["auth"])[:100],
                                     "created_at": utcnow()}, upsert_on="endpoint")


def unsubscribe(db: Database, endpoint: str) -> None:
    db.x("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))


def send_push(cfg: Config, db: Database, title: str, body: str, url: str = "/") -> int:
    subs = db.q("SELECT * FROM push_subscriptions")
    if not subs:
        raise RuntimeError("Nenhum aparelho inscrito para push (ative em Configurações no dashboard)")
    from pywebpush import WebPushException, webpush
    keys = vapid_keys(cfg)
    contact = cfg.get("notificacoes", "push_contato", "") or "mailto:admin@localhost"
    sent = 0
    for s in subs:
        if not allowed_endpoint(s["endpoint"]):
            unsubscribe(db, s["endpoint"])
            continue
        try:
            webpush(subscription_info={"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}},
                    data=json.dumps({"title": title, "body": body, "url": url}, ensure_ascii=False),
                    vapid_private_key=keys["private"], vapid_claims={"sub": contact}, ttl=6 * 3600)
            sent += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):          # inscrição expirada: remove
                unsubscribe(db, s["endpoint"])
            log.warning("Push falhou (HTTP %s)", code)
        except Exception as e:  # noqa: BLE001 — uma inscrição ruim não impede as demais
            log.warning("Push falhou (%s)", type(e).__name__)
    if not sent:
        raise RuntimeError("Nenhum push entregue")
    return sent
