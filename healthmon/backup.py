"""Backups criptografados do banco (SQLite online backup).

HMBK2 (preferido, quando existe $HEALTHMON_HOME/recovery_public.key): criptografia ASSIMÉTRICA.
  b"HMBK2" | tamanho do cabeçalho (4 bytes, big-endian) | cabeçalho JSON | nonce (12) | texto cifrado
  | SHA-256 de tudo que o precede (32, só detecta corrupção sem a chave).
  Chave efêmera X25519 por backup + ECDH com a chave pública de recuperação + HKDF-SHA256 +
  ChaCha20-Poly1305; o cabeçalho é autenticado (AAD). O servidor só tem a chave pública: cria, mas
  NÃO decifra. A chave privada fica só com o usuário.
HMBK1 (legado): b"HMBK1" | salt (16) | token Fernet; chave derivada da frase-senha HEALTHMON_BACKUP_PASSPHRASE.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import Config, ensure_private_dir, write_private_file

MAGIC = b"HMBK1"
MAGIC2 = b"HMBK2"
HKDF_INFO = b"healthmon-hmbk2-v1"
PUBKEY_FILE = "recovery_public.key"
_RAW = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


class BackupError(RuntimeError):
    pass


# --- chave de recuperação (X25519) -------------------------------------------------
def _b32(raw: bytes) -> str:
    return base64.b32encode(raw).decode().rstrip("=")


def generate_recovery_keypair() -> tuple[str, str]:
    """Devolve (chave_privada_texto, chave_publica_texto). A privada é para o usuário anotar
    (nunca gravar no servidor); a pública vai para $HEALTHMON_HOME/recovery_public.key."""
    sk = X25519PrivateKey.generate()
    raw = sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    body = _b32(raw + hashlib.sha256(raw).digest()[:4])           # 4 bytes de verificação contra erro de digitação
    private = "HMRK-" + "-".join(body[i:i + 4] for i in range(0, len(body), 4))
    public = base64.urlsafe_b64encode(sk.public_key().public_bytes(**_RAW)).decode()
    return private, public


def _parse_private(text: str) -> X25519PrivateKey:
    t = "".join(text.split()).replace("-", "").upper()
    if t.startswith("HMRK"):
        t = t[4:]
    try:
        raw = base64.b32decode(t + "=" * (-len(t) % 8))
    except Exception:
        raise BackupError("Chave de recuperação inválida (formato)") from None
    if len(raw) != 36 or hashlib.sha256(raw[:32]).digest()[:4] != raw[32:]:
        raise BackupError("Chave de recuperação inválida (confira a digitação)")
    return X25519PrivateKey.from_private_bytes(raw[:32])


def public_key_path(cfg: Config) -> Path:
    return cfg.home / PUBKEY_FILE


def save_public_key(cfg: Config, public: str) -> Path:
    p = public_key_path(cfg)
    write_private_file(p, public.strip() + "\n")
    return p


def _load_public(cfg: Config) -> bytes | None:
    p = public_key_path(cfg)
    if not p.exists():
        return None
    try:
        raw = base64.urlsafe_b64decode(p.read_text().strip())
        X25519PublicKey.from_public_bytes(raw)
    except Exception:
        raise BackupError(f"{PUBKEY_FILE} inválido: gere o par de chaves novamente") from None
    return raw


def _derive(shared: bytes, eph_pub: bytes, recip_pub: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=eph_pub, info=HKDF_INFO + recip_pub).derive(shared)


def _key(passphrase: str, salt: bytes) -> bytes:
    raw = hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    return base64.urlsafe_b64encode(raw)


def _passphrase(cfg: Config) -> str:
    p = cfg.secret("HEALTHMON_BACKUP_PASSPHRASE")
    if not p or len(p) < 12:
        raise RuntimeError("Defina HEALTHMON_BACKUP_PASSPHRASE (mín. 12 caracteres) em secrets.env")
    return p


def _snapshot(cfg: Config, tmp: Path) -> dict:
    """Snapshot consistente via API de backup do SQLite (seguro com o banco em uso/WAL), validado."""
    src = sqlite3.connect(cfg.db_path, timeout=30)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)                                  # cópia transacional única: estado consistente
        dst.execute("PRAGMA journal_mode=DELETE")        # arquivo autônomo, sem -wal
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise BackupError(f"Backup abortado: o banco falhou em integrity_check ({ok})")
        rows = dst.execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0]
        uv = dst.execute("PRAGMA user_version").fetchone()[0]
        ddl = "\n".join(r[0] or "" for r in dst.execute("SELECT sql FROM sqlite_master ORDER BY name"))
        dst.commit()
    finally:
        src.close()
        dst.close()
    return {"linhas_daily_metrics": rows,
            "esquema": {"user_version": uv, "hash": hashlib.sha256(ddl.encode()).hexdigest()[:16]}}


def _encrypt_v2(data: bytes, recip_pub: bytes, meta: dict) -> bytes:
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(**_RAW)
    key = _derive(eph.exchange(X25519PublicKey.from_public_bytes(recip_pub)), eph_pub, recip_pub)
    header = json.dumps({"v": 2, "criado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "sha256_banco": hashlib.sha256(data).hexdigest(), **meta,
                         "efemera": base64.urlsafe_b64encode(eph_pub).decode(),
                         "destinatario": hashlib.sha256(recip_pub).hexdigest()[:16]},
                        sort_keys=True, separators=(",", ":")).encode()
    pre = MAGIC2 + len(header).to_bytes(4, "big") + header
    nonce = os.urandom(12)
    body = pre + nonce + ChaCha20Poly1305(key).encrypt(nonce, data, pre)
    return body + hashlib.sha256(body).digest()


def _split_v2(blob: bytes) -> tuple[dict, bytes, bytes, bytes, bytes]:
    """-> (cabeçalho, pre(AAD), nonce, ciphertext, trailer). Valida o checksum de corrupção."""
    if len(blob) < 5 + 4 + 12 + 16 + 32 or not blob.startswith(MAGIC2):
        raise BackupError("Arquivo não é um backup HMBK2 válido")
    if hashlib.sha256(blob[:-32]).digest() != blob[-32:]:
        raise BackupError("Backup corrompido (checksum do arquivo não confere)")
    hlen = int.from_bytes(blob[5:9], "big")
    if 9 + hlen + 12 + 16 > len(blob) - 32:
        raise BackupError("Cabeçalho do backup inválido")
    try:
        header = json.loads(blob[9:9 + hlen])
    except ValueError:
        raise BackupError("Cabeçalho do backup inválido") from None
    pre = blob[:9 + hlen]
    return header, pre, blob[9 + hlen:21 + hlen], blob[21 + hlen:-32], blob[-32:]


def read_header(path: Path) -> dict | None:
    """Cabeçalho (não secreto) de um backup HMBK2; None para HMBK1."""
    blob = Path(path).read_bytes()
    if blob.startswith(MAGIC2):
        return _split_v2(blob)[0]
    return None


def create_backup(cfg: Config, dest_dir: Path | None = None) -> Path:
    dest_dir = dest_dir or cfg.backup_dir
    ensure_private_dir(dest_dir)
    recip = _load_public(cfg)
    if recip is None:
        _passphrase(cfg)                                 # falha cedo se nem HMBK1 for possível
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "snap.db"
        meta = _snapshot(cfg, tmp)
        data = tmp.read_bytes()
    if recip is not None:
        blob = _encrypt_v2(data, recip, meta)
    else:
        salt = os.urandom(16)
        blob = MAGIC + salt + Fernet(_key(_passphrase(cfg), salt)).encrypt(data)
    stamp = datetime.now(cfg.tz).strftime("%Y%m%d-%H%M%S")
    out = dest_dir / f"healthmon-{stamp}.hmbk"
    write_private_file(out, blob)
    rotate(cfg, dest_dir)
    return out


def decrypt_backup(cfg: Config, path: Path, recovery_key: str | None = None) -> bytes:
    blob = Path(path).read_bytes()
    if blob.startswith(MAGIC2):
        if not recovery_key:
            raise BackupError("Backup HMBK2: informe a chave de recuperação (o servidor não a possui)")
        header, pre, nonce, ct, _ = _split_v2(blob)
        sk = _parse_private(recovery_key)
        try:
            eph_pub = base64.urlsafe_b64decode(header["efemera"])
            recip_pub = sk.public_key().public_bytes(**_RAW)
            key = _derive(sk.exchange(X25519PublicKey.from_public_bytes(eph_pub)), eph_pub, recip_pub)
            data = ChaCha20Poly1305(key).decrypt(nonce, ct, pre)
        except (InvalidTag, KeyError, ValueError):
            raise BackupError("Chave de recuperação incorreta ou backup adulterado") from None
        if hashlib.sha256(data).hexdigest() != header.get("sha256_banco"):
            raise BackupError("Backup adulterado: SHA-256 do banco não confere")
        return data
    if not blob.startswith(MAGIC):
        raise ValueError("Arquivo não é um backup do healthmon")
    salt, token = blob[5:21], blob[21:]
    return Fernet(_key(_passphrase(cfg), salt)).decrypt(token)


def _check_db_bytes(data: bytes) -> tuple[str, int]:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "check.db"
        tmp.write_bytes(data)
        con = sqlite3.connect(tmp)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            n = con.execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0]
        finally:
            con.close()
    return ok, n


def verify_backup(cfg: Config, path: Path, recovery_key: str | None = None) -> dict:
    """Teste sem tocar no banco em uso. HMBK1 e HMBK2 com chave: decifra, abre e roda integrity_check.
    HMBK2 sem chave (caso do servidor): valida estrutura e checksum e informa o que o cabeçalho declara
    (a integridade do banco foi checada ANTES de cifrar)."""
    path = Path(path)
    blob = path.read_bytes()
    if blob.startswith(MAGIC2) and not recovery_key:
        h = _split_v2(blob)[0]
        return {"arquivo": path.name, "integridade": "ok (checada antes de cifrar)", "dias": h.get("linhas_daily_metrics"),
                "bytes": len(blob), "modo": "cabecalho", "criado_utc": h.get("criado_utc")}
    data = decrypt_backup(cfg, path, recovery_key)
    ok, n = _check_db_bytes(data)
    if ok != "ok":
        raise RuntimeError(f"Backup {path.name} falhou na verificação de integridade: {ok}")
    return {"arquivo": path.name, "integridade": ok, "dias": n, "bytes": len(data), "modo": "completo"}


def restore_backup(cfg: Config, path: Path, recovery_key: str | None = None, target: Path | None = None) -> Path:
    """Restaura o banco (HMBK1 ou HMBK2). `target` permite restaurar fora de cfg.db_path (ensaios)."""
    data = decrypt_backup(cfg, path, recovery_key)
    ok, _ = _check_db_bytes(data)
    if ok != "ok":
        raise RuntimeError(f"Backup falhou na verificação de integridade: {ok}")
    target = Path(target) if target else cfg.db_path
    if target.exists():
        keep = target.with_suffix(f".antes-restauracao-{datetime.now().strftime('%Y%m%d%H%M%S')}.db")
        target.rename(keep)
        for ext in ("-wal", "-shm"):
            side = Path(str(target) + ext)
            if side.exists():
                side.unlink()
    write_private_file(target, data)
    con = sqlite3.connect(target)
    try:
        con.execute("PRAGMA integrity_check").fetchone()
    finally:
        con.close()
    return target


def latest_backup(cfg: Config, dest_dir: Path | None = None) -> Path | None:
    files = sorted((dest_dir or cfg.backup_dir).glob("healthmon-*.hmbk"))
    return files[-1] if files else None


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
