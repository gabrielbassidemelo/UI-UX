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


def _gen_key(cfg, monkeypatch, typed=None):
    """Roda `chaves-recuperacao` com chave sintética; `typed` simula o que o usuário cola na conferência."""
    made = {}
    real = backup.generate_recovery_keypair

    def gen():
        made["priv"], made["pub"] = real()
        return made["priv"], made["pub"]
    monkeypatch.setattr(backup, "generate_recovery_keypair", gen)
    monkeypatch.setattr(builtins, "input", lambda *_: "")
    monkeypatch.setattr(getpass, "getpass", lambda *_: typed if typed is not None else made["priv"])
    rc = cli.main(["chaves-recuperacao"])
    return rc, made["priv"]


def test_recovery_key_must_be_confirmed_and_is_never_stored(cfg, db, monkeypatch):
    rc, _ = _gen_key(cfg, monkeypatch, typed="HMRK-AAAA-BBBB")           # usuário não guardou a chave certa
    assert rc == 1 and not backup.public_key_path(cfg).exists()             # nada passa a valer
    rc, key = _gen_key(cfg, monkeypatch)
    assert rc == 0 and backup.public_key_path(cfg).exists() and backup.expected_fingerprint(cfg)
    for f in cfg.home.rglob("*"):
        if f.is_file():
            assert key.encode() not in f.read_bytes(), f                    # a chave privada não fica em nenhum arquivo
    assert cli.main(["chaves-recuperacao"]) == 1                            # não sobrescreve sem --forcar


def test_restore_flow_confirms_content_and_rebuilds_public_key(cfg, db, monkeypatch, capsys):
    rc, key = _gen_key(cfg, monkeypatch)
    populate(db, 10, END, stress_last=0)
    p = backup.create_backup(cfg)
    assert backup.read_header(p) is not None                                # HMBK2
    with pytest.raises(backup.BackupError):
        backup.decrypt_backup(cfg, p)                                       # o servidor sozinho não abre o backup
    monkeypatch.setattr(getpass, "getpass", lambda *_: key)
    assert cli.main(["verificar-backup", "--com-chave"]) == 0
    before = db.q1("SELECT COUNT(*) n FROM daily_metrics")["n"]
    db.x("DELETE FROM daily_metrics")
    monkeypatch.setattr(builtins, "input", lambda *_: "nao")
    assert cli.main(["restaurar", str(p)]) == 1                             # sem confirmar, nada muda
    assert db.q1("SELECT COUNT(*) n FROM daily_metrics")["n"] == 0
    backup.public_key_path(cfg).unlink()                                    # simula servidor reconstruído
    backup.pin_path(cfg).unlink()
    monkeypatch.setattr(builtins, "input", lambda *_: "RESTAURAR")
    assert cli.main(["restaurar", str(p)]) == 0
    assert db.q1("SELECT COUNT(*) n FROM daily_metrics")["n"] == before
    assert backup.public_key_path(cfg).exists()                             # mesma chave volta a cifrar os próximos backups
    monkeypatch.setattr(getpass, "getpass", lambda *_: key)
    assert backup.verify_backup(cfg, backup.create_backup(cfg), key)["modo"] == "completo"
    monkeypatch.setattr(getpass, "getpass", lambda *_: "HMRK-AAAA-BBBB")
    with pytest.raises(Exception):
        cli.main(["restaurar", str(p)])                                     # chave errada nunca restaura


