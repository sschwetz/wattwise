from __future__ import annotations

import datetime
from dataclasses import dataclass

from core_api.local_rates import find_overlap, minutes_of_day, windows_overlap

UTC = datetime.UTC
T = datetime.time


@dataclass
class W:
    effective_from: datetime.datetime = datetime.datetime(2026, 1, 1, tzinfo=UTC)
    effective_to: datetime.datetime | None = None
    months: tuple[int, ...] = tuple(range(1, 13))
    days_of_week: tuple[int, ...] = tuple(range(7))
    start_time: datetime.time = T(0, 0)
    end_time: datetime.time = T(0, 0)


def test_equal_times_mean_all_day():
    assert minutes_of_day(T(0, 0), T(0, 0)) == frozenset(range(1440))


def test_end_time_is_excluded():
    assert minutes_of_day(T(7, 0), T(7, 2)) == frozenset({420, 421})


def test_end_before_start_wraps_past_midnight():
    wrapped = minutes_of_day(T(23, 58), T(0, 2))
    assert wrapped == frozenset({1438, 1439, 0, 1})


def test_windows_touching_at_a_boundary_do_not_overlap():
    night = W(start_time=T(0, 0), end_time=T(7, 0))
    day = W(start_time=T(7, 0), end_time=T(22, 0))
    assert not windows_overlap(night, day)


def test_wrapping_window_overlaps_the_early_morning_window():
    overnight = W(start_time=T(22, 0), end_time=T(7, 0))
    early = W(start_time=T(6, 0), end_time=T(8, 0))
    assert windows_overlap(overnight, early)


def test_disjoint_months_do_not_overlap():
    summer = W(months=(12, 1, 2))
    winter = W(months=(6, 7, 8))
    assert not windows_overlap(summer, winter)


def test_disjoint_days_do_not_overlap():
    weekday = W(days_of_week=(0, 1, 2, 3, 4))
    weekend = W(days_of_week=(5, 6))
    assert not windows_overlap(weekday, weekend)


def test_a_closed_window_does_not_overlap_one_that_starts_when_it_ends():
    old = W(effective_to=datetime.datetime(2026, 7, 1, tzinfo=UTC))
    new = W(effective_from=datetime.datetime(2026, 7, 1, tzinfo=UTC))
    assert not windows_overlap(old, new)


def test_an_open_ended_window_overlaps_a_later_one():
    current = W()
    later = W(effective_from=datetime.datetime(2027, 1, 1, tzinfo=UTC))
    assert windows_overlap(current, later)


def test_find_overlap_returns_the_clashing_window():
    flat = W()
    peak = W(start_time=T(7, 0), end_time=T(9, 0))
    assert find_overlap(peak, [flat]) is flat


def test_find_overlap_returns_none_when_clear():
    night = W(start_time=T(0, 0), end_time=T(7, 0))
    day = W(start_time=T(7, 0), end_time=T(22, 0))
    assert find_overlap(day, [night]) is None
