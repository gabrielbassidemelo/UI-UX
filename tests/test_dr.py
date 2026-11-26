"""Backup externo e recuperação de desastres: HMBK2 (assimétrico), PAR do Oracle (servidor HTTP falso)
e ensaio completo de desastre. Somente dados sintéticos e armazenamento SIMULADO; nada toca a Oracle."""
import hashlib
import json
import logging
import shutil
import socket
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from healthmon import backup, offsite
from healthmon.backup import BackupError
from healthmon.config import init_home, load_config
from healthmon.db import Database
from healthmon.demo import populate, populate_phase2

from .conftest import END

TOKEN = "SEGREDO-TOKEN-DA-PAR-123"
PREFIX = f"/p/{TOKEN}/n/nsteste/b/bucket-teste/o/"


class FakeBucket(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.objects: dict[str, bytes] = {}
        self.fail_next = 0            # próximas N requisições respondem 503
        self.page = 2                 # tamanho de página da listagem (força paginação)
        self.requests = 0

    @property
    def par_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}{PREFIX}"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _gate(self):
        s = self.server
        s.requests += 1
        if s.fail_next > 0:
            s.fail_next -= 1
            self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers()
            return None
        u = urlsplit(self.path)
        if not u.path.startswith(PREFIX):
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
            return None
        return u, unquote(u.path[len(PREFIX):])

    def do_PUT(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        g = self._gate()
        if g:
            self.server.objects[g[1]] = body
            self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()

    def do_GET(self):
        g = self._gate()
        if not g:
            return
        u, name = g
        if name == "":
            q = parse_qs(u.query)
            names = sorted(k for k in self.server.objects if k.startswith(q.get("prefix", [""])[0]))
            start = q.get("start", [""])[0]
            names = [k for k in names if k >= start]
            page, rest = names[:self.server.page], names[self.server.page:]
            doc = {"objects": [{"name": k, "size": len(self.server.objects[k])} for k in page]}
            if rest:
                doc["nextStartWith"] = rest[0]
            body = json.dumps(doc).encode()
        elif name in self.server.objects:
            body = self.server.objects[name]
        else:
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
            return
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def bucket():
    s = FakeBucket()
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown(); s.server_close()


@pytest.fixture
def small_db(db):
    populate(db, days=30, end=END, stress_last=6)
    return db


@pytest.fixture
def keys(cfg):
    private, public = backup.generate_recovery_keypair()
    backup.save_public_key(cfg, public)
    return private


FAST = dict(espera_base=0, tentativas=3, _inseguro_para_testes=True)


def fingerprint(path):
    """contagem + hash do conteúdo por tabela."""
    con = sqlite3.connect(path)
    try:
        out = {}
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1"):
            rows = sorted(repr(r) for r in con.execute(f'SELECT * FROM "{t}"'))
            out[t] = (len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest())
        return out
    finally:
        con.close()


# --- HMBK2 -----------------------------------------------------------------------------
def test_hmbk2_roundtrip_and_header(cfg, small_db, keys):
    p = backup.create_backup(cfg)
    blob = p.read_bytes()
    assert blob.startswith(b"HMBK2") and b"SQLite format" not in blob
    h = backup.read_header(p)
    assert h["v"] == 2 and h["criado_utc"].endswith("Z") and len(h["sha256_banco"]) == 64
    assert h["linhas_daily_metrics"] == 30 and "user_version" in h["esquema"]
    data = backup.decrypt_backup(cfg, p, keys)
    assert hashlib.sha256(data).hexdigest() == h["sha256_banco"]
    v = backup.verify_backup(cfg, p, keys)
    assert v["dias"] == 30 and v["integridade"] == "ok"
    target = cfg.home / "restaurado.db"
    backup.restore_backup(cfg, p, keys, target=target)
    assert fingerprint(target) == fingerprint(cfg.db_path)


def test_without_public_key_falls_back_to_hmbk1(cfg, small_db):
    p = backup.create_backup(cfg)
    assert p.read_bytes().startswith(b"HMBK1")
    assert backup.verify_backup(cfg, p)["dias"] == 30


def test_server_cannot_decrypt(cfg, small_db, keys):
    p = backup.create_backup(cfg)
    with pytest.raises(BackupError):
        backup.decrypt_backup(cfg, p)                       # servidor: sem chave privada
    # o que o servidor guarda não contém a privada em nenhum arquivo do HOME
    chave_pura = keys.replace("-", "")[4:12]
    for f in cfg.home.rglob("*"):
        if f.is_file():
            assert chave_pura.encode() not in f.read_bytes(), f
    # o servidor ainda consegue conferir estrutura/checksum (job de backup)
    assert backup.verify_backup(cfg, p)["modo"] == "cabecalho"


def test_wrong_key_fails(cfg, small_db, keys):
    p = backup.create_backup(cfg)
    outra, _ = backup.generate_recovery_keypair()
    with pytest.raises(BackupError, match="incorreta"):
        backup.decrypt_backup(cfg, p, outra)
    with pytest.raises(BackupError, match="inválida"):
        backup.decrypt_backup(cfg, p, "HMRK-ABCD-EFGH")
    typo = keys[:-1] + ("A" if keys[-1] != "A" else "B")
    with pytest.raises(BackupError, match="inválida"):
        backup.decrypt_backup(cfg, p, typo)


def test_tampered_file_fails(cfg, small_db, keys):
    p = backup.create_backup(cfg)
    orig = p.read_bytes()
    # 1) byte alterado: checksum de corrupção acusa mesmo sem a chave
    b = bytearray(orig); b[-40] ^= 1
    p.write_bytes(bytes(b))
    with pytest.raises(BackupError):
        backup.verify_backup(cfg, p)
    with pytest.raises(BackupError):
        backup.decrypt_backup(cfg, p, keys)
    # 2) atacante recalcula o checksum: a autenticação AEAD (cabeçalho incluso) ainda falha
    for pos in (len(orig) - 50, 12):                         # corpo cifrado e cabeçalho
        b = bytearray(orig[:-32]); b[pos] ^= 1
        p.write_bytes(bytes(b) + hashlib.sha256(bytes(b)).digest())
        with pytest.raises(BackupError):
            backup.decrypt_backup(cfg, p, keys)


def test_integrity_failure_aborts_backup(cfg, small_db, keys):
    wal = cfg.db_path.with_name(cfg.db_path.name + "-wal")
    con = sqlite3.connect(cfg.db_path); con.execute("PRAGMA wal_checkpoint(TRUNCATE)"); con.close()
    raw = bytearray(cfg.db_path.read_bytes())
    for i in range(8192, min(len(raw), 8192 + 40960), 97):
        raw[i] = 0xFF
    cfg.db_path.write_bytes(bytes(raw))
    for ext in ("-wal", "-shm"):
        wal.with_name(cfg.db_path.name + ext).unlink(missing_ok=True)
    with pytest.raises(Exception):
        backup.create_backup(cfg)
    assert not list(cfg.backup_dir.glob("*.hmbk"))


def test_backup_during_continuous_writes(cfg, small_db, keys):
    """Uma thread grava sem parar (pares atômicos em duas tabelas) enquanto os backups são feitos."""
    with small_db.conn() as c:
        c.execute("CREATE TABLE dr_a(n INTEGER PRIMARY KEY)")
        c.execute("CREATE TABLE dr_b(n INTEGER PRIMARY KEY)")
    stop = threading.Event()
    erros = []
    contador = [0]

    def writer():
        con = sqlite3.connect(cfg.db_path, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        try:
            while not stop.is_set():
                n = contador[0] = contador[0] + 1
                with con:                                   # transação: as duas linhas ou nenhuma
                    con.execute("INSERT INTO dr_a VALUES(?)", (n,))
                    con.execute("INSERT INTO dr_b VALUES(?)", (n,))
        except Exception as e:                              # pragma: no cover
            erros.append(e)
        finally:
            con.close()

    t = threading.Thread(target=writer)
    t.start()
    try:
        while contador[0] < 50:
            pass
        files = []
        for i in range(3):
            files.append(backup.create_backup(cfg, cfg.home / f"bk{i}"))
            n0 = contador[0]
            while contador[0] < n0 + 50:
                pass
    finally:
        stop.set(); t.join()
    assert not erros
    vistos = []
    for i, p in enumerate(files):
        dest = cfg.home / f"rest{i}.db"
        backup.restore_backup(cfg, p, keys, target=dest)
        con = sqlite3.connect(dest)
        try:
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            a = con.execute("SELECT COUNT(*), MAX(n) FROM dr_a").fetchone()
            b = con.execute("SELECT COUNT(*), MAX(n) FROM dr_b").fetchone()
            assert a == b and a[0] == a[1] and a[0] >= 1     # prefixo contíguo e consistente
            assert con.execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0] == 30
        finally:
            con.close()
        vistos.append(a[0])
    assert vistos == sorted(vistos) and vistos[0] < vistos[-1]


# --- armazenamento externo (PAR simulada) ---------------------------------------------------
def test_upload_list_download_verify(cfg, small_db, keys, bucket, monkeypatch):
    monkeypatch.setenv("OCI_PAR_URL", bucket.par_url)
    p = backup.create_backup(cfg)
    name = offsite.upload(cfg, p, **FAST)
    assert bucket.objects[name] == p.read_bytes()
    assert [o["name"] for o in offsite.list_remote(cfg, **FAST)] == [p.name]
    assert offsite.verify_remote(cfg, p, **FAST) is True
    bucket.objects[name] = bucket.objects[name][:-1] + b"X"          # corrupção remota é detectada
    assert offsite.verify_remote(cfg, p, **FAST) is False
    dest = offsite.download(cfg, name, cfg.home / "baixado.hmbk", **FAST)
    assert dest.stat().st_mode & 0o077 == 0


def test_list_paginates_and_sync_latest(cfg, small_db, keys, bucket, monkeypatch):
    monkeypatch.setenv("OCI_PAR_URL", bucket.par_url)
    for i in range(5):
        bucket.objects[f"healthmon-2020010{i}-000000.hmbk"] = b"x"
    assert len(offsite.list_remote(cfg, **FAST)) == 5               # 3 páginas de 2
    assert offsite.status(cfg)["alerta"] is True and offsite.status(cfg)["ultimo_envio_ok"] is None
    p = backup.create_backup(cfg)
    assert offsite.status(cfg)["pendente"] is True
    r = offsite.sync_latest(cfg, **FAST)
    assert r["status"] == "ok" and r["verificado"] is True and bucket.objects[r["objeto"]] == p.read_bytes()
    assert offsite.sync_latest(cfg, **FAST)["status"] == "em_dia"
    s = offsite.status(cfg)
    assert s["ultimo_envio_ok"] and s["falhas_consecutivas"] == 0 and not s["alerta"] and not s["pendente"]
    assert json.loads((cfg.home / "offsite_state.json").read_text())["ultimo_arquivo"] == p.name


def test_retry_then_success(cfg, small_db, keys, bucket, monkeypatch):
    monkeypatch.setenv("OCI_PAR_URL", bucket.par_url)
    backup.create_backup(cfg)
    bucket.fail_next = 2                                            # 2 falhas 503, 3ª tentativa passa
    assert offsite.sync_latest(cfg, **FAST)["status"] == "ok"


def test_network_failure_records_error_keeps_local(cfg, small_db, keys, monkeypatch):
    with socket.socket() as s:                                      # porta livre e fechada
        s.bind(("127.0.0.1", 0)); porta = s.getsockname()[1]
    monkeypatch.setenv("OCI_PAR_URL", f"http://127.0.0.1:{porta}{PREFIX}")
    p = backup.create_backup(cfg)
    r = offsite.sync_latest(cfg, **FAST)
    assert r["status"] == "erro" and p.exists()
    r = offsite.sync_latest(cfg, **FAST)
    assert r["falhas_consecutivas"] == 2 and p.exists()
    s = offsite.status(cfg)
    assert s["falhas_consecutivas"] == 2 and s["ultimo_erro"]["mensagem"] and s["alerta"] and s["pendente"]
    assert TOKEN not in json.dumps(s) and TOKEN not in (cfg.home / "offsite_state.json").read_text()


def test_sync_disabled_without_secret(cfg, small_db, keys, monkeypatch):
    monkeypatch.delenv("OCI_PAR_URL", raising=False)
    backup.create_backup(cfg)
    assert offsite.sync_latest(cfg)["status"] == "desativado"
    assert offsite.status(cfg)["configurado"] is False


def test_par_host_validation(cfg, monkeypatch):
    for url in ("http://objectstorage.sa-saopaulo-1.oraclecloud.com" + PREFIX,       # sem TLS
                "https://evil.example.com" + PREFIX,                                  # host de terceiros
                "https://oraclecloud.com.evil.io" + PREFIX,
                "https://objectstorage.sa-saopaulo-1.oraclecloud.com/sem/par"):
        with pytest.raises(offsite.OffsiteError) as e:
            offsite.list_remote(cfg, par_url=url)
        assert TOKEN not in str(e.value) and "evil" not in str(e.value)
    monkeypatch.delenv("OCI_PAR_URL", raising=False)
    with pytest.raises(offsite.OffsiteError):
        offsite.list_remote(cfg)


def test_par_url_never_leaks(cfg, small_db, keys, bucket, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("OCI_PAR_URL", bucket.par_url)
    p = backup.create_backup(cfg)
    textos = []
    offsite.sync_latest(cfg, **FAST)
    bucket.fail_next = 99                                           # 503 sempre
    for fn in (lambda: offsite.upload(cfg, p, **FAST), lambda: offsite.list_remote(cfg, **FAST),
               lambda: offsite.download(cfg, "x", cfg.home / "x", **FAST)):
        with pytest.raises(offsite.OffsiteError) as e:
            fn()
        textos += [str(e.value), repr(e.value)]
    bucket.fail_next = 0
    with pytest.raises(offsite.OffsiteError) as e:                  # 404: objeto inexistente
        offsite.download(cfg, "nao-existe", cfg.home / "y", **FAST)
    textos.append(str(e.value))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); porta = s.getsockname()[1]
    monkeypatch.setenv("OCI_PAR_URL", f"http://127.0.0.1:{porta}{PREFIX}")
    with pytest.raises(offsite.OffsiteError) as e:                  # erro de rede (requests embute a URL)
        offsite.upload(cfg, p, **FAST)
    import traceback
    textos.append("".join(traceback.format_exception(e.value)))
    offsite.sync_latest(cfg, par_url=f"http://127.0.0.1:{porta}{PREFIX}", **FAST)
    textos.append(caplog.text)
    textos.append((cfg.home / "offsite_state.json").read_text())
    for t in textos:
        assert TOKEN not in t, t[:200]
    assert "/p/***" in caplog.text or "/p/" not in caplog.text


# --- ensaio completo de desastre -----------------------------------------------------------
def test_full_disaster_drill(tmp_path, monkeypatch, bucket):
    monkeypatch.setenv("HEALTHMON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OCI_PAR_URL", bucket.par_url)
    cfg = load_config(init_home(tmp_path / "home"))
    db = Database(cfg.db_path)
    populate(db, days=60, end=END, stress_last=6)
    from healthmon import profile
    try:                                                              # triagem de segurança obrigatória (módulo safety)
        from healthmon.safety import TRIAGE
        profile.update(db, {k: False for k in TRIAGE})
    except ImportError:
        pass
    populate_phase2(db, cfg, days=45, end=END)
    private, public = backup.generate_recovery_keypair()
    backup.save_public_key(cfg, public)

    antigo = backup.create_backup(cfg)
    assert offsite.sync_latest(cfg, **FAST)["status"] == "ok"
    snapshot_origem = tmp_path / "origem.db"                         # estado do banco no momento do backup
    con = sqlite3.connect(cfg.db_path); dst = sqlite3.connect(snapshot_origem); con.backup(dst); dst.close(); con.close()
    original = fingerprint(snapshot_origem)
    assert sum(n for n, _ in original.values()) > 500 and original["daily_metrics"][0] == 60

    db.set_setting("depois_do_backup", "perdido")                    # dado posterior ao último backup
    assert db.get_setting("depois_do_backup") == "perdido"

    shutil.rmtree(tmp_path / "home")                                 # desastre: VM e HOME perdidos
    assert not antigo.exists()

    novo = tmp_path / "home-novo"                                    # VM nova, HOME vazio
    monkeypatch.setenv("HEALTHMON_HOME", str(novo))
    cfg2 = load_config(init_home(novo))
    assert not cfg2.db_path.exists()
    baixado = offsite.download_latest(cfg2, cfg2.backup_dir, **{"par_url": bucket.par_url, **FAST})
    assert baixado.name == antigo.name
    backup.restore_backup(cfg2, baixado, private)

    assert fingerprint(cfg2.db_path) == original                     # por tabela: contagem e hash idênticos
    con = sqlite3.connect(cfg2.db_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM settings WHERE key='depois_do_backup'").fetchone()[0] == 0
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()
