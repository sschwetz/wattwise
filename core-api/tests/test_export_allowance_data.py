from __future__ import annotations

import datetime
import decimal

from core_api.export_allowance_data import allowance_periods, allowance_window_spans, period_containing
from core_api.jobs.demand_config import total_export_adjustment_c_per_kwh
from core_api.models.billing import BillingCycle, ExportAdjustmentWindow, SiteTariff
from tests.conftest import sydney_local

TZ_NAME = "Australia/Sydney"


def _cycle(effective_from, anchor_day=1) -> BillingCycle:
    return BillingCycle(site_id=1, effective_from=effective_from, cycle_months=1, anchor_day=anchor_day)


def _window(allowance: str, adjustment: str = "-3") -> ExportAdjustmentWindow:
    return ExportAdjustmentWindow(
        site_tariff_id=1,
        label="w",
        months=list(range(1, 13)),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time(0, 0),
        end_time=datetime.time(23, 59),
        adjustment_c_per_kwh=decimal.Decimal(adjustment),
        daily_allowance_kwh=decimal.Decimal(allowance),
    )


def test_allowance_periods_returns_the_period_overlapping_the_range():
    cycles = [_cycle(sydney_local(2026, 1, 1))]

    periods = allowance_periods(cycles, TZ_NAME, sydney_local(2026, 1, 15), sydney_local(2026, 1, 16))

    assert [(p.start, p.end) for p in periods] == [(sydney_local(2026, 1, 1), sydney_local(2026, 2, 1))]


def test_allowance_periods_includes_the_partial_period_before_the_first_anchor_day():
    cycles = [_cycle(sydney_local(2026, 1, 10))]

    periods = allowance_periods(cycles, TZ_NAME, sydney_local(2026, 1, 15), sydney_local(2026, 1, 16))

    assert [(p.start, p.end) for p in periods] == [(sydney_local(2026, 1, 1), sydney_local(2026, 2, 1))]


def test_allowance_periods_splits_a_range_that_crosses_a_boundary():
    cycles = [_cycle(sydney_local(2026, 1, 1))]

    periods = allowance_periods(cycles, TZ_NAME, sydney_local(2026, 1, 31), sydney_local(2026, 2, 2))

    assert [p.start for p in periods] == [sydney_local(2026, 1, 1), sydney_local(2026, 2, 1)]


def test_allowance_periods_is_empty_without_a_billing_cycle():
    assert allowance_periods([], TZ_NAME, sydney_local(2026, 1, 15), sydney_local(2026, 1, 16)) == []


def test_period_containing_finds_the_period_or_none():
    periods = allowance_periods([_cycle(sydney_local(2026, 1, 1))], TZ_NAME, sydney_local(2026, 1, 1), sydney_local(2026, 3, 1))

    assert period_containing(periods, sydney_local(2026, 2, 1)).start == sydney_local(2026, 2, 1)
    assert period_containing(periods, sydney_local(2025, 12, 31)) is None


def test_total_export_adjustment_skips_allowance_windows_on_request():
    at_local = sydney_local(2026, 1, 1, 12).astimezone(datetime.timezone.utc)
    windows = [_window("0", "5"), _window("10", "-3")]

    assert total_export_adjustment_c_per_kwh(windows, at_local) == decimal.Decimal("2")
    assert total_export_adjustment_c_per_kwh(windows, at_local, skip_allowance=True) == decimal.Decimal("5")


def test_allowance_window_spans_clip_each_window_to_its_tariff_and_period():
    periods = allowance_periods([_cycle(sydney_local(2026, 1, 1))], TZ_NAME, sydney_local(2026, 1, 1), sydney_local(2026, 2, 1))
    tariff_a = SiteTariff(id=1, site_id=1, effective_from=sydney_local(2025, 6, 1), effective_to=sydney_local(2026, 1, 10))
    tariff_b = SiteTariff(id=2, site_id=1, effective_from=sydney_local(2026, 1, 10, 15), effective_to=None)
    window_a, window_b, plain = _window("1"), _window("2"), _window("0")
    window_a.id, window_b.id, plain.id = 11, 12, 13

    spans = allowance_window_spans(periods, [tariff_a, tariff_b], {1: [window_a, plain], 2: [window_b]}, TZ_NAME)

    # A started before the period and ended on 10 Jan; B started at 15:00 on
    # 10 Jan, which rounds down to that day's midnight. The no-allowance window is skipped.
    assert [(s.tariff_id, s.window.id, s.start, s.end) for s in spans] == [
        (1, 11, sydney_local(2026, 1, 1), sydney_local(2026, 1, 10)),
        (2, 12, sydney_local(2026, 1, 10), sydney_local(2026, 2, 1)),
    ]


def test_allowance_window_spans_skip_a_tariff_that_ends_before_the_period():
    periods = allowance_periods([_cycle(sydney_local(2026, 1, 1))], TZ_NAME, sydney_local(2026, 1, 1), sydney_local(2026, 2, 1))
    old = SiteTariff(id=1, site_id=1, effective_from=sydney_local(2025, 1, 1), effective_to=sydney_local(2025, 12, 1))
    window = _window("1")
    window.id = 11

    assert list(allowance_window_spans(periods, [old], {1: [window]}, TZ_NAME)) == []
