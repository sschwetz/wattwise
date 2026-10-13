from __future__ import annotations

import datetime

from amber_pricing_collector.scheduling import next_aligned_tick

UTC = datetime.timezone.utc


def dt(h: int, m: int, s: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 9, 30, h, m, s, tzinfo=UTC)


def test_well_before_next_boundary():
    assert next_aligned_tick(dt(10, 2, 0)) == dt(10, 5, 15)


def test_within_buffer_window_of_current_boundary():
    # Already at the :05 minute, but the 15s buffer hasn't elapsed yet --
    # this cycle's tick is still pending, not skipped.
    assert next_aligned_tick(dt(10, 5, 5)) == dt(10, 5, 15)


def test_just_after_buffer_elapsed_skips_to_next_boundary():
    assert next_aligned_tick(dt(10, 5, 16)) == dt(10, 10, 15)


def test_late_in_the_interval_rounds_to_next_boundary():
    assert next_aligned_tick(dt(10, 4, 59)) == dt(10, 5, 15)


def test_handles_hour_rollover():
    assert next_aligned_tick(dt(10, 57, 0)) == dt(11, 0, 15)


def test_result_is_always_strictly_after_now():
    now = dt(10, 5, 15)  # exactly on a would-be tick
    result = next_aligned_tick(now)
    assert result > now
    assert result == dt(10, 10, 15)
