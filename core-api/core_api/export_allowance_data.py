"""Database side of the export allowance: billing periods, grid export, and
the two results the rest of the app needs -- cost deltas for cost.py and a
marginal adjustment function for the price endpoint. The arithmetic itself is
in export_allowance.py."""

from __future__ import annotations

import datetime
import decimal
from typing import Callable, Iterator, NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.export_allowance import (
    CumulativeExport,
    charge_cost_deltas_c,
    charged_kwh_steps,
    is_charging_next_kwh,
)
from core_api.jobs.billing_periods import (
    BillingPeriod,
    billing_periods_for_site,
    effective_billing_cycles_for_site,
    partial_first_period,
)
from core_api.jobs.demand_config import (
    in_export_window_pattern,
    tariff_effective_at,
    total_export_adjustment_c_per_kwh,
    window_in_force,
)
from core_api.models.billing import BillingCycle, ExportAdjustmentWindow, SiteTariff
from core_api.models.readings import EnergyMetric, EnergyReading
from core_api.models.sources import Source, SourceKind
from core_api.time_utils import as_utc, local_midnight, site_tz

# Same factor as cost.py's MINUTES_TO_KWH: 1 minute at N watts is N/60000 kWh.
_MINUTE_W_TO_KWH = decimal.Decimal(1) / decimal.Decimal(60000)
ZERO = decimal.Decimal("0")


def _has_allowance(windows_by_tariff: dict[int, list[ExportAdjustmentWindow]]) -> bool:
    return any(w.daily_allowance_kwh > 0 for windows in windows_by_tariff.values() for w in windows)


def allowance_periods(
    cycles: list[BillingCycle], site_timezone: str, start: datetime.datetime, end: datetime.datetime
) -> list[BillingPeriod]:
    """Billing periods overlapping [start, end), including the partial period
    before the first anchor day. Empty when no billing cycle is configured."""
    start_utc, end_utc = as_utc(start), as_utc(end)
    periods = {p.start: p for p in billing_periods_for_site(cycles, site_timezone, end_utc)}
    for probe in (start_utc, end_utc - datetime.timedelta(microseconds=1)):
        partial = partial_first_period(cycles, site_timezone, probe)
        if partial is not None:
            periods[partial.start] = partial
    overlapping = [p for p in periods.values() if p.start < end_utc and p.end > start_utc]
    return sorted(overlapping, key=lambda p: p.start)


def period_containing(periods: list[BillingPeriod], at: datetime.datetime) -> BillingPeriod | None:
    at_utc = as_utc(at)
    for period in periods:
        if period.start <= at_utc < period.end:
            return period
    return None


