from __future__ import annotations

import datetime
import zoneinfo

from sqlalchemy import delete, insert, select

from core_api.jobs.demand_charge_job import run_demand_charge_job
from core_api.models.billing import BillingCycle, DemandCharge, DemandInterval, DemandWindow, SiteTariff
from core_api.models.readings import EnergyMetric, EnergyReading
from core_api.models.sources import Source, SourceKind
from tests.conftest import UTC, sydney_local

# conftest's `site` fixture is Australia/Sydney -- kept as-is (not
# overridden to UTC), both because billing_periods_for_site must be
# exercised against a real local calendar (see Task 3's non_utc_site_
# timezone test) and because the 15:00-21:00 DemandWindow pattern below is
# Sydney local time, same reasoning as Task 2/4.


def make_billing_cycle(db, site, effective_from, cycle_months=1, anchor_day=1):
    cyc = BillingCycle(site_id=site.id, effective_from=effective_from, cycle_months=cycle_months, anchor_day=anchor_day)
    db.add(cyc)
    db.commit()
    return cyc


def make_tariff_and_window(db, site, effective_from, rate="45.0", minutes=30):
    tariff = SiteTariff(site_id=site.id, effective_from=effective_from, service_availability_c_per_day="80.0")
    db.add(tariff)
    db.commit()
    window = DemandWindow(
        site_tariff_id=tariff.id, label="Peak", months=list(range(1, 13)), days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time(15, 0), end_time=datetime.time(21, 0), rate_c_per_kw_per_day=rate,
        measurement_interval_minutes=minutes,
    )
    db.add(window)
    db.commit()
    return tariff, window


def add_demand_intervals_for_period(db, site, period_start, period_end, in_window_w):
    """Fills a billing period with one DemandInterval per 5-min slot: every
    slot inside 15:00-21:00 *local* gets rolling_sum_w=in_window_w and a
    rate; every other slot is zeroed, exactly like Job 1 would produce.
    period_start/end are UTC instants, so each slot is converted to the
    site's local time before the pattern check -- comparing slot.time()
    directly (UTC wall-clock) against the window's local bounds would be
    wrong for any site not on UTC."""
    tz = zoneinfo.ZoneInfo(site.timezone)
    slot = period_start
    while slot < period_end:
        inside = datetime.time(15, 0) <= slot.astimezone(tz).time() <= datetime.time(21, 0)
        db.add(DemandInterval(
            site_id=site.id, interval_start=slot, interval_end=slot + datetime.timedelta(minutes=5),
            rolling_sum_w=in_window_w if inside else 0.0, is_demand_window=inside,
            rate_applied="45.0" if inside else None,
        ))
        slot += datetime.timedelta(minutes=5)
    db.commit()


