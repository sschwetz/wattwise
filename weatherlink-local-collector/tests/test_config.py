from __future__ import annotations

import pytest

from weatherlink_local_collector.config import get_settings


def test_get_settings_reads_env_vars(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000/")
    monkeypatch.setenv("COLLECTOR_KEY", "secret-key")
    monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("DISCOVERY_REFRESH_SECONDS", raising=False)

    settings = get_settings()

    assert settings.core_api_url == "http://localhost:8000"
    assert settings.collector_key == "secret-key"
    assert settings.poll_interval_seconds == 60
    assert settings.discovery_refresh_seconds == 300


def test_get_settings_respects_custom_intervals(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("DISCOVERY_REFRESH_SECONDS", "120")

    settings = get_settings()
    assert settings.poll_interval_seconds == 30
    assert settings.discovery_refresh_seconds == 120


def test_get_settings_raises_if_required_var_missing(monkeypatch):
    monkeypatch.delenv("CORE_API_URL", raising=False)
    monkeypatch.delenv("COLLECTOR_KEY", raising=False)

    with pytest.raises(RuntimeError):
        get_settings()


def test_get_settings_live_poll_seconds_defaults_to_10(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.delenv("LIVE_POLL_SECONDS", raising=False)

    assert get_settings().live_poll_seconds == 10


def test_get_settings_accepts_live_poll_seconds_of_5(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.setenv("LIVE_POLL_SECONDS", "5")

    assert get_settings().live_poll_seconds == 5


def test_get_settings_rejects_live_poll_seconds_below_2(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.setenv("LIVE_POLL_SECONDS", "1")

    with pytest.raises(RuntimeError, match="LIVE_POLL_SECONDS must be at least 2"):
        get_settings()
