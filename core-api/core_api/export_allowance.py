"""Arithmetic for ExportAdjustmentWindow.daily_allowance_kwh, with no database access.

A window with an allowance of z kWh/day adjusts only the exports beyond
z * D, where D is the number of local days elapsed in the billing period
(counting the current day). The allowance accumulates and applies
backwards: with z = 1, exporting 3 kWh on day 1 charges 2 kWh, and the
charge falls to 0 by day 3 if nothing else is exported. The balance resets
at each billing period start. Nothing is stored; callers recompute from
readings, so late-arriving readings are picked up next time.
"""

from __future__ import annotations

import bisect
import datetime
import decimal
import zoneinfo

from core_api.time_utils import as_utc, local_midnight

ZERO = decimal.Decimal("0")


def days_elapsed(period_start: datetime.datetime, at: datetime.datetime, tz: zoneinfo.ZoneInfo) -> int:
    """Local calendar days from the period start through `at`, inclusive.
    Counts dates, not 24h blocks, so a DST change doesn't shift the count."""
    start_date = as_utc(period_start).astimezone(tz).date()
    return (as_utc(at).astimezone(tz).date() - start_date).days + 1


def charged_kwh(exported_kwh: decimal.Decimal, allowance_kwh_per_day: decimal.Decimal, days: int) -> decimal.Decimal:
    return max(ZERO, exported_kwh - allowance_kwh_per_day * days)


def _period_events(
    period_start: datetime.datetime,
    period_end: datetime.datetime,
    export_kwh_by_minute: dict[datetime.datetime, decimal.Decimal],
    tz: zoneinfo.ZoneInfo,
) -> dict[datetime.datetime, decimal.Decimal]:
    """Every instant where the charged kWh can change: each local midnight in
    the period (a new day's allowance arrives) and each export minute."""
    start_utc, end_utc = as_utc(period_start), as_utc(period_end)
    events: dict[datetime.datetime, decimal.Decimal] = {}
    day = start_utc.astimezone(tz).date()
    while True:
        midnight = local_midnight(day, tz).astimezone(datetime.timezone.utc)
        if midnight >= end_utc:
            break
        events[midnight] = ZERO
        day += datetime.timedelta(days=1)
    for ts, kwh in export_kwh_by_minute.items():
        ts_utc = as_utc(ts)
        if start_utc <= ts_utc < end_utc:
            events[ts_utc] = events.get(ts_utc, ZERO) + kwh
    return events


def charged_kwh_steps(
    allowance_kwh_per_day: decimal.Decimal,
    period_start: datetime.datetime,
    period_end: datetime.datetime,
    export_kwh_by_minute: dict[datetime.datetime, decimal.Decimal],
    tz: zoneinfo.ZoneInfo,
) -> list[tuple[datetime.datetime, decimal.Decimal]]:
    """(instant, charged kWh from that instant on) for every instant in the
    period where the charged kWh differs from the one before. Starts from 0.
    `export_kwh_by_minute` holds only the exports that count (already filtered
    to the window's pattern)."""
    events = _period_events(period_start, period_end, export_kwh_by_minute, tz)
    exported = ZERO
    previous = ZERO
    steps: list[tuple[datetime.datetime, decimal.Decimal]] = []
    for ts in sorted(events):
        exported += events[ts]
        current = charged_kwh(exported, allowance_kwh_per_day, days_elapsed(period_start, ts, tz))
        if current != previous:
            steps.append((ts, current))
            previous = current
    return steps


def charge_cost_deltas_c(
    rate_c_per_kwh: decimal.Decimal, steps: list[tuple[datetime.datetime, decimal.Decimal]]
) -> list[tuple[datetime.datetime, decimal.Decimal]]:
    """Cost change in cents at each step. `rate_c_per_kwh` is the window's
    adjustment_c_per_kwh, so a negative rate (a charge) gives a positive cost
    and a drop in charged kWh gives a refund (negative cost)."""
    previous = ZERO
    deltas = []
    for ts, current in steps:
        deltas.append((ts, -rate_c_per_kwh * (current - previous)))
        previous = current
    return deltas


def is_charging_next_kwh(
    allowance_kwh_per_day: decimal.Decimal,
    period_start: datetime.datetime,
    at: datetime.datetime,
    exported_before_kwh: decimal.Decimal,
    tz: zoneinfo.ZoneInfo,
) -> bool:
    """Would the next kWh exported at `at` be adjusted? True once the exports
    so far have used up the allowance accumulated so far."""
    return exported_before_kwh >= allowance_kwh_per_day * days_elapsed(period_start, at, tz)


class CumulativeExport:
    """Running total of a window's exports, for looking up how much was
    exported before an instant."""

    def __init__(self, export_kwh_by_minute: dict[datetime.datetime, decimal.Decimal]) -> None:
        items = sorted((as_utc(ts), kwh) for ts, kwh in export_kwh_by_minute.items())
        self._timestamps = [ts for ts, _ in items]
        self._totals: list[decimal.Decimal] = []
        running = ZERO
        for _, kwh in items:
            running += kwh
            self._totals.append(running)

    def before(self, at: datetime.datetime) -> decimal.Decimal:
        """Total of the minutes strictly before `at`. An instant after the
        last reading gets the full total."""
        index = bisect.bisect_left(self._timestamps, as_utc(at))
        return self._totals[index - 1] if index else ZERO
