from __future__ import annotations

import datetime
import zoneinfo
from dataclasses import dataclass

from local_pricing_collector.models import Coverage
from local_pricing_collector.rates import CHANNELS, RateWindow
from local_pricing_collector.triggers import Triggers

UTC = datetime.timezone.utc
ONE_DAY = datetime.timedelta(days=1)
CHUNK_DAYS = 7


@dataclass(frozen=True)
class PlannedRange:
    channel: str
    start: datetime.datetime
    end: datetime.datetime


def local_midnight(day: datetime.date, tz: zoneinfo.ZoneInfo) -> datetime.datetime:
    """The instant a local calendar day begins, as aware UTC."""
    return datetime.datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(UTC)


def _whole_minute(value: datetime.datetime) -> datetime.datetime:
    return value.replace(second=0, microsecond=0)


def _day_edges(now: datetime.datetime, tz: zoneinfo.ZoneInfo) -> tuple[datetime.datetime, datetime.datetime]:
    """The end of the current local day and the end of the next one."""
    today = now.astimezone(tz).date()
    return local_midnight(today + ONE_DAY, tz), local_midnight(today + 2 * ONE_DAY, tz)


def _history_start(
    windows: list[RateWindow], floor: datetime.datetime | None, now: datetime.datetime
) -> datetime.datetime:
    """Where this channel's history begins: the site's first data, or now when
    the site has none, and never before the channel's first window."""
    earliest = min(w.effective_from for w in windows)
    return max(_whole_minute(floor) if floor is not None else _whole_minute(now), earliest)


def _end_limit(windows: list[RateWindow]) -> datetime.datetime | None:
    """The latest end among the windows, or None when any window is open ended."""
    if any(w.effective_to is None for w in windows):
        return None
    return max(w.effective_to for w in windows)


def _wanted_ranges(
    coverage: Coverage | None,
    history_start: datetime.datetime,
    today_end: datetime.datetime,
    tomorrow_end: datetime.datetime,
    triggers: Triggers,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    ranges = []
    if triggers.catch_up:
        if coverage is None:
            ranges.append((history_start, tomorrow_end))
        else:
            if coverage.first > history_start:
                ranges.append((history_start, coverage.first))
            if coverage.last < today_end:
                ranges.append((coverage.last, tomorrow_end))
    if triggers.scheduled:
        ranges.append((today_end, tomorrow_end))
    return ranges


def _clamp_and_merge(
    ranges: list[tuple[datetime.datetime, datetime.datetime]],
    low: datetime.datetime,
    high: datetime.datetime | None,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    clamped = sorted((max(start, low), end if high is None else min(end, high)) for start, end in ranges)
    merged: list[tuple[datetime.datetime, datetime.datetime]] = []
    for start, end in clamped:
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def plan_ranges(
    windows: list[RateWindow],
    coverage: dict[str, Coverage],
    floor: datetime.datetime | None,
    tz: zoneinfo.ZoneInfo,
    now: datetime.datetime,
    triggers: Triggers,
) -> list[PlannedRange]:
    """What to price this tick, per channel. Ranges are limited to when the
    channel's windows were in effect, so a source whose windows have all ended
    does not rebuild empty history."""
    today_end, tomorrow_end = _day_edges(now, tz)
    planned = []
    for channel in CHANNELS:
        channel_windows = [w for w in windows if w.channel == channel]
        if not channel_windows:
            continue
        wanted = _wanted_ranges(
            coverage.get(channel), _history_start(channel_windows, floor, now), today_end, tomorrow_end, triggers
        )
        earliest = min(w.effective_from for w in channel_windows)
        for start, end in _clamp_and_merge(wanted, earliest, _end_limit(channel_windows)):
            planned.append(PlannedRange(channel, start, end))
    return planned


def chunk_range(planned: PlannedRange, days: int = CHUNK_DAYS) -> list[PlannedRange]:
    """Cuts a range into pieces of at most `days` days, one request each."""
    step = datetime.timedelta(days=days)
    chunks = []
    start = planned.start
    while start < planned.end:
        end = min(start + step, planned.end)
        chunks.append(PlannedRange(planned.channel, start, end))
        start = end
    return chunks
