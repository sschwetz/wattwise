from __future__ import annotations

import datetime
import decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.jobs.time_buckets import BUCKET_WIDTH
from core_api.models.billing import (
    ConditionalCreditWindow,
    DemandCharge,
    DemandWindow,
    ExportAdjustmentWindow,
    MonthlyFeeDiscountWindow,
    SiteTariff,
)
from core_api.models.pricing import PriceInterval
from core_api.models.sources import Source, SourceKind
from core_api.time_utils import as_utc, site_tz


def tariff_effective_at(tariffs: list[SiteTariff], at: datetime.datetime) -> SiteTariff | None:
    """The SiteTariff in force at `at`, picked from an already-loaded list —
    non-retroactive: a later rate/window change must not reinterpret a past
    timestamp (spec: 'Job 1'). Pulled out of effective_site_tariff_at so a
    caller resolving many instants for the same site (e.g. cost.py's daily
    service charge) can load the site's tariffs once instead of re-querying
    per instant."""
    for tariff in tariffs:
        if as_utc(tariff.effective_from) <= at and (
            tariff.effective_to is None or as_utc(tariff.effective_to) > at
        ):
            return tariff
    return None


class _DatedWindow(Protocol):
    effective_from: datetime.datetime | None
    effective_to: datetime.datetime | None


def window_in_force(window: _DatedWindow, at: datetime.datetime) -> bool:
    """Is `at` inside the window's [effective_from, effective_to)? A missing
    bound is open, which also covers a window built in code without dates."""
    at = as_utc(at)
    if window.effective_from is not None and as_utc(window.effective_from) > at:
        return False
    return window.effective_to is None or at < as_utc(window.effective_to)


def site_tariffs_for_site(db: Session, site_id: int) -> list[SiteTariff]:
    """All of a site's SiteTariff rows, ordered newest-effective-first --
    the shape tariff_effective_at expects."""
    stmt = select(SiteTariff).where(SiteTariff.site_id == site_id).order_by(SiteTariff.effective_from.desc())
    return list(db.execute(stmt).scalars())


def effective_site_tariff_at(db: Session, site_id: int, at: datetime.datetime) -> SiteTariff | None:
    """The SiteTariff in force at `at` — non-retroactive: a later rate/window
    change must not reinterpret a past timestamp (spec: 'Job 1')."""
    return tariff_effective_at(site_tariffs_for_site(db, site_id), at)


class _TimeWindowPattern(Protocol):
    """The months/days-of-week/time-of-day shape shared by DemandWindow and
    ExportAdjustmentWindow -- lets _in_time_pattern take either window type
    directly instead of callers unpacking its fields."""

    months: list[int]
    days_of_week: list[int]
    start_time: datetime.time
    end_time: datetime.time


def _in_time_pattern(window: _TimeWindowPattern, at_local: datetime.datetime) -> bool:
    """Does the local wall-clock time `at_local` fall inside this window's
    months/days-of-week/time-of-day pattern (both time bounds inclusive)?
    Handles a time-of-day range that wraps past midnight generically, even
    though every tariff this system targets today is same-day (e.g.
    15:00-21:00). days_of_week uses Python's Monday=0..Sunday=6, matching
    the existing demand-window test fixtures."""
    if at_local.month not in window.months:
        return False
    if at_local.weekday() not in window.days_of_week:
        return False
    t = at_local.time()
    if window.start_time <= window.end_time:
        return window.start_time <= t <= window.end_time
    return t >= window.start_time or t <= window.end_time


def in_demand_window_pattern(window: DemandWindow, at_local: datetime.datetime) -> bool:
    """Public wrapper over _in_time_pattern for callers outside this module
    (cost.py's demand-charge bucket spreading) that need to test a single
    DemandWindow's pattern directly."""
    return _in_time_pattern(window, at_local)


def first_matching_demand_window(
    db: Session, site_id: int, at: datetime.datetime, site_timezone: str
) -> DemandWindow | None:
    """The first (lowest id) DemandWindow whose pattern covers `at`, among
    those on whichever SiteTariff was effective at `at` — 'the first
    matching DemandWindow wins' (spec: 'Job 1'). None if no SiteTariff is
    effective, or none of its windows match `at`'s pattern."""
    tariff = effective_site_tariff_at(db, site_id, at)
    if tariff is None:
        return None
    at_local = at.astimezone(site_tz(site_timezone))
    stmt = select(DemandWindow).where(DemandWindow.site_tariff_id == tariff.id).order_by(DemandWindow.id)
    for window in db.execute(stmt).scalars():
        if in_demand_window_pattern(window, at_local) and window_in_force(window, at):
            return window
    return None


