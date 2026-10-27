from __future__ import annotations

import datetime
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from weatherlink_local_collector.config import Settings, get_settings
from weatherlink_local_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    push_live_sample,
    push_weather_readings,
)
from weatherlink_local_collector.mapping import RainState, map_barometer_readings, map_iss_readings, map_live_values
from weatherlink_local_collector.weatherlink_client import fetch_current_conditions

logger = logging.getLogger("weatherlink_local_collector")

# Readings whose push failed, per source, sent again with the next cycle's.
# Core API upserts on (source, ts, metric), so a batch that was in fact stored
# after the timeout is harmless to send twice. rain_mm is never kept: a failed
# push rolls the rain baseline back, so the next cycle's rain_mm already covers
# the missed rain. Held in memory only, and capped; the oldest are dropped first.
MAX_PENDING_READINGS = 5000
_pending_readings: dict[int, list[dict]] = {}
# Sources whose last push got a 403 (site_disabled, source_disabled or
# source_expired). Core API will refuse every resend, so those readings are
# dropped, and only the first drop of a streak is logged.
_dropping: set[int] = set()


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
    ts = datetime.datetime.now(datetime.UTC).replace(second=0, microsecond=0)
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

    batch = _pending_readings.pop(source_id, []) + readings
    if not _push_or_keep(settings, source_id, batch):
        rain_state.last_daily_mm = prev_rain_baseline


def _push_or_keep(settings: Settings, source_id: int, batch: list[dict]) -> bool:
    """Pushes the batch. Returns False only when the push failed and the
    batch (less rain_mm) is kept for the next cycle, so the caller rolls the
    rain baseline back. A 403 drops the batch with its rain and returns True."""
    try:
        push_weather_readings(settings.core_api_url, source_id, settings.collector_key, batch)
    except Exception as exc:
        if isinstance(exc, CoreApiError) and exc.status_code == 403:
            if source_id not in _dropping:
                logger.warning("Core API refused readings for source %s (%s); dropping them", source_id, exc)
            _dropping.add(source_id)
            return True
        logger.exception(
            "Failed to push %s readings to Core API this cycle for source %s; will resend next cycle",
            len(batch),
            source_id,
        )
        kept = [r for r in batch if r["metric"] != "rain_mm"]
        _pending_readings[source_id] = kept[-MAX_PENDING_READINGS:]
        return False
    _dropping.discard(source_id)
    return True


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


# Sources whose last live poll failed. Only the first failure of a streak and
# the recovery are logged, so an unreachable station does not log every 10 seconds.
_live_failing: set[int] = set()


def live_poll_once(settings: Settings, source_id: int, config: dict) -> None:
    """One live poll for one source. Never raises, and never touches the
    pending queue or any rain state: live values are not stored here."""
    try:
        data = fetch_current_conditions(config["host"])
        values = map_live_values(data["data"]["conditions"])
        if values:
            push_live_sample(
                settings.core_api_url,
                source_id,
                settings.collector_key,
                datetime.datetime.now(datetime.UTC),
                values,
            )
    except Exception:
        if source_id not in _live_failing:
            _live_failing.add(source_id)
            logger.exception("Live poll failed for source %s", source_id)
        return
    if source_id in _live_failing:
        _live_failing.discard(source_id)
        logger.info("Live poll recovered for source %s", source_id)


def run_live_loop(settings: Settings, get_sources: Callable[[], dict[int, _Source]], stop: threading.Event) -> None:
    """Polls every known source each live_poll_seconds until `stop` is set."""
    while not stop.is_set():
        started = time.monotonic()
        for source_id, source in list(get_sources().items()):
            if stop.is_set():
                return
            live_poll_once(settings, source_id, source.config)
        stop.wait(max(0.0, settings.live_poll_seconds - (time.monotonic() - started)))


class _SourceHolder:
    """Shared reference to the current sources dict, which main() replaces
    wholesale on each discovery refresh. The live thread reads it through get()."""

    def __init__(self, sources: dict[int, _Source]) -> None:
        self.sources = sources

    def get(self) -> dict[int, _Source]:
        return self.sources


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
    holder = _SourceHolder(sources)
    threading.Thread(
        target=run_live_loop, args=(settings, holder.get, threading.Event()), name="live-loop", daemon=True
    ).start()

    while True:
        # Sleep first so startup isn't itself a poll trigger.
        time.sleep(settings.poll_interval_seconds)

        now = time.monotonic()
        if now - last_refresh >= settings.discovery_refresh_seconds:
            sources = _refresh_sources(settings, sources)
            holder.sources = sources
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
