from __future__ import annotations

import datetime

from local_pricing_collector.scheduling import next_aligned_tick

UTC = datetime.timezone.utc


def test_the_next_five_minute_boundary_plus_buffer():
    now = datetime.datetime(2026, 6, 15, 10, 31, 0, tzinfo=UTC)
    assert next_aligned_tick(now) == datetime.datetime(2026, 6, 15, 10, 35, 5, tzinfo=UTC)


def test_a_one_minute_interval_gives_the_next_minute_plus_buffer():
    now = datetime.datetime(2026, 6, 15, 10, 31, 20, tzinfo=UTC)
    assert next_aligned_tick(now, interval_minutes=1) == datetime.datetime(2026, 6, 15, 10, 32, 5, tzinfo=UTC)


def test_a_one_minute_interval_before_the_buffer_gives_this_minute():
    now = datetime.datetime(2026, 6, 15, 10, 31, 2, tzinfo=UTC)
    assert next_aligned_tick(now, interval_minutes=1) == datetime.datetime(2026, 6, 15, 10, 31, 5, tzinfo=UTC)


def test_the_tick_is_always_after_now():
    now = datetime.datetime(2026, 6, 15, 10, 35, 5, tzinfo=UTC)
    assert next_aligned_tick(now) > now
