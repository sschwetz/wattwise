from __future__ import annotations

import pytest

from envoy_collector.config import get_settings


def test_get_settings_reads_env_vars(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000/")
    monkeypatch.setenv("COLLECTOR_KEY", "secret-key")
    monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("DISCOVERY_REFRESH_SECONDS", raising=False)

    settings = get_settings()

    assert settings.core_api_url == "http://localhost:8000"  # trailing slash stripped
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
