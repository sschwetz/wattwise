from __future__ import annotations

import datetime

from core_api.jobs.billing_periods import (
    billing_periods_for_site,
    current_billing_period_start,
    partial_first_period,
)
from core_api.models.billing import BillingCycle
from tests.conftest import UTC


def cycle(site_id, effective_from, effective_to=None, cycle_months=1, anchor_day=1):
    return BillingCycle(
        site_id=site_id, effective_from=effective_from, effective_to=effective_to,
        cycle_months=cycle_months, anchor_day=anchor_day,
    )


def test_monthly_periods_from_anchor_day(site):
    cycles = [cycle(site.id, datetime.datetime(2026, 1, 18, tzinfo=UTC), anchor_day=18, cycle_months=1)]
    periods = billing_periods_for_site(cycles, "UTC", datetime.datetime(2026, 4, 1, tzinfo=UTC))

    starts = [p.start for p in periods]
    assert starts == [
        datetime.datetime(2026, 1, 18, tzinfo=UTC),
        datetime.datetime(2026, 2, 18, tzinfo=UTC),
        datetime.datetime(2026, 3, 18, tzinfo=UTC),
    ]
    assert periods[0].end == datetime.datetime(2026, 2, 18, tzinfo=UTC)


def test_quarterly_periods_step_three_months(site):
    cycles = [cycle(site.id, datetime.datetime(2026, 1, 1, tzinfo=UTC), anchor_day=1, cycle_months=3)]
    periods = billing_periods_for_site(cycles, "UTC", datetime.datetime(2026, 8, 1, tzinfo=UTC))

    assert [p.start for p in periods] == [
        datetime.datetime(2026, 1, 1, tzinfo=UTC),
        datetime.datetime(2026, 4, 1, tzinfo=UTC),
        datetime.datetime(2026, 7, 1, tzinfo=UTC),
    ]


def test_anchor_day_clamps_to_shorter_month(site):
    cycles = [cycle(site.id, datetime.datetime(2026, 1, 31, tzinfo=UTC), anchor_day=31, cycle_months=1)]
    periods = billing_periods_for_site(cycles, "UTC", datetime.datetime(2026, 4, 1, tzinfo=UTC))

    # February 2026 has 28 days -> clamped.
    assert periods[1].start == datetime.datetime(2026, 2, 28, tzinfo=UTC)


def test_cycle_change_mid_sequence_uses_cycle_effective_at_each_periods_start(site):
    monthly = cycle(site.id, datetime.datetime(2026, 1, 1, tzinfo=UTC), datetime.datetime(2026, 3, 1, tzinfo=UTC), cycle_months=1, anchor_day=1)
    quarterly = cycle(site.id, datetime.datetime(2026, 3, 1, tzinfo=UTC), None, cycle_months=3, anchor_day=1)
    periods = billing_periods_for_site([monthly, quarterly], "UTC", datetime.datetime(2026, 9, 1, tzinfo=UTC))

    assert [p.start for p in periods] == [
        datetime.datetime(2026, 1, 1, tzinfo=UTC),
        datetime.datetime(2026, 2, 1, tzinfo=UTC),
        datetime.datetime(2026, 3, 1, tzinfo=UTC),
        datetime.datetime(2026, 6, 1, tzinfo=UTC),
    ]
    assert periods[2].cycle is quarterly  # the switch takes effect for the period starting at the switch


def test_no_cycles_returns_no_periods():
    assert billing_periods_for_site([], "UTC", datetime.datetime(2026, 1, 1, tzinfo=UTC)) == []


def test_until_in_the_past_returns_no_periods(site):
    cycles = [cycle(site.id, datetime.datetime(2026, 6, 1, tzinfo=UTC), anchor_day=1, cycle_months=1)]
    periods = billing_periods_for_site(cycles, "UTC", datetime.datetime(2026, 1, 1, tzinfo=UTC))
    assert periods == []


