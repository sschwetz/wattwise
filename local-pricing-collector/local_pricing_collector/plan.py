from __future__ import annotations

import datetime
import zoneinfo
from dataclasses import dataclass

from local_pricing_collector.models import Coverage
from local_pricing_collector.rates import CHANNELS, RateWindow, ceil_minute
from local_pricing_collector.triggers import Triggers

UTC = datetime.UTC
ONE_DAY = datetime.timedelta(days=1)
CHUNK_DAYS = 7


@dataclass(frozen=True)
class PlannedRange:
    channel: str
    start: datetime.datetime
    end: datetime.datetime
    backward: bool = False


def local_midnight(day: datetime.date, tz: zoneinfo.ZoneInfo) -> datetime.datetime:
    """The instant a local calendar day begins, as aware UTC."""
    return datetime.datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(UTC)


def _whole_minute(value: datetime.datetime) -> datetime.datetime:
    return value.replace(second=0, microsecond=0)


def _day_edges(now: datetime.datetime, tz: zoneinfo.ZoneInfo) -> tuple[datetime.datetime, datetime.datetime]:
    """The end of the current local day and the end of the next one."""
    today = now.astimezone(tz).date()
    return local_midnight(today + ONE_DAY, tz), local_midnight(today + 2 * ONE_DAY, tz)


def _earliest(windows: list[RateWindow]) -> datetime.datetime:
    return ceil_minute(min(w.effective_from for w in windows))


def _history_start(
    windows: list[RateWindow], floor: datetime.datetime | None, now: datetime.datetime
) -> datetime.datetime:
    """Where this channel's history begins: the site's first data, or now when
    the site has none, and never before the channel's first window."""
    earliest = _earliest(windows)
    return max(_whole_minute(floor) if floor is not None else _whole_minute(now), earliest)


def _end_limit(windows: list[RateWindow]) -> datetime.datetime | None:
    """The latest end among the windows, or None when any window is open ended."""
    ends = [w.effective_to for w in windows]
    latest = [end for end in ends if end is not None]
    if len(latest) < len(ends):
        return None
    return ceil_minute(max(latest))


Wanted = tuple[datetime.datetime, datetime.datetime, bool]


def _wanted_ranges(
    coverage: Coverage | None,
    history_start: datetime.datetime,
    edges: tuple[datetime.datetime, datetime.datetime],
    triggers: Triggers,
    tz: zoneinfo.ZoneInfo,
) -> list[Wanted]:
    """(start, end, backward). Only the history range is backward: it is
    pushed newest chunk first so an interrupted fill leaves no hole. A
    reprice marker adds a forward range from the marker to the end of tomorrow."""
    today_end, tomorrow_end = edges
    ranges = []
    if triggers.catch_up:
        if coverage is None:
            ranges.append((history_start, tomorrow_end, False))
        else:
            if coverage.first > history_start:
                ranges.append((history_start, coverage.first, True))
            if coverage.last < today_end:
                ranges.append((coverage.last, tomorrow_end, False))
    if triggers.scheduled_for is not None:
        day = triggers.scheduled_for
        ranges.append((local_midnight(day, tz), local_midnight(day + ONE_DAY, tz), False))
    if triggers.reprice is not None:
        ranges.append((max(triggers.reprice.start, history_start), tomorrow_end, False))
    return ranges


def _clamp_and_merge(
    ranges: list[Wanted],
    low: datetime.datetime,
    high: datetime.datetime | None,
) -> list[Wanted]:
    """A merged range is backward only if every range merged into it was."""
    clamped = sorted((max(start, low), end if high is None else min(end, high), back) for start, end, back in ranges)
    merged: list[Wanted] = []
    for start, end, back in clamped:
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            last = merged[-1]
            merged[-1] = (last[0], max(last[1], end), last[2] and back)
        else:
            merged.append((start, end, back))
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
    edges = _day_edges(now, tz)
    planned = []
    for channel in CHANNELS:
        channel_windows = [w for w in windows if w.channel == channel]
        if not channel_windows:
            continue
        wanted = _wanted_ranges(
            coverage.get(channel), _history_start(channel_windows, floor, now), edges, triggers, tz
        )
        earliest = _earliest(channel_windows)
        for start, end, back in _clamp_and_merge(wanted, earliest, _end_limit(channel_windows)):
            planned.append(PlannedRange(channel, start, end, back))
    return planned


def chunk_range(planned: PlannedRange, days: int = CHUNK_DAYS) -> list[PlannedRange]:
    """Cuts a range into pieces of at most `days` days, one request each."""
    step = datetime.timedelta(days=days)
    chunks = []
    start = planned.start
    while start < planned.end:
        end = min(start + step, planned.end)
        chunks.append(PlannedRange(planned.channel, start, end, planned.backward))
        start = end
    return chunks
