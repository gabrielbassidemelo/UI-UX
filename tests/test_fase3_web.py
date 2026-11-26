"""Fase 3 na API web: check-in validado, triagem, calibração, evidências, conferência Garmin e backup (dados SINTÉTICOS)."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from healthmon import backup, safety
from healthmon.auth import set_password
from healthmon.demo import populate, populate_phase2
from healthmon.web.app import create_app

from .conftest import END

H = {"X-HM": "1"}
DIA = END.isoformat()
NEW_GET = ["/api/calibracao", "/api/evidencias/resumo", f"/api/garmin/conferencia?dia={DIA}", "/api/backup/status",
           "/api/seguranca/triagem"]
SECRET_URL = "https://objectstorage.sa-saopaulo-1.oraclecloud.com/p/SEGREDOTOKEN123/n/ns/b/bk/o/"


@pytest.fixture(scope="module")
def modelo(tmp_path_factory):
    """Banco sintético montado UMA vez por módulo; cada teste recebe uma cópia própria (isolamento sem repetir o custo)."""
    import sqlite3
    from healthmon.config import init_home, load_config
    from healthmon.db import Database
    c = load_config(init_home(tmp_path_factory.mktemp("modelo") / "home"))
    d = Database(c.db_path)
    populate(d, 90, END)
    populate_phase2(d, c, 60, END)
    con = sqlite3.connect(c.db_path)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    return c.db_path


@pytest.fixture
def app_db(cfg, modelo):
    import shutil
    from healthmon.db import Database
    shutil.copy(modelo, cfg.db_path)
    db = Database(cfg.db_path)
    set_password(cfg, "senha-forte-123")
    return create_app(cfg, db, sync_fn=lambda: None), db


@pytest.fixture
def client(app_db):
    c = TestClient(app_db[0])
    assert c.post("/api/login", json={"senha": "senha-forte-123"}, headers=H).status_code == 200
    return c


TRI_OK = {k: False for k in safety.SYMPTOMS}


def ck(client, **kw):
    return client.post("/api/checkin", json={"dia": DIA, **kw}, headers=H)


# ------------------------------------------------------------------ autenticação
def test_new_endpoints_require_authentication(app_db):
    c = TestClient(app_db[0])
    for p in NEW_GET:
        assert c.get(p, headers=H).status_code == 401, p
    assert c.post("/api/garmin/conferencia", json={"chave": "steps", "ok": True, "dia": DIA}, headers=H).status_code == 401


# ------------------------------------------------------------------ check-in
def test_checkin_new_fields_roundtrip(client, app_db):
    r = ck(client, fatigue=4, perceived_stress="2", sleep_quality=3, adherence="parcial", perceived_recovery=7,
           triagem=TRI_OK)
    assert r.status_code == 200, r.text
    assert r.json()["urgente"] is False and r.json()["aviso_urgente"] is None
    assert r.json()["prontidao"]["triagem_respondida"] is True
    row = client.get(f"/api/checkin?dia={DIA}").json()
    assert (row["fatigue"], row["perceived_stress"], row["sleep_quality"], row["adherence"]) == (4, 2, 3, 0.5)
    assert json.loads(row["triagem"]) == TRI_OK


def test_checkin_absent_is_none_never_zero(client):
    assert ck(client, fatigue="", perceived_stress=None, adherence="").status_code == 200
    row = client.get(f"/api/checkin?dia={DIA}").json()
    assert row["fatigue"] is None and row["perceived_stress"] is None and row["adherence"] is None
    assert row["triagem"] is None


@pytest.mark.parametrize("payload", [
    {"fatigue": 0}, {"fatigue": 6}, {"perceived_stress": 9}, {"sleep_quality": 0}, {"perceived_recovery": 11},
    {"fatigue": 2.5}, {"fatigue": "abc"}, {"fatigue": True}, {"fatigue": "nan"}, {"adherence": "talvez"},
    {"adherence": 1}, {"energy": -1},
])
def test_checkin_rejects_invalid(client, payload):
    assert ck(client, **payload).status_code == 400


@pytest.mark.parametrize("tri", [
    {**TRI_OK, "inventado": True},                      # chave desconhecida
    {k: v for k, v in list(TRI_OK.items())[1:]},        # faltando sintoma: não respondido não vira "não"
    {**TRI_OK, "peito": "nao"}, {**TRI_OK, "peito": 0}, [True], "não json",
])
def test_checkin_rejects_bad_triage(client, tri):
    assert ck(client, triagem=tri).status_code == 400


def test_bad_checkin_does_not_save_anything(client):
    assert ck(client, fatigue=3, triagem={"peito": True}).status_code == 400
    assert client.get(f"/api/checkin?dia={DIA}").json().get("fatigue") is None


def test_urgent_symptom_returns_samu_warning(client):
    r = ck(client, triagem={**TRI_OK, "peito": True})
    assert r.status_code == 200
    j = r.json()
    assert j["urgente"] is True and "SAMU 192" in j["aviso_urgente"]
    assert j["prontidao"]["nivel"] == "parar"


def test_nonurgent_symptom_is_not_flagged_urgent(client):
    j = ck(client, triagem={**TRI_OK, "febre": True}).json()
    assert j["urgente"] is False and j["prontidao"]["nivel"] == "parar" and "febre" in j["prontidao"]["sintomas"]


def test_partial_checkin_keeps_previous_triage(client):
    ck(client, triagem={**TRI_OK, "febre": True})
    ck(client, energy=3)
    assert json.loads(client.get(f"/api/checkin?dia={DIA}").json()["triagem"])["febre"] is True


# ------------------------------------------------------------------ endpoints novos
def test_calibracao(client):
    j = client.get(f"/api/calibracao?dia={DIA}").json()
    assert j["estagio"] in ("observacao", "linha_de_base", "personalizado", "longitudinal")
    assert j["dias_validos"] >= 28 and isinstance(j["suspenso"], list)
    assert set(j["suspenso"]) == {k for k, v in j["permite"].items() if not v}
    assert client.get("/api/calibracao?dia=ontem").status_code == 400


def test_calibracao_early_stage_lists_suspended(cfg, db):
    populate(db, 3, END)
    set_password(cfg, "senha-forte-123")
    c = TestClient(create_app(cfg, db, sync_fn=lambda: None))
    c.post("/api/login", json={"senha": "senha-forte-123"}, headers=H)
    j = c.get(f"/api/calibracao?dia={DIA}").json()
    assert j["estagio"] == "observacao" and "progressao_de_carga" in j["suspenso"]


def test_evidencias_resumo(client):
    j = client.get("/api/evidencias/resumo").json()
    assert j["total"] > 0 and sum(j["por_classe"].values()) == j["total"]
    assert set(j["por_classe"]) == {"verificada", "parcialmente_verificada", "nao_verificada", "inadequada"}
    assert "não é auditoria humana independente" in j["aviso"]
    refs = client.get("/api/kb").json()["resultados"]
    assert all(r["classe"] in j["classes"] and r["classe_texto"] for r in refs)


def test_garmin_conferencia(client):
    j = client.get(f"/api/garmin/conferencia?dia={DIA}").json()
    assert j["dia"] == DIA and j["itens"] and all({"chave", "rotulo", "valor", "situacao"} <= set(i) for i in j["itens"])
    key = j["itens"][0]["chave"]
    assert client.post("/api/garmin/conferencia", json={"chave": key, "ok": True, "dia": DIA}, headers=H).json() == {"ok": True}
    assert client.post("/api/garmin/conferencia", json={"chave": j["itens"][1]["chave"], "ok": False, "dia": DIA}, headers=H).status_code == 200
    j2 = client.get(f"/api/garmin/conferencia?dia={DIA}").json()
    assert j2["itens"][0]["situacao"] == "confere" and j2["itens"][1]["situacao"] == "diverge"


@pytest.mark.parametrize("body", [{"chave": "inexistente", "ok": True}, {"chave": "steps", "ok": "sim"}, {"chave": "steps"},
                                  {"ok": True}, {"chave": 5, "ok": True}, {"chave": "steps", "ok": True, "dia": "x"}])
def test_garmin_conferencia_rejects_invalid(client, body):
    assert client.post("/api/garmin/conferencia", json=body, headers=H).status_code == 400


def test_seguranca_triagem_lists_questions_and_missing(client, app_db):
    app_db[1].set_json("perfil", {})                                   # o demo já traz triagem respondida
    j = client.get("/api/seguranca/triagem").json()
    assert [q["chave"] for q in j["perguntas"]] == list(safety.TRIAGE) and len(j["perguntas"]) == 15
    assert all(q["resposta"] is None for q in j["perguntas"])          # nada pré-marcado
    assert {s["chave"]: s["urgente"] for s in j["sintomas"]} == {k: u for k, (_, u) in safety.SYMPTOMS.items()}
    assert set(j["faltam"]) >= set(safety.TRIAGE) and set(j["limitacoes_opcoes"]) == {"ombro", "joelho", "lombar", "punho", "quadril"}
    assert "SAMU 192" in j["aviso_urgente"]
    assert client.post("/api/perfil", json={"diabetes": False, "alergia_peixe": True}, headers=H).status_code == 200
    j = client.get("/api/seguranca/triagem").json()
    ans = {q["chave"]: q["resposta"] for q in j["perguntas"]}
    assert ans["diabetes"] is False and ans["alergia_peixe"] is True and ans["doenca_renal"] is None
    assert "diabetes" not in j["faltam"] and "doenca_renal" in j["faltam"]


def test_profile_limitations_closed_set(client):
    assert client.post("/api/perfil", json={"limitacoes": ["joelho", "lombar"]}, headers=H).status_code == 200
    assert client.post("/api/perfil", json={"limitacoes": ["cotovelo"]}, headers=H).status_code == 400


# ------------------------------------------------------------------ backup
def test_backup_status_no_backup(client):
    j = client.get("/api/backup/status").json()
    assert j["local"] == {"existe": False} and j["externo"]["configurado"] is False and j["externo"]["alerta"] is False


def test_backup_status_does_not_leak_url_secret_or_paths(client, cfg, monkeypatch):
    monkeypatch.setenv("OCI_PAR_URL", SECRET_URL)
    f = backup.create_backup(cfg)
    (cfg.home / "offsite_state.json").write_text(json.dumps({
        "ultimo_erro": {"quando": "2026-11-19T10:00:00Z", "arquivo": f.name,
                        "mensagem": f"falha em {SECRET_URL}healthmon.hmbk via {cfg.home}/backups/x.hmbk"},
        "falhas_consecutivas": 3, "enviados": []}), encoding="utf-8")
    r = client.get("/api/backup/status")
    txt = r.text
    assert r.status_code == 200
    for leak in ("SEGREDOTOKEN123", "oraclecloud", "https://", str(cfg.home), "frase-senha-de-teste-123"):
        assert leak not in txt, leak
    j = r.json()
    assert j["externo"]["configurado"] is True and j["externo"]["alerta"] is True and j["externo"]["falhas_consecutivas"] == 3
    assert j["local"]["existe"] and j["local"]["arquivo"] == f.name and j["local"]["tamanho_bytes"] > 0


def test_backup_status_reads_header_of_v2_backup(client, cfg):
    _priv, pub = backup.generate_recovery_keypair()
    backup.save_public_key(cfg, pub)
    f = backup.create_backup(cfg)
    j = client.get("/api/backup/status").json()["local"]
    assert j["formato"] == "HMBK2" and j["criado_utc"] and j["linhas_daily_metrics"] > 0 and j["arquivo"] == f.name
    for k in ("sha256_banco", "efemera", "destinatario"):
        assert k not in j


# ------------------------------------------------------------------ bloqueios nunca mostram números antigos
def test_blocked_nutrition_and_training_hide_old_numbers(client, app_db, cfg):
    _, db = app_db
    from healthmon import nutrition as nut
    t = nut.compute_targets(db, cfg, END)
    assert t["disponivel"]
    db.set_json("meta_nutricional", {**t, "kcal": 2222, "fibra_g": None, "agua_ml": None})
    db.set_json("perfil", {})                      # triagem não respondida
    # perfil sem triagem respondida: tudo bloqueado
    n = client.get("/api/nutricao").json()
    assert n["revisao"]["estado"] == "indisponivel" and n["resumo"]["meta"] is None and n["revisao"]["vigente"] is None
    assert n["revisao"]["calculada"]["faltam"] or n["revisao"]["calculada"]["bloqueio"]
    assert "2222" not in client.get("/api/nutricao").text
    h = client.get("/api/home").json()
    assert h["nutricao"]["meta"] is None and h["nutricao"]["meta_bloqueada"] is True
    t = client.get("/api/treino").json()
    assert t["bloqueio"] and t["ativo"] is None and t["proposto"] is None and t["hoje"]["tipo"] in ("bloqueado", "descanso")
    s = client.get("/api/suplementos").json()
    assert s["disponivel"] is False


def test_demo_profile_has_triage_answered_and_pages_work(client):
    j = client.get("/api/seguranca/triagem").json()
    assert j["faltam"] == [] and all(isinstance(q["resposta"], bool) for q in j["perguntas"])
    for p in ("/api/nutricao", "/api/treino", "/api/suplementos", "/api/home"):
        assert client.get(p).status_code == 200, p


def test_backup_download(client, cfg):
    assert client.get("/api/backup/baixar").status_code == 404
    f = backup.create_backup(cfg)
    r = client.get("/api/backup/baixar")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/octet-stream")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-disposition"] == f'attachment; filename="{f.name}"'
    assert str(cfg.home) not in r.headers["content-disposition"]
    assert r.content.startswith(b"HMBK") and not r.content.startswith(b"SQLite format")
    assert r.content == f.read_bytes()


def test_backup_download_requires_auth(app_db):
    assert TestClient(app_db[0]).get("/api/backup/baixar", headers=H).status_code == 401


def test_confirmed_target_without_fiber_and_water_does_not_break_pages(client, app_db, cfg):
    from healthmon import nutrition as nut
    _, db = app_db
    t = nut.compute_targets(db, cfg, END)
    db.set_json("meta_nutricional", {**t, "fibra_g": None, "agua_ml": None})
    for p in ("/api/home", "/api/nutricao"):
        assert client.get(p).status_code == 200, p


def test_feedback_endpoint_is_read_only_and_authenticated(app_db, client):
    assert TestClient(app_db[0]).get("/api/feedback", headers=H).status_code == 401
    r = client.get("/api/feedback").json()
    assert r["cruzamento"]["pares"] and "disponivel" in r["recomendacoes"]
    assert all("texto" in p for p in r["cruzamento"]["pares"])
