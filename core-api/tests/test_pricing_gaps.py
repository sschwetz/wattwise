from __future__ import annotations

import datetime

from core_api.pricing_gaps import coalesce_missing_days, missing_days_in_range


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
