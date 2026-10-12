from __future__ import annotations

import pytest

from core_api.config import get_settings


REQUIRED_ENV = {
    "JWT_SECRET": "test-secret",
    "ENVOY_COLLECTOR_KEY": "envoy-key",
    "WEATHER_COLLECTOR_KEY": "weather-key",
    "PRICING_COLLECTOR_KEY": "pricing-key",
}


def _set_required_env(monkeypatch, **overrides):
    env = {**REQUIRED_ENV, **overrides}
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_get_settings_reads_collector_keys(monkeypatch):
    _set_required_env(monkeypatch)
    settings = get_settings()
    assert settings.envoy_collector_key == "envoy-key"
    assert settings.weather_collector_key == "weather-key"
    assert settings.pricing_collector_key == "pricing-key"


def test_get_settings_raises_if_envoy_collector_key_missing(monkeypatch):
    _set_required_env(monkeypatch, ENVOY_COLLECTOR_KEY=None)
    with pytest.raises(RuntimeError):
        get_settings()


def test_get_settings_raises_if_weather_collector_key_missing(monkeypatch):
    _set_required_env(monkeypatch, WEATHER_COLLECTOR_KEY=None)
    with pytest.raises(RuntimeError):
        get_settings()


def test_get_settings_raises_if_pricing_collector_key_missing(monkeypatch):
    _set_required_env(monkeypatch, PRICING_COLLECTOR_KEY=None)
    with pytest.raises(RuntimeError):
        get_settings()
