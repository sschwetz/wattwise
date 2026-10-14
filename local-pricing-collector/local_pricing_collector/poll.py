from __future__ import annotations

import datetime
import logging
import multiprocessing
import multiprocessing.process
import sys
import time
import zoneinfo
from collections.abc import Callable
from dataclasses import dataclass, field

from local_pricing_collector.config import Settings
from local_pricing_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_price_coverage,
    fetch_site_data_floor,
    push_price_intervals,
)
from local_pricing_collector.models import Coverage, LocalSource
from local_pricing_collector.plan import PlannedRange, chunk_range, plan_ranges
from local_pricing_collector.rates import RateWindow, channel_intervals, parse_windows
from local_pricing_collector.scheduling import next_aligned_tick
from local_pricing_collector.triggers import SourceState, Triggers, after_success, due

logger = logging.getLogger("local_pricing_collector")

PUSH_TIMEOUT_SECONDS = 120
MAX_RUN = datetime.timedelta(hours=2)
CONFIG_ERRORS = (KeyError, TypeError, zoneinfo.ZoneInfoNotFoundError, ValueError)


def site_timezone(config: dict) -> zoneinfo.ZoneInfo:
    return zoneinfo.ZoneInfo(config["timezone"])


def _coverage_and_floor(
    settings: Settings, source_id: int, triggers: Triggers
) -> tuple[dict[str, Coverage], datetime.datetime | None]:
    """Only a catch-up needs to know what is already priced. The scheduled
    push is the next local day whatever is stored."""
    if not triggers.catch_up:
        return {}, None
    url, key = settings.core_api_url, settings.collector_key
    return fetch_price_coverage(url, source_id, key), fetch_site_data_floor(url, source_id, key)


def _push_range(
    settings: Settings, source_id: int, windows: list[RateWindow], tz: zoneinfo.ZoneInfo, planned: PlannedRange
) -> None:
    chunks = chunk_range(planned)
    for chunk in reversed(chunks) if planned.backward else chunks:
        intervals = channel_intervals(windows, chunk.channel, tz, chunk.start, chunk.end)
        if intervals:
            push_price_intervals(
                settings.core_api_url, source_id, settings.collector_key, intervals, timeout=PUSH_TIMEOUT_SECONDS
            )


def _sync(settings: Settings, source: LocalSource, triggers: Triggers, now: datetime.datetime) -> bool:
    try:
        tz = site_timezone(source.config)
        windows = parse_windows(source.config["rate_windows"])
    except CONFIG_ERRORS:
        logger.exception("Failed to read config for pricing source %s this cycle", source.source_id)
        return False
    if not windows:
        return True
    try:
        coverage, floor = _coverage_and_floor(settings, source.source_id, triggers)
        for planned in plan_ranges(windows, coverage, floor, tz, now, triggers):
            _push_range(settings, source.source_id, windows, tz, planned)
    except CoreApiError:
        logger.exception("Failed to price source %s this cycle; will retry", source.source_id)
        return False
    return True


def sync_one_source(settings: Settings, source: LocalSource, triggers: Triggers, now: datetime.datetime) -> bool:
    """Prices what the triggers call for. Never raises, so one source's
    failure cannot stop the others. Returns True when everything planned was
    pushed (or nothing was needed) and False when the parent should retry."""
    try:
        return _sync(settings, source, triggers, now)
    except Exception:
        logger.exception("Unexpected error syncing pricing source %s this cycle", source.source_id)
        return False


def sync_child(settings: Settings, source: LocalSource, triggers: Triggers, now: datetime.datetime) -> None:
    """Process entry point. The exit code tells the parent whether to record
    the work as done."""
    sys.exit(0 if sync_one_source(settings, source, triggers, now) else 1)


@dataclass
class RunningJob:
    process: multiprocessing.process.BaseProcess
    triggers: Triggers
    tz: zoneinfo.ZoneInfo
    started: datetime.datetime


@dataclass
class TickState:
    """What the parent loop remembers between ticks. In memory only: after a
    restart every source starts with no record and does a catch-up."""

    states: dict[int, SourceState] = field(default_factory=dict)
    running: dict[int, RunningJob] = field(default_factory=dict)


def _finish(tick: TickState, source_id: int, job: RunningJob) -> None:
    job.process.join()
    if job.process.exitcode == 0:
        tick.states[source_id] = after_success(tick.states.get(source_id), job.triggers, job.started, job.tz)
    else:
        logger.warning("Pricing source %s did not finish its work (exit %s); retrying", source_id, job.process.exitcode)


def _reap(tick: TickState, now: datetime.datetime) -> None:
    for source_id, job in list(tick.running.items()):
        if job.process.is_alive():
            if now - job.started <= MAX_RUN:
                continue
            logger.error("Process for local pricing source %s ran too long; terminating", source_id)
            job.process.terminate()
            job.process.join(timeout=10)
            if job.process.is_alive():
                job.process.kill()
                job.process.join()
        else:
            _finish(tick, source_id, job)
        del tick.running[source_id]


def _start_if_due(
    settings: Settings,
    tick: TickState,
    source: LocalSource,
    now: datetime.datetime,
    process_factory: Callable[..., multiprocessing.process.BaseProcess],
) -> None:
    if source.source_id in tick.running:
        return
    try:
        tz = site_timezone(source.config)
    except CONFIG_ERRORS:
        logger.exception("Unusable time zone for pricing source %s; skipping it this tick", source.source_id)
        return
    triggers = due(tick.states.get(source.source_id), now, tz)
    if not triggers.any:
        return
    process = process_factory(target=sync_child, args=(settings, source, triggers, now))
    process.start()
    tick.running[source.source_id] = RunningJob(process, triggers, tz, now)


def run_tick(
    settings: Settings,
    tick: TickState,
    now: datetime.datetime,
    process_factory: Callable[..., multiprocessing.process.BaseProcess] = multiprocessing.Process,
) -> None:
    """One pass of the loop. Collects finished children, then starts a child
    for each source whose work is due. It never waits for a child, so a long
    history fill does not delay the next minute's checks."""
    _reap(tick, now)
    try:
        sources = fetch_collector_sources(settings.core_api_url, settings.collector_key)
    except CoreApiError:
        logger.exception("Failed to refresh the local pricing source list this tick; skipping")
        return
    live = {source.source_id for source in sources}
    for source_id in [sid for sid in tick.states if sid not in live]:
        del tick.states[source_id]
    for source in sources:
        _start_if_due(settings, tick, source, now, process_factory)


def run_forever(settings: Settings) -> None:
    """Wakes every minute. This process stays single-threaded so the forks
    are safe."""
    tick = TickState()
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            time.sleep(max((next_aligned_tick(now, interval_minutes=1) - now).total_seconds(), 0))
            run_tick(settings, tick, datetime.datetime.now(datetime.timezone.utc))
        except Exception:
            logger.exception("Unexpected error in the local pricing loop; continuing")
