from datetime import date

import pytest

from healthmon.config import init_home, load_config
from healthmon.db import Database
from healthmon.demo import populate

END = date(2026, 11, 20)


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
