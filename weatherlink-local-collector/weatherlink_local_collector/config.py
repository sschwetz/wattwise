from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    collector_key: str
    poll_interval_seconds: int
    discovery_refresh_seconds: int
    stream_enabled: bool = True
    stream_duration_seconds: int = 300
    stream_port: int = 22222


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    collector_key = os.environ.get("COLLECTOR_KEY")
    if not core_api_url or not collector_key:
        raise RuntimeError("CORE_API_URL and COLLECTOR_KEY must both be set")
    stream_duration_seconds = int(os.environ.get("STREAM_DURATION_SECONDS", "300"))
    # 86400 is the longest broadcast the device accepts; it answers a longer request with a 400.
    if not 30 <= stream_duration_seconds <= 86400:
        raise RuntimeError("STREAM_DURATION_SECONDS must be between 30 and 86400")
    return Settings(
        core_api_url=core_api_url.rstrip("/"),
        collector_key=collector_key,
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "60")),
        discovery_refresh_seconds=int(os.environ.get("DISCOVERY_REFRESH_SECONDS", "300")),
        stream_enabled=os.environ.get("STREAM_ENABLED", "true").lower() not in ("0", "false", "no", "off"),
        stream_duration_seconds=stream_duration_seconds,
        stream_port=int(os.environ.get("STREAM_PORT", "22222")),
    )
