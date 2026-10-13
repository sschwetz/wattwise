from __future__ import annotations

import datetime
from collections.abc import Iterable, Sequence
from typing import Protocol, TypeVar

from core_api.time_utils import as_utc

DAY_MINUTES = 24 * 60
_FAR_FUTURE = datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)


class RateWindowLike(Protocol):
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None
    months: Sequence[int]
    days_of_week: Sequence[int]
    start_time: datetime.time
    end_time: datetime.time


W = TypeVar("W", bound=RateWindowLike)


def minutes_of_day(start: datetime.time, end: datetime.time) -> frozenset[int]:
    """The minutes of the local day a window covers. The start is included and
    the end is excluded. An end earlier than the start wraps past midnight, and
    equal times mean the whole day."""
    start_minute = start.hour * 60 + start.minute
    end_minute = end.hour * 60 + end.minute
    if start_minute == end_minute:
        return frozenset(range(DAY_MINUTES))
    if start_minute < end_minute:
        return frozenset(range(start_minute, end_minute))
    return frozenset(range(start_minute, DAY_MINUTES)) | frozenset(range(end_minute))


def _span(window: RateWindowLike) -> tuple[datetime.datetime, datetime.datetime]:
    end = as_utc(window.effective_to) if window.effective_to else _FAR_FUTURE
    return as_utc(window.effective_from), end


def windows_overlap(a: RateWindowLike, b: RateWindowLike) -> bool:
    """True when some minute could match both windows. The caller compares
    windows on the same channel only."""
    a_from, a_to = _span(a)
    b_from, b_to = _span(b)
    return (
        a_from < b_to
        and b_from < a_to
        and bool(set(a.months) & set(b.months))
        and bool(set(a.days_of_week) & set(b.days_of_week))
        and bool(minutes_of_day(a.start_time, a.end_time) & minutes_of_day(b.start_time, b.end_time))
    )


def find_overlap(candidate: RateWindowLike, existing: Iterable[W]) -> W | None:
    for window in existing:
        if windows_overlap(candidate, window):
            return window
    return None
