from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass

from weatherlink_local_collector.config import Settings, get_settings
from weatherlink_local_collector.core_api_client import fetch_collector_sources, push_weather_readings
from weatherlink_local_collector.mapping import RainState, map_barometer_readings, map_iss_readings
from weatherlink_local_collector.weatherlink_client import fetch_current_conditions

logger = logging.getLogger("weatherlink_local_collector")


def _map_iss_or_empty(conditions: list[dict], ts: datetime.datetime, rain_state: RainState) -> list[dict]:
    try:
        iss = next(c for c in conditions if c["data_structure_type"] == 1)
        return map_iss_readings(iss, ts, rain_state)
    except Exception:
        logger.exception("Failed to find/parse ISS (outdoor) data this cycle; continuing without it")
        return []


def _map_barometer_or_empty(conditions: list[dict], ts: datetime.datetime) -> list[dict]:
    try:
        barometer = next(c for c in conditions if c["data_structure_type"] == 3)
        return map_barometer_readings(barometer, ts)
    except Exception:
        logger.exception("Failed to find/parse barometer data this cycle; continuing without it")
        return []


def poll_once(settings: Settings, source_id: int, config: dict, rain_state: RainState) -> None:
    """Runs one poll cycle for one source. Unlike the Envoy collector there's
    no config to refresh (no auth), so config is read-only here."""
    ts = datetime.datetime.now(datetime.timezone.utc).replace(second=0, microsecond=0)
    try:
        data = fetch_current_conditions(config["host"])
        conditions = data["data"]["conditions"]
    except Exception:
        logger.exception("Failed to poll WeatherLink this cycle for source %s", source_id)
        return

    # map_iss_readings advances rain_state's baseline as a side effect of
    # computing this cycle's rain_mm delta. Snapshot it first so a failed
    # push can be rolled back -- otherwise that rain is lost forever: the
    # next successful push would only report the delta since the dropped
    # cycle, silently undercounting the running rain total.
    prev_rain_baseline = rain_state.last_daily_mm
    readings = _map_iss_or_empty(conditions, ts, rain_state) + _map_barometer_or_empty(conditions, ts)
    if not readings:
        return

    try:
        push_weather_readings(settings.core_api_url, source_id, settings.collector_key, readings)
    except Exception:
        rain_state.last_daily_mm = prev_rain_baseline
        logger.exception("Failed to push readings to Core API this cycle for source %s", source_id)


@dataclass
class _Source:
    """One discovered source's live state between poll cycles -- a plain
    (dict, RainState) tuple works but reads as positional noise at every call
    site (known[source_id][1], etc); named fields are clearer."""

    config: dict
    rain_state: RainState


def _refresh_sources(settings: Settings, known: dict[int, _Source]) -> dict[int, _Source]:
    try:
        discovered = fetch_collector_sources(settings.core_api_url, settings.collector_key)
    except Exception:
        logger.exception("Failed to refresh source list from Core API; keeping last-known sources")
        return known
    refreshed: dict[int, _Source] = {}
    for entry in discovered:
        source_id = entry["source_id"]
        if source_id in known:
            refreshed[source_id] = _Source(entry["config"], known[source_id].rain_state)
        else:
            refreshed[source_id] = _Source(entry["config"], RainState())
    return refreshed


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    sources: dict[int, _Source] = {}
    while not sources:
        sources = _refresh_sources(settings, sources)
        if not sources:
            logger.warning("No weather sources discovered yet; retrying in %s seconds", settings.poll_interval_seconds)
            time.sleep(settings.poll_interval_seconds)
    last_refresh = time.monotonic()

    while True:
        # Sleep first so startup isn't itself a poll trigger.
        time.sleep(settings.poll_interval_seconds)

        now = time.monotonic()
        if now - last_refresh >= settings.discovery_refresh_seconds:
            sources = _refresh_sources(settings, sources)
            last_refresh = now

        # Not wrapped in list(): nothing inserts/deletes from `sources` during
        # this loop (poll_once mutates rain_state in place; the dict itself is
        # only ever reassigned wholesale, by _refresh_sources above), so there
        # is no mutate-while-iterating hazard to guard against here.
        for source_id, source in sources.items():
            try:
                poll_once(settings, source_id, source.config, source.rain_state)
            except Exception:
                logger.exception("Unexpected error in poll cycle for source %s; continuing", source_id)


if __name__ == "__main__":
    main()
