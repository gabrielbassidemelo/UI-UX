"""Fase 3 — operação: backup diário com envio externo, alertas de falha e comandos de recuperação.
Somente dados sintéticos; o armazenamento externo é simulado (nada toca a Oracle)."""
import builtins
import getpass

import pytest

from healthmon import backup, cli, offsite
from healthmon.demo import populate
from healthmon.service import Service

from .conftest import END


@pytest.fixture
def svc(cfg, db):
    populate(db, 20, END, stress_last=0)
    return Service(cfg, db, source=object())


def kinds(db):
    return [a["kind"] for a in db.alerts(50)]


def test_backup_without_offsite_copy_warns_every_day(cfg, db, svc):
    out = svc.job_backup()
    assert "verificado" in out and "backup_externo_ausente" in kinds(db)
    svc.job_backup()
    assert kinds(db).count("backup_externo_ausente") == 1          # sem repetir o mesmo alerta no mesmo dia


def test_local_backup_failure_raises_alert_and_job_is_marked_failed(cfg, db, svc, monkeypatch):
    def boom(_cfg):
        raise backup.BackupError("disco cheio")
    monkeypatch.setattr("healthmon.service.create_backup", boom)
    with pytest.raises(backup.BackupError):
        svc.job_backup()
    assert "falha_backup" in kinds(db)
    assert db.q1("SELECT status FROM job_runs WHERE job='backup' ORDER BY id DESC")["status"] == "erro"


def test_offsite_failures_alert_and_retry_recovers(cfg, db, svc, monkeypatch):
    cfg.secrets["OCI_PAR_URL"] = "https://objectstorage.sa-saopaulo-1.oraclecloud.com/p/tok/n/ns/b/bk/o/"
    state = {"fail": True, "sent": []}

    def fake_upload(_cfg, path, **kw):
        if state["fail"]:
            raise offsite.OffsiteError("HTTP 503")
        state["sent"].append(path.name)
        return path.name
    monkeypatch.setattr(offsite, "upload", fake_upload)
    monkeypatch.setattr(offsite, "verify_remote", lambda *a, **k: True)
    assert "externo: erro" in svc.job_backup()
    assert "falha_backup_externo" in kinds(db)                     # nunca enviou: já conta como atrasado
    assert svc.job_offsite_retry() == "erro"
    st = offsite.status(cfg)
    assert st["falhas_consecutivas"] == 2 and st["alerta"] and st["pendente"]
    assert "tok" not in str(st) and "oraclecloud" not in str(st)   # a URL não aparece no estado
    state["fail"] = False
    assert svc.job_offsite_retry() == "ok" and state["sent"]
    st = offsite.status(cfg)
    assert st["falhas_consecutivas"] == 0 and not st["alerta"] and not st["pendente"]
    assert svc.job_offsite_retry() == "em_dia"


def test_scheduler_has_backup_and_offsite_retry_jobs(cfg, db, svc):
    class Sched:
        def __init__(self):
            self.ids = []

        def add_job(self, *a, **k):
            self.ids.append(k.get("id"))
    s = Sched()
    svc.schedule(s)
    assert {"backup", "offsite-retry", "sync", "daily", "nightly"} <= set(s.ids)


def test_recovery_key_is_shown_once_never_stored_and_not_overwritten(cfg, db, capsys, monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *_: "")
    assert cli.main(["chaves-recuperacao"]) == 0
    out = capsys.readouterr().out
    key = next(w for w in out.split() if w.startswith("HMRK-"))
    assert backup.public_key_path(cfg).exists()
    for f in cfg.home.rglob("*"):
        if f.is_file():
            assert key.encode() not in f.read_bytes(), f              # a chave privada não fica em nenhum arquivo
    assert cli.main(["chaves-recuperacao"]) == 1                      # não sobrescreve sem --forcar
    populate(db, 10, END, stress_last=0)
    p = backup.create_backup(cfg)
    assert backup.read_header(p) is not None                          # HMBK2
    with pytest.raises(backup.BackupError):
        backup.decrypt_backup(cfg, p)                                 # o servidor sozinho não abre o backup
    monkeypatch.setattr(getpass, "getpass", lambda *_: key)
    assert cli.main(["verificar-backup", "--com-chave"]) == 0
    before = db.q1("SELECT COUNT(*) n FROM daily_metrics")["n"]
    db.x("DELETE FROM daily_metrics")
    assert cli.main(["restaurar", str(p)]) == 0
    assert db.q1("SELECT COUNT(*) n FROM daily_metrics")["n"] == before
    monkeypatch.setattr(getpass, "getpass", lambda *_: "HMRK-AAAA-BBBB")
    with pytest.raises(Exception):
        cli.main(["restaurar", str(p)])                               # chave errada nunca restaura


def test_restore_command_never_accepts_key_as_argument():
    with pytest.raises(SystemExit):
        cli.main(["restaurar", "arquivo.hmbk", "--chave", "HMRK-X"])