def test_restore_refused_while_service_is_running_and_previous_db_is_kept_readable(cfg, db, monkeypatch):
    import sqlite3
    populate(db, 8, END, stress_last=0)
    priv, pub = backup.generate_recovery_keypair()
    backup.save_public_key(cfg, pub)
    p = backup.create_backup(cfg)
    (cfg.home / "servico.ativo").touch()
    with pytest.raises(backup.BackupError, match="em execução"):
        backup.restore_backup(cfg, p, priv)
    (cfg.home / "servico.ativo").unlink()
    populate(db, 12, END, stress_last=0)                                    # dados mais novos que o backup, parte só no WAL
    backup.restore_backup(cfg, p, priv)
    kept = sorted(cfg.db_path.parent.glob("*antes-restauracao*"))
    con = sqlite3.connect(kept[-1])
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0] == 12   # nada do banco anterior se perde
    con.close()
    assert db.q1("SELECT COUNT(*) n FROM daily_metrics")["n"] == 8


def test_no_silent_fallback_to_server_readable_format_and_key_swap_is_detected(cfg, db):
    populate(db, 5, END, stress_last=0)
    _, pub = backup.generate_recovery_keypair()
    backup.save_public_key(cfg, pub)
    backup.create_backup(cfg)
    backup.public_key_path(cfg).unlink()                                    # chave pública some; frase-senha legada existe
    with pytest.raises(backup.BackupError, match="sumiu"):
        backup.create_backup(cfg)
    _, other = backup.generate_recovery_keypair()
    backup.public_key_path(cfg).write_text(other + "\n")                    # alguém troca a chave pública
    with pytest.raises(backup.BackupError, match="trocada"):
        backup.create_backup(cfg)


def test_old_backup_copied_under_newer_name_is_flagged(cfg, db):
    populate(db, 5, END, stress_last=0)
    priv, pub = backup.generate_recovery_keypair()
    backup.save_public_key(cfg, pub)
    p = backup.create_backup(cfg)
    assert backup.header_matches_name(p, backup.read_header(p)) is True
    fake = p.with_name("healthmon-20991231-235959.hmbk")
    fake.write_bytes(p.read_bytes())
    assert "nome do arquivo indica" in backup.header_matches_name(fake, backup.read_header(fake))


@pytest.mark.parametrize("name", ["../../etc/passwd", "x/../../evil.hmbk", "healthmon-20261120-030000.hmbk/../x",
                                  "outro.hmbk", "healthmon-2026.hmbk", "pasta/healthmon-20261120-030000.hmbk"])
def test_unexpected_remote_object_names_are_rejected(cfg, name):
    with pytest.raises(offsite.OffsiteError):
        offsite._safe_name(name, "")
    assert offsite._safe_name("healthmon-20261120-030000.hmbk", "") == "healthmon-20261120-030000.hmbk"
    assert offsite._safe_name("pre/healthmon-20261120-030000.hmbk", "pre/") == "healthmon-20261120-030000.hmbk"


def test_secrets_are_written_atomically_never_in_repr_and_garmin_key_rotation(cfg, db, monkeypatch):
    from healthmon.config import load_config, set_secret
    set_secret(cfg.home, "OCI_PAR_URL", "https://x.oraclecloud.com/p/tok&|$1/o/")
    with pytest.raises(ValueError):
        set_secret(cfg.home, "OCI_PAR_URL", "a\nGARMIN_PERSONAL_API_KEY=injetada")
    monkeypatch.setattr(getpass, "getpass", lambda *_: "chave-sintetica-de-teste-0123456789")
    monkeypatch.setattr(builtins, "input", lambda *_: "2027-06-30")
    assert cli.main(["trocar-chave-garmin"]) == 0
    c = load_config(cfg.home)
    assert c.secret("GARMIN_PERSONAL_API_KEY") == "chave-sintetica-de-teste-0123456789"
    assert c.secret("OCI_PAR_URL").endswith("tok&|$1/o/") and c.get("garmin", "chave_expira_em") == "2027-06-30"
    assert "chave-sintetica" not in repr(c) and "tok&" not in repr(c)
    assert oct((cfg.home / "secrets.env").stat().st_mode)[-3:] == "600"


def test_restore_command_never_accepts_key_as_argument():
    with pytest.raises(SystemExit):
        cli.main(["restaurar", "arquivo.hmbk", "--chave", "HMRK-X"])