def periods_for_allowance_windows(
    db: Session,
    site_id: int,
    windows_by_tariff: dict[int, list[ExportAdjustmentWindow]],
    site_timezone: str,
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[BillingPeriod]:
    """allowance_periods, but skips the query when no window has an allowance."""
    if not _has_allowance(windows_by_tariff):
        return []
    return allowance_periods(effective_billing_cycles_for_site(db, site_id), site_timezone, start, end)


def _grid_export_kwh_by_minute(
    db: Session, site_id: int, start: datetime.datetime, end: datetime.datetime
) -> dict[datetime.datetime, decimal.Decimal]:
    stmt = (
        select(EnergyReading.ts, EnergyReading.value)
        .join(Source, EnergyReading.source_id == Source.id)
        .where(
            Source.site_id == site_id,
            Source.kind == SourceKind.ENERGY,
            EnergyReading.metric == EnergyMetric.GRID_EXPORT_W,
            EnergyReading.ts >= start,
            EnergyReading.ts < end,
        )
    )
    return {as_utc(ts): decimal.Decimal(str(value)) * _MINUTE_W_TO_KWH for ts, value in db.execute(stmt)}


def _window_export_kwh(
    window: ExportAdjustmentWindow,
    tariff_id: int,
    tariffs: list[SiteTariff],
    exports: dict[datetime.datetime, decimal.Decimal],
    site_timezone: str,
) -> dict[datetime.datetime, decimal.Decimal]:
    """The exports that count toward `window`: inside its pattern, and made
    while its tariff was the one in force."""
    tz = site_tz(site_timezone)
    counted = {}
    for ts, kwh in exports.items():
        tariff = tariff_effective_at(tariffs, ts)
        if (
            tariff is not None
            and tariff.id == tariff_id
            and in_export_window_pattern(window, ts.astimezone(tz))
            and window_in_force(window, ts)
        ):
            counted[ts] = kwh
    return counted


class AllowanceSpan(NamedTuple):
    """One window's allowance accumulation: it counts local days from `start`
    (the day of `start` is day 1) and stops changing at `end`."""

    period: BillingPeriod
    tariff_id: int
    window: ExportAdjustmentWindow
    start: datetime.datetime
    end: datetime.datetime


def _clip_to_window(
    window: ExportAdjustmentWindow, start: datetime.datetime, end: datetime.datetime
) -> tuple[datetime.datetime, datetime.datetime]:
    """`start` and `end` narrowed to the window's own dates."""
    if window.effective_from is not None:
        start = max(start, as_utc(window.effective_from))
    if window.effective_to is not None:
        end = min(end, as_utc(window.effective_to))
    return start, end


def allowance_window_spans(
    periods: list[BillingPeriod],
    tariffs: list[SiteTariff],
    windows_by_tariff: dict[int, list[ExportAdjustmentWindow]],
    site_timezone: str,
) -> Iterator[AllowanceSpan]:
    """Every (period, tariff, window with an allowance) with the span over which
    its allowance accumulates: from local midnight of the later of the period's
    first day and the tariff's first day, to the earlier of the period end and
    the tariff's effective_to. Spans that come out empty are skipped."""
    tz = site_tz(site_timezone)
    tariffs_by_id = {t.id: t for t in tariffs}
    for period in periods:
        for tariff_id, windows in windows_by_tariff.items():
            tariff = tariffs_by_id.get(tariff_id)
            if tariff is None:
                continue
            first_day = max(period.start.astimezone(tz).date(), as_utc(tariff.effective_from).astimezone(tz).date())
            start = local_midnight(first_day, tz).astimezone(datetime.timezone.utc)
            end = period.end if tariff.effective_to is None else min(period.end, as_utc(tariff.effective_to))
            if start >= end:
                continue
            for window in (w for w in windows if w.daily_allowance_kwh > 0):
                window_start, window_end = _clip_to_window(window, start, end)
                if window_start < window_end:
                    yield AllowanceSpan(period, tariff_id, window, window_start, window_end)


def allowance_cost_deltas(
    db: Session,
    site_id: int,
    periods: list[BillingPeriod],
    start: datetime.datetime,
    end: datetime.datetime,
    site_timezone: str,
    tariffs: list[SiteTariff],
    windows_by_tariff: dict[int, list[ExportAdjustmentWindow]],
    now: datetime.datetime,
) -> list[tuple[datetime.datetime, decimal.Decimal]]:
    """Cost changes in cents, at instants inside [start, end) and not after
    `now`, from windows with an allowance. A midnight refund in the future is
    not booked yet, the same as a service fee for a day not yet reached.
    Exports are read from each period's start so a range that begins
    mid-period still sees the exports before it."""
    tz = site_tz(site_timezone)
    start_utc, end_utc, now_utc = as_utc(start), as_utc(end), as_utc(now)
    exports_by_period: dict[datetime.datetime, dict[datetime.datetime, decimal.Decimal]] = {}
    deltas: list[tuple[datetime.datetime, decimal.Decimal]] = []
    for span in allowance_window_spans(periods, tariffs, windows_by_tariff, site_timezone):
        if span.period.start not in exports_by_period:
            read_end = min(span.period.end, end_utc, now_utc + datetime.timedelta(microseconds=1))
            exports_by_period[span.period.start] = _grid_export_kwh_by_minute(db, site_id, span.period.start, read_end)
        counted = _window_export_kwh(span.window, span.tariff_id, tariffs, exports_by_period[span.period.start], site_timezone)
        steps = charged_kwh_steps(span.window.daily_allowance_kwh, span.start, span.end, counted, tz)
        for ts, delta in charge_cost_deltas_c(span.window.adjustment_c_per_kwh, steps):
            if start_utc <= ts < end_utc and ts <= now_utc:
                deltas.append((ts, delta))
    return deltas


def _cumulative_exports(
    db: Session,
    site_id: int,
    periods: list[BillingPeriod],
    tariffs: list[SiteTariff],
    windows_by_tariff: dict[int, list[ExportAdjustmentWindow]],
    site_timezone: str,
    now: datetime.datetime,
) -> dict[tuple[datetime.datetime, int], tuple[AllowanceSpan, CumulativeExport]]:
    """Per (period start, window id): the window's span and its exports up to `now`."""
    cumulative = {}
    exports_by_period: dict[datetime.datetime, dict[datetime.datetime, decimal.Decimal]] = {}
    for span in allowance_window_spans(periods, tariffs, windows_by_tariff, site_timezone):
        if span.period.start not in exports_by_period:
            exports_by_period[span.period.start] = _grid_export_kwh_by_minute(
                db, site_id, span.period.start, min(span.period.end, as_utc(now))
            )
        counted = _window_export_kwh(span.window, span.tariff_id, tariffs, exports_by_period[span.period.start], site_timezone)
        cumulative[(span.period.start, span.window.id)] = (span, CumulativeExport(counted))
    return cumulative


def marginal_adjustment_fn(
    db: Session,
    site_id: int,
    site_timezone: str,
    tariffs: list[SiteTariff],
    windows_by_tariff: dict[int, list[ExportAdjustmentWindow]],
    start: datetime.datetime,
    end: datetime.datetime,
    now: datetime.datetime,
) -> Callable[[datetime.datetime], decimal.Decimal]:
    """A function giving the adjustment_c_per_kwh total that applies to the
    next kWh exported at an instant. Windows without an allowance (or with
    one but no billing period covering the instant) contribute their rate
    whenever their pattern matches. Windows with an allowance contribute
    their rate only once exports so far have used up the allowance so far.
    Exports are known up to `now`, so a later instant sees the exports as of
    now while its allowance keeps growing."""
    tz = site_tz(site_timezone)
    periods = periods_for_allowance_windows(db, site_id, windows_by_tariff, site_timezone, start, end)
    cumulative = _cumulative_exports(db, site_id, periods, tariffs, windows_by_tariff, site_timezone, now)

    def adjustment_at(at: datetime.datetime) -> decimal.Decimal:
        at_utc = as_utc(at)
        tariff = tariff_effective_at(tariffs, at_utc)
        if tariff is None:
            return ZERO
        windows = windows_by_tariff.get(tariff.id, [])
        at_local = at_utc.astimezone(tz)
        period = period_containing(periods, at_utc)
        total = total_export_adjustment_c_per_kwh(windows, at_local, skip_allowance=period is not None)
        if period is None:
            return total
        for window in (w for w in windows if w.daily_allowance_kwh > 0):
            entry = cumulative.get((period.start, window.id))
            if entry is None:
                continue
            span, exports = entry
            if not span.start <= at_utc < span.end:
                continue
            if in_export_window_pattern(window, at_local) and is_charging_next_kwh(
                window.daily_allowance_kwh, span.start, at_utc, exports.before(at_utc), tz
            ):
                total += window.adjustment_c_per_kwh
        return total

    return adjustment_at
