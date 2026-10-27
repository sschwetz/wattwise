from __future__ import annotations

import datetime
import logging
import multiprocessing
import time
from collections.abc import Callable

from amber_pricing_collector.amber_client import fetch_current_prices
from amber_pricing_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_source_config,
    push_price_intervals,
)
from amber_pricing_collector.mapping import map_price_intervals
from amber_pricing_collector.scheduling import next_aligned_tick

logger = logging.getLogger("amber_pricing_collector")

CHILD_JOIN_TIMEOUT_SECONDS = 60

# Amber's "current prices" endpoint only forecasts `next` intervals ahead of
# now, and the webapp's today-pricing chart wants forecast coverage through the
# rest of the calendar day no matter what time of day this poll happens to run.
# At resolution=5 Amber returns 13 five-minute intervals followed by 30-minute
# intervals out to about 24 hours (~59 per channel), so 300 is more than it
# serves and acts as "everything available". Well under Amber's combined
# previous+next cap of 2048.
FORECAST_INTERVALS = 300

# 3 x 5-minute intervals = 15 minutes of trailing coverage. Amber sometimes
# revises an interval's price after it closes (estimate -> settled actual);
# poll_one_site's own previous=1 at a 5-minute cadence only ever observes
# each interval once, right as it closes, and never looks at it again.
# poll_one_site_recent re-sends this same trailing window every minute so a
# revision lands in the DB within one minute of Amber publishing it, for as
# long as 15 minutes after the interval closes.
RECENT_PREVIOUS_INTERVALS = 3


def _poll_and_push(core_api_url: str, source_id: int, collector_key: str, *, previous: int, next_count: int) -> None:
    """Shared body of poll_one_site/poll_one_site_recent: fetch this site's
    config, poll Amber, map, push, exit. No internal retry -- the next tick
    spawns a fresh child regardless, so retry happens via the scheduler
    rather than an indefinite-retry loop."""
    try:
        config = fetch_source_config(core_api_url, source_id, collector_key)
        amber_site_id = config["amber_site_id"]
        amber_api_key = config["amber_api_key"]
    except Exception:
        logger.exception("Failed to fetch config for pricing source %s this cycle", source_id)
        return

    try:
        raw_intervals = fetch_current_prices(amber_site_id, amber_api_key, previous=previous, next_count=next_count)
    except Exception:
        logger.exception("Failed to fetch current prices from Amber for source %s this cycle", source_id)
        return

    mapped = map_price_intervals(raw_intervals)
    if not mapped:
        return

    try:
        push_price_intervals(core_api_url, source_id, collector_key, mapped)
    except CoreApiError:
        logger.exception("Failed to push price intervals to Core API for source %s this cycle", source_id)


def poll_one_site(core_api_url: str, source_id: int, collector_key: str) -> None:
    """One-shot: the wide poll -- one just-closed interval plus the rest of
    the day's forecast. Run every 5 minutes by run_live_poll_forever."""
    _poll_and_push(core_api_url, source_id, collector_key, previous=1, next_count=FORECAST_INTERVALS)


def poll_one_site_recent(core_api_url: str, source_id: int, collector_key: str) -> None:
    """One-shot: the narrow poll -- just the last RECENT_PREVIOUS_INTERVALS
    closed intervals, no forecast. Run every minute by run_recent_poll_forever
    to catch Amber's post-close revisions without re-pushing the whole day's
    forecast on every tick."""
    _poll_and_push(core_api_url, source_id, collector_key, previous=RECENT_PREVIOUS_INTERVALS, next_count=0)


def _run_poll_loop_forever(
    core_api_url: str,
    collector_key: str,
    *,
    next_tick: Callable[[datetime.datetime], datetime.datetime],
    worker_target: Callable[[str, int, str], None],
) -> None:
    """Shared scheduling loop behind run_live_poll_forever and
    run_recent_poll_forever: wait for the next tick, fork one child per
    pricing source to run `worker_target`, and wait for them all before the
    next tick. `worker_target` must be a module-level, picklable function --
    see poll_one_site's own docstring -- so it survives both 'fork' and
    'spawn' multiprocessing start methods."""
    while True:
        try:
            now = datetime.datetime.now(datetime.UTC)
            tick = next_tick(now)
            time.sleep(max((tick - now).total_seconds(), 0))

            try:
                sources = fetch_collector_sources(core_api_url, collector_key)
            except CoreApiError:
                logger.exception("Failed to refresh pricing source list this tick; skipping")
                continue

            children = [
                multiprocessing.Process(target=worker_target, args=(core_api_url, s.source_id, collector_key))
                for s in sources
            ]
            for child in children:
                child.start()
            for child in children:
                child.join(timeout=CHILD_JOIN_TIMEOUT_SECONDS)
                if child.is_alive():
                    logger.error("Pricing source child process did not finish in time; terminating")
                    child.terminate()
                    child.join()
        except Exception:
            logger.exception("Unexpected error in poll scheduling loop; continuing")


def run_live_poll_forever(core_api_url: str, collector_key: str) -> None:
    _run_poll_loop_forever(core_api_url, collector_key, next_tick=next_aligned_tick, worker_target=poll_one_site)


def run_recent_poll_forever(core_api_url: str, collector_key: str) -> None:
    """Every minute, re-validate the last RECENT_PREVIOUS_INTERVALS closed
    intervals against Amber -- see RECENT_PREVIOUS_INTERVALS's own docstring
    for why. Not aligned to Amber's own 5-minute publish boundary (unlike
    run_live_poll_forever): this loop isn't waiting for a new interval to be
    published, just re-checking already-published ones, so a plain 2s buffer
    off the minute boundary is enough to dodge the second-rollover race."""
    _run_poll_loop_forever(
        core_api_url,
        collector_key,
        next_tick=lambda now: next_aligned_tick(now, interval_minutes=1, buffer_seconds=2),
        worker_target=poll_one_site_recent,
    )
