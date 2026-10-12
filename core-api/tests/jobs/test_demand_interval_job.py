from __future__ import annotations

import datetime

from sqlalchemy import select

from core_api.jobs.demand_interval_job import run_demand_interval_job
from core_api.models.billing import DemandInterval, DemandWindow, SiteTariff
from core_api.models.pricing import PriceChannel, PriceInterval
from core_api.models.readings import EnergyMetric, EnergyReading
from core_api.models.sources import Source, SourceKind
from core_api.time_utils import as_utc
from tests.conftest import UTC, sydney_local

# conftest's `site` fixture is Australia/Sydney -- kept as-is (not
# overridden to UTC) so the two boundary tests below actually exercise
# Job 1's local<->UTC conversion instead of a no-op; see Task 2's note on
# why a UTC test fixture would hide that class of bug.


def make_energy_source(db, site):
    source = Source(
        site_id=site.id, kind=SourceKind.ENERGY, provider="envoy", config={}, effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def add_reading(db, source, ts, value):
    db.add(EnergyReading(source_id=source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=value))


def make_tariff_and_window(db, site, start="15:00", end="21:00", minutes=30, rate="45.0"):
    tariff = SiteTariff(
        site_id=site.id, effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
        service_availability_c_per_day="80.0",
    )
    db.add(tariff)
    db.commit()
    window = DemandWindow(
        site_tariff_id=tariff.id, label="Peak", months=list(range(1, 13)), days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time.fromisoformat(start), end_time=datetime.time.fromisoformat(end),
        rate_c_per_kw_per_day=rate, measurement_interval_minutes=minutes,
    )
    db.add(window)
    db.commit()
    return tariff, window


def test_rolling_sum_zeroes_slots_outside_window_and_sums_n_slots_inside(db, site):
    """Six slots (N=6 for a 30-min window) ending right at window close:
    the two slots before window-open contribute 0, the four inside
    contribute their real average. Timestamps are expressed via
    sydney_local, since the window's 15:00-21:00 bounds are Sydney *local*
    time — using bare UTC timestamps here would only pass by accident of
    local-time arithmetic never actually running on a non-UTC site."""
    source = make_energy_source(db, site)
    make_tariff_and_window(db, site, start="15:00", end="21:00", minutes=30)

    # Slots at 14:50, 14:55 local are outside the window; 15:00..15:15 local are inside.
    for minute, value in [(50, 1000.0), (55, 1000.0), (0, 2000.0), (5, 2000.0), (10, 2000.0), (15, 2000.0)]:
        hour = 14 if minute >= 50 else 15
        add_reading(db, source, sydney_local(2026, 1, 5, hour, minute), value)
    db.commit()

    run_demand_interval_job(db, now=sydney_local(2026, 1, 5, 15, 25))

    closing_slot = db.get(DemandInterval, (site.id, sydney_local(2026, 1, 5, 15, 15)))
    assert closing_slot is not None
    assert closing_slot.rolling_sum_w == 4 * 2000.0  # the two pre-window slots contributed 0
    assert closing_slot.is_demand_window is True
    assert closing_slot.rate_applied == 45.0


def test_slot_exactly_at_window_open_and_close_boundary_is_included(db, site):
    source = make_energy_source(db, site)
    make_tariff_and_window(db, site, start="15:00", end="21:00", minutes=5)

    at_open = sydney_local(2026, 1, 5, 15, 0)
    at_close = sydney_local(2026, 1, 5, 21, 0)
    add_reading(db, source, at_open, 3000.0)
    add_reading(db, source, at_close, 3000.0)
    db.commit()

    run_demand_interval_job(db, now=at_close + datetime.timedelta(minutes=10))

    at_open_row = db.get(DemandInterval, (site.id, at_open))
    at_close_row = db.get(DemandInterval, (site.id, at_close))
    assert at_open_row.is_demand_window is True
    assert at_close_row.is_demand_window is True


def test_window_change_mid_range_uses_window_effective_at_each_slots_own_timestamp(db, site):
    source = make_energy_source(db, site)
    old_tariff = SiteTariff(
        site_id=site.id, effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
        effective_to=datetime.datetime(2026, 1, 5, 12, tzinfo=UTC), service_availability_c_per_day="80.0",
    )
    db.add(old_tariff)
    db.commit()
    db.add(DemandWindow(
        site_tariff_id=old_tariff.id, label="Old", months=list(range(1, 13)), days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time(0, 0), end_time=datetime.time(23, 59), rate_c_per_kw_per_day="10.0",
        measurement_interval_minutes=5,
    ))
    new_tariff = SiteTariff(
        site_id=site.id, effective_from=datetime.datetime(2026, 1, 5, 12, tzinfo=UTC), service_availability_c_per_day="80.0",
    )
    db.add(new_tariff)
    db.commit()
    db.add(DemandWindow(
        site_tariff_id=new_tariff.id, label="New", months=list(range(1, 13)), days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time(0, 0), end_time=datetime.time(23, 59), rate_c_per_kw_per_day="99.0",
        measurement_interval_minutes=5,
    ))
    db.commit()

    before_switch = datetime.datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    after_switch = datetime.datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    add_reading(db, source, before_switch, 1000.0)
    add_reading(db, source, after_switch, 1000.0)
    db.commit()

    run_demand_interval_job(db, now=after_switch + datetime.timedelta(minutes=10))

    before_row = db.get(DemandInterval, (site.id, before_switch))
    after_row = db.get(DemandInterval, (site.id, after_switch))
    assert before_row.rate_applied == 10.0
    assert after_row.rate_applied == 99.0


def test_rerun_over_already_derived_range_is_idempotent(db, site):
    source = make_energy_source(db, site)
    make_tariff_and_window(db, site)
    day = datetime.datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    add_reading(db, source, day, 2500.0)
    db.commit()

    now = day + datetime.timedelta(minutes=10)
    run_demand_interval_job(db, now=now)
    first_pass = list(db.execute(select(DemandInterval).where(DemandInterval.site_id == site.id)).scalars())

    run_demand_interval_job(db, now=now)
    second_pass = list(db.execute(select(DemandInterval).where(DemandInterval.site_id == site.id)).scalars())

    assert len(first_pass) == len(second_pass)
    assert {(r.interval_start, r.rolling_sum_w) for r in first_pass} == {(r.interval_start, r.rolling_sum_w) for r in second_pass}


def test_catch_up_resumes_from_latest_already_derived_bucket(db, site):
    source = make_energy_source(db, site)
    make_tariff_and_window(db, site)
    already_derived = datetime.datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    db.add(DemandInterval(
        site_id=site.id, interval_start=already_derived, interval_end=already_derived + datetime.timedelta(minutes=5),
        rolling_sum_w=0.0, is_demand_window=False,
    ))
    # An EnergyReading far earlier than `already_derived` must NOT be (re)processed.
    add_reading(db, source, already_derived - datetime.timedelta(days=1), 9999.0)
    add_reading(db, source, already_derived + datetime.timedelta(minutes=5), 500.0)
    db.commit()

    run_demand_interval_job(db, now=already_derived + datetime.timedelta(minutes=15))

    rows = list(db.execute(select(DemandInterval).where(DemandInterval.site_id == site.id)).scalars())
    # SQLite drops tzinfo on write; use as_utc() to normalize datetimes from DB for comparison
    assert already_derived - datetime.timedelta(days=1) not in {as_utc(r.interval_start) for r in rows}
    assert any(as_utc(r.interval_start) == already_derived + datetime.timedelta(minutes=5) for r in rows)


def test_backfilled_reading_into_already_derived_bucket_is_picked_up_on_rerun(db, site):
    """I4 regression: a reading backfilled into a bucket that was already
    derived on a prior run must not be stranded at rolling_sum_w=0.0 forever
    -- Job 1 must re-derive a trailing window of already-derived buckets on
    every run, not just resume strictly forward from the latest one."""
    source = make_energy_source(db, site)
    make_tariff_and_window(db, site, start="15:00", end="21:00", minutes=5)

    stale_bucket = sydney_local(2026, 1, 5, 15, 5)
    add_reading(db, source, sydney_local(2026, 1, 5, 15, 0), 1000.0)
    add_reading(db, source, sydney_local(2026, 1, 5, 15, 20), 1000.0)
    db.commit()

    run_demand_interval_job(db, now=sydney_local(2026, 1, 5, 15, 25))
    before = db.get(DemandInterval, (site.id, stale_bucket))
    assert before is not None
    assert before.rolling_sum_w == 0.0  # no reading landed in this bucket yet

    # A collector backfills a delayed reading into the already-derived bucket.
    add_reading(db, source, stale_bucket, 9000.0)
    db.commit()

    run_demand_interval_job(db, now=sydney_local(2026, 1, 5, 15, 25))

    after = db.get(DemandInterval, (site.id, stale_bucket))
    assert after.rolling_sum_w == 9000.0


def test_reading_far_outside_rederive_window_is_still_not_reprocessed(db, site):
    """The trailing re-derive window is bounded -- a reading backfilled well
    before it must not resurrect ancient buckets on every run."""
    source = make_energy_source(db, site)
    make_tariff_and_window(db, site)
    recent = datetime.datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    db.add(DemandInterval(
        site_id=site.id, interval_start=recent, interval_end=recent + datetime.timedelta(minutes=5),
        rolling_sum_w=0.0, is_demand_window=False,
    ))
    ancient_bucket = recent - datetime.timedelta(days=10)
    add_reading(db, source, ancient_bucket, 9999.0)
    db.commit()

    run_demand_interval_job(db, now=recent + datetime.timedelta(minutes=5))

    rows = list(db.execute(select(DemandInterval).where(DemandInterval.site_id == site.id)).scalars())
    assert ancient_bucket not in {as_utc(r.interval_start) for r in rows}


def make_pricing_source(db, site):
    source = Source(
        site_id=site.id, kind=SourceKind.PRICING, provider="amber", config={}, effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def test_price_interval_flagged_slot_uses_configured_windows_n_not_one(db, site):
    """A slot flagged only by an is_demand_window PriceInterval has no
    matching DemandWindow pattern, but Job 2 still divides that slot's
    rolling_sum_w by the configured window's N when finalizing. So N here
    must come from the same place — falling back to 1 would understate the
    charge by that factor (6x for a 30-minute window)."""
    source = make_energy_source(db, site)
    pricing_source = make_pricing_source(db, site)
    # 15:00-21:00 local pattern: deliberately does NOT cover the 03:00 local
    # slots below, so first_matching_demand_window returns None for them.
    make_tariff_and_window(db, site, start="15:00", end="21:00", minutes=30)

    first_slot = sydney_local(2026, 1, 5, 3, 0)
    closing_slot = sydney_local(2026, 1, 5, 3, 25)
    db.add(PriceInterval(
        pricing_source_id=pricing_source.id, channel=PriceChannel.BUY,
        interval_start=first_slot, interval_end=sydney_local(2026, 1, 5, 4, 0),
        price="30.0", is_demand_window=True,
    ))
    for i in range(6):
        add_reading(db, source, first_slot + i * datetime.timedelta(minutes=5), 2000.0)
    db.commit()

    run_demand_interval_job(db, now=sydney_local(2026, 1, 5, 3, 30))

    row = db.get(DemandInterval, (site.id, closing_slot))
    assert row is not None
    assert row.is_demand_window is True
    assert row.rolling_sum_w == 6 * 2000.0  # N=6 from the configured 30-min window, not 1


def test_no_demand_window_configured_still_derives_intervals_with_rolling_sum_of_one(db, site):
    source = make_energy_source(db, site)
    ts = datetime.datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    add_reading(db, source, ts, 1200.0)
    db.commit()

    run_demand_interval_job(db, now=ts + datetime.timedelta(minutes=10))

    row = db.get(DemandInterval, (site.id, ts))
    assert row is not None
    assert row.is_demand_window is False
    assert row.rolling_sum_w == 0.0
    assert row.rate_applied is None
