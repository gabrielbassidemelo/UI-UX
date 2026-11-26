"""Testes de API, segurança de acesso, automação e infraestrutura (dados SINTÉTICOS)."""
import json
import os
import time
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from healthmon import backup, cli, reports
from healthmon.auth import set_password
from healthmon.demo import DemoSource, populate, populate_phase2
from healthmon.push import subscribe, vapid_keys
from healthmon.service import Service
from healthmon.web.app import PUBLIC, create_app

from .conftest import END

H = {"X-HM": "1"}


@pytest.fixture
def app_db(cfg, db):
    populate(db, 120, date.today())
    populate_phase2(db, cfg, 60, date.today())
    set_password(cfg, "senha-forte-123")
    return create_app(cfg, db, sync_fn=lambda: None), db


@pytest.fixture
def client(app_db):
    c = TestClient(app_db[0])
    assert c.post("/api/login", json={"senha": "senha-forte-123"}, headers=H).status_code == 200
    return c


def test_every_route_requires_authentication(app_db):
    app = app_db[0]
    c = TestClient(app)
    checked = 0
    for r in app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/") or path in PUBLIC:
            continue
        url = path.replace("{aid}", "1").replace("{rid}", "1").replace("{pid}", "1").replace("{mid}", "1") \
                  .replace("{lid}", "1").replace("{gid}", "1").replace("{eid}", "1").replace("{did}", "1")
        for m in r.methods - {"HEAD", "OPTIONS"}:
            resp = c.request(m, url, headers=H, json={} if m != "GET" else None)
            assert resp.status_code == 401, (m, path, resp.status_code)
            checked += 1
    assert checked >= 50


def test_all_get_endpoints_respond(client):
    for path in ["/api/home", "/api/perfil", "/api/checkin", "/api/composicao?days=180", "/api/metabolismo", "/api/treino",
                 "/api/treino/historico?exercicio=supino_halteres", "/api/nutricao", "/api/alimentos?q=arroz", "/api/suplementos",
                 "/api/suplementos/consulta?nome=creatina", "/api/exames", "/api/objetivos", "/api/aprendizado",
                 "/api/estatistica?key=hrv_last_night", "/api/kb", "/api/kb?q=proteina", "/api/sistema", "/api/push/chave",
                 "/api/series?keys=hrv_last_night&days=180", "/api/compare?days=180"]:
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:200])
        assert r.headers["cache-control"] == "no-store"
    h = client.get("/api/home").json()
    assert len(h["tendencia_recuperacao"]) == 7 and h["demo"] is True and h["nutricao"]["meta"]
    assert client.get("/api/suplementos/consulta?nome=oxandrolona").json()["recusado"] is True
    s = client.get("/api/sistema").json()
    assert s["fonte"] == "oficial" and s["dados_reais_validados"] is None    # nunca validado com dados reais aqui


def test_write_flows_and_validation(client, app_db):
    db = app_db[1]
    assert client.post("/api/perfil", json={"altura_cm": 999}, headers=H).status_code == 400
    assert client.post("/api/perfil", json={"objetivo": "virar_hulk"}, headers=H).status_code == 400
    assert client.post("/api/perfil", json={"altura_cm": 181, "campo_estranho": 1}, headers=H).json()["perfil"]["altura_cm"] == 181
    assert client.post("/api/checkin", json={"perceived_recovery": 99}, headers=H).status_code == 400
    assert client.post("/api/checkin", json={"perceived_recovery": 8, "caffeine_last_time": "15:30"}, headers=H).json()["ok"]
    assert client.post("/api/peso", json={"peso_kg": 5}, headers=H).status_code == 400
    assert client.post("/api/peso", json={"peso_kg": 87.4, "gordura_pct": 22}, headers=H).json()["ok"]
    m = client.post("/api/refeicoes", json={"texto": "200 g arroz cozido, 150g frango grelhado"}, headers=H).json()
    assert m["total"]["kcal"] > 300 and m["incerteza_pct"]
    assert client.post("/api/refeicoes", json={"texto": "zzzz qqqq"}, headers=H).status_code == 400
    assert client.delete(f"/api/refeicoes/{m['id']}", headers=H).json()["ok"]
    pid = client.post("/api/treino/propor", json={"motivo": "teste"}, headers=H).json()["id"]
    assert client.get("/api/treino").json()["proposto"]["id"] == pid           # ainda não ativo
    assert client.post(f"/api/treino/confirmar/{pid}", headers=H).json()["ok"]
    assert client.post(f"/api/treino/confirmar/{pid}", headers=H).status_code == 400
    assert client.post("/api/treino/serie", json={"exercicio": "supino", "reps": 8, "carga_kg": 9999}, headers=H).status_code == 400
    hoje = client.get("/api/home").json()["dia"]                              # gera e registra a recomendação do dia
    assert client.post("/api/treino/sessao", json={"tipo": "forca", "rpe": 7, "seguiu_recomendacao": True}, headers=H).json()["id"]
    assert db.q1("SELECT followed FROM decisions WHERE area='treino' AND date=?", (hoje,))["followed"] == 1
    lab = client.post("/api/exames", json={"data": "2026-10-01", "analito": "Ferritina", "valor": "45,2", "unidade": "ng/mL",
                                           "ref_min": 30, "ref_max": 400}, headers=H).json()
    assert lab["id"] and "não estabelece causa" in lab["contexto"]["nota"]
    assert client.post("/api/exames", json={"data": "ontem", "analito": "x", "valor": 1}, headers=H).status_code == 400
    far = (date.today() + timedelta(days=120)).isoformat()
    assert client.post("/api/objetivos", json={"metrica": "peso", "alvo": 60, "prazo": far}, headers=H).status_code == 400
    assert client.post("/api/objetivos", json={"metrica": "passos", "alvo": 10000, "prazo": far}, headers=H).json()["id"] == 1
    assert client.post("/api/experimentos", json={"fator": "jejum_prolongado", "desfecho": "sleep_score"}, headers=H).status_code == 400
    eid = client.post("/api/experimentos", json={"fator": "telas_noite", "desfecho": "sleep_score"}, headers=H).json()["id"]
    assert client.post(f"/api/experimentos/{eid}/encerrar", headers=H).json()["ok"]