def make_energy_source(db, site):
    source = Source(
        site_id=site.id, kind=SourceKind.ENERGY, provider="envoy", config={}, effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def add_energy_readings_for_period(db, source, period_start, period_end, skip=()):
    """One GRID_IMPORT_W reading per 5-min slot of the period, so Job 2 sees
    full raw coverage. `skip` is an iterable of (from, to) UTC instants whose
    slots are left with no readings at all, standing in for a collector
    outage. Bulk-inserted: a month is ~8.9k rows."""
    rows = []
    slot = period_start
    while slot < period_end:
        if not any(start <= slot < end for start, end in skip):
            rows.append({
                "source_id": source.id, "ts": slot,
                "metric": EnergyMetric.GRID_IMPORT_W, "value": 3000.0,
            })
        slot += datetime.timedelta(minutes=5)
    db.execute(insert(EnergyReading), rows)
    db.commit()


def test_zero_demand_window_usage_gives_exactly_zero_charge(db, site):
    period_start = sydney_local(2026, 1, 1)
    period_end = sydney_local(2026, 2, 1)
    make_billing_cycle(db, site, period_start)
    make_tariff_and_window(db, site, period_start)
    add_demand_intervals_for_period(db, site, period_start, period_start + datetime.timedelta(hours=1), in_window_w=0.0)

    run_demand_charge_job(db, now=period_end + datetime.timedelta(days=1))

    charge = db.execute(
        select(DemandCharge).where(DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period_start)
    ).scalar_one()
    assert charge.charge_amount == 0


def test_gaps_in_energy_reading_coverage_mark_is_estimated_true(db, site):
    period_start = sydney_local(2026, 1, 1)
    period_end = sydney_local(2026, 2, 1)
    make_billing_cycle(db, site, period_start)
    make_tariff_and_window(db, site, period_start)
    # Only 1 hour of DemandInterval rows exist out of the whole month -> a gap.
    add_demand_intervals_for_period(db, site, period_start, period_start + datetime.timedelta(hours=1), in_window_w=3000.0)

    run_demand_charge_job(db, now=period_end + datetime.timedelta(days=1))

    charge = db.execute(
        select(DemandCharge).where(DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period_start)
    ).scalar_one()
    assert charge.is_estimated is True


def test_mid_period_energy_reading_outage_marks_is_estimated_true(db, site):
    """A collector outage mid-period must still flag the charge as estimated.
    Job 1 writes a DemandInterval row for every slot regardless of whether
    any EnergyReading backs it (a dataless slot just gets 0 W), so this
    period has *complete* DemandInterval coverage — the only evidence of the
    outage is the missing raw readings themselves."""
    period_start = sydney_local(2026, 1, 1)
    period_end = sydney_local(2026, 2, 1)
    make_billing_cycle(db, site, period_start)
    make_tariff_and_window(db, site, period_start)
    source = make_energy_source(db, site)
    add_demand_intervals_for_period(db, site, period_start, period_end, in_window_w=3000.0)
    outage_start = period_start + datetime.timedelta(days=10)
    add_energy_readings_for_period(
        db, source, period_start, period_end,
        skip=[(outage_start, outage_start + datetime.timedelta(days=10))],
    )

    run_demand_charge_job(db, now=period_end + datetime.timedelta(days=1))

    charge = db.execute(
        select(DemandCharge).where(DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period_start)
    ).scalar_one()
    assert charge.is_estimated is True


def test_period_finalizes_on_the_run_after_it_closes(db, site):
    period_start = sydney_local(2026, 1, 1)
    period_end = sydney_local(2026, 2, 1)
    make_billing_cycle(db, site, period_start)
    make_tariff_and_window(db, site, period_start)
    source = make_energy_source(db, site)
    add_demand_intervals_for_period(db, site, period_start, period_end, in_window_w=3000.0)
    add_energy_readings_for_period(db, source, period_start, period_end)

    # First run: period still open.
    run_demand_charge_job(db, now=period_start + datetime.timedelta(days=5))
    mid_run = db.execute(
        select(DemandCharge).where(DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period_start)
    ).scalar_one()
    assert mid_run.is_estimated is True

    # Second run: period has closed, full coverage.
    run_demand_charge_job(db, now=period_end + datetime.timedelta(days=1))
    final_run = db.execute(
        select(DemandCharge).where(DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period_start)
    ).scalar_one()
    assert final_run.is_estimated is False
    assert final_run.charge_amount > 0


def test_finalized_charge_is_not_recomputed_on_a_later_run(db, site):
    period_start = sydney_local(2026, 1, 1)
    period_end = sydney_local(2026, 2, 1)
    make_billing_cycle(db, site, period_start)
    make_tariff_and_window(db, site, period_start)
    source = make_energy_source(db, site)
    add_demand_intervals_for_period(db, site, period_start, period_end, in_window_w=3000.0)
    add_energy_readings_for_period(db, source, period_start, period_end)
    run_demand_charge_job(db, now=period_end + datetime.timedelta(days=1))
    finalized = db.execute(
        select(DemandCharge).where(DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period_start)
    ).scalar_one()
    original_amount = finalized.charge_amount

    # A new, much higher peak appears after finalization -- must NOT change the stable row.
    # Delete the existing interval at this time to make room for the higher-peak replacement.
    db.execute(
        delete(DemandInterval).where(
            DemandInterval.site_id == site.id,
            DemandInterval.interval_start == period_start + datetime.timedelta(minutes=5),
        )
    )
    db.commit()
    db.add(DemandInterval(
        site_id=site.id, interval_start=period_start + datetime.timedelta(minutes=5),
        interval_end=period_start + datetime.timedelta(minutes=10), rolling_sum_w=999999.0,
        is_demand_window=True, rate_applied="45.0",
    ))
    db.commit()
    run_demand_charge_job(db, now=period_end + datetime.timedelta(days=2))

    unchanged = db.execute(
        select(DemandCharge).where(DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period_start)
    ).scalar_one()
    assert unchanged.charge_amount == original_amount


def test_site_with_no_demand_tariff_configured_gets_no_demand_charges(db, site):
    period_start = sydney_local(2026, 1, 1)
    make_billing_cycle(db, site, period_start)
    # No SiteTariff/DemandWindow at all.
    run_demand_charge_job(db, now=sydney_local(2026, 2, 2))

    charges = list(db.execute(select(DemandCharge).where(DemandCharge.site_id == site.id)).scalars())
    assert charges == []
