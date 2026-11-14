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


def _required_env(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    for name in ("STREAM_ENABLED", "STREAM_DURATION_SECONDS", "STREAM_PORT"):
        monkeypatch.delenv(name, raising=False)


def test_get_settings_stream_defaults(monkeypatch):
    _required_env(monkeypatch)

    settings = get_settings()

    assert settings.stream_enabled is True
    assert settings.stream_duration_seconds == 300
    assert settings.stream_port == 22222


def test_get_settings_reads_stream_env_vars(monkeypatch):
    _required_env(monkeypatch)
    monkeypatch.setenv("STREAM_ENABLED", "false")
    monkeypatch.setenv("STREAM_DURATION_SECONDS", "600")
    monkeypatch.setenv("STREAM_PORT", "22223")

    settings = get_settings()

    assert settings.stream_enabled is False
    assert settings.stream_duration_seconds == 600
    assert settings.stream_port == 22223


@pytest.mark.parametrize("value", ["29", "86401"])
def test_get_settings_rejects_stream_duration_outside_the_devices_range(monkeypatch, value):
    _required_env(monkeypatch)
    monkeypatch.setenv("STREAM_DURATION_SECONDS", value)

    with pytest.raises(RuntimeError, match="STREAM_DURATION_SECONDS must be between 30 and 86400"):
        get_settings()
