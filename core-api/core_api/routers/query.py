from __future__ import annotations

import dataclasses
import datetime
import decimal
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core_api.cost import CostBucket, compute_cost, compute_cost_series, monthly_fee_discounts_c, partial_period_discounts_c
from core_api.deps import get_db_session, require_site_access
from core_api.errors import AppError
from core_api.forecast_fallback import FallbackRange, fallback_ranges, in_fallback
from core_api.jobs.billing_periods import (
    current_billing_period_bounds,
    effective_billing_cycles_for_site,
    partial_first_period,
)
from core_api.jobs.demand_config import (
    adjusted_export_price,
    export_adjustment_windows_by_tariff,
    first_matching_demand_window,
    site_tariffs_for_site,
)
from core_api.models.billing import DemandCharge, DemandWindow
from core_api.models.identity import Site
from core_api.models.pricing import PriceChannel, PriceReading, PriceReadingRollup5m, SpikeStatus
from core_api.models.readings import (
    EnergyMetric,
    EnergyReading,
    EnergyReadingRollup5m,
    WeatherMetric,
    WeatherReading,
    WeatherReadingRollup5m,
)
from core_api.models.sources import Source, SourceKind
from core_api.price_bands import IntervalBand, band_at, interval_bands
from core_api.readings_query import query_readings
from core_api.schemas import BillingPeriodBoundsOut, BillingPeriodCostOut, CostOut, CostSeriesPointOut, DemandChargeOut, DemandWindowOut, LatestReadingOut, PriceReadingOut, ReadingOut, UtcDatetime

router = APIRouter(prefix="/sites/{site_id}", tags=["query"])


def _energy_source_ids(db: Session, site_id: int) -> list[int]:
    stmt = select(Source.id).where(Source.site_id == site_id, Source.kind == SourceKind.ENERGY)
    return list(db.execute(stmt).scalars().all())


