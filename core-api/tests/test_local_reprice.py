from __future__ import annotations

import datetime
import decimal

from core_api.local_reprice import reprice_start

UTC = datetime.UTC


def dt(*args) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=UTC)


def snap(**overrides) -> dict:
    base = {
        "channel": "buy",
        "effective_from": dt(2026, 1, 1),
        "effective_to": None,
        "months": [1, 2, 3],
        "days_of_week": [0, 1],
        "start_time": datetime.time(0, 0),
        "end_time": datetime.time(7, 0),
        "rate_c_per_kwh": decimal.Decimal("30"),
    }
    return {**base, **overrides}


def test_a_rate_change_marks_from_the_window_start():
    assert reprice_start(snap(), snap(rate_c_per_kwh=decimal.Decimal("35"))) == dt(2026, 1, 1)


def test_a_rate_that_is_equal_but_written_differently_is_no_change():
    old = snap(rate_c_per_kwh=decimal.Decimal("30.0000"))
    assert reprice_start(old, snap(rate_c_per_kwh=decimal.Decimal("30"))) is None


def test_a_pattern_change_marks_from_the_window_start():
    assert reprice_start(snap(), snap(months=[1, 2])) == dt(2026, 1, 1)


def test_a_channel_change_marks_from_the_window_start():
    assert reprice_start(snap(), snap(channel="sell")) == dt(2026, 1, 1)


def test_moving_the_start_marks_the_earlier_start():
    assert reprice_start(snap(), snap(effective_from=dt(2026, 3, 1))) == dt(2026, 1, 1)
    assert reprice_start(snap(effective_from=dt(2026, 3, 1)), snap()) == dt(2026, 1, 1)


def test_ending_an_open_window_marks_the_new_end():
    assert reprice_start(snap(), snap(effective_to=dt(2026, 6, 15, 10, 0))) == dt(2026, 6, 15, 10, 0)


def test_reopening_an_ended_window_marks_the_old_end():
    assert reprice_start(snap(effective_to=dt(2026, 6, 15, 10, 0)), snap()) == dt(2026, 6, 15, 10, 0)


def test_moving_an_end_earlier_marks_the_new_end():
    old = snap(effective_to=dt(2026, 6, 20))
    assert reprice_start(old, snap(effective_to=dt(2026, 6, 10))) == dt(2026, 6, 10)


def test_moving_an_end_later_marks_the_old_end():
    old = snap(effective_to=dt(2026, 6, 10))
    assert reprice_start(old, snap(effective_to=dt(2026, 6, 20))) == dt(2026, 6, 10)


def test_no_change_marks_nothing():
    assert reprice_start(snap(), snap()) is None
    assert reprice_start(snap(effective_to=dt(2026, 6, 10)), snap(effective_to=dt(2026, 6, 10))) is None
