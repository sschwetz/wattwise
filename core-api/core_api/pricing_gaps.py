from __future__ import annotations

import datetime
from collections.abc import Iterable

from core_api.time_utils import as_utc

# Core API stores Amber's history as 5-minute intervals (the collector asks for
# resolution=5). A day counts as covered when it holds at least this share of
# the 5-minute intervals that have elapsed in it. Amber leaves the odd interval
# out, and treating that as a gap would refetch the same day every pass for good.
MIN_COVERAGE_RATIO = 0.95
INTERVAL_SECONDS = 5 * 60


def covered_days(
    interval_starts: Iterable[datetime.datetime],
    since: datetime.datetime,
    now: datetime.datetime,
) -> set[datetime.date]:
    """UTC days from `since` to `now` that hold most of their expected 5-minute
    intervals. Counting intervals, not the time they span, matters: forecast
    rows are 30 minutes wide and would span a whole day with 48 rows. The
    first day counts from `since` and today up to `now`, so neither needs a
    full 24 hours of data."""
    since, now = as_utc(since), as_utc(now)
    counts: dict[datetime.date, int] = {}
    for start in interval_starts:
        start = as_utc(start)
        if since <= start < now:
            counts[start.date()] = counts.get(start.date(), 0) + 1

    midnight = datetime.time()
    utc = datetime.timezone.utc
    days = set()
    day = since.date()
    while day <= now.date():
        day_start = max(datetime.datetime.combine(day, midnight, tzinfo=utc), since)
        day_end = min(datetime.datetime.combine(day + datetime.timedelta(days=1), midnight, tzinfo=utc), now)
        expected = (day_end - day_start).total_seconds() / INTERVAL_SECONDS
        if counts.get(day, 0) >= MIN_COVERAGE_RATIO * expected:
            days.add(day)
        day += datetime.timedelta(days=1)
    return days


def missing_days_in_range(
    since_date: datetime.date,
    until_date: datetime.date,
    covered_days: set[datetime.date],
    marked_days: set[datetime.date],
) -> list[datetime.date]:
    """Every calendar day in [since_date, until_date] that has neither real
    PriceInterval coverage nor a confirmed-unavailable PriceGapMarker."""
    missing = []
    day = since_date
    while day <= until_date:
        if day not in covered_days and day not in marked_days:
            missing.append(day)
        day += datetime.timedelta(days=1)
    return missing


def coalesce_missing_days(missing_days: list[datetime.date]) -> list[tuple[datetime.date, datetime.date]]:
    """Group a sorted list of missing calendar days into contiguous
    (start, end) runs, so the caller can turn each run directly into a
    chunked historical-API request range."""
    if not missing_days:
        return []
    runs = []
    run_start = missing_days[0]
    run_end = missing_days[0]
    for day in missing_days[1:]:
        if day == run_end + datetime.timedelta(days=1):
            run_end = day
        else:
            runs.append((run_start, run_end))
            run_start = day
            run_end = day
    runs.append((run_start, run_end))
    return runs
