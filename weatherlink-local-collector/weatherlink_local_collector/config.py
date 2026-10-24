from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    collector_key: str
    poll_interval_seconds: int
    discovery_refresh_seconds: int
    live_poll_seconds: int = 10


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    collector_key = os.environ.get("COLLECTOR_KEY")
    if not core_api_url or not collector_key:
        raise RuntimeError("CORE_API_URL and COLLECTOR_KEY must both be set")
    live_poll_seconds = int(os.environ.get("LIVE_POLL_SECONDS", "10"))
    if live_poll_seconds < 2:
        raise RuntimeError("LIVE_POLL_SECONDS must be at least 2")
    return Settings(
        core_api_url=core_api_url.rstrip("/"),
        collector_key=collector_key,
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "60")),
        discovery_refresh_seconds=int(os.environ.get("DISCOVERY_REFRESH_SECONDS", "300")),
        live_poll_seconds=live_poll_seconds,
    )
