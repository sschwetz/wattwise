from __future__ import annotations

import pytest

from pricing_collector.config import get_settings


def test_get_settings_reads_env_vars(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000/")
    monkeypatch.setenv("COLLECTOR_KEY", "secret-key")

    settings = get_settings()

    assert settings.core_api_url == "http://localhost:8000"
    assert settings.collector_key == "secret-key"


def test_get_settings_raises_when_core_api_url_missing(monkeypatch):
    monkeypatch.delenv("CORE_API_URL", raising=False)
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    with pytest.raises(RuntimeError):
        get_settings()


def test_get_settings_raises_when_collector_key_missing(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.delenv("COLLECTOR_KEY", raising=False)
    with pytest.raises(RuntimeError):
        get_settings()
