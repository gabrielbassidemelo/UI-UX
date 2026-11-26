import os
import shutil
import tempfile
from datetime import date
from pathlib import Path

import pytest

from healthmon.config import init_home, load_config
from healthmon.db import Database
from healthmon.demo import populate

END = date(2026, 11, 20)


def _base_rapida() -> str | None:
    """Diretorio em memoria (tmpfs) para bancos SQLite temporarios, se houver e for gravavel."""
    d = "/dev/shm"
    return d if os.path.isdir(d) and os.access(d, os.W_OK | os.X_OK) else None


@pytest.fixture
def tmp_path(request, tmp_path_factory):
    """Substitui o tmp_path padrao: usa tmpfs quando disponivel (so velocidade de CI).

    O SQLite em WAL grava e sincroniza muito nos bancos sinteticos; em disco o setup de cada teste web
    levava ~6 s, em tmpfs ~2 s. Nada de comportamento do produto muda (mesmos arquivos, mesmas permissoes
    0700). Sem /dev/shm, cai no tmp_path padrao do pytest."""
    base = _base_rapida()
    if base is None:
        yield tmp_path_factory.mktemp("t", numbered=True)
        return
    d = Path(tempfile.mkdtemp(prefix="hm_test_", dir=base))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTHMON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HEALTHMON_BACKUP_PASSPHRASE", "frase-senha-de-teste-123")
    home = init_home(tmp_path / "home")
    return load_config(home)


@pytest.fixture
def db(cfg):
    return Database(cfg.db_path)


@pytest.fixture
def demo_db(db):
    populate(db, days=90, end=END, stress_last=6)
    return db
