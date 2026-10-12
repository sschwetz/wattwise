from __future__ import annotations

import datetime

from core_api.jobs.billing_periods import billing_periods_for_site
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
