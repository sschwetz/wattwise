from __future__ import annotations

import bisect
import dataclasses
import datetime
import decimal
from typing import Literal

from sqlalchemy.orm import Session

from core_api.cost import (
    MINUTES_TO_KWH,
    _energy_by_ts,
    _local_days_in_range,
    _minute_costs,
    _price_by_ts,
    _source_ids,
    compute_cost,
)
from core_api.jobs.demand_config import demand_charges_overlapping, site_tariffs_for_site
from core_api.models.billing import DemandCharge
from core_api.models.pricing import PriceChannel
from core_api.models.readings import EnergyMetric
from core_api.models.sources import SourceKind
from core_api.time_utils import as_utc, site_tz

ZERO = decimal.Decimal("0")

TotalsBucket = Literal["day", "hour", "minute"]

# Fields summed as watt-minutes per bucket, keyed by the metric they come from.
POWER_METRICS = {
    "production": EnergyMetric.PRODUCTION_W,
    "consumption": EnergyMetric.CONSUMPTION_W,
    "grid_import": EnergyMetric.GRID_IMPORT_W,
    "grid_export": EnergyMetric.GRID_EXPORT_W,
    "battery_charge": EnergyMetric.BATTERY_CHARGE_W,
    "battery_discharge": EnergyMetric.BATTERY_DISCHARGE_W,
}


@dataclasses.dataclass
class TotalsRow:
    start: datetime.datetime
    date: datetime.date
    hours: float
    cost_c: decimal.Decimal
    avg_import_price_c: decimal.Decimal | None
    avg_export_price_c: decimal.Decimal | None
    production_kwh: decimal.Decimal
    consumption_kwh: decimal.Decimal
    solar_to_battery_kwh: decimal.Decimal
    grid_to_battery_kwh: decimal.Decimal
    grid_import_kwh: decimal.Decimal
    grid_export_kwh: decimal.Decimal
    battery_charge_kwh: decimal.Decimal
    battery_discharge_kwh: decimal.Decimal
    battery_soc_pct: decimal.Decimal | None
    demand_charge_c: decimal.Decimal | None
    demand_charge_estimated: bool


@dataclasses.dataclass
class _Sums:
    """One bucket's accumulated readings: watt-minutes per field, the battery
    split, the latest state of charge and the buy/sell price samples."""

    watt_minutes: dict[str, decimal.Decimal] = dataclasses.field(
        default_factory=lambda: {name: ZERO for name in POWER_METRICS}
    )
    solar_to_battery_w: decimal.Decimal = ZERO
    grid_to_battery_w: decimal.Decimal = ZERO
    soc_pct: decimal.Decimal | None = None
    buy_prices: list[decimal.Decimal] = dataclasses.field(default_factory=list)
    sell_prices: list[decimal.Decimal] = dataclasses.field(default_factory=list)


Buckets = list[tuple[datetime.datetime, datetime.datetime]]


