"""Fonte OFICIAL: Garmin Connect Personal API (biblioteca garmin-connect-personal-api-python).

- Autenticação: chave pessoal (GARMIN_PERSONAL_API_KEY, só em secrets.env/variável de
  ambiente) trocada por um token JWT de 24 h; a biblioteca oficial guarda o token em
  arquivo 0600 e o renova quando falta menos de 1 h. Não há refresh token.
- A chave pessoal expira (30/90/180/365 dias, definido por você ao criá-la); a data é
  informada em [garmin] chave_expira_em para gerar alertas antecipados.
- Limites de requisição não são publicados pela Garmin: usamos pausa entre chamadas,
  orçamento diário configurável, backoff exponencial e respeito a Retry-After.

ATENÇÃO: os caminhos dos endpoints vêm da documentação oficial; o FORMATO das respostas
não pôde ser verificado no ambiente de desenvolvimento (rede bloqueada). O parser é
defensivo e `healthmon validar-garmin` mostra a estrutura real (sem valores) para ajuste.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from .config import Config, ensure_private_dir
from .garmin import AuthRequired

log = logging.getLogger(__name__)

KEY_ENV = "GARMIN_PERSONAL_API_KEY"

# nome interno -> chamada no cliente oficial (todas GET, somente leitura)
ENDPOINTS: dict[str, Callable[[Any, str], Any]] = {
    "daily_summary": lambda c, d: c.get_daily_summaries(d, d),
    "sleep": lambda c, d: c.get_daily_sleep(d),
    "hrv": lambda c, d: c.get_daily_hrv(d),
    "heart_rate": lambda c, d: c.get_daily_heart_rate(d),
    "body_battery": lambda c, d: c.get_daily_body_battery(d, d),
    "stress": lambda c, d: c.get_daily_stress(d, d),
    "spo2": lambda c, d: c.get_daily_spo2(d),
    "max_metrics": lambda c, d: c.get_daily_vo2_max(d, d),
    "activities": lambda c, d: c.search_activities(start=0, limit=50, start_date=d, end_date=d),
    "weight": lambda c, d: c.get_weight_measurements(d, d),
    "hydration": lambda c, d: c.get_daily_hydration(d, d),
    "food_logs": lambda c, d: c.get_daily_food_logs(d),
}
# Indicadores do sistema anterior SEM endpoint na Personal API (não coletados pela via oficial)
NOT_IN_PERSONAL_API = ["training_readiness (prontidão para treino)", "respiração (endpoint dedicado)"]


class RateLimited(RuntimeError):
    def __init__(self, retry_after: float | None):
        super().__init__("Limite de requisições da Garmin atingido")
        self.retry_after = retry_after


class BudgetExceeded(RuntimeError):
    pass


def _is_empty(v: Any) -> bool:
    return v is None or v == {} or v == []


def key_expiry_status(cfg: Config, today: date | None = None) -> dict[str, Any]:
    """Situação da validade da chave pessoal (data informada pelo usuário na configuração)."""
    raw = str(cfg.get("garmin", "chave_expira_em", "") or "")
    today = today or datetime.now(cfg.tz).date()
    if not raw:
        return {"configurada": False, "dias": None, "nivel": "desconhecido"}
    try:
        exp = date.fromisoformat(raw)
    except ValueError:
        return {"configurada": False, "dias": None, "nivel": "data_invalida"}
    dias = (exp - today).days
    nivel = "expirada" if dias < 0 else "critico" if dias <= 3 else "aviso" if dias <= 14 else "ok"
    return {"configurada": True, "expira_em": raw, "dias": dias, "nivel": nivel}


@dataclass
class PersonalApiSource:
    cfg: Config
    db: Any = None                      # Database, para o orçamento diário de requisições
    session: Any = None                 # requests.Session injetável (testes)
    sleep: Callable[[float], None] = time.sleep
    _client: Any = None
    _auth: Any = None
    stats: dict[str, int] = field(default_factory=lambda: {"requisicoes": 0, "retentativas": 0})

    # ------------------------------------------------------------ autenticação
    def _api_key(self) -> str:
        key = self.cfg.secret(KEY_ENV)
        if not key:
            raise AuthRequired(
                f"Chave pessoal ausente. Gere em connect.garmin.com/app/settings/personalApiToken e "
                f"grave {KEY_ENV}=... em secrets.env (nunca no chat, no código ou no Git).")
        return key

    def _token(self, force: bool = False) -> str:
        from garmin_personal_api import HttpError, PersonalApiAuthenticator
        if self._auth is None:
            ensure_private_dir(self.cfg.token_dir)
            self._auth = PersonalApiAuthenticator(
                session=self.session, bearer_token_cache_file=self.cfg.token_dir / "garmin-personal-bearer.json")
        try:
            return self._auth.obtain_token(self._api_key(), force_refresh=force).access_token
        except HttpError as e:
            if e.status_code in (401, 403):
                raise AuthRequired("A Garmin recusou a chave pessoal (inválida, revogada ou expirada). "
                                   "Gere uma nova chave e atualize secrets.env.") from None
            if e.status_code == 429:
                raise RateLimited(None) from None
            raise RuntimeError(f"Falha ao obter token (HTTP {e.status_code})") from None

    def connect(self) -> Any:
        from garmin_personal_api import GarminPersonalApiClient
        if self._client is None:
            self._client = GarminPersonalApiClient(self._token(), session=self.session)
        return self._client

    # ------------------------------------------------------------ orçamento
    def _budget_check(self) -> None:
        if self.db is None:
            return
        limit = int(self.cfg.get("garmin", "max_requisicoes_dia", 600))
        today = datetime.now(timezone.utc).date().isoformat()
        day, _, n = (self.db.get_setting("garmin_req_count", f"{today}:0") or f"{today}:0").partition(":")
        n = int(n) if day == today else 0
        if n >= limit:
            raise BudgetExceeded(f"Orçamento diário de {limit} requisições à Garmin atingido")
        self.db.set_setting("garmin_req_count", f"{today}:{n + 1}")

    def _blocked_until(self) -> datetime | None:
        if self.db is None:
            return None
        v = self.db.get_setting("garmin_bloqueado_ate")
        if not v:
            return None
        t = datetime.fromisoformat(v)
        return t if t > datetime.now(timezone.utc) else None

    # ------------------------------------------------------------ chamadas
    def _call(self, name: str, fn: Callable[[Any, str], Any], day: str) -> tuple[str, Any]:
        from garmin_personal_api import HttpError
        import requests
        max_try = int(self.cfg.get("garmin", "retentativas", 4))
        refreshed = False
        attempt = 0
        while True:
            self._budget_check()
            self.stats["requisicoes"] += 1
            try:
                payload = fn(self.connect(), day)
                return ("vazio" if _is_empty(payload) else "ok", payload)
            except HttpError as e:
                code = e.status_code
                if code == 401 and not refreshed:   # token revogado antes do prazo: renova uma vez
                    refreshed = True
                    self._client.update_bearer_token(self._token(force=True))
                    continue
                if code in (401, 403):
                    raise AuthRequired("Token recusado pela Garmin mesmo após renovação.") from None
                if code == 404:
                    return ("indisponivel", None)
                if code == 429:
                    raise RateLimited(None) from None
                if code < 500:
                    log.warning("Garmin %s: HTTP %s", name, code)
                    return ("erro", {"erro": f"HTTP {code}"})
                err = f"HTTP {code}"
            except (requests.ConnectionError, requests.Timeout) as e:
                err = type(e).__name__
            except ValueError:                      # JSON inválido = possível mudança na API
                return ("erro", {"erro": "resposta não é JSON"})
            attempt += 1
            if attempt > max_try:
                return ("erro", {"erro": err})
            self.stats["retentativas"] += 1
            self.sleep(min(120.0, 2.0 ** attempt + random.uniform(0, 1)))  # backoff exponencial + jitter

    def fetch_day(self, day: str) -> dict[str, tuple[str, Any]]:
        blocked = self._blocked_until()
        if blocked:
            raise RateLimited((blocked - datetime.now(timezone.utc)).total_seconds())
        pause = float(self.cfg.get("coleta", "pausa_entre_chamadas_s", 1.5))
        out: dict[str, tuple[str, Any]] = {}
        try:
            for name, fn in ENDPOINTS.items():
                out[name] = self._call(name, fn, day)
                self.sleep(pause)
        except RateLimited:
            # circuit breaker: não insiste; libera após o período configurado
            mins = int(self.cfg.get("garmin", "pausa_apos_429_min", 60))
            if self.db is not None:
                self.db.set_setting("garmin_bloqueado_ate",
                                    (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat())
            raise
        return out

    def close(self) -> None:
        for o in (self._client, self._auth):
            if o is not None and self.session is None:
                o.close()


def shape(obj: Any, depth: int = 0, max_depth: int = 4) -> Any:
    """Estrutura de um JSON (nomes de campos e tipos), SEM valores — segura para compartilhar."""
    if isinstance(obj, dict):
        if depth >= max_depth:
            return "{…}"
        return {k: shape(v, depth + 1, max_depth) for k, v in list(obj.items())[:80]}
    if isinstance(obj, list):
        return [shape(obj[0], depth + 1, max_depth)] if obj else []
    return type(obj).__name__
