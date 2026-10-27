from __future__ import annotations

import datetime

from core_api.pricing_gaps import coalesce_missing_days, covered_days, missing_days_in_range


def d(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def test_missing_days_excludes_covered_and_marked_days():
    covered = {d("2025-01-02")}
    marked = {d("2025-01-03")}
    missing = missing_days_in_range(d("2025-01-01"), d("2025-01-04"), covered, marked)
    assert missing == [d("2025-01-01"), d("2025-01-04")]


def test_missing_days_returns_empty_when_fully_covered():
    covered = {d("2025-01-01"), d("2025-01-02")}
    missing = missing_days_in_range(d("2025-01-01"), d("2025-01-02"), covered, set())
    assert missing == []


def test_coalesce_groups_consecutive_days_into_one_run():
    days = [d("2025-01-01"), d("2025-01-02"), d("2025-01-03")]
    assert coalesce_missing_days(days) == [(d("2025-01-01"), d("2025-01-03"))]


def test_coalesce_splits_non_consecutive_days_into_separate_runs():
    days = [d("2025-01-01"), d("2025-01-05")]
    assert coalesce_missing_days(days) == [(d("2025-01-01"), d("2025-01-01")), (d("2025-01-05"), d("2025-01-05"))]


def test_coalesce_of_empty_list_is_empty():
    assert coalesce_missing_days([]) == []


UTC = datetime.UTC


def five_min_starts(day: str, count: int, start_minute: int = 0) -> list[datetime.datetime]:
    base = datetime.datetime.fromisoformat(day).replace(tzinfo=UTC) + datetime.timedelta(minutes=start_minute)
    return [base + datetime.timedelta(minutes=5 * i) for i in range(count)]


def test_covered_days_needs_most_of_a_days_five_minute_intervals():
    since = datetime.datetime(2025, 1, 1, tzinfo=UTC)
    now = datetime.datetime(2025, 1, 3, 12, 0, tzinfo=UTC)
    starts = five_min_starts("2025-01-01", 288) + five_min_starts("2025-01-02", 100)
    assert covered_days(starts, since, now) == {d("2025-01-01")}


def test_covered_days_tolerates_a_few_missing_intervals():
    since = datetime.datetime(2025, 1, 1, tzinfo=UTC)
    now = datetime.datetime(2025, 1, 2, 12, 0, tzinfo=UTC)
    assert covered_days(five_min_starts("2025-01-01", 283), since, now) == {d("2025-01-01")}


def test_covered_days_does_not_count_half_hour_rows_as_a_full_day():
    since = datetime.datetime(2025, 1, 1, tzinfo=UTC)
    now = datetime.datetime(2025, 1, 2, 12, 0, tzinfo=UTC)
    half_hours = [since + datetime.timedelta(minutes=30 * i) for i in range(48)]
    assert covered_days(half_hours, since, now) == set()


def test_covered_days_judges_the_first_and_current_day_by_their_elapsed_part():
    since = datetime.datetime(2025, 1, 1, 18, 0, tzinfo=UTC)  # 6 hours of day 1
    now = datetime.datetime(2025, 1, 2, 1, 0, tzinfo=UTC)  # 1 hour of day 2
    starts = five_min_starts("2025-01-01", 72, start_minute=18 * 60) + five_min_starts("2025-01-02", 12)
    assert covered_days(starts, since, now) == {d("2025-01-01"), d("2025-01-02")}