def any_configured_demand_window(db: Session, site_id: int, at: datetime.datetime) -> DemandWindow | None:
    """The first DemandWindow configured on whichever SiteTariff was
    effective at `at`, ignoring its day/time pattern — a fallback source of
    rate/N when a billing period has zero in-pattern usage but the site does
    have a demand tariff configured (spec: 'Job 2')."""
    tariff = effective_site_tariff_at(db, site_id, at)
    if tariff is None:
        return None
    stmt = select(DemandWindow).where(DemandWindow.site_tariff_id == tariff.id).order_by(DemandWindow.id)
    return next((w for w in db.execute(stmt).scalars() if window_in_force(w, at)), None)


def _windows_by_tariff(db: Session, site_id: int, model_cls: type) -> dict[int, list]:
    """All of a site's rows of a given tariff-child window model, grouped by
    site_tariff_id -- shared by export_adjustment_windows_by_tariff and
    monthly_fee_discount_windows_by_tariff, loaded once per site so a caller
    resolving many instants doesn't re-query per minute/day, the same reason
    site_tariffs_for_site is loaded once up front."""
    stmt = select(model_cls).join(SiteTariff, model_cls.site_tariff_id == SiteTariff.id).where(
        SiteTariff.site_id == site_id
    )
    grouped: dict[int, list] = {}
    for window in db.execute(stmt).scalars():
        grouped.setdefault(window.site_tariff_id, []).append(window)
    return grouped


def export_adjustment_windows_by_tariff(db: Session, site_id: int) -> dict[int, list[ExportAdjustmentWindow]]:
    return _windows_by_tariff(db, site_id, ExportAdjustmentWindow)


def in_export_window_pattern(window: ExportAdjustmentWindow, at_local: datetime.datetime) -> bool:
    """Public wrapper over _in_time_pattern for export_allowance_data.py."""
    return _in_time_pattern(window, at_local)


def total_export_adjustment_c_per_kwh(
    windows: list[ExportAdjustmentWindow], at_local: datetime.datetime, skip_allowance: bool = False
) -> decimal.Decimal:
    """Sum of adjustment_c_per_kwh for every window in `windows` (already
    filtered to one tariff) whose pattern covers the local wall-clock
    instant `at_local`. Windows stack -- unlike a DemandWindow's 'first
    match wins' -- since each is an independent price adjustment rather
    than a single applicable rate.

    `skip_allowance` leaves out windows with a daily allowance. The caller
    sets it when a billing period covers the instant, because those windows
    are then charged through export_allowance_data instead."""
    total = decimal.Decimal("0")
    for window in windows:
        if skip_allowance and window.daily_allowance_kwh > 0:
            continue
        if _in_time_pattern(window, at_local) and window_in_force(window, at_local):
            total += window.adjustment_c_per_kwh
    return total


def conditional_credit_windows_by_tariff(db: Session, site_id: int) -> dict[int, list[ConditionalCreditWindow]]:
    return _windows_by_tariff(db, site_id, ConditionalCreditWindow)


def credit_window_applies_on(window: ConditionalCreditWindow, local_date: datetime.date) -> bool:
    """Does `local_date` (a calendar day in the site's time zone) fall in the
    window's months and days of week? The time of day is not checked here:
    the credit looks at the whole window on that day, not at one instant."""
    return local_date.month in window.months and local_date.weekday() in window.days_of_week


def monthly_fee_discount_windows_by_tariff(db: Session, site_id: int) -> dict[int, list[MonthlyFeeDiscountWindow]]:
    return _windows_by_tariff(db, site_id, MonthlyFeeDiscountWindow)


def monthly_fee_c_for_day(
    tariff: SiteTariff, windows: list[MonthlyFeeDiscountWindow], at: datetime.datetime
) -> decimal.Decimal | None:
    """The fee amount that applies to the local calendar day containing
    `at` -- tariff.monthly_fee_c, unless a MonthlyFeeDiscountWindow covers
    `at`, in which case that window's discounted_fee_c. Windows are
    absolute spans (not a recurring pattern), so the first one (by id)
    whose [starts_at, ends_at) contains `at` wins -- only one fee can
    apply to a given day, unlike ExportAdjustmentWindow's stacking.
    None if the tariff has no monthly_fee_c configured at all."""
    if tariff.monthly_fee_c is None:
        return None
    for window in windows:
        if as_utc(window.starts_at) <= at < as_utc(window.ends_at):
            return window.discounted_fee_c
    return tariff.monthly_fee_c


