from __future__ import annotations

import os
from dataclasses import dataclass

LIVE_BATTERY_SOURCES = ("ensemble", "livedata")


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    collector_key: str
    poll_interval_seconds: int
    discovery_refresh_seconds: int
    live_sample_seconds: int = 3
    live_stale_seconds: int = 30
    # Where live-path battery figures come from: "ensemble" (/ivp/ensemble/power,
    # read once a minute) or "livedata" (the live feed's per-phase storage power).
    live_battery_source: str = "ensemble"


def _live_battery_source() -> str:
    """Read LIVE_BATTERY_SOURCE, defaulting to "ensemble". Raises RuntimeError on any other value."""
    value = os.environ.get("LIVE_BATTERY_SOURCE", "ensemble")
    if value not in LIVE_BATTERY_SOURCES:
        raise RuntimeError("LIVE_BATTERY_SOURCE must be 'ensemble' or 'livedata'")
    return value


def _live_intervals() -> tuple[int, int]:
    """Read (LIVE_SAMPLE_SECONDS, LIVE_STALE_SECONDS). The sample interval
    must be at least 1 and the stale threshold at least the sample interval,
    else RuntimeError naming the variable."""
    sample = int(os.environ.get("LIVE_SAMPLE_SECONDS", "3"))
    stale = int(os.environ.get("LIVE_STALE_SECONDS", "30"))
    if sample < 1:
        raise RuntimeError("LIVE_SAMPLE_SECONDS must be at least 1")
    if stale < sample:
        raise RuntimeError("LIVE_STALE_SECONDS must be at least LIVE_SAMPLE_SECONDS")
    return sample, stale


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    collector_key = os.environ.get("COLLECTOR_KEY")
    if not core_api_url or not collector_key:
        raise RuntimeError("CORE_API_URL and COLLECTOR_KEY must both be set")
    live_sample_seconds, live_stale_seconds = _live_intervals()
    return Settings(
        core_api_url=core_api_url.rstrip("/"),
        collector_key=collector_key,
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "60")),
        discovery_refresh_seconds=int(os.environ.get("DISCOVERY_REFRESH_SECONDS", "300")),
        live_sample_seconds=live_sample_seconds,
        live_stale_seconds=live_stale_seconds,
        live_battery_source=_live_battery_source(),
    )
