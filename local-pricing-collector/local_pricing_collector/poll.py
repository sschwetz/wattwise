from __future__ import annotations

import datetime
import logging
import multiprocessing
import time
import zoneinfo

from local_pricing_collector.config import Settings
from local_pricing_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_source_config,
    push_price_intervals,
)
from local_pricing_collector.rates import build_intervals, parse_windows
from local_pricing_collector.scheduling import next_aligned_tick

logger = logging.getLogger("local_pricing_collector")

CHILD_JOIN_TIMEOUT_SECONDS = 120


def sync_one_source(settings: Settings, source_id: int, *, now: datetime.datetime | None = None) -> None:
    """One-shot: read this source's rate windows and push the next
    horizon_hours of prices. Never raises, so one source's failure cannot stop
    the others. There is no retry: the next tick runs it again."""
    try:
        config = fetch_source_config(settings.core_api_url, source_id, settings.collector_key)
    except CoreApiError:
        logger.exception("Failed to fetch config for pricing source %s this cycle", source_id)
        return
    try:
        tz = zoneinfo.ZoneInfo(config["timezone"])
        windows = parse_windows(config["rate_windows"])
    except (KeyError, zoneinfo.ZoneInfoNotFoundError, ValueError):
        logger.exception("Failed to read config for pricing source %s this cycle", source_id)
        return

    now = now or datetime.datetime.now(datetime.timezone.utc)
    intervals = build_intervals(windows, tz, now, settings.horizon_hours)
    if not intervals:
        return
    try:
        push_price_intervals(settings.core_api_url, source_id, settings.collector_key, intervals)
    except CoreApiError:
        logger.exception("Failed to push price intervals for pricing source %s this cycle", source_id)


def _run_tick(settings: Settings) -> None:
    try:
        source_ids = fetch_collector_sources(settings.core_api_url, settings.collector_key)
    except CoreApiError:
        logger.exception("Failed to refresh the local pricing source list this tick; skipping")
        return
    children = [multiprocessing.Process(target=sync_one_source, args=(settings, sid)) for sid in source_ids]
    for child in children:
        child.start()
    for child in children:
        child.join(timeout=CHILD_JOIN_TIMEOUT_SECONDS)
        if child.is_alive():
            logger.error("Local pricing source process did not finish in time; terminating")
            child.terminate()
            child.join()


def run_forever(settings: Settings) -> None:
    """Every 5 minutes, fork one process per local source. This process stays
    single-threaded so the forks are safe."""
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            time.sleep(max((next_aligned_tick(now) - now).total_seconds(), 0))
            _run_tick(settings)
        except Exception:
            logger.exception("Unexpected error in the local pricing loop; continuing")
