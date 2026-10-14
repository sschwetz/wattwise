from __future__ import annotations

import dataclasses
import datetime
import decimal
import zoneinfo
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.jobs.billing_periods import billing_period_days, billing_periods_for_site, effective_billing_cycles_for_site
from core_api.jobs.demand_config import (
    conditional_credit_windows_by_tariff,
    credit_window_applies_on,
    demand_charge_bucket_shares,
    demand_charges_overlapping,
    demand_window_for_charge,
    export_adjustment_windows_by_tariff,
    monthly_fee_c_for_day,
    monthly_fee_discount_windows_by_tariff,
    site_tariffs_for_site,
    tariff_effective_at,
    total_export_adjustment_c_per_kwh,
)
from core_api.models.billing import ConditionalCreditWindow, SiteTariff
from core_api.models.pricing import PriceChannel, PriceReading
from core_api.models.readings import EnergyMetric, EnergyReading
from core_api.models.sources import Source, SourceKind
from core_api.time_utils import as_utc, local_midnight, site_tz

MINUTES_TO_KWH = decimal.Decimal(1) / decimal.Decimal(60000)  # 1 minute at N watts -> N/60000 kWh


@dataclasses.dataclass
class CostResult:
    total_cost_c: decimal.Decimal
    total_import_kwh: decimal.Decimal
    total_export_kwh: decimal.Decimal
    minutes_missing_price: int
    monthly_fee_savings_c: decimal.Decimal = decimal.Decimal("0")


@dataclasses.dataclass
class _MinuteCost:
    ts: datetime.datetime
    cost_c: decimal.Decimal
    import_kwh: decimal.Decimal
    export_kwh: decimal.Decimal


@dataclasses.dataclass
class CostSeriesPoint:
    bucket_start: datetime.datetime
    cost_c: decimal.Decimal


def _source_ids(db: Session, site_id: int, kind: SourceKind) -> list[int]:
    stmt = select(Source.id).where(Source.site_id == site_id, Source.kind == kind)
    return list(db.execute(stmt).scalars())


def _energy_by_ts(
    db: Session, source_ids: list[int], metric: EnergyMetric, start: datetime.datetime, end: datetime.datetime
) -> dict[datetime.datetime, decimal.Decimal]:
    if not source_ids:
        return {}
    stmt = select(EnergyReading.ts, EnergyReading.value).where(
        EnergyReading.source_id.in_(source_ids),
        EnergyReading.metric == metric,
        EnergyReading.ts >= start,
        EnergyReading.ts < end,
    )
    return {ts: decimal.Decimal(str(value)) for ts, value in db.execute(stmt)}


def _price_by_ts(
    db: Session, pricing_source_ids: list[int], channel: PriceChannel, start: datetime.datetime, end: datetime.datetime
) -> dict[datetime.datetime, decimal.Decimal]:
    if not pricing_source_ids:
        return {}
    stmt = select(PriceReading.ts, PriceReading.value).where(
        PriceReading.pricing_source_id.in_(pricing_source_ids),
        PriceReading.channel == channel,
        PriceReading.ts >= start,
        PriceReading.ts < end,
    )
    return {ts: value for ts, value in db.execute(stmt)}


