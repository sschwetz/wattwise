from __future__ import annotations

import datetime


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
