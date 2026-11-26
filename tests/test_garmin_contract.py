"""Garante que o coletor usa apenas métodos/assinaturas reais da biblioteca garminconnect."""
from unittest import mock

from garminconnect import Garmin

from healthmon.garmin import ENDPOINTS, GarminConnectSource


def test_endpoints_match_library_signatures(cfg, monkeypatch):
    api = mock.create_autospec(Garmin, instance=True)
    for name in ENDPOINTS:
        getattr(api, {"user_summary": "get_user_summary", "sleep": "get_sleep_data", "hrv": "get_hrv_data",
                      "training_readiness": "get_training_readiness", "respiration": "get_respiration_data",
                      "spo2": "get_spo2_data", "max_metrics": "get_max_metrics",
                      "activities": "get_activities_by_date"}[name]).return_value = {"ok": 1}
    src = GarminConnectSource(cfg, _api=api)
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = src.fetch_day("2026-11-20")
    assert set(out) == set(ENDPOINTS)
    assert all(status == "ok" for status, _ in out.values())  # autospec falharia em assinatura errada
    api.get_activities_by_date.assert_called_once_with("2026-11-20", "2026-11-20")


def test_endpoint_error_isolated(cfg, monkeypatch):
    api = mock.create_autospec(Garmin, instance=True)
    api.get_hrv_data.side_effect = RuntimeError("boom")
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = GarminConnectSource(cfg, _api=api).fetch_day("2026-11-20")
    assert out["hrv"][0] == "erro" and out["sleep"][0] in ("ok", "vazio")
