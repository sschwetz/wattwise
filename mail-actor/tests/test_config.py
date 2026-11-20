from __future__ import annotations

import pytest

from mail_actor.config import get_settings


def test_reads_the_mail_actor_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORE_API_URL", "http://core-api:8000/")
    monkeypatch.setenv("MAIL_ACTOR_KEY", "mail-secret")
    monkeypatch.delenv("ACTOR_KEY", raising=False)
    settings = get_settings()
    assert settings.core_api_url == "http://core-api:8000"
    assert settings.mail_actor_key == "mail-secret"
    assert "mail-secret" not in repr(settings)


@pytest.mark.parametrize("missing", ["CORE_API_URL", "MAIL_ACTOR_KEY"])
def test_refuses_to_start_without_a_setting(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.setenv("CORE_API_URL", "http://core-api:8000")
    monkeypatch.setenv("MAIL_ACTOR_KEY", "mail-secret")
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match="CORE_API_URL and MAIL_ACTOR_KEY"):
        get_settings()


def test_the_device_actor_key_is_not_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORE_API_URL", "http://core-api:8000")
    monkeypatch.delenv("MAIL_ACTOR_KEY", raising=False)
    monkeypatch.setenv("ACTOR_KEY", "device-secret")
    with pytest.raises(RuntimeError):
        get_settings()
