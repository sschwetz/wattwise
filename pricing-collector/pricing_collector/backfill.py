from __future__ import annotations

import datetime
import logging
import time

from pricing_collector.amber_client import fetch_historical_prices
from pricing_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_price_gaps,
    fetch_site_data_floor,
    fetch_source_config,
    push_price_intervals,
    report_gap_marker,
)
from pricing_collector.mapping import CHANNEL_MAP, map_price_intervals

logger = logging.getLogger("pricing_collector")

MAX_CHUNK_DAYS = 7
BACKFILL_INTERVAL_SECONDS = 12 * 60 * 60

# A 7-day chunk is up to 4,032 intervals, which the server fans out to one row per
# covered minute (~20,160 upserts) inside a single request. The client's 10s default
# is sized for a live-poll tick, not for that, and a timeout here does not converge:
# the server may well have committed, and the next cycle re-fetches the same chunk.
PUSH_TIMEOUT_SECONDS = 120


def _chunk_date_range(start_date: datetime.date, end_date: datetime.date) -> list[tuple[datetime.date, datetime.date]]:
    """Amber's historical endpoint caps startDate/endDate span at 7 days."""
    chunks = []
    chunk_start = start_date
    while chunk_start <= end_date:
        chunk_end = min(chunk_start + datetime.timedelta(days=MAX_CHUNK_DAYS - 1), end_date)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + datetime.timedelta(days=1)
    return chunks


def _days_in_range(start_date: datetime.date, end_date: datetime.date) -> list[datetime.date]:
    days = []
    day = start_date
    while day <= end_date:
        days.append(day)
        day += datetime.timedelta(days=1)
    return days


def _backfill_one_gap(
    core_api_url: str, source_id: int, collector_key: str, amber_site_id: str, amber_api_key: str, gap: dict
) -> None:
    channel = gap["channel"]
    start_date = datetime.date.fromisoformat(gap["start_date"])
    end_date = datetime.date.fromisoformat(gap["end_date"])

    for chunk_start, chunk_end in _chunk_date_range(start_date, end_date):
        raw_intervals = fetch_historical_prices(amber_site_id, amber_api_key, chunk_start, chunk_end)

        mapped = map_price_intervals(raw_intervals)
        if mapped:
            push_price_intervals(core_api_url, source_id, collector_key, mapped, timeout=PUSH_TIMEOUT_SECONDS)

        target_channel_present = any(CHANNEL_MAP.get(i.get("channelType")) == channel for i in raw_intervals)
        if not target_channel_present:
            for day in _days_in_range(chunk_start, chunk_end):
                report_gap_marker(core_api_url, source_id, collector_key, channel, day)


def backfill_one_site(core_api_url: str, source_id: int, collector_key: str) -> None:
    try:
        config = fetch_source_config(core_api_url, source_id, collector_key)
        amber_site_id = config["amber_site_id"]
        amber_api_key = config["amber_api_key"]
    except Exception:
        logger.exception("Failed to fetch config for pricing source %s during backfill", source_id)
        return

    try:
        floor_ts = fetch_site_data_floor(core_api_url, source_id, collector_key)
    except CoreApiError:
        logger.exception("Failed to fetch site-data floor for source %s during backfill", source_id)
        return
    if floor_ts is None:
        return  # brand-new site, nothing to backfill against yet

    try:
        gaps = fetch_price_gaps(core_api_url, source_id, collector_key, floor_ts)
    except CoreApiError:
        logger.exception("Failed to fetch price gaps for source %s during backfill", source_id)
        return

    for gap in gaps:
        try:
            _backfill_one_gap(core_api_url, source_id, collector_key, amber_site_id, amber_api_key, gap)
        except Exception:
            logger.exception("Failed to backfill gap %r for source %s; will retry next cycle", gap, source_id)


def run_backfill_forever(core_api_url: str, collector_key: str) -> None:
    while True:
        # Sleep first so startup isn't itself a backfill trigger.
        time.sleep(BACKFILL_INTERVAL_SECONDS)
        try:
            sources = fetch_collector_sources(core_api_url, collector_key)
        except Exception:
            logger.exception("Failed to refresh pricing source list for backfill pass; skipping this pass")
            sources = []
        for s in sources:
            try:
                backfill_one_site(core_api_url, s.source_id, collector_key)
            except Exception:
                logger.exception("Unexpected error backfilling pricing source %s; continuing", s.source_id)
