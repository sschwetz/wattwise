from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core_api.jobs.billing_periods import billing_periods_for_site, effective_billing_cycles_for_site
from core_api.jobs.demand_config import (
    any_configured_demand_window,
    energy_source_ids_for_site,
    first_matching_demand_window,
)
from core_api.jobs.time_buckets import BUCKET_WIDTH, floor_to_5min
from core_api.models.billing import DemandCharge, DemandInterval
from core_api.models.identity import Site
from core_api.models.readings import EnergyReading
from core_api.time_utils import as_utc


def run_demand_charge_job(db: Session, now: datetime.datetime) -> None:
    for site in db.execute(select(Site)).scalars():
        _finalize_for_site(db, site, now)
    db.commit()


def _finalize_for_site(db: Session, site: Site, now: datetime.datetime) -> None:
    cycles = effective_billing_cycles_for_site(db, site.id)
    for period in billing_periods_for_site(cycles, site.timezone, now):
        existing = db.execute(
            select(DemandCharge).where(
                DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period.start
            )
        ).scalar_one_or_none()
        if existing is not None and not existing.is_estimated:
            continue  # finalized -- stable once written
        _upsert_charge_for_period(db, site, period.start, period.end, now)


def _has_energy_reading_gaps(
    db: Session,
    site_id: int,
    period_start: datetime.datetime,
    period_end: datetime.datetime,
    expected_slots: int,
) -> bool:
    """Does real EnergyReading coverage miss any 5-minute slot of the period?

    Job 1 writes a DemandInterval row for every slot unconditionally (a slot
    with no readings simply gets 0 W), so counting DemandInterval rows can
    never see a collector outage — coverage has to be measured against the
    raw readings themselves (spec: 'Job 2', is_estimated)."""
    energy_source_ids = energy_source_ids_for_site(db, site_id)
    if not energy_source_ids:
        return True
    timestamps = db.execute(
        select(EnergyReading.ts).where(
            EnergyReading.source_id.in_(energy_source_ids),
            EnergyReading.ts >= period_start,
            EnergyReading.ts < period_end,
        )
    ).scalars().all()
    covered_buckets = {floor_to_5min(as_utc(ts)) for ts in timestamps}
    return len(covered_buckets) < expected_slots


def _upsert_charge_for_period(
    db: Session, site: Site, period_start: datetime.datetime, period_end: datetime.datetime, now: datetime.datetime
) -> None:
    configured_window = any_configured_demand_window(db, site.id, period_start)
    if configured_window is None:
        return  # no demand tariff in force for this period -- nothing to charge

    peak_row = db.execute(
        select(DemandInterval)
        .where(
            DemandInterval.site_id == site.id,
            DemandInterval.interval_start >= period_start,
            DemandInterval.interval_start < period_end,
        )
        .order_by(DemandInterval.rolling_sum_w.desc(), DemandInterval.interval_start.asc())
        .limit(1)
    ).scalar_one_or_none()
    if peak_row is None:
        return  # Job 1 hasn't derived any slots for this period yet -- catch up next run

    expected_slots = (period_end - period_start) // BUCKET_WIDTH
    has_gaps = _has_energy_reading_gaps(db, site.id, period_start, period_end, expected_slots)

    peak_interval_start = as_utc(peak_row.interval_start)
    window = first_matching_demand_window(db, site.id, peak_interval_start, site.timezone) or configured_window
    n = window.measurement_interval_minutes // 5

    peak_avg_import_w = peak_row.rolling_sum_w / n
    peak_demand_kw = peak_avg_import_w / 1000
    rate_c_per_kw_per_day = peak_row.rate_applied if peak_row.rate_applied is not None else window.rate_c_per_kw_per_day
    billing_period_days = (period_end - period_start).total_seconds() / 86400
    charge_amount_c = peak_demand_kw * float(rate_c_per_kw_per_day) * billing_period_days

    is_estimated = (period_end > now) or has_gaps

    stmt = sqlite_insert(DemandCharge).values(
        site_id=site.id,
        billing_period_start=period_start,
        billing_period_end=period_end,
        peak_interval_start=peak_interval_start,
        peak_avg_import_w=peak_avg_import_w,
        rate_applied=rate_c_per_kw_per_day,
        charge_amount=charge_amount_c,
        is_estimated=is_estimated,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["site_id", "billing_period_start"],
        set_={
            "billing_period_end": stmt.excluded.billing_period_end,
            "peak_interval_start": stmt.excluded.peak_interval_start,
            "peak_avg_import_w": stmt.excluded.peak_avg_import_w,
            "rate_applied": stmt.excluded.rate_applied,
            "charge_amount": stmt.excluded.charge_amount,
            "is_estimated": stmt.excluded.is_estimated,
        },
    )
    db.execute(stmt)
