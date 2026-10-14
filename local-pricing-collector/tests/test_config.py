from __future__ import annotations

import pytest

from local_pricing_collector.config import get_settings


def test_reads_url_and_key_and_strips_a_trailing_slash(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://core-api:8000/")
    monkeypatch.setenv("COLLECTOR_KEY", "k")

    settings = get_settings()

    assert settings.core_api_url == "http://core-api:8000"
    assert settings.collector_key == "k"


def test_missing_url_raises(monkeypatch):
    monkeypatch.delenv("CORE_API_URL", raising=False)
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    with pytest.raises(RuntimeError):
        get_settings()


def test_missing_key_raises(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://x")
    monkeypatch.delenv("COLLECTOR_KEY", raising=False)
    with pytest.raises(RuntimeError):
        get_settings()
