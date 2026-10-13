from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_HORIZON_HOURS = 48


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    collector_key: str
    horizon_hours: int


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    collector_key = os.environ.get("COLLECTOR_KEY")
    if not core_api_url or not collector_key:
        raise RuntimeError("CORE_API_URL and COLLECTOR_KEY must both be set")
    raw_horizon = os.environ.get("LOCAL_HORIZON_HOURS", str(DEFAULT_HORIZON_HOURS))
    try:
        horizon_hours = int(raw_horizon)
    except ValueError as exc:
        raise RuntimeError(f"LOCAL_HORIZON_HOURS must be a whole number, got {raw_horizon!r}") from exc
    if horizon_hours < 1:
        raise RuntimeError(f"LOCAL_HORIZON_HOURS must be at least 1, got {raw_horizon!r}")
    return Settings(core_api_url=core_api_url.rstrip("/"), collector_key=collector_key, horizon_hours=horizon_hours)