def _local_days_in_range(
    start: datetime.datetime, end: datetime.datetime, site_timezone: str
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Every local calendar day for [start, end)'s span, in the site's own
    timezone, as (midnight_utc, next_midnight_utc) pairs -- a day whose
    midnight falls before `start` (a range starting mid-day) is excluded,
    same half-open-interval handling as the rest of this module. The pair's
    actual (DST-aware) length is what _day_elapsed_fraction below divides
    by, rather than assuming a fixed 24h."""
    tz = site_tz(site_timezone)
    start_utc = as_utc(start)
    end_utc = as_utc(end)
    local_date = start_utc.astimezone(tz).date()

    days = []
    while True:
        midnight_utc = local_midnight(local_date, tz).astimezone(datetime.timezone.utc)
        if midnight_utc >= end_utc:
            break
        next_midnight_utc = local_midnight(local_date + datetime.timedelta(days=1), tz).astimezone(datetime.timezone.utc)
        if midnight_utc >= start_utc:
            days.append((midnight_utc, next_midnight_utc))
        local_date += datetime.timedelta(days=1)
    return days


def _day_elapsed_fraction(
    midnight_utc: datetime.datetime, next_midnight_utc: datetime.datetime, now_utc: datetime.datetime
) -> decimal.Decimal:
    """How much of a local calendar day has actually happened yet, as a
    fraction in [0, 1] -- 1 once the day has fully rolled over into the
    next, 0 before it's started (shouldn't normally occur: callers only
    pass days already known to be <= `end`), and the real elapsed share
    while it's still in progress. A day's own flat service/monthly fee is
    charged in proportion to this, rather than in full the instant its
    midnight passes -- see _service_charge_rows and _monthly_fee_rows."""
    if now_utc >= next_midnight_utc:
        return decimal.Decimal(1)
    if now_utc <= midnight_utc:
        return decimal.Decimal(0)
    elapsed = (now_utc - midnight_utc).total_seconds()
    total = (next_midnight_utc - midnight_utc).total_seconds()
    return decimal.Decimal(elapsed) / decimal.Decimal(total)


def _service_charge_rows(
    days: list[tuple[datetime.datetime, datetime.datetime]],
    tariffs: list[SiteTariff],
    now_utc: datetime.datetime | None,
) -> list[_MinuteCost]:
    """One row per local calendar day (already resolved by the caller, and
    shared with _monthly_fee_rows -- both amortize a flat per-day amount for
    the same days), for whichever SiteTariff was effective at that instant --
    same non-retroactive lookup the demand-charge job already uses. Prorated
    by _day_elapsed_fraction when `now_utc` is given: a day still in progress
    (the common case for "today") contributes only its elapsed share, not
    the full day's charge the instant its midnight passes -- a day that's
    already finished (every earlier day in a multi-day range) still gets
    the full amount, since its fraction is 1. `now_utc=None` (compute_cost's
    prorate_fees=False) skips proration entirely -- every day in range gets
    its full charge immediately, for callers that want "what's committed
    today" rather than a smoothly ticking total (the dashboard's live
    tiles; see get_cost/get_billing_period_cost). `tariffs` is loaded once
    by the caller and shared with _minute_costs, rather than each querying
    it separately. Used only by compute_cost's total (never
    compute_cost_series -- see its own docstring). Zero kWh so these rows
    add to the cost total without skewing import/export totals."""
    rows = []
    for midnight, next_midnight in days:
        tariff = tariff_effective_at(tariffs, midnight)
        if tariff is None:
            continue
        fraction = _day_elapsed_fraction(midnight, next_midnight, now_utc) if now_utc is not None else decimal.Decimal(1)
        rows.append(
            _MinuteCost(
                ts=midnight,
                cost_c=tariff.service_availability_c_per_day * fraction,
                import_kwh=decimal.Decimal("0"),
                export_kwh=decimal.Decimal("0"),
            )
        )
    return rows


def _monthly_fee_day_amounts(
    db: Session,
    site_id: int,
    days: list[tuple[datetime.datetime, datetime.datetime]],
    site_timezone: str,
    tariffs: list[SiteTariff],
    now_utc: datetime.datetime | None,
) -> list[tuple[datetime.datetime, decimal.Decimal, decimal.Decimal]]:
    """One (midnight, normal, applied) tuple per local calendar day (already resolved by the caller -- see
    _service_charge_rows), at that day's local midnight -- whichever
    SiteTariff.monthly_fee_c (or, if a MonthlyFeeDiscountWindow covers that
    day, its discounted_fee_c) was effective, amortized evenly across the
    real length of that day's billing cycle (BillingCycle's
    anchor_day/cycle_months -- the same per-cycle-day divisor
    demand_charge_job.py already uses for demand charges, via
    billing_period_days) and then, like the service charge, prorated by
    _day_elapsed_fraction when `now_utc` is given (None skips proration --
    see _service_charge_rows). No row for a day with no BillingCycle configured to
    divide by, or whose effective tariff has no monthly_fee_c set. Used
    only by compute_cost's total (never compute_cost_series -- see
    _service_charge_rows). `normal` is the day's share of the tariff's
    undiscounted monthly_fee_c, `applied` the share of the fee actually
    charged (discounted when a window covers the day), both in cents.

    Skips the BillingCycle/period lookup entirely when no tariff on the
    site has a monthly_fee_c set at all -- the common case while the
    feature is unused -- rather than regenerating the site's whole billing
    period history just to find nothing to charge."""
    if not days or not any(tariff.monthly_fee_c is not None for tariff in tariffs):
        return []

    cycles = effective_billing_cycles_for_site(db, site_id)
    if not cycles:
        return []
    periods = billing_periods_for_site(cycles, site_timezone, days[-1][1])
    windows_by_tariff = monthly_fee_discount_windows_by_tariff(db, site_id)

    amounts = []
    period_idx = 0
    for midnight, next_midnight in days:
        tariff = tariff_effective_at(tariffs, midnight)
        if tariff is None:
            continue
        # Both days and periods are already sorted ascending, so a single
        # forward-advancing pointer finds each midnight's period in
        # O(days + periods) total rather than rescanning all periods per day.
        while period_idx < len(periods) and periods[period_idx].end <= midnight:
            period_idx += 1
        if period_idx >= len(periods) or periods[period_idx].start > midnight:
            continue
        period = periods[period_idx]
        windows = windows_by_tariff.get(tariff.id, [])
        fee_c = monthly_fee_c_for_day(tariff, windows, midnight)
        if fee_c is None:
            continue
        fraction = _day_elapsed_fraction(midnight, next_midnight, now_utc) if now_utc is not None else decimal.Decimal(1)
        period_days = billing_period_days(period)
        amounts.append((midnight, (tariff.monthly_fee_c / period_days) * fraction, (fee_c / period_days) * fraction))
    return amounts


def _monthly_fee_rows(
    db: Session,
    site_id: int,
    days: list[tuple[datetime.datetime, datetime.datetime]],
    site_timezone: str,
    tariffs: list[SiteTariff],
    now_utc: datetime.datetime | None,
) -> list[_MinuteCost]:
    """The monthly fee actually charged, one zero-kWh row per day (see
    _monthly_fee_day_amounts), so the rows add to the cost total without
    skewing import/export totals. Used only by compute_cost's total."""
    return [
        _MinuteCost(
            ts=midnight,
            cost_c=applied_c,
            import_kwh=decimal.Decimal("0"),
            export_kwh=decimal.Decimal("0"),
        )
        for midnight, _normal_c, applied_c in _monthly_fee_day_amounts(db, site_id, days, site_timezone, tariffs, now_utc)
    ]


