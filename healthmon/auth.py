"""Autenticação do dashboard: senha com scrypt, sessões aleatórias e bloqueio por tentativas."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from .config import Config, write_private_file
from .db import Database

N, R, P = 2**17, 8, 1          # parâmetros recomendados pela OWASP para scrypt
MAXMEM = 256 * 1024 * 1024


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=N, r=R, p=P, maxmem=MAXMEM, dklen=32)
    return f"scrypt${N}${R}${P}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              maxmem=MAXMEM, dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


def set_password(cfg: Config, pw: str) -> None:
    if len(pw) < 10:
        raise ValueError("Use uma senha com pelo menos 10 caracteres.")
    write_private_file(cfg.auth_file, json.dumps({"password": hash_password(pw)}))
    # Trocar a senha encerra todas as sessões abertas (ex.: aparelho perdido).
    if cfg.db_path.exists():
        with Database(cfg.db_path).conn() as c:
            c.execute("DELETE FROM sessions")


def stored_hash(cfg: Config) -> str | None:
    if not cfg.auth_file.exists():
        return None
    return json.loads(cfg.auth_file.read_text()).get("password")


def _th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Database, days: int, ua: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with db.conn() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
        c.execute("INSERT INTO sessions VALUES(?,?,?,?)",
                  (_th(token), now.isoformat(), (now + timedelta(days=days)).isoformat(), ua[:200]))
    return token


def valid_session(db: Database, token: str | None) -> bool:
    if not token:
        return False
    with db.conn() as c:
        r = c.execute("SELECT expires_at FROM sessions WHERE token_hash=?", (_th(token),)).fetchone()
    return bool(r and datetime.fromisoformat(r["expires_at"]) > datetime.now(timezone.utc))


def end_session(db: Database, token: str | None) -> None:
    if token:
        with db.conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_th(token),))


class LoginThrottle:
    """Após 5 tentativas erradas em 15 min, bloqueia por 15 min.

    Atrás do Docker e do tailscale serve todo cliente aparece com o mesmo IP, então o limite é
    GLOBAL de propósito (o app tem um único usuário). `attempt()` reserva a tentativa ANTES de
    verificar a senha e serializa a verificação, o que impede disparar tentativas em paralelo."""

    def attempt(self, verify) -> bool | None:
        """Retorna None se bloqueado; senão o resultado de verify() (True/False)."""
        with self.verify_lock:
            if self.blocked("global"):
                return None
            self.fail("global")                 # reserva
            ok = bool(verify())
            if ok:
                self.reset("global")
            return ok

    def __init__(self, max_fail: int = 5, window_s: int = 900):
        self.max_fail, self.window = max_fail, window_s
        self.fails: dict[str, list[float]] = {}
        self.lock = threading.Lock()
        self.verify_lock = threading.Lock()

    def blocked(self, ip: str) -> bool:
        with self.lock:
            now = time.time()
            self.fails[ip] = [t for t in self.fails.get(ip, []) if now - t < self.window]
            return len(self.fails[ip]) >= self.max_fail

    def fail(self, ip: str) -> None:
        with self.lock:
            self.fails.setdefault(ip, []).append(time.time())

    def reset(self, ip: str) -> None:
        with self.lock:
            self.fails.pop(ip, None)
