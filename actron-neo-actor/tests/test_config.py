import pytest

from actron_neo_actor.config import Settings, get_settings


def test_defaults_and_trailing_slash(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://core/")
    monkeypatch.setenv("ACTOR_KEY", "k")
    settings = get_settings()
    assert settings.core_api_url == "http://core"
    assert (settings.tick_seconds, settings.poll_fallback_seconds) == (30, 30)
    assert (settings.reconcile_seconds, settings.heartbeat_seconds) == (120, 60)
    assert settings.test_hold_seconds == 30


def test_overrides(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://core")
    monkeypatch.setenv("ACTOR_KEY", "k")
    monkeypatch.setenv("TICK_SECONDS", "5")
    monkeypatch.setenv("HEARTBEAT_SECONDS", "10")
    assert get_settings().tick_seconds == 5
    assert get_settings().heartbeat_seconds == 10


@pytest.mark.parametrize("missing", ["CORE_API_URL", "ACTOR_KEY"])
def test_required(monkeypatch, missing):
    monkeypatch.setenv("CORE_API_URL", "http://core")
    monkeypatch.setenv("ACTOR_KEY", "k")
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match="CORE_API_URL and ACTOR_KEY"):
        get_settings()


def test_key_not_in_repr():
    assert "topsecret" not in repr(Settings("http://core", "topsecret"))


def test_test_hold_seconds(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://core")
    monkeypatch.setenv("ACTOR_KEY", "k")
    monkeypatch.setenv("TEST_HOLD_SECONDS", "5")
    assert get_settings().test_hold_seconds == 5
    for bad in ("0", "1.5"):
        monkeypatch.setenv("TEST_HOLD_SECONDS", bad)
        with pytest.raises(RuntimeError, match="TEST_HOLD_SECONDS"):
            get_settings()
