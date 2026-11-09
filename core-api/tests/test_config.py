from __future__ import annotations

import pytest

from core_api.config import get_settings

REQUIRED_ENV = {
    "JWT_SECRET": "test-secret",
    "ENVOY_COLLECTOR_KEY": "envoy-key",
    "WEATHER_COLLECTOR_KEY": "weather-key",
    "PRICING_COLLECTOR_KEY": "pricing-key",
    "ACTOR_KEY": "actor-key",
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


def test_get_settings_reads_actor_key(monkeypatch):
    _set_required_env(monkeypatch)
    assert get_settings().actor_key == "actor-key"


def test_get_settings_raises_if_actor_key_missing(monkeypatch):
    _set_required_env(monkeypatch, ACTOR_KEY=None)
    with pytest.raises(RuntimeError):
        get_settings()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0123456789abcdef", "0123456"),
        ("ABCDEF0123", "abcdef0"),
        ("  abcdef0123  ", "abcdef0"),
        ("abc12", "unknown"),
        ("HEAD", "unknown"),
        ('ab"cdef0123', "unknown"),
        ("", "unknown"),
        ("dev", "dev"),
        ("unknown", "unknown"),
    ],
)
def test_get_settings_normalises_git_commit(monkeypatch, raw, expected):
    _set_required_env(monkeypatch, GIT_COMMIT=raw)
    assert get_settings().git_commit == expected


def test_get_settings_git_commit_defaults_to_unknown(monkeypatch):
    _set_required_env(monkeypatch, GIT_COMMIT=None)
    assert get_settings().git_commit == "unknown"
