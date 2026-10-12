from __future__ import annotations

import dataclasses
import datetime
import decimal
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.cost import CostBucket, compute_cost, compute_cost_series
from core_api.deps import get_db_session, require_site_access
from core_api.errors import AppError
from core_api.jobs.billing_periods import current_billing_period_start, effective_billing_cycles_for_site
from core_api.jobs.demand_config import adjusted_export_price, export_adjustment_windows_by_tariff, site_tariffs_for_site
from core_api.models.billing import DemandCharge
from core_api.models.identity import Site
from core_api.models.pricing import PriceChannel, PriceReading, PriceReadingRollup5m
from core_api.models.readings import (
    EnergyMetric,
    EnergyReading,
    EnergyReadingRollup5m,
    WeatherMetric,
    WeatherReading,
    WeatherReadingRollup5m,
)
from core_api.models.sources import Source, SourceKind
from core_api.readings_query import query_readings
from core_api.schemas import CostOut, CostSeriesPointOut, DemandChargeOut, ReadingOut, UtcDatetime

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


@router.get("/prices", response_model=list[ReadingOut])
def get_prices(
    channel: PriceChannel,
    start: UtcDatetime,
    end: UtcDatetime,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[ReadingOut]:
    stmt = select(Source.id).where(Source.site_id == site.id, Source.kind == SourceKind.PRICING)
    source_ids = list(db.execute(stmt).scalars().all())
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
    sorted_rows = sorted(rows, key=lambda r: r.ts)

    if channel != PriceChannel.SELL:
        return [ReadingOut(ts=r.ts, metric=r.channel.value, value=float(r.value)) for r in sorted_rows]

    # The export price shown here (e.g. the dashboard's "today's pricing"
    # chart) must reflect any configured incentive/disincentive, the same
    # as the $ cost totals already do -- not just the raw Amber feed.
    tariffs = site_tariffs_for_site(db, site.id)
    windows_by_tariff = export_adjustment_windows_by_tariff(db, site.id)
    return [
        ReadingOut(
            ts=r.ts,
            metric=r.channel.value,
            value=float(
                adjusted_export_price(decimal.Decimal(str(r.value)), tariffs, windows_by_tariff, r.ts, site.timezone)
            ),
        )
        for r in sorted_rows
    ]


@router.get("/cost", response_model=CostOut)
def get_cost(
    start: UtcDatetime,
    end: UtcDatetime,
    method: Literal["net", "gross"],
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> CostOut:
    result = compute_cost(db, site.id, start, end, method, site.timezone)
    return CostOut(**dataclasses.asdict(result))


@router.get("/cost/billing-period", response_model=CostOut)
def get_billing_period_cost(
    method: Literal["net", "gross"],
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> CostOut:
    """Running cost/credit for the billing period `now` falls inside, from
    its start up to now -- the billing-cycle analogue of the dashboard's
    "today's cost" live tile. Falls back to the active cycle's own
    effective_from when its first anchor day hasn't arrived yet (see
    current_billing_period_start), so a freshly-configured cycle still shows
    a running total instead of 404ing until that first anchor day."""
    now = datetime.datetime.now(datetime.timezone.utc)
    cycles = effective_billing_cycles_for_site(db, site.id)
    start = current_billing_period_start(cycles, site.timezone, now)
    if start is None:
        raise AppError(404, "no_active_billing_cycle", "No billing cycle is currently active for this site")
    result = compute_cost(db, site.id, start, now, method, site.timezone)
    return CostOut(**dataclasses.asdict(result))


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
