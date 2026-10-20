from __future__ import annotations

import datetime
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.datetime_utils import naive_utc
from core_api.models.pricing import PriceChannel, PriceInterval

FallbackRange = tuple[datetime.datetime, datetime.datetime]


def fallback_ranges(
    db: Session,
    source_ids: Sequence[int],
    channel: PriceChannel,
    start: datetime.datetime,
    end: datetime.datetime,
    now: datetime.datetime,
) -> list[FallbackRange]:
    """Whole-minute [start, end) spans of forecast intervals that arrived
    without Amber's advancedPrice band, so their price fell back to perKwh.

    A forecast interval starts after `now`; settled and current intervals
    never carry a band and are not fallbacks. The start is floored to the
    minute because price readings are (see ingestion's _fan_out_price_readings).
    """
    if not source_ids:
        return []
    stmt = select(PriceInterval.interval_start, PriceInterval.interval_end).where(
        PriceInterval.pricing_source_id.in_(source_ids),
        PriceInterval.channel == channel,
        PriceInterval.interval_start > now,
        PriceInterval.interval_start <= end,
        PriceInterval.interval_end >= start,
        PriceInterval.price_low.is_(None),
    )
    return [
        (naive_utc(interval_start).replace(second=0, microsecond=0), naive_utc(interval_end))
        for interval_start, interval_end in db.execute(stmt)
    ]


def in_fallback(ts: datetime.datetime, ranges: Sequence[FallbackRange]) -> bool:
    ts = naive_utc(ts)
    return any(range_start <= ts < range_end for range_start, range_end in ranges)