def _demand_window_slot_starts(
    window: DemandWindow, period_start: datetime.datetime, period_end: datetime.datetime, site_timezone: str
) -> list[datetime.datetime]:
    """Every 5-minute slot start (UTC) in [period_start, period_end) whose
    local time falls inside `window`'s pattern -- the same granularity
    DemandInterval/the demand-charge job already measures peak usage at, so
    a charge gets spread only across the moments that actually earned it."""
    tz = site_tz(site_timezone)
    period_start_utc = as_utc(period_start)
    period_end_utc = as_utc(period_end)
    slots = []
    cursor = period_start_utc
    while cursor < period_end_utc:
        if in_demand_window_pattern(window, cursor.astimezone(tz)):
            slots.append(cursor)
        cursor += BUCKET_WIDTH
    return slots


def demand_charge_bucket_shares(
    charge: DemandCharge, window: DemandWindow, site_timezone: str
) -> dict[datetime.datetime, decimal.Decimal]:
    """Map of 5-minute slot_start -> even share of `charge.charge_amount`,
    for every slot in the charge's billing period that falls inside
    `window`'s pattern. {} if the window never matched during the period
    (shouldn't happen for a real charge, but the caller shouldn't divide by
    zero either)."""
    slots = _demand_window_slot_starts(window, charge.billing_period_start, charge.billing_period_end, site_timezone)
    if not slots:
        return {}
    share = charge.charge_amount / len(slots)
    return {slot: share for slot in slots}


def demand_charges_overlapping(
    db: Session, site_id: int, start: datetime.datetime, end: datetime.datetime, *, finalized_only: bool
) -> list[DemandCharge]:
    """Every DemandCharge for this site whose billing period overlaps
    [start, end). `finalized_only` excludes still-estimated charges -- used
    by compute_cost_series, which must not plot a charge before it settles,
    while compute_cost's running total includes the current best guess."""
    stmt = select(DemandCharge).where(
        DemandCharge.site_id == site_id,
        DemandCharge.billing_period_start < end,
        DemandCharge.billing_period_end > start,
    )
    if finalized_only:
        stmt = stmt.where(DemandCharge.is_estimated.is_(False))
    return list(db.execute(stmt).scalars())


def demand_window_for_charge(db: Session, charge: DemandCharge, site_timezone: str) -> DemandWindow | None:
    """The DemandWindow that produced `charge` -- the same resolution
    demand_charge_job used when it was finalized (first_matching_demand_window
    at the peak, falling back to any_configured_demand_window at the period
    start), re-derived here since DemandCharge doesn't persist a window
    reference."""
    configured_window = any_configured_demand_window(db, charge.site_id, as_utc(charge.billing_period_start))
    if configured_window is None:
        return None
    return (
        first_matching_demand_window(db, charge.site_id, as_utc(charge.peak_interval_start), site_timezone)
        or configured_window
    )


def energy_source_ids_for_site(db: Session, site_id: int) -> list[int]:
    stmt = select(Source.id).where(Source.site_id == site_id, Source.kind == SourceKind.ENERGY)
    return list(db.execute(stmt).scalars())


def pricing_source_ids_for_site(db: Session, site_id: int) -> list[int]:
    stmt = select(Source.id).where(Source.site_id == site_id, Source.kind == SourceKind.PRICING)
    return list(db.execute(stmt).scalars())


def price_interval_flags_demand(
    db: Session, pricing_source_ids: list[int], bucket_start: datetime.datetime, bucket_end: datetime.datetime
) -> bool:
    """Is [bucket_start, bucket_end) covered by an is_demand_window-flagged
    PriceInterval from any of this site's pricing sources? An alternate way
    of flagging a demand period, feeding the same rolling-sum calculation as
    a manually-configured DemandWindow (spec: 'Job 1')."""
    if not pricing_source_ids:
        return False
    stmt = select(PriceInterval.pricing_source_id).where(
        PriceInterval.pricing_source_id.in_(pricing_source_ids),
        PriceInterval.is_demand_window.is_(True),
        PriceInterval.interval_start < bucket_end,
        PriceInterval.interval_end > bucket_start,
    ).limit(1)
    return db.execute(stmt).first() is not None
