"""Backups criptografados do banco (SQLite online backup + Fernet/AES com chave derivada por scrypt).

Formato do arquivo .hmbk: b"HMBK1" | salt (16 bytes) | token Fernet (AES-128-CBC + HMAC-SHA256).
Sem a frase-senha (HEALTHMON_BACKUP_PASSPHRASE) não é possível ler o backup.
"""
from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet

from .config import Config, ensure_private_dir, write_private_file

MAGIC = b"HMBK1"


def _key(passphrase: str, salt: bytes) -> bytes:
    raw = hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    return base64.urlsafe_b64encode(raw)


def _passphrase(cfg: Config) -> str:
    p = cfg.secret("HEALTHMON_BACKUP_PASSPHRASE")
    if not p or len(p) < 12:
        raise RuntimeError("Defina HEALTHMON_BACKUP_PASSPHRASE (mín. 12 caracteres) em secrets.env")
    return p


def create_backup(cfg: Config, dest_dir: Path | None = None) -> Path:
    dest_dir = dest_dir or cfg.backup_dir
    ensure_private_dir(dest_dir)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "snap.db"
        src = sqlite3.connect(cfg.db_path)
        dst = sqlite3.connect(tmp)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        data = tmp.read_bytes()
    salt = os.urandom(16)
    token = Fernet(_key(_passphrase(cfg), salt)).encrypt(data)
    stamp = datetime.now(cfg.tz).strftime("%Y%m%d-%H%M%S")
    out = dest_dir / f"healthmon-{stamp}.hmbk"
    write_private_file(out, MAGIC + salt + token)
    rotate(cfg, dest_dir)
    return out


def decrypt_backup(cfg: Config, path: Path) -> bytes:
    blob = path.read_bytes()
    if not blob.startswith(MAGIC):
        raise ValueError("Arquivo não é um backup do healthmon")
    salt, token = blob[5:21], blob[21:]
    return Fernet(_key(_passphrase(cfg), salt)).decrypt(token)


def verify_backup(cfg: Config, path: Path) -> dict:
    """Teste de restauração sem tocar no banco em uso: decifra, abre e roda integrity_check."""
    data = decrypt_backup(cfg, path)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "check.db"
        tmp.write_bytes(data)
        con = sqlite3.connect(tmp)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            n = con.execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0]
        finally:
            con.close()
    if ok != "ok":
        raise RuntimeError(f"Backup {path.name} falhou na verificação de integridade: {ok}")
    return {"arquivo": path.name, "integridade": ok, "dias": n, "bytes": len(data)}


def restore_backup(cfg: Config, path: Path) -> Path:
    data = decrypt_backup(cfg, path)
    target = cfg.db_path
    if target.exists():
        keep = target.with_suffix(f".antes-restauracao-{datetime.now().strftime('%Y%m%d%H%M%S')}.db")
        target.rename(keep)
        for ext in ("-wal", "-shm"):
            side = Path(str(target) + ext)
            if side.exists():
                side.unlink()
    write_private_file(target, data)
    sqlite3.connect(target).execute("PRAGMA integrity_check").fetchone()
    return target


def rotate(cfg: Config, dest_dir: Path) -> list[Path]:
    """Mantém os N backups diários mais recentes + 1 por semana das últimas M semanas."""
    keep_daily = int(cfg.get("backup", "manter_diarios", 14))
    keep_weekly = int(cfg.get("backup", "manter_semanais", 8))
    files = sorted(dest_dir.glob("healthmon-*.hmbk"), reverse=True)
    keep: set[Path] = set(files[:keep_daily])
    weeks: dict[str, Path] = {}
    for f in files:
        try:
            dt = datetime.strptime(f.stem.split("-", 1)[1], "%Y%m%d-%H%M%S")
        except ValueError:
            continue
        wk = dt.strftime("%G-%V")
        weeks.setdefault(wk, f)
    keep |= set(list(weeks.values())[:keep_weekly])
    removed = []
    for f in files:
        if f not in keep:
            f.unlink()
            removed.append(f)
    return removed
