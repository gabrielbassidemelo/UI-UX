"""Notificações por Telegram e/ou e-mail, com nível de conteúdo, horário silencioso e limites."""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime, time, timedelta, timezone
from email.message import EmailMessage

import requests

from .config import Config
from .db import Database

log = logging.getLogger(__name__)


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def in_quiet_hours(cfg: Config, now: datetime | None = None) -> bool:
    now = (now or datetime.now(cfg.tz)).astimezone(cfg.tz)
    a = _parse_hhmm(cfg.get("notificacoes", "silencio_inicio", "22:30"))
    b = _parse_hhmm(cfg.get("notificacoes", "silencio_fim", "07:00"))
    t = now.time()
    return (a <= t or t < b) if a > b else (a <= t < b)


def send_telegram(cfg: Config, text: str) -> None:
    token, chat = cfg.secret("TELEGRAM_BOT_TOKEN"), cfg.secret("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise RuntimeError("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID não configurados")
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True})
    r.raise_for_status()


def send_email(cfg: Config, subject: str, text: str) -> None:
    host = cfg.get("notificacoes", "smtp_host")
    if not host:
        raise RuntimeError("smtp_host não configurado")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("notificacoes", "email_de")
    msg["To"] = cfg.get("notificacoes", "email_para")
    msg.set_content(text)
    with smtplib.SMTP(host, int(cfg.get("notificacoes", "smtp_porta", 587)), timeout=30) as s:
        s.starttls()
        user = cfg.get("notificacoes", "smtp_usuario")
        if user:
            s.login(user, cfg.secret("SMTP_PASSWORD") or "")
        s.send_message(msg)


def send(cfg: Config, subject: str, text: str) -> list[str]:
    sent = []
    for ch in cfg.get("notificacoes", "canais", []) or []:
        try:
            if ch == "telegram":
                send_telegram(cfg, f"{subject}\n\n{text}")
            elif ch == "email":
                send_email(cfg, subject, text)
            else:
                continue
            sent.append(ch)
        except Exception as e:  # noqa: BLE001
            log.warning("Falha ao enviar por %s: %s", ch, type(e).__name__)
    return sent


def _link(cfg: Config) -> str:
    url = cfg.get("notificacoes", "url_dashboard", "")
    return f"\n{url}" if url else ""


def notify_report(cfg: Config, kind: str, title: str, summary: str) -> list[str]:
    if cfg.get("notificacoes", "nivel_conteudo", "minimo") == "completo":
        body = summary
    else:
        body = "Seu relatório está disponível no dashboard."
    return send(cfg, title if cfg.get("notificacoes", "nivel_conteudo") == "completo"
                else f"healthmon: relatório {kind}", body + _link(cfg))


def dispatch_alerts(cfg: Config, db: Database, now: datetime | None = None) -> int:
    """Envia alertas pendentes respeitando horário silencioso e limite diário."""
    if not cfg.get("notificacoes", "canais", []):
        return 0
    now = now or datetime.now(cfg.tz)
    if in_quiet_hours(cfg, now):
        return 0
    limit = int(cfg.get("notificacoes", "max_alertas_dia", 2))
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    sent_count = 0
    full = cfg.get("notificacoes", "nivel_conteudo", "minimo") == "completo"
    for a in db.pending_alerts():
        is_sync = a["kind"] == "falha_sincronizacao"
        if not is_sync and db.count_notified_since(since) >= limit:
            break
        body = a["message"] if (full or is_sync) else "Há um novo alerta no dashboard."
        subject = f"healthmon: {a['title']}" if (full or is_sync) else "healthmon: novo alerta"
        if send(cfg, subject, body + _link(cfg)):
            db.mark_alert_notified(a["id"])
            sent_count += 1
    return sent_count
