"""Carregamento de configuração e segredos.

- Configuração não sensível: $HEALTHMON_HOME/config.toml
- Segredos: variáveis de ambiente ou $HEALTHMON_HOME/secrets.env (permissão 600)
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PACKAGE_DIR = Path(__file__).resolve().parent
EXAMPLE_CONFIG = PACKAGE_DIR / "config.example.toml"

SECRET_KEYS = (
    "HEALTHMON_BACKUP_PASSPHRASE",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "SMTP_PASSWORD",
    "ANTHROPIC_API_KEY",
    "GARMIN_PERSONAL_API_KEY",
)


def home_dir() -> Path:
    return Path(os.environ.get("HEALTHMON_HOME", Path.home() / ".healthmon")).expanduser()


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def write_private_file(path: Path, content: str | bytes) -> None:
    """Grava arquivo com permissão 600 desde a criação."""
    ensure_private_dir(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(content.encode() if isinstance(content, str) else content)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_secrets_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    mode = path.stat().st_mode
    if os.name == "posix" and mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(
            f"{path} está acessível a outros usuários. Corrija com: chmod 600 {path}"
        )
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


@dataclass
class Config:
    home: Path
    data: dict[str, Any]
    secrets: dict[str, str] = field(default_factory=dict, repr=False)   # nunca em repr/log

    # --- atalhos -----------------------------------------------------
    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.data.get(section, {}).get(key, default)

    def secret(self, key: str) -> str | None:
        return os.environ.get(key) or self.secrets.get(key) or None

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.get("perfil", "fuso_horario", "America/Sao_Paulo"))

    @property
    def db_path(self) -> Path:
        return Path(os.environ.get("HEALTHMON_DB", self.home / "healthmon.db"))

    @property
    def token_dir(self) -> Path:
        return self.home / "tokens"

    @property
    def reports_dir(self) -> Path:
        return self.home / "relatorios"

    @property
    def backup_dir(self) -> Path:
        d = self.get("backup", "diretorio", "")
        return Path(d).expanduser() if d else self.home / "backups"

    @property
    def auth_file(self) -> Path:
        return self.home / "dashboard_auth.json"

    @property
    def consent_file(self) -> Path:
        return self.home / "consentimento_ia_nuvem.json"


def load_config(home: Path | None = None) -> Config:
    home = home or home_dir()
    base = tomllib.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    cfg_path = home / "config.toml"
    if cfg_path.exists():
        base = _deep_merge(base, tomllib.loads(cfg_path.read_text(encoding="utf-8")))
    secrets = load_secrets_file(home / "secrets.env")
    return Config(home=home, data=base, secrets=secrets)


def init_home(home: Path | None = None) -> Path:
    home = home or home_dir()
    ensure_private_dir(home)
    for sub in ("tokens", "relatorios", "backups"):
        ensure_private_dir(home / sub)
    cfg = home / "config.toml"
    if not cfg.exists():
        shutil.copy(EXAMPLE_CONFIG, cfg)
        cfg.chmod(0o600)
    sec = home / "secrets.env"
    if not sec.exists():
        write_private_file(
            sec,
            "# Segredos do healthmon — permissão 600, nunca versionar.\n"
            "# Chave pessoal da Garmin (connect.garmin.com/app/settings/personalApiToken):\n"
            "GARMIN_PERSONAL_API_KEY=\n"
            "# Frase-senha do formato antigo de backup (só usada se não houver chave pública de recuperação):\n"
            "HEALTHMON_BACKUP_PASSPHRASE=\n"
            "# Cópia externa dos backups: URL de acesso pré-autenticado (PAR) do bucket no Oracle Object Storage.\n"
            "# É uma credencial: não cole em conversas, código ou GitHub.\n"
            "OCI_PAR_URL=\n"
            "# Telegram (opcional): token do bot criado no @BotFather e seu chat id\n"
            "TELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n"
            "# E-mail (opcional): senha de app do SMTP\nSMTP_PASSWORD=\n"
            "# IA em nuvem (opcional, exige consentimento): chave da API Anthropic\n"
            "ANTHROPIC_API_KEY=\n",
        )
    return home


def set_secret(home: Path, name: str, value: str) -> None:
    """Grava/atualiza um segredo em secrets.env de forma atômica (600). O valor nunca passa por linha de comando."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or "\n" in value or "\r" in value:
        raise ValueError("segredo inválido")
    path = home / "secrets.env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [ln for ln in lines if not ln.startswith(name + "=")] + [f"{name}={value}"]
    tmp = path.with_name(".secrets.env.tmp")
    write_private_file(tmp, "\n".join(lines) + "\n")
    os.replace(tmp, path)


def set_config_value(home: Path, section: str, key: str, value: str) -> None:
    """Troca `key = "..."` dentro de [section] no config.toml (valor de texto simples)."""
    if not re.fullmatch(r"[A-Za-z0-9_.:/\- ]*", value):
        raise ValueError("valor inválido")
    path = home / "config.toml"
    out, cur, done = [], None, False
    for ln in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\[([^\]]+)\]", ln.strip())
        if m:
            cur = m.group(1)
        if cur == section and re.match(rf"^{re.escape(key)}\s*=", ln) and not done:
            ln, done = (f"{key} = {value}" if value in ("true", "false") else f'{key} = "{value}"'), True
        out.append(ln)
    if not done:
        raise KeyError(f"[{section}] {key} não encontrado em config.toml")
    tmp = path.with_name(".config.toml.tmp")
    write_private_file(tmp, "\n".join(out) + "\n")
    os.replace(tmp, path)
