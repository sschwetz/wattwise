from __future__ import annotations

import datetime

from sqlalchemy import select

from core_api.jobs.demand_charge_job import invalidate_demand_data
from core_api.models.billing import DemandCharge, DemandInterval
from tests.conftest import sydney_local

FIVE_MIN = datetime.timedelta(minutes=5)


def _charge(db, site, start, end) -> DemandCharge:
    slot = DemandInterval(site_id=site.id, interval_start=start, interval_end=start + FIVE_MIN, rolling_sum_w=1.0)
    db.add(slot)
    db.flush()
    charge = DemandCharge(
        site_id=site.id,
        billing_period_start=start,
        billing_period_end=end,
        peak_interval_start=start,
        peak_avg_import_w=1.0,
        rate_applied="45.0",
        charge_amount="100",
        is_estimated=False,
    )
    db.add(charge)
    db.commit()
    return charge


def _interval(db, site, start) -> None:
    db.add(DemandInterval(site_id=site.id, interval_start=start, interval_end=start + FIVE_MIN, rolling_sum_w=0.0))
    db.commit()


def _periods(db) -> list[datetime.datetime]:
    return [c.billing_period_start for c in db.execute(select(DemandCharge).order_by(DemandCharge.billing_period_start)).scalars()]


def test_charges_ending_after_the_instant_are_deleted_and_earlier_ones_kept(db, site):
    _charge(db, site, sydney_local(2026, 5, 1), sydney_local(2026, 6, 1))
    _charge(db, site, sydney_local(2026, 6, 1), sydney_local(2026, 7, 1))
    _charge(db, site, sydney_local(2026, 7, 1), sydney_local(2026, 8, 1))

    invalidate_demand_data(db, site.id, sydney_local(2026, 6, 15))

    assert [as_syd(p) for p in _periods(db)] == [(2026, 5, 1)]


def test_intervals_from_the_start_of_the_range_onward_are_deleted(db, site):
    before, inside, after = sydney_local(2026, 6, 14), sydney_local(2026, 6, 16), sydney_local(2026, 9, 1)
    for slot in (before, inside, after):
        _interval(db, site, slot)

    invalidate_demand_data(db, site.id, sydney_local(2026, 6, 15))

    remaining = [i.interval_start for i in db.execute(select(DemandInterval)).scalars()]
    assert [as_syd(r) for r in remaining] == [(2026, 6, 14)]


def as_syd(value: datetime.datetime) -> tuple[int, int, int]:
    from tests.conftest import SYDNEY
    from core_api.time_utils import as_utc

    local = as_utc(value).astimezone(SYDNEY)
    return local.year, local.month, local.day
