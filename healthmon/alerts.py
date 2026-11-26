"""Regras de alerta — sempre relativas à SUA linha de base, com persistência e cooldown.

Nenhum limite fixo/clínico é usado: um alerta só dispara quando um padrão
desfavorável se mantém por vários dias em relação ao seu próprio histórico.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from . import analysis as an
from .config import Config
from .db import Database
from .metrics import METRICS

TITLES = {
    "hrv_queda_persistente": "HRV abaixo do seu padrão há vários dias",
    "fc_repouso_aumento": "FC de repouso acima do seu padrão há vários dias",
    "sono_deterioracao": "Sono abaixo do seu padrão há vários dias",
    "recuperacao_insuficiente": "Sinais repetidos de recuperação insuficiente",
    "estresse_elevado": "Estresse acima do seu padrão há vários dias",
    "falha_sincronizacao": "Dados do Garmin não estão sincronizando",
}


def _cooldown_ok(db: Database, kind: str, hours: float) -> bool:
    last = db.last_alert(kind)
    if not last:
        return True
    t = datetime.fromisoformat(last["created_at"])
    return datetime.now(timezone.utc) - t >= timedelta(hours=hours)


def evaluate(cfg: Config, db: Database, day: date) -> list[int]:
    enabled = cfg.data.get("alertas", {})
    cooldown = float(cfg.get("notificacoes", "cooldown_horas", 72))
    w = int(cfg.get("analise", "janela_base_dias", 28))
    mn = int(cfg.get("analise", "minimo_dias_base", 10))
    store = an.load_store(db, day, w + 60)
    created: list[int] = []

    def fire(kind: str, msg: str, evidence: dict) -> None:
        if enabled.get(kind, True) and _cooldown_ok(db, kind, cooldown):
            created.append(db.add_alert(day.isoformat(), kind, "atencao", TITLES[kind], msg, evidence))

    def persistent_bad(key: str) -> an.Assessment | None:
        a = an.assess(store, key, day, w, mn)
        if a.base.confiavel and a.padrao == "persistente" and a.leitura == "desfavoravel":
            return a
        return None

    for key, kind in (("hrv_last_night", "hrv_queda_persistente"), ("resting_hr", "fc_repouso_aumento"),
                      ("stress_avg", "estresse_elevado")):
        a = persistent_bad(key)
        if a:
            m = METRICS[key]
            fire(kind, (f"{m.label}: média dos últimos 7 dias {a.as_dict()['media7']} {m.unit} vs. "
                        f"mediana usual {a.as_dict()['mediana']} {m.unit}; {a.dias_fora7} de 7 dias fora da "
                        "sua faixa. Considere priorizar descanso e sono; se houver sintomas, procure um profissional."),
                 a.as_dict())

    s1, s2 = persistent_bad("sleep_score"), persistent_bad("sleep_seconds")
    if s1 or s2:
        a = s1 or s2
        fire("sono_deterioracao", (f"{METRICS[a.key].label} abaixo do seu padrão em {a.dias_fora7} dos "
                                   "últimos 7 dias. Veja o relatório para sugestões de rotina."), a.as_dict())

    rec = an.recovery(store, day, w, mn)
    if rec.persistente:
        fire("recuperacao_insuficiente", ("Sinais desfavoráveis em pelo menos 3 dos últimos 4 dias: "
                                          + ", ".join(rec.sinais) + ". Sugere-se reduzir a intensidade "
                                          "dos treinos e priorizar recuperação."), rec.__dict__)
    return created


def evaluate_key_expiry(cfg: Config, db: Database) -> int | None:
    """Aviso antecipado do vencimento da chave pessoal da Garmin (14 e 3 dias antes; e após vencer)."""
    from .garmin_official import key_expiry_status
    if cfg.get("garmin", "fonte", "oficial") != "oficial":
        return None
    st = key_expiry_status(cfg)
    if st["nivel"] not in ("aviso", "critico", "expirada"):
        return None
    kind = f"chave_garmin_{st['nivel']}"
    if not _cooldown_ok(db, kind, 24 * (10 if st["nivel"] == "aviso" else 2)):
        return None
    msg = ("A chave pessoal da Garmin expirou." if st["nivel"] == "expirada"
           else f"A chave pessoal da Garmin expira em {st['dias']} dia(s) ({st['expira_em']}).")
    return db.add_alert(date.today().isoformat(), kind, "atencao", "Chave da Garmin perto do vencimento"
                        if st["nivel"] != "expirada" else "Chave da Garmin expirada",
                        msg + " Gere uma nova em connect.garmin.com/app/settings/personalApiToken, grave em secrets.env "
                        "e atualize [garmin] chave_expira_em.", st)


def evaluate_sync(cfg: Config, db: Database, auth_error: bool = False) -> int | None:
    evaluate_key_expiry(cfg, db)
    if not cfg.data.get("alertas", {}).get("falha_sincronizacao", True):
        return None
    cooldown = min(24.0, float(cfg.get("notificacoes", "cooldown_horas", 72)))
    if auth_error:
        if _cooldown_ok(db, "falha_sincronizacao", cooldown):
            return db.add_alert(date.today().isoformat(), "falha_sincronizacao", "atencao",
                                "Acesso à Garmin recusado",
                                "A chave pessoal está ausente, inválida ou expirada. Gere uma nova chave no site da "
                                "Garmin e grave em secrets.env no servidor (GARMIN_PERSONAL_API_KEY).", {})
        return None
    hist = db.sync_history(3)
    last_ok = db.last_successful_sync()
    stale = True
    if last_ok:
        stale = datetime.now(timezone.utc) - datetime.fromisoformat(last_ok) > timedelta(hours=24)
    three_fail = len(hist) == 3 and all(h["status"] == "erro" for h in hist)
    if (stale or three_fail) and hist and _cooldown_ok(db, "falha_sincronizacao", cooldown):
        return db.add_alert(date.today().isoformat(), "falha_sincronizacao", "atencao",
                            TITLES["falha_sincronizacao"],
                            "Nenhuma sincronização bem-sucedida recente. Verifique a internet do computador, "
                            "e abra o Garmin Connect no iPhone para enviar os dados da pulseira.",
                            {"ultima_ok": last_ok, "ultimos": [h["status"] for h in hist]})
    return None