def _monthly_fee_savings_c(
    db: Session,
    site_id: int,
    days: list[tuple[datetime.datetime, datetime.datetime]],
    site_timezone: str,
    tariffs: list[SiteTariff],
    now_utc: datetime.datetime | None,
) -> decimal.Decimal:
    """Cents saved by monthly fee discount windows over `days`: the normal
    fee minus the discounted fee, summed per day."""
    amounts = _monthly_fee_day_amounts(db, site_id, days, site_timezone, tariffs, now_utc)
    return sum((normal_c - applied_c for _midnight, normal_c, applied_c in amounts), decimal.Decimal("0"))


def _window_average_import_w(
    db: Session, energy_source_ids: list[int], start_utc: datetime.datetime, end_utc: datetime.datetime
) -> decimal.Decimal | None:
    """Mean grid_import_w over the whole minutes of [start_utc, end_utc), or
    None when any of those minutes has no grid reading -- a collector outage
    must not look like a quiet window. The Envoy collector writes
    grid_export_w and no grid_import_w while the site exports, so a minute
    with only an export reading counts as present at 0 W import. Zero-watt
    readings count as readings."""
    expected_minutes = int((end_utc - start_utc).total_seconds() // 60)
    imports = _energy_by_ts(db, energy_source_ids, EnergyMetric.GRID_IMPORT_W, start_utc, end_utc)
    exports = _energy_by_ts(db, energy_source_ids, EnergyMetric.GRID_EXPORT_W, start_utc, end_utc)
    covered_minutes = set(imports) | set(exports)
    if expected_minutes <= 0 or len(covered_minutes) < expected_minutes:
        return None
    return sum(imports.values(), decimal.Decimal("0")) / len(covered_minutes)


def _window_credit_c(
    db: Session,
    energy_source_ids: list[int],
    window: ConditionalCreditWindow,
    local_date: datetime.date,
    tz: zoneinfo.ZoneInfo,
    real_now_utc: datetime.datetime,
) -> decimal.Decimal:
    """The credit `window` earns on `local_date` as negative cents, or 0 when
    the window has not ended yet, a minute is missing, or the average is not
    below the limit. The window's wall-clock bounds are converted to UTC, so a
    DST day has its real length."""
    start_utc = datetime.datetime.combine(local_date, window.start_time, tzinfo=tz).astimezone(datetime.timezone.utc)
    end_utc = datetime.datetime.combine(local_date, window.end_time, tzinfo=tz).astimezone(datetime.timezone.utc)
    if end_utc > real_now_utc:
        return decimal.Decimal("0")
    average_w = _window_average_import_w(db, energy_source_ids, start_utc, end_utc)
    if average_w is None or average_w >= window.max_avg_import_w:
        return decimal.Decimal("0")
    return -window.credit_c


def _conditional_credit_total_c(
    db: Session,
    site_id: int,
    days: list[tuple[datetime.datetime, datetime.datetime]],
    site_timezone: str,
    tariffs: list[SiteTariff],
    real_now_utc: datetime.datetime,
) -> decimal.Decimal:
    """Sum of every ConditionalCreditWindow's credit (negative cents) over the
    local days already resolved by the caller, as for _service_charge_rows: a
    matching window earns its credit on a day whose average grid import over
    [start_time, end_time) is strictly below max_avg_import_w. Windows stack.
    Used only by compute_cost's total, never compute_cost_series, for the same
    reason as the daily fees."""
    windows_by_tariff = conditional_credit_windows_by_tariff(db, site_id)
    if not days or not windows_by_tariff:
        return decimal.Decimal("0")
    tz = site_tz(site_timezone)
    energy_source_ids = _source_ids(db, site_id, SourceKind.ENERGY)
    total_c = decimal.Decimal("0")
    for midnight, _ in days:
        tariff = tariff_effective_at(tariffs, midnight)
        if tariff is None:
            continue
        local_date = midnight.astimezone(tz).date()
        for window in windows_by_tariff.get(tariff.id, []):
            if credit_window_applies_on(window, local_date):
                total_c += _window_credit_c(db, energy_source_ids, window, local_date, tz, real_now_utc)
    return total_c


def _minute_costs(
    db: Session,
    site_id: int,
    start: datetime.datetime,
    end: datetime.datetime,
    method: Literal["net", "gross"],
    site_timezone: str,
    tariffs: list[SiteTariff],
) -> tuple[list[_MinuteCost], int]:
    """Per-minute cost rows plus a count of minutes skipped for missing prices.
    Shared by compute_cost (summed into one total) and compute_cost_series
    (bucketed into a time series) so both stay consistent with the same
    buy-cost-minus-sell-revenue math. Energy-derived only -- the daily
    service charge is added separately, by compute_cost alone (see
    _service_charge_rows), so the usage chart compute_cost_series feeds
    isn't skewed by a lump-sum spike at each day's midnight bucket.
    `tariffs` is loaded once by the caller and shared with
    _service_charge_rows, rather than each querying it separately."""
    energy_source_ids = _source_ids(db, site_id, SourceKind.ENERGY)
    pricing_source_ids = _source_ids(db, site_id, SourceKind.PRICING)
    export_windows_by_tariff = export_adjustment_windows_by_tariff(db, site_id)
    tz = site_tz(site_timezone)

    import_metric = EnergyMetric.GRID_IMPORT_W if method == "net" else EnergyMetric.CONSUMPTION_W
    export_metric = EnergyMetric.GRID_EXPORT_W if method == "net" else EnergyMetric.PRODUCTION_W

    import_by_ts = _energy_by_ts(db, energy_source_ids, import_metric, start, end)
    export_by_ts = _energy_by_ts(db, energy_source_ids, export_metric, start, end)
    buy_price_by_ts = _price_by_ts(db, pricing_source_ids, PriceChannel.BUY, start, end)
    sell_price_by_ts = _price_by_ts(db, pricing_source_ids, PriceChannel.SELL, start, end)

    rows: list[_MinuteCost] = []
    minutes_missing_price = 0

    all_minutes = set(import_by_ts) | set(export_by_ts)
    for minute in all_minutes:
        minute_utc = as_utc(minute)
        import_w = import_by_ts.get(minute, decimal.Decimal("0"))
        export_w = export_by_ts.get(minute, decimal.Decimal("0"))
        buy_price = buy_price_by_ts.get(minute)
        sell_price = sell_price_by_ts.get(minute)

        if (import_w and buy_price is None) or (export_w and sell_price is None):
            minutes_missing_price += 1
            continue

        import_kwh = import_w * MINUTES_TO_KWH
        export_kwh = export_w * MINUTES_TO_KWH
        cost_c = decimal.Decimal("0")
        if import_w:
            cost_c += import_kwh * buy_price
        if export_w:
            adjustment_c_per_kwh = decimal.Decimal("0")
            tariff = tariff_effective_at(tariffs, minute_utc)
            if tariff is not None:
                at_local = minute_utc.astimezone(tz)
                windows = export_windows_by_tariff.get(tariff.id, [])
                adjustment_c_per_kwh = total_export_adjustment_c_per_kwh(windows, at_local)
            # Amber stores the feed-in (sell) price with its raw sign -- negative
            # in the normal case, since the customer is paid to export (positive
            # only during a negative-FiT event). Adding it (not subtracting) is
            # what makes a dominant export minute net-negative, i.e. revenue.
            # Subtracting the configured adjustment makes a positive (incentive)
            # value push the price further negative -- more revenue -- and a
            # negative (disincentive) value pull it back up -- less revenue.
            cost_c += export_kwh * (sell_price - adjustment_c_per_kwh)

        rows.append(_MinuteCost(ts=minute_utc, cost_c=cost_c, import_kwh=import_kwh, export_kwh=export_kwh))

    return rows, minutes_missing_price


def _demand_charge_slot_shares(
    db: Session,
    site_id: int,
    start: datetime.datetime,
    end: datetime.datetime,
    site_timezone: str,
    *,
    finalized_only: bool,
) -> dict[datetime.datetime, decimal.Decimal]:
    """Map of 5-minute slot_start -> cost_c contribution from every
    DemandCharge overlapping [start, end), clipped to that range. Each
    charge's full charge_amount is spread evenly across its own billing
    period's window-matching slots (demand_config.demand_charge_bucket_shares)
    -- this keeps only the slots that fall inside the caller's query range,
    so a sub-range only sees its fair share, not the whole period's charge."""
    start_utc = as_utc(start)
    end_utc = as_utc(end)
    shares: dict[datetime.datetime, decimal.Decimal] = {}
    for charge in demand_charges_overlapping(db, site_id, start_utc, end_utc, finalized_only=finalized_only):
        window = demand_window_for_charge(db, charge, site_timezone)
        if window is None:
            continue
        for slot_start, slot_share in demand_charge_bucket_shares(charge, window, site_timezone).items():
            if start_utc <= slot_start < end_utc:
                shares[slot_start] = shares.get(slot_start, decimal.Decimal("0")) + slot_share
    return shares


def compute_cost(
    db: Session,
    site_id: int,
    start: datetime.datetime,
    end: datetime.datetime,
    method: Literal["net", "gross"],
    site_timezone: str,
    now: datetime.datetime | None = None,
    prorate_fees: bool = True,
) -> CostResult:
    """`now` is the real wall-clock instant used to prorate the service and
    monthly fees for a day still in progress (see _day_elapsed_fraction) --
    distinct from `end`, which may itself reach into the future (the
    dashboard's "today" query runs its range through next local midnight
    regardless of the actual time, so its chart always covers a fixed
    calendar span -- see DashboardPage's rangeForMode). Defaults to the
    real current time; callers pass it explicitly only to get a
    reproducible result (tests) or to reuse an instant already computed
    for other purposes (get_billing_period_cost).

    `prorate_fees=False` charges each day's service/monthly fee in full the
    instant its midnight passes, ignoring `now` for that purpose entirely --
    for callers that want the committed daily amount visible right away
    rather than a smoothly ticking total (the dashboard's live tiles).

    Conditional credits always compare their window end with the real current
    time, whatever `prorate_fees` is."""
    real_now_utc = as_utc(now) if now is not None else datetime.datetime.now(datetime.timezone.utc)
    now_utc = real_now_utc if prorate_fees else None
    tariffs = site_tariffs_for_site(db, site_id)
    rows, minutes_missing_price = _minute_costs(db, site_id, start, end, method, site_timezone, tariffs)

    total_cost_c = decimal.Decimal("0")
    total_import_kwh = decimal.Decimal("0")
    total_export_kwh = decimal.Decimal("0")
    for row in rows:
        total_cost_c += row.cost_c
        total_import_kwh += row.import_kwh
        total_export_kwh += row.export_kwh

    days = _local_days_in_range(start, end, site_timezone)

    for charge in _service_charge_rows(days, tariffs, now_utc):
        total_cost_c += charge.cost_c

    for charge in _monthly_fee_rows(db, site_id, days, site_timezone, tariffs, now_utc):
        total_cost_c += charge.cost_c
    monthly_fee_savings_c = _monthly_fee_savings_c(db, site_id, days, site_timezone, tariffs, now_utc)

    total_cost_c += _conditional_credit_total_c(db, site_id, days, site_timezone, tariffs, real_now_utc)

    # Demand charges count toward the total as soon as there's a best
    # estimate -- even before the billing period closes and the charge is
    # finalized (unlike compute_cost_series below, which must wait).
    for slot_share in _demand_charge_slot_shares(db, site_id, start, end, site_timezone, finalized_only=False).values():
        total_cost_c += slot_share

    return CostResult(
        total_cost_c=total_cost_c,
        total_import_kwh=total_import_kwh,
        total_export_kwh=total_export_kwh,
        minutes_missing_price=minutes_missing_price,
        monthly_fee_savings_c=monthly_fee_savings_c,
    )


CostBucket = Literal["15min", "hour", "6hour"]


def _bucket_start(ts: datetime.datetime, bucket: CostBucket) -> datetime.datetime:
    if bucket == "15min":
        return ts.replace(minute=(ts.minute // 15) * 15, second=0, microsecond=0)
    if bucket == "hour":
        return ts.replace(minute=0, second=0, microsecond=0)
    return ts.replace(hour=(ts.hour // 6) * 6, minute=0, second=0, microsecond=0)


def compute_cost_series(
    db: Session,
    site_id: int,
    start: datetime.datetime,
    end: datetime.datetime,
    method: Literal["net", "gross"],
    bucket: CostBucket,
    site_timezone: str,
) -> list[CostSeriesPoint]:
    """Same per-minute cost math as compute_cost, bucketed into a time series
    instead of collapsed into one total -- for charting cost as a trend.
    Deliberately omits compute_cost's daily service charge and amortized
    monthly fee: the chart shows usage-driven cost only, so a flat per-day
    fee doesn't spike one bucket and skew the y-axis for the rest of the
    range. Finalized demand charges
    ARE included (unlike the service charge) -- spread evenly across
    whichever buckets fall inside the DemandWindow pattern that earned
    them, not shown at all until the billing period closes and the charge
    is no longer an estimate."""
    tariffs = site_tariffs_for_site(db, site_id)
    rows, _minutes_missing_price = _minute_costs(db, site_id, start, end, method, site_timezone, tariffs)

    cost_by_bucket: dict[datetime.datetime, decimal.Decimal] = {}
    for row in rows:
        bucket_start = _bucket_start(row.ts, bucket)
        cost_by_bucket[bucket_start] = cost_by_bucket.get(bucket_start, decimal.Decimal("0")) + row.cost_c

    demand_charge_slot_shares = _demand_charge_slot_shares(db, site_id, start, end, site_timezone, finalized_only=True)
    for slot_start, slot_share in demand_charge_slot_shares.items():
        bucket_start = _bucket_start(slot_start, bucket)
        cost_by_bucket[bucket_start] = cost_by_bucket.get(bucket_start, decimal.Decimal("0")) + slot_share

    return [
        CostSeriesPoint(bucket_start=bucket_start, cost_c=cost_by_bucket[bucket_start])
        for bucket_start in sorted(cost_by_bucket)
    ]
