from __future__ import annotations

import datetime
import decimal

from core_api.export_allowance import (
    CumulativeExport,
    charge_cost_deltas_c,
    charged_kwh,
    charged_kwh_steps,
    days_elapsed,
    is_charging_next_kwh,
)
from tests.conftest import SYDNEY, sydney_local

D = decimal.Decimal
ONE_MINUTE = datetime.timedelta(minutes=1)


def test_days_elapsed_counts_the_current_day():
    start = sydney_local(2026, 1, 1)
    assert days_elapsed(start, sydney_local(2026, 1, 1, 12), SYDNEY) == 1
    assert days_elapsed(start, sydney_local(2026, 1, 3, 0, 0), SYDNEY) == 3


def test_days_elapsed_counts_local_dates_across_a_dst_change():
    # Sydney clocks go forward on 2026-10-04, so that day has 23 hours.
    start = sydney_local(2026, 10, 1)
    assert days_elapsed(start, sydney_local(2026, 10, 5, 0, 0), SYDNEY) == 5
    assert days_elapsed(start, sydney_local(2026, 10, 4, 23, 59), SYDNEY) == 4


def test_charged_kwh_never_goes_below_zero():
    assert charged_kwh(D("3"), D("1"), 2) == D("1")
    assert charged_kwh(D("1"), D("1"), 5) == D("0")


def test_steps_overage_is_cancelled_by_later_days():
    start = sydney_local(2026, 1, 1)
    end = sydney_local(2026, 2, 1)
    noon = sydney_local(2026, 1, 1, 12, 0)
    exports = {noon: D("1"), noon + ONE_MINUTE: D("1"), noon + 2 * ONE_MINUTE: D("1")}

    steps = charged_kwh_steps(D("1"), start, end, exports, SYDNEY)

    assert steps == [
        (noon + ONE_MINUTE, D("1")),
        (noon + 2 * ONE_MINUTE, D("2")),
        (sydney_local(2026, 1, 2), D("1")),
        (sydney_local(2026, 1, 3), D("0")),
    ]


def test_steps_ignore_exports_outside_the_period():
    start = sydney_local(2026, 2, 1)
    end = sydney_local(2026, 3, 1)
    exports = {sydney_local(2026, 1, 31, 12, 0): D("50")}

    assert charged_kwh_steps(D("1"), start, end, exports, SYDNEY) == []


def test_cost_deltas_turn_a_charge_into_positive_cost_and_a_refund_into_negative():
    t1 = sydney_local(2026, 1, 1, 12, 0)
    t2 = sydney_local(2026, 1, 2)
    steps = [(t1, D("2")), (t2, D("1"))]

    assert charge_cost_deltas_c(D("-10"), steps) == [(t1, D("20")), (t2, D("-10"))]


def test_cumulative_export_counts_only_minutes_before_the_instant():
    t0 = sydney_local(2026, 1, 1, 12, 0)
    cumulative = CumulativeExport({t0: D("1"), t0 + ONE_MINUTE: D("2")})

    assert cumulative.before(t0) == D("0")
    assert cumulative.before(t0 + datetime.timedelta(seconds=1)) == D("1")
    assert cumulative.before(t0 + datetime.timedelta(days=5)) == D("3")


def test_is_charging_next_kwh_flips_when_export_reaches_the_allowance():
    start = sydney_local(2026, 1, 1)
    at = sydney_local(2026, 1, 2, 12)  # day 2, so 2 kWh of allowance with z = 1
    assert is_charging_next_kwh(D("1"), start, at, D("1.9"), SYDNEY) is False
    assert is_charging_next_kwh(D("1"), start, at, D("2"), SYDNEY) is True
