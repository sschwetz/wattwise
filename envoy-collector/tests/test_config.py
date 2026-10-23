from __future__ import annotations

import pytest

from envoy_collector.config import get_settings


def test_get_settings_reads_env_vars(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000/")
    monkeypatch.setenv("COLLECTOR_KEY", "secret-key")
    monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("DISCOVERY_REFRESH_SECONDS", raising=False)
    monkeypatch.delenv("LIVE_SAMPLE_SECONDS", raising=False)
    monkeypatch.delenv("LIVE_STALE_SECONDS", raising=False)

    settings = get_settings()

    assert settings.core_api_url == "http://localhost:8000"  # trailing slash stripped
    assert settings.collector_key == "secret-key"
    assert settings.poll_interval_seconds == 60
    assert settings.discovery_refresh_seconds == 300
    assert settings.live_sample_seconds == 3
    assert settings.live_stale_seconds == 30


def test_get_settings_respects_custom_intervals(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("DISCOVERY_REFRESH_SECONDS", "120")

    settings = get_settings()
    assert settings.poll_interval_seconds == 30
    assert settings.discovery_refresh_seconds == 120


def test_get_settings_reads_live_data_overrides(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.setenv("LIVE_SAMPLE_SECONDS", "5")
    monkeypatch.setenv("LIVE_STALE_SECONDS", "45")

    settings = get_settings()
    assert settings.live_sample_seconds == 5
    assert settings.live_stale_seconds == 45


def test_get_settings_raises_if_required_var_missing(monkeypatch):
    monkeypatch.delenv("CORE_API_URL", raising=False)
    monkeypatch.delenv("COLLECTOR_KEY", raising=False)

    with pytest.raises(RuntimeError):
        get_settings()


def _base_env(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("COLLECTOR_KEY", "k")


def test_live_battery_source_defaults_to_ensemble(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("LIVE_BATTERY_SOURCE", raising=False)
    assert get_settings().live_battery_source == "ensemble"


def test_live_battery_source_accepts_livedata(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("LIVE_BATTERY_SOURCE", "livedata")
    assert get_settings().live_battery_source == "livedata"


def test_live_battery_source_rejects_other_values(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("LIVE_BATTERY_SOURCE", "meters")
    with pytest.raises(RuntimeError, match="LIVE_BATTERY_SOURCE must be 'ensemble' or 'livedata'"):
        get_settings()
