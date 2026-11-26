"""Acesso ao Garmin Connect via biblioteca `garminconnect` (não oficial).

Autenticação: login interativo único (e-mail + senha + MFA, se ativo). A senha é usada
apenas em memória e descartada; a biblioteca grava somente os tokens de sessão em
$HEALTHMON_HOME/tokens (permissão 600). As execuções automáticas usam só os tokens.
"""
from __future__ import annotations

import getpass
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .config import Config, ensure_private_dir

log = logging.getLogger(__name__)


class AuthRequired(RuntimeError):
    """Tokens ausentes ou expirados: é preciso rodar `healthmon login`."""


class GarminSource(Protocol):
    def fetch_day(self, day: str) -> dict[str, tuple[str, Any]]: ...


# endpoint -> (descrição, função que recebe (api, dia))
ENDPOINTS: dict[str, Callable[[Any, str], Any]] = {
    "user_summary": lambda api, d: api.get_user_summary(d),
    "sleep": lambda api, d: api.get_sleep_data(d),
    "hrv": lambda api, d: api.get_hrv_data(d),
    "training_readiness": lambda api, d: api.get_training_readiness(d),
    "respiration": lambda api, d: api.get_respiration_data(d),
    "spo2": lambda api, d: api.get_spo2_data(d),
    "max_metrics": lambda api, d: api.get_max_metrics(d),
    "activities": lambda api, d: api.get_activities_by_date(d, d),
}


def _is_empty(v: Any) -> bool:
    return v is None or v == {} or v == []


@dataclass
class GarminConnectSource:
    cfg: Config
    _api: Any = None

    @property
    def tokenstore(self) -> str:
        ensure_private_dir(self.cfg.token_dir)
        return str(self.cfg.token_dir)

    def connect(self) -> Any:
        if self._api is not None:
            return self._api
        from garminconnect import Garmin, GarminConnectAuthenticationError
        api = Garmin()  # sem credenciais: só tokens
        try:
            api.login(self.tokenstore)
        except GarminConnectAuthenticationError as e:
            raise AuthRequired(
                "Sessão Garmin ausente ou expirada. Rode `healthmon login` uma vez."
            ) from e
        self._api = api
        return api

    def interactive_login(self, email: str | None = None) -> str:
        from garminconnect import Garmin
        email = email or input("E-mail da conta Garmin: ").strip()
        password = getpass.getpass("Senha Garmin (não será armazenada): ")
        api = Garmin(email=email, password=password,
                     prompt_mfa=lambda: input("Código MFA recebido: ").strip())
        del password
        api.login(self.tokenstore)
        self._api = api
        return api.get_full_name() if hasattr(api, "get_full_name") else "ok"

    def fetch_day(self, day: str) -> dict[str, tuple[str, Any]]:
        """Retorna {endpoint: (status, payload)} sem interromper por falha isolada."""
        from garminconnect import (GarminConnectAuthenticationError,
                                   GarminConnectNotFoundError,
                                   GarminConnectTooManyRequestsError)
        api = self.connect()
        pause = float(self.cfg.get("coleta", "pausa_entre_chamadas_s", 1.5))
        out: dict[str, tuple[str, Any]] = {}
        for name, fn in ENDPOINTS.items():
            try:
                payload = fn(api, day)
                out[name] = ("vazio" if _is_empty(payload) else "ok", payload)
            except GarminConnectNotFoundError:
                out[name] = ("indisponivel", None)
            except GarminConnectAuthenticationError as e:
                raise AuthRequired(str(e)) from e
            except GarminConnectTooManyRequestsError:
                raise
            except Exception as e:  # noqa: BLE001 — registrar e seguir
                log.warning("Falha em %s (%s): %s", name, day, type(e).__name__)
                out[name] = ("erro", {"erro": type(e).__name__})
            time.sleep(pause)
        return out
