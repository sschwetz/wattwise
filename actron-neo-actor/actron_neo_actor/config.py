from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    actor_key: str = field(repr=False)
    tick_seconds: int = 30
    poll_fallback_seconds: int = 30
    reconcile_seconds: int = 300
    heartbeat_seconds: int = 60
    # How long a test run holds the action before it restores.
    test_hold_seconds: int = 30


def _seconds(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a whole number of seconds") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be at least 1")
    return value


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    actor_key = os.environ.get("ACTOR_KEY")
    if not core_api_url or not actor_key:
        raise RuntimeError("CORE_API_URL and ACTOR_KEY must both be set")
    return Settings(
        core_api_url=core_api_url.rstrip("/"),
        actor_key=actor_key,
        tick_seconds=_seconds("TICK_SECONDS", 30),
        poll_fallback_seconds=_seconds("POLL_FALLBACK_SECONDS", 30),
        reconcile_seconds=_seconds("RECONCILE_SECONDS", 300),
        heartbeat_seconds=_seconds("HEARTBEAT_SECONDS", 60),
        test_hold_seconds=_seconds("TEST_HOLD_SECONDS", 30),
    )