@router.get("/readings/energy", response_model=list[ReadingOut])
def get_energy_readings(
    metric: EnergyMetric,
    start: UtcDatetime,
    end: UtcDatetime,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[EnergyReading]:
    source_ids = _energy_source_ids(db, site.id)
    if not source_ids:
        return []
    rows: list = []
    for source_id in source_ids:
        rows.extend(
            query_readings(
                db,
                EnergyReading,
                EnergyReadingRollup5m,
                key_column=EnergyReading.source_id,
                key_value=source_id,
                ts_start=start,
                ts_end=end,
                metric_column=EnergyReading.metric,
                metric_value=metric,
            )
        )
    return sorted(rows, key=lambda r: r.ts)


@router.get("/readings/energy/latest", response_model=LatestReadingOut)
def get_latest_energy_reading_time(
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> LatestReadingOut:
    """When the site's energy collectors last wrote a reading, so the dashboard
    can show how stale its data is. Read from raw readings only: a recent
    reading is never rollup-only."""
    source_ids = _energy_source_ids(db, site.id)
    if not source_ids:
        return LatestReadingOut(ts=None)
    latest = db.execute(select(func.max(EnergyReading.ts)).where(EnergyReading.source_id.in_(source_ids))).scalar()
    return LatestReadingOut(ts=latest)


@router.get("/readings/weather", response_model=list[ReadingOut])
def get_weather_readings(
    metric: WeatherMetric,
    start: UtcDatetime,
    end: UtcDatetime,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[WeatherReading]:
    stmt = select(Source.id).where(Source.site_id == site.id, Source.kind == SourceKind.WEATHER)
    source_id = db.execute(stmt).scalar_one_or_none()
    if source_id is None:
        return []
    return query_readings(
        db,
        WeatherReading,
        WeatherReadingRollup5m,
        key_column=WeatherReading.source_id,
        key_value=source_id,
        ts_start=start,
        ts_end=end,
        metric_column=WeatherReading.metric,
        metric_value=metric,
    )


def _price_rows(db: Session, source_ids: list[int], channel: PriceChannel, start: datetime.datetime, end: datetime.datetime) -> list:
    rows: list = []
    for source_id in source_ids:
        rows.extend(
            query_readings(
                db,
                PriceReading,
                PriceReadingRollup5m,
                key_column=PriceReading.pricing_source_id,
                key_value=source_id,
                ts_start=start,
                ts_end=end,
                metric_column=PriceReading.channel,
                metric_value=channel,
            )
        )
    return sorted(rows, key=lambda r: r.ts)


def _displayed_price_fn(db: Session, site: Site, channel: PriceChannel) -> Callable[[PriceReading], decimal.Decimal]:
    """The price the dashboard shows for a reading. The export price (e.g. the
    "today's pricing" chart) must reflect any configured incentive/disincentive,
    the same as the $ cost totals already do -- not just the raw Amber feed."""
    if channel != PriceChannel.SELL:
        return lambda r: decimal.Decimal(str(r.value))
    tariffs = site_tariffs_for_site(db, site.id)
    windows_by_tariff = export_adjustment_windows_by_tariff(db, site.id)
    return lambda r: adjusted_export_price(decimal.Decimal(str(r.value)), tariffs, windows_by_tariff, r.ts, site.timezone)


@router.get("/prices", response_model=list[PriceReadingOut])
def get_prices(
    channel: PriceChannel,
    start: UtcDatetime,
    end: UtcDatetime,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[PriceReadingOut]:
    stmt = select(Source.id).where(Source.site_id == site.id, Source.kind == SourceKind.PRICING)
    source_ids = list(db.execute(stmt).scalars().all())
    rows = _price_rows(db, source_ids, channel, start, end)
    fallbacks = fallback_ranges(db, source_ids, channel, start, end, datetime.datetime.now(datetime.timezone.utc))

    bands = interval_bands(db, source_ids, channel, start, end)

    value_of = _displayed_price_fn(db, site, channel)
    return [_price_reading_out(r, value_of(r), fallbacks, bands) for r in rows]


def _price_reading_out(
    reading: PriceReading, value: decimal.Decimal, fallbacks: list[FallbackRange], bands: list[IntervalBand]
) -> PriceReadingOut:
    band = band_at(reading.ts, bands)
    return PriceReadingOut(
        ts=reading.ts,
        metric=reading.channel.value,
        value=float(value),
        forecast_fallback=in_fallback(reading.ts, fallbacks),
        price_low=band.price_low if band else None,
        price_high=band.price_high if band else None,
        spike_status=band.spike_status if band else SpikeStatus.NONE,
    )


@router.get("/cost", response_model=CostOut)
def get_cost(
    start: UtcDatetime,
    end: UtcDatetime,
    method: Literal["net", "gross"],
    prorate_fees: bool = True,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> CostOut:
    """`prorate_fees` (default True) spreads a day's service/monthly fee
    across its elapsed time instead of charging it in full the instant its
    midnight passes -- see compute_cost. The dashboard's chart-card "Cost"
    figure wants that (a smoothly ticking total); its live tiles ask for
    prorate_fees=false instead, via useTodayCost."""
    result = compute_cost(db, site.id, start, end, method, site.timezone, prorate_fees=prorate_fees)
    return CostOut(**dataclasses.asdict(result))


@router.get("/cost/billing-period", response_model=BillingPeriodCostOut)
def get_billing_period_cost(
    method: Literal["net", "gross"],
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> BillingPeriodCostOut:
    """Running cost/credit for the billing period `now` falls inside, from
    its start up to now -- the billing-cycle analogue of the dashboard's
    "today's cost" live tile. Falls back to the active cycle's own
    effective_from when its first anchor day hasn't arrived yet (see
    current_billing_period_bounds), so a freshly-configured cycle still shows
    a running total instead of 404ing until that first anchor day.

    A live tile, like get_cost's own prorate_fees=false callers -- shows
    the committed service/monthly fee for today in full right away rather
    than prorating it by elapsed time. monthly_fee_discounts_c is the gross
    monthly fee covered by discount windows across the whole period, not just
    the part elapsed. Before the cycle's first anchor day it is the full
    discounted fee of any covering window, not prorated."""
    now = datetime.datetime.now(datetime.timezone.utc)
    cycles = effective_billing_cycles_for_site(db, site.id)
    bounds = current_billing_period_bounds(cycles, site.timezone, now)
    if bounds is None:
        raise AppError(404, "no_active_billing_cycle", "No billing cycle is currently active for this site")
    start, period_end = bounds
    result = compute_cost(db, site.id, start, now, method, site.timezone, prorate_fees=False)
    partial = partial_first_period(cycles, site.timezone, now)
    if partial is not None:
        discounts_c = partial_period_discounts_c(db, site.id, partial.start, partial.end, site.timezone)
    else:
        discounts_c = monthly_fee_discounts_c(db, site.id, start, period_end, site.timezone)
    return BillingPeriodCostOut(**dataclasses.asdict(result), monthly_fee_discounts_c=discounts_c)


@router.get("/billing-period", response_model=BillingPeriodBoundsOut)
def get_billing_period_bounds(
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> BillingPeriodBoundsOut:
    """Start and end of the current billing period, so the dashboard can chart
    it as a date range. 404 `no_active_billing_cycle` when no cycle is active."""
    now = datetime.datetime.now(datetime.timezone.utc)
    cycles = effective_billing_cycles_for_site(db, site.id)
    bounds = current_billing_period_bounds(cycles, site.timezone, now)
    if bounds is None:
        raise AppError(404, "no_active_billing_cycle", "No billing cycle is currently active for this site")
    return BillingPeriodBoundsOut(start=bounds[0], end=bounds[1])


@router.get("/cost/series", response_model=list[CostSeriesPointOut])
def get_cost_series(
    start: UtcDatetime,
    end: UtcDatetime,
    method: Literal["net", "gross"],
    bucket: CostBucket,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[CostSeriesPointOut]:
    points = compute_cost_series(db, site.id, start, end, method, bucket, site.timezone)
    return [CostSeriesPointOut(bucket_start=p.bucket_start, cost_c=p.cost_c) for p in points]


@router.get("/demand-charges", response_model=list[DemandChargeOut])
def get_demand_charges(
    site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
) -> list[DemandCharge]:
    stmt = select(DemandCharge).where(DemandCharge.site_id == site.id).order_by(DemandCharge.billing_period_start)
    return db.execute(stmt).scalars().all()


@router.get("/demand-window/active", response_model=DemandWindowOut)
def get_active_demand_window(
    site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
) -> DemandWindow:
    """The DemandWindow active right now, if any -- the same 'first match
    wins' resolution the demand-charge job itself uses. 404s (code
    "no_active_demand_window") when none matches, the same "nothing to show,
    not an error" convention as /cost/billing-period."""
    now = datetime.datetime.now(datetime.timezone.utc)
    window = first_matching_demand_window(db, site.id, now, site.timezone)
    if window is None:
        raise AppError(404, "no_active_demand_window", "No demand window is currently active for this site")
    return window