def test_non_utc_site_timezone_returns_utc_instants_not_local(site):
    """Calendar math (which date is 'the 1st of next month') must happen in
    the site's local calendar, but every returned boundary must be a UTC
    instant -- these values get written straight into
    DemandCharge.billing_period_start/end and compared against
    DemandInterval.interval_start (stored UTC), and SQLite silently drops
    tzinfo on write. Asserting only value equality wouldn't catch a
    regression here: an aware datetime still in the site's local tzinfo
    compares equal (by absolute instant) to its UTC equivalent in plain
    Python, even though writing the local-tz one to SQLite would silently
    store the wrong wall-clock numbers. So this test asserts tzinfo itself."""
    # 2025-12-31 13:00 UTC == 2026-01-01 00:00 AEDT (Sydney, UTC+11 in southern summer).
    sydney_jan1_midnight_utc = datetime.datetime(2025, 12, 31, 13, 0, tzinfo=UTC)
    cycles = [cycle(site.id, sydney_jan1_midnight_utc, anchor_day=1, cycle_months=1)]
    periods = billing_periods_for_site(cycles, "Australia/Sydney", datetime.datetime(2026, 2, 1, tzinfo=UTC))

    assert periods[0].start == sydney_jan1_midnight_utc
    assert periods[0].start.tzinfo == UTC
    # 2026-02-01 00:00 AEDT == 2026-01-31 13:00 UTC.
    assert periods[0].end == datetime.datetime(2026, 1, 31, 13, 0, tzinfo=UTC)
    assert periods[0].end.tzinfo == UTC


def test_current_billing_period_start_mid_period(site):
    cycles = [cycle(site.id, datetime.datetime(2026, 1, 18, tzinfo=UTC), anchor_day=18, cycle_months=1)]
    start = current_billing_period_start(cycles, "UTC", datetime.datetime(2026, 3, 5, tzinfo=UTC))
    assert start == datetime.datetime(2026, 2, 18, tzinfo=UTC)


def test_current_billing_period_start_no_cycles_configured():
    assert current_billing_period_start([], "UTC", datetime.datetime(2026, 1, 1, tzinfo=UTC)) is None


def test_current_billing_period_start_before_first_cycle_starts(site):
    cycles = [cycle(site.id, datetime.datetime(2026, 6, 1, tzinfo=UTC), anchor_day=1, cycle_months=1)]
    assert current_billing_period_start(cycles, "UTC", datetime.datetime(2026, 1, 1, tzinfo=UTC)) is None


def test_current_billing_period_start_falls_back_to_cycle_start_before_first_anchor_day(site):
    """A cycle created mid-month (anchor_day=1) hasn't reached its first
    anchor day yet -- billing_periods_for_site models no period there at all
    (by design: a partial period before the first anchor isn't modeled).
    The running-cost tile should still show something for that stretch,
    using the cycle's own effective_from rather than 404ing until the
    anchor day arrives."""
    cycles = [cycle(site.id, datetime.datetime(2026, 10, 7, 9, 29, 44, tzinfo=UTC), anchor_day=1, cycle_months=1)]
    start = current_billing_period_start(cycles, "Australia/Sydney", datetime.datetime(2026, 10, 12, tzinfo=UTC))
    assert start == datetime.datetime(2026, 10, 7, 9, 29, 44, tzinfo=UTC)


def test_current_billing_period_start_after_cycle_ends_with_no_successor(site):
    ended = cycle(
        site.id, datetime.datetime(2026, 1, 1, tzinfo=UTC), datetime.datetime(2026, 2, 1, tzinfo=UTC), anchor_day=1, cycle_months=1
    )
    # The cycle ends exactly on an anchor day, so there's no partial period
    # straddling the gap -- "now" sits in the gap after it with nothing to cover it.
    start = current_billing_period_start([ended], "UTC", datetime.datetime(2026, 3, 1, tzinfo=UTC))
    assert start is None


def test_partial_first_period_runs_from_effective_from_to_the_first_anchor_day(site):
    effective_from = datetime.datetime(2026, 10, 7, 9, 29, 44, tzinfo=UTC)
    cycles = [cycle(site.id, effective_from, anchor_day=1, cycle_months=1)]
    period = partial_first_period(cycles, "UTC", datetime.datetime(2026, 10, 14, tzinfo=UTC))
    assert period is not None
    assert period.start == effective_from
    assert period.end == datetime.datetime(2026, 11, 1, tzinfo=UTC)


def test_partial_first_period_is_none_once_the_first_anchor_day_has_passed(site):
    cycles = [cycle(site.id, datetime.datetime(2026, 10, 7, tzinfo=UTC), anchor_day=1, cycle_months=1)]
    assert partial_first_period(cycles, "UTC", datetime.datetime(2026, 11, 2, tzinfo=UTC)) is None


def test_partial_first_period_is_none_without_cycles_or_before_the_cycle_starts(site):
    assert partial_first_period([], "UTC", datetime.datetime(2026, 10, 14, tzinfo=UTC)) is None
    cycles = [cycle(site.id, datetime.datetime(2026, 10, 7, tzinfo=UTC), anchor_day=1, cycle_months=1)]
    assert partial_first_period(cycles, "UTC", datetime.datetime(2026, 10, 1, tzinfo=UTC)) is None
