from __future__ import annotations

import pytest

from local_pricing_collector.config import get_settings


def test_reads_url_key_and_default_horizon(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://core-api:8000/")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.delenv("LOCAL_HORIZON_HOURS", raising=False)

    settings = get_settings()

    assert settings.core_api_url == "http://core-api:8000"
    assert settings.collector_key == "k"
    assert settings.horizon_hours == 48


def test_horizon_can_be_set(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://x")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.setenv("LOCAL_HORIZON_HOURS", "24")
    assert get_settings().horizon_hours == 24


def test_missing_url_or_key_raises(monkeypatch):
    monkeypatch.delenv("CORE_API_URL", raising=False)
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    with pytest.raises(RuntimeError):
        get_settings()


def test_a_horizon_below_one_raises(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://x")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.setenv("LOCAL_HORIZON_HOURS", "0")
    with pytest.raises(RuntimeError):
        get_settings()


def test_a_non_numeric_horizon_raises(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://x")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.setenv("LOCAL_HORIZON_HOURS", "soon")
    with pytest.raises(RuntimeError):
        get_settings()
