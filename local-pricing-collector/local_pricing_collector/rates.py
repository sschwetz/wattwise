from __future__ import annotations

import datetime
import decimal
import logging
import zoneinfo
from dataclasses import dataclass

logger = logging.getLogger("local_pricing_collector")

CHANNELS = ("buy", "sell", "controlled_load")
DAY_MINUTES = 24 * 60
ONE_MINUTE = datetime.timedelta(minutes=1)


@dataclass(frozen=True)
class RateWindow:
    channel: str
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None
    months: frozenset[int]
    days_of_week: frozenset[int]
    minutes: frozenset[int]
    rate_c_per_kwh: decimal.Decimal


def _minutes_of_day(start: datetime.time, end: datetime.time) -> frozenset[int]:
    """Start included, end excluded. An end before the start wraps past
    midnight, and equal times mean the whole day."""
    start_minute = start.hour * 60 + start.minute
    end_minute = end.hour * 60 + end.minute
    if start_minute == end_minute:
        return frozenset(range(DAY_MINUTES))
    if start_minute < end_minute:
        return frozenset(range(start_minute, end_minute))
    return frozenset(range(start_minute, DAY_MINUTES)) | frozenset(range(end_minute))


def _parse_window(raw: dict) -> RateWindow:
    effective_to = raw["effective_to"]
    return RateWindow(
        channel=raw["channel"],
        effective_from=datetime.datetime.fromisoformat(raw["effective_from"]),
        effective_to=datetime.datetime.fromisoformat(effective_to) if effective_to else None,
        months=frozenset(raw["months"]),
        days_of_week=frozenset(raw["days_of_week"]),
        minutes=_minutes_of_day(
            datetime.time.fromisoformat(raw["start_time"]), datetime.time.fromisoformat(raw["end_time"])
        ),
        rate_c_per_kwh=decimal.Decimal(raw["rate_c_per_kwh"]),
    )


def parse_windows(raw_windows: list[dict]) -> list[RateWindow]:
    """Skips a malformed window and logs it, so one bad row does not stop the
    rest of the tariff from being priced."""
    windows = []
    for raw in raw_windows:
        try:
            windows.append(_parse_window(raw))
        except (KeyError, ValueError, decimal.InvalidOperation):
            logger.exception("Skipping malformed rate window: %r", raw)
    return windows


def _applies(window: RateWindow, at_utc: datetime.datetime, local: datetime.datetime) -> bool:
    return (
        window.effective_from <= at_utc
        and (window.effective_to is None or at_utc < window.effective_to)
        and local.month in window.months
        and local.weekday() in window.days_of_week
        and local.hour * 60 + local.minute in window.minutes
    )


def price_at(
    windows: list[RateWindow], channel: str, at_utc: datetime.datetime, tz: zoneinfo.ZoneInfo
) -> decimal.Decimal | None:
    """The rate in c/kWh for this channel at this instant, or None when no
    window covers it. Core-api rejects overlapping windows, so at most one
    matches; the first one wins if that ever changes."""
    local = at_utc.astimezone(tz)
    for window in windows:
        if window.channel == channel and _applies(window, at_utc, local):
            return window.rate_c_per_kwh
    return None


def _signed(channel: str, rate: decimal.Decimal) -> decimal.Decimal:
    """Amber stores the sell price negative when the customer is paid, and the
    cost math adds it, so local sell rates are pushed with the same sign."""
    return -rate if channel == "sell" and rate else rate


def _interval(
    channel: str, start: datetime.datetime, end: datetime.datetime, rate: decimal.Decimal
) -> dict:
    return {
        "channel": channel,
        "interval_start": start.isoformat(),
        "interval_end": end.isoformat(),
        "price": str(_signed(channel, rate)),
    }


def _channel_intervals(
    windows: list[RateWindow],
    channel: str,
    tz: zoneinfo.ZoneInfo,
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[dict]:
    """Walks minute by minute and closes an interval at each top of the hour
    and wherever the rate changes. A minute with no rate closes the interval
    and opens nothing."""
    intervals = []
    run_start, run_rate = start, price_at(windows, channel, start, tz)
    minute = start + ONE_MINUTE
    while minute <= end:
        rate = price_at(windows, channel, minute, tz) if minute < end else None
        if minute == end or minute.minute == 0 or rate != run_rate:
            if run_rate is not None:
                intervals.append(_interval(channel, run_start, minute, run_rate))
            run_start, run_rate = minute, rate
        minute += ONE_MINUTE
    return intervals


def build_intervals(
    windows: list[RateWindow], tz: zoneinfo.ZoneInfo, now: datetime.datetime, horizon_hours: int
) -> list[dict]:
    """Price intervals from the top of the current UTC hour for horizon_hours.
    Starting on the hour keeps every interval's start time the same across
    pushes, so repeating a push overwrites rows instead of adding them."""
    start = now.astimezone(datetime.timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(hours=horizon_hours)
    intervals: list[dict] = []
    for channel in CHANNELS:
        if any(w.channel == channel for w in windows):
            intervals.extend(_channel_intervals(windows, channel, tz, start, end))
    return intervals
