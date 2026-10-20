from __future__ import annotations

import datetime
import decimal
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.forecast_fallback import _naive_utc
from core_api.models.pricing import PriceChannel, PriceInterval, SpikeStatus


@dataclass(frozen=True)
class IntervalBand:
    """What a price interval says about its own reliability: Amber's forecast
    band (None for settled intervals and fallback forecasts) and spike flag."""

    start: datetime.datetime
    end: datetime.datetime
    price_low: decimal.Decimal | None
    price_high: decimal.Decimal | None
    spike_status: SpikeStatus


def interval_bands(
    db: Session,
    source_ids: Sequence[int],
    channel: PriceChannel,
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[IntervalBand]:
    """Every interval overlapping [start, end). The start is floored to the
    minute because price readings are (see ingestion's _fan_out_price_readings),
    so a reading's ts always falls inside its own interval's [start, end)."""
    if not source_ids:
        return []
    stmt = select(
        PriceInterval.interval_start,
        PriceInterval.interval_end,
        PriceInterval.price_low,
        PriceInterval.price_high,
        PriceInterval.spike_status,
    ).where(
        PriceInterval.pricing_source_id.in_(source_ids),
        PriceInterval.channel == channel,
        PriceInterval.interval_start <= end,
        PriceInterval.interval_end >= start,
    )
    return [
        IntervalBand(
            start=_naive_utc(interval_start).replace(second=0, microsecond=0),
            end=_naive_utc(interval_end),
            price_low=price_low,
            price_high=price_high,
            spike_status=spike_status,
        )
        for interval_start, interval_end, price_low, price_high, spike_status in db.execute(stmt)
    ]


def band_at(ts: datetime.datetime, bands: Sequence[IntervalBand]) -> IntervalBand | None:
    ts = _naive_utc(ts)
    return next((band for band in bands if band.start <= ts < band.end), None)