def _buckets(start: datetime.datetime, end: datetime.datetime, bucket: TotalsBucket, site_timezone: str) -> Buckets:
    """(start, end) UTC pairs: local calendar days, or consecutive hours or
    minutes from `start` (callers pass an hour- or day-aligned `start`)."""
    if bucket == "day":
        return _local_days_in_range(start, end, site_timezone)
    step = datetime.timedelta(hours=1) if bucket == "hour" else datetime.timedelta(minutes=1)
    begin, finish = as_utc(start), as_utc(end)
    count = max(0, (finish - begin) // step)
    return [(begin + step * i, begin + step * (i + 1)) for i in range(count)]


def _split_battery_charge(production_w, consumption_w, charge_w) -> tuple[decimal.Decimal, decimal.Decimal]:
    """Solar feeds the house first, then the battery up to its charge rate;
    whatever charge is left over is counted as coming from the grid."""
    solar_w = min(charge_w, max(ZERO, production_w - consumption_w))
    return solar_w, charge_w - solar_w


def _accumulate_energy(db: Session, site_id: int, buckets: Buckets, sums: list[_Sums]) -> None:
    source_ids = _source_ids(db, site_id, SourceKind.ENERGY)
    start, end = buckets[0][0], buckets[-1][1]
    starts = [bucket_start for bucket_start, _end in buckets]
    by_metric = {name: _energy_by_ts(db, source_ids, metric, start, end) for name, metric in POWER_METRICS.items()}
    for ts in set().union(*(readings.keys() for readings in by_metric.values())):
        index = bisect.bisect_right(starts, as_utc(ts)) - 1
        if index < 0:
            continue
        values = {name: readings.get(ts, ZERO) for name, readings in by_metric.items()}
        for name, value in values.items():
            sums[index].watt_minutes[name] += value
        solar_w, grid_w = _split_battery_charge(values["production"], values["consumption"], values["battery_charge"])
        sums[index].solar_to_battery_w += solar_w
        sums[index].grid_to_battery_w += grid_w
    soc = _energy_by_ts(db, source_ids, EnergyMetric.BATTERY_SOC_PCT, start, end)
    for ts in sorted(soc):  # the last reading in a bucket wins
        index = bisect.bisect_right(starts, as_utc(ts)) - 1
        if index >= 0:
            sums[index].soc_pct = soc[ts]


def _accumulate_prices(db: Session, site_id: int, buckets: Buckets, sums: list[_Sums]) -> None:
    pricing_ids = _source_ids(db, site_id, SourceKind.PRICING)
    start, end = buckets[0][0], buckets[-1][1]
    starts = [bucket_start for bucket_start, _end in buckets]
    for channel, attr in ((PriceChannel.BUY, "buy_prices"), (PriceChannel.SELL, "sell_prices")):
        for ts, price in _price_by_ts(db, pricing_ids, channel, start, end).items():
            index = bisect.bisect_right(starts, as_utc(ts)) - 1
            if index >= 0:
                getattr(sums[index], attr).append(price)


def _costs_by_bucket(
    db: Session,
    site_id: int,
    buckets: Buckets,
    bucket: TotalsBucket,
    method: Literal["net", "gross"],
    site_timezone: str,
    now: datetime.datetime | None,
) -> list[decimal.Decimal]:
    """Day rows use the full cost (fees included); hour and minute rows add up
    the per-minute energy cost only, as the cost chart does, so a daily fee
    does not land in one row."""
    if bucket == "day":
        return [compute_cost(db, site_id, s, e, method, site_timezone, now=now).total_cost_c for s, e in buckets]
    now_utc = as_utc(now) if now is not None else datetime.datetime.now(datetime.UTC)
    tariffs = site_tariffs_for_site(db, site_id)
    rows, _missing = _minute_costs(db, site_id, buckets[0][0], buckets[-1][1], method, site_timezone, tariffs, now_utc)
    starts = [bucket_start for bucket_start, _end in buckets]
    costs = [ZERO] * len(buckets)
    for row in rows:
        index = bisect.bisect_right(starts, row.ts) - 1
        if index >= 0:
            costs[index] += row.cost_c
    return costs


def _daily_demand_charge(charge: DemandCharge) -> decimal.Decimal:
    """The period's demand charge is peak kW x daily rate x days in the
    period, so one day's share is the same flat amount on every day."""
    period_seconds = (as_utc(charge.billing_period_end) - as_utc(charge.billing_period_start)).total_seconds()
    return charge.charge_amount * 86400 / decimal.Decimal(repr(period_seconds))


def _charge_for_day(charges: list[DemandCharge], midnight: datetime.datetime) -> DemandCharge | None:
    for charge in charges:
        if as_utc(charge.billing_period_start) <= midnight < as_utc(charge.billing_period_end):
            return charge
    return None


def _average(values: list[decimal.Decimal]) -> decimal.Decimal | None:
    return sum(values, ZERO) / len(values) if values else None


def _row(
    span: tuple[datetime.datetime, datetime.datetime],
    sums: _Sums,
    cost_c: decimal.Decimal,
    charge: DemandCharge | None,
    tz,
) -> TotalsRow:
    kwh = {name: watt_minutes * MINUTES_TO_KWH for name, watt_minutes in sums.watt_minutes.items()}
    return TotalsRow(
        start=span[0],
        date=span[0].astimezone(tz).date(),
        hours=(span[1] - span[0]).total_seconds() / 3600,
        cost_c=cost_c,
        avg_import_price_c=_average(sums.buy_prices),
        avg_export_price_c=_average(sums.sell_prices),
        production_kwh=kwh["production"],
        consumption_kwh=kwh["consumption"],
        solar_to_battery_kwh=sums.solar_to_battery_w * MINUTES_TO_KWH,
        grid_to_battery_kwh=sums.grid_to_battery_w * MINUTES_TO_KWH,
        grid_import_kwh=kwh["grid_import"],
        grid_export_kwh=kwh["grid_export"],
        battery_charge_kwh=kwh["battery_charge"],
        battery_discharge_kwh=kwh["battery_discharge"],
        battery_soc_pct=sums.soc_pct,
        demand_charge_c=_daily_demand_charge(charge) if charge else None,
        demand_charge_estimated=charge.is_estimated if charge else False,
    )


def compute_totals(
    db: Session,
    site_id: int,
    start: datetime.datetime,
    end: datetime.datetime,
    method: Literal["net", "gross"],
    bucket: TotalsBucket,
    site_timezone: str,
    now: datetime.datetime | None = None,
) -> list[TotalsRow]:
    """One row per local day, hour or minute in [start, end), oldest first.
    Demand charges and average prices apply to days and to hours and minutes
    respectively, and are None on the other buckets."""
    buckets = _buckets(start, end, bucket, site_timezone)
    if not buckets:
        return []
    sums = [_Sums() for _ in buckets]
    _accumulate_energy(db, site_id, buckets, sums)
    if bucket != "day":
        _accumulate_prices(db, site_id, buckets, sums)
    costs = _costs_by_bucket(db, site_id, buckets, bucket, method, site_timezone, now)
    charges = (
        demand_charges_overlapping(db, site_id, as_utc(start), as_utc(end), finalized_only=False)
        if bucket == "day"
        else []
    )
    tz = site_tz(site_timezone)
    return [
        _row(span, bucket_sums, cost, _charge_for_day(charges, span[0]), tz)
        for span, bucket_sums, cost in zip(buckets, sums, costs, strict=True)
    ]