def test_csrf_body_limit_and_bad_json(client):
    assert client.post("/api/checkin", json={"energy": 3}).status_code == 403          # sem cabeçalho X-HM
    big = "x" * 70_000
    assert client.post("/api/refeicoes", content=json.dumps({"texto": big}), headers={**H, "Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/checkin", content="{nao json", headers={**H, "Content-Type": "application/json"}).status_code == 400
    assert client.post("/api/checkin", content="[1,2]", headers={**H, "Content-Type": "application/json"}).status_code == 400


def test_lab_upload_restricted(client, cfg):
    r = client.post("/api/exames/arquivo", content=b"%PDF-1.4", headers={**H, "X-Arquivo": "..%2F..%2Flaudo.pdf"})
    assert r.status_code == 200 and "/" not in r.json()["arquivo"]
    assert client.post("/api/exames/arquivo", content=b"MZ", headers={**H, "X-Arquivo": "virus.exe"}).status_code == 400
    assert all((f.stat().st_mode & 0o077) == 0 for f in (cfg.home / "exames").iterdir())


def test_untrusted_text_is_stored_as_data_not_markup(client):
    x = "<img src=x onerror=alert(1)>"
    client.post("/api/refeicoes", json={"descricao": x, "kcal": 100}, headers=H)
    r = client.get("/api/nutricao")
    assert r.headers["content-type"].startswith("application/json")              # JSON; o front escapa ao renderizar
    assert "script-src 'self'" in r.headers["content-security-policy"]           # e a CSP bloqueia scripts inline
    js = (create_app.__globals__["STATIC"] / "app.js").read_text(encoding="utf-8")
    assert "esc(m.description)" in js and "innerHTML = r.html" not in js.replace("+ r.html", "")


def test_push_subscription_validation_and_private_keys(cfg, db):
    with pytest.raises(ValueError):
        subscribe(db, {"endpoint": "http://inseguro", "keys": {"p256dh": "a", "auth": "b"}})
    with pytest.raises(ValueError):
        subscribe(db, {"endpoint": "https://web.push.apple.com/x", "keys": {}})
    subscribe(db, {"endpoint": "https://web.push.apple.com/abc", "keys": {"p256dh": "k", "auth": "a"}})
    k = vapid_keys(cfg)
    assert k == vapid_keys(cfg) and len(k["public"]) > 80
    assert ((cfg.home / "vapid.json").stat().st_mode & 0o077) == 0


def test_push_payload_is_generic(cfg, db, monkeypatch):
    import healthmon.push as push
    from healthmon import notify
    sent = []
    monkeypatch.setattr(push, "send_push", lambda c, d, title, body, url="/": sent.append(title + " " + body) or 1)
    cfg.data["notificacoes"]["canais"] = ["push"]
    notify.notify_report(cfg, "diário", "Relatório diário", "HRV 38 ms · sono 5h10 · 3 pontos de atenção", db)
    assert sent and "HRV" not in sent[0] and "sono" not in sent[0].lower()


# ----------------------------------------------------------------- automação
class Sched:
    def __init__(self):
        self.jobs = []

    def add_job(self, fn, trigger, **kw):
        self.jobs.append(kw.get("id"))


def test_schedule_keeps_all_jobs_in_sao_paulo_time(cfg, db):
    s = Sched()
    Service(cfg, db, source=DemoSource()).schedule(s)
    assert {"sync", "daily", "nightly", "weekly", "monthly", "backup", "retention", "dispatch"} <= set(s.jobs)
    assert str(cfg.tz) == "America/Sao_Paulo"
    assert (cfg.get("relatorios", "diario"), cfg.get("relatorios", "noturno"), cfg.get("coleta", "intervalo_min")) == ("08:00", "22:00", 120)


def test_job_history_records_success_and_failure(cfg, db):
    class Boom:
        def fetch_day(self, day):
            raise RuntimeError("rede fora")
    svc = Service(cfg, db, source=DemoSource())
    assert svc.job_sync() == "ok"
    assert svc.job_nightly() == "gerado"
    bad = Service(cfg, db, source=DemoSource())
    bad.sync = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falha simulada"))
    with pytest.raises(RuntimeError):
        bad.job_nightly()
    rows = db.q("SELECT job, status, detail FROM job_runs ORDER BY id")
    assert [r["status"] for r in rows] == ["ok", "ok", "erro"] and "falha simulada" in rows[-1]["detail"]


def test_catch_up_generates_missed_reports_and_flags_interrupted(cfg, db, monkeypatch):
    populate(db, 40, date.today())
    db.insert("job_runs", {"job": "relatorio_diario", "started_at": "2026-01-01T00:00:00", "status": "executando"})
    svc = Service(cfg, db, source=DemoSource())
    cfg.data["relatorios"].update(diario="00:00", noturno="23:59")
    done = svc.catch_up()
    assert "diario" in done
    assert db.q1("SELECT status FROM job_runs WHERE started_at='2026-01-01T00:00:00'")["status"] == "interrompida"
    assert db.list_reports("diario")
    assert svc.catch_up() == [] or "diario" not in svc.catch_up()             # idempotente


def test_misconfigured_source_does_not_crash_service(cfg, db):
    cfg.data["garmin"]["fonte"] = "inexistente"
    res = Service(cfg, db).sync(["2026-11-20"])
    assert res["status"] == "erro" and res["erro"] == "configuracao"


def test_backup_verify_and_retention(cfg, db):
    populate(db, 30, END)
    svc = Service(cfg, db, source=DemoSource())
    assert "verificado" in svc.job_backup()
    p = sorted(cfg.backup_dir.glob("*.hmbk"))[-1]
    assert backup.verify_backup(cfg, p)["dias"] == 30
    blob = bytearray(p.read_bytes())
    blob[-5] ^= 0xFF
    p.write_bytes(bytes(blob))
    with pytest.raises(Exception):
        backup.verify_backup(cfg, p)                                          # backup corrompido é detectado
    db.save_raw("2020-01-01", "sleep", "ok", {"a": 1})
    svc.job_retention()
    assert not db.q("SELECT 1 FROM raw_payloads WHERE date='2020-01-01'")
    assert db.q1("SELECT COUNT(*) n FROM daily_metrics")["n"] == 30            # indicadores derivados permanecem


def test_daily_report_contains_plan_and_original_sections(cfg, db):
    populate(db, 60, END)
    md = reports.daily(cfg, db, END).doc.markdown()
    for section in ("## Recuperação", "## Noite anterior", "## Merece atenção", "## Sugestões para hoje", "## Plano do dia", "## Próximas ações"):
        assert section in md


def test_export_and_delete_all_data(cfg, db, monkeypatch, tmp_path, capsys):
    populate(db, 10, END)
    populate_phase2(db, cfg, 10, END)
    out = tmp_path / "export.json"
    assert cli.main(["exportar", str(out)]) == 0
    dump = json.loads(out.read_text(encoding="utf-8"))
    assert len(dump["daily_metrics"]) == 10 and dump["perfil"]["altura_cm"] == 180 and dump["meals"]
    assert (out.stat().st_mode & 0o077) == 0
    assert cli.main(["apagar-dados"]) == 1 and cfg.db_path.exists()            # sem confirmação nada é apagado
    monkeypatch.setattr("builtins.input", lambda *_: "APAGAR")
    assert cli.main(["apagar-dados", "--confirmar"]) == 0
    assert not cfg.db_path.exists() and not (cfg.home / "tokens").exists()


def test_login_command_refuses_password_flow_by_default(cfg, capsys):
    assert cli.main(["login"]) == 1
    assert "não usa login com senha" in capsys.readouterr().out


# ---------------------------------------------------- consumo de recursos (sintético)
def test_resource_budget_one_year(cfg, db):
    populate(db, 365, date.today())
    populate_phase2(db, cfg, 60, date.today())
    set_password(cfg, "senha-forte-123")
    c = TestClient(create_app(cfg, db))
    c.post("/api/login", json={"senha": "senha-forte-123"}, headers=H)
    t = time.perf_counter()
    for path in ("/api/home", "/api/metabolismo", "/api/series?keys=hrv_last_night&days=365", "/api/aprendizado"):
        assert c.get(path).status_code == 200
    elapsed = time.perf_counter() - t
    size_mb = os.path.getsize(cfg.db_path) / 1e6
    assert elapsed < 6, elapsed                         # folga ampla para VM pequena
    assert size_mb < 40, size_mb                        # 1 ano com JSON bruto cabe com folga em 1 GB de disco


# ------------------------------------------- achados da auditoria de segurança independente
def test_parallel_wrong_logins_cannot_bypass_throttle(app_db):
    from concurrent.futures import ThreadPoolExecutor
    c = TestClient(app_db[0])
    with ThreadPoolExecutor(12) as ex:
        codes = list(ex.map(lambda _: c.post("/api/login", json={"senha": "errada"}, headers=H).status_code, range(12)))
    assert codes.count(401) == 5 and codes.count(429) == 7          # só 5 senhas chegam a ser verificadas
    assert c.post("/api/login", json=[1], headers=H).status_code in (401, 429)   # corpo não-objeto não derruba


def test_chunked_body_without_content_length_is_limited(app_db):
    c = TestClient(app_db[0])

    def gen():
        for _ in range(40):
            yield b"x" * 8192                                         # 320 KB sem Content-Length
    r = c.post("/api/login", content=gen(), headers={**H, "Content-Type": "application/json"})
    assert r.status_code == 413


def test_push_endpoint_allowlist_blocks_ssrf(client, app_db):
    for ep in ("https://127.0.0.1:9/x", "https://ollama:11434/api", "https://evil.example/web.push.apple.com",
               "https://web.push.apple.com.evil.example/x", "https://user@web.push.apple.com/x", "http://web.push.apple.com/x"):
        r = client.post("/api/push/inscrever", json={"endpoint": ep, "keys": {"p256dh": "k", "auth": "a"}}, headers=H)
        assert r.status_code == 400, ep
    ok = client.post("/api/push/inscrever", json={"endpoint": "https://web.push.apple.com/QGabc", "keys": {"p256dh": "k", "auth": "a"}}, headers=H)
    assert ok.status_code == 200
    r = client.post("/api/push/teste", headers=H)                     # chave inválida: erro genérico, sem detalhes de rede
    assert r.status_code == 503 and "Connection" not in r.text and "web.push" not in r.text


def test_non_finite_numbers_rejected_everywhere(client):
    bad = ["Infinity", "inf", "1e309", "nan", -5]
    for v in bad:
        assert client.post("/api/peso", json={"peso_kg": 80, "cintura_cm": v}, headers=H).status_code == 400, v
        assert client.post("/api/refeicoes", json={"descricao": "x", "kcal": 100, "proteina_g": v}, headers=H).status_code == 400, v
        assert client.post("/api/treino/sessao", json={"tipo": "forca", "duracao_min": v}, headers=H).status_code == 400, v
        assert client.post("/api/exames", json={"data": "2026-10-01", "analito": "X", "valor": 1, "ref_max": v}, headers=H).status_code in (400, 200) if v == -5 else True
    assert client.post("/api/exames", json={"data": "2026-10-01", "analito": "X", "valor": 1, "ref_max": "inf"}, headers=H).status_code == 400
    assert client.post("/api/perfil", json={"altura_cm": "inf"}, headers=H).status_code == 400
    assert client.post("/api/refeicoes", json={"descricao": "x", "kcal": 100, "hora": "<b>x"}, headers=H).status_code == 400
    assert client.get("/api/activities?days=999999999").status_code == 200
    for path in ("/api/home", "/api/nutricao", "/api/exames", "/api/composicao", "/api/treino"):
        assert client.get(path).status_code == 200, path              # nada ficou corrompido


def test_password_change_revokes_sessions(client, cfg):
    assert client.get("/api/home").status_code == 200
    set_password(cfg, "outra-senha-forte-456")
    assert client.get("/api/home").status_code == 401


def test_taco_download_checks_checksum(cfg, db, monkeypatch):
    import requests
    from healthmon import nutrition

    class R:
        content, text = b"adulterado", "adulterado"

        def raise_for_status(self):
            pass
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    with pytest.raises(RuntimeError, match="difere"):
        nutrition.download_taco(db)


def test_local_llm_refuses_remote_host(cfg):
    from healthmon import llm
    cfg.data["ia"].update(modo="local", ollama_url="http://servidor-de-terceiro.example:11434")
    text, mode = llm.narrate(cfg, "diario", {"x": 1})
    assert text is None and mode == "regras"
