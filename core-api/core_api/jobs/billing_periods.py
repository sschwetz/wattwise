from __future__ import annotations

import calendar
import dataclasses
import datetime
import decimal
import zoneinfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.models.billing import BillingCycle
from core_api.time_utils import as_utc, local_midnight


@dataclasses.dataclass(frozen=True)
class BillingPeriod:
    start: datetime.datetime
    end: datetime.datetime
    cycle: BillingCycle


def billing_period_days(period: BillingPeriod) -> decimal.Decimal:
    """The real length of `period` in days, as a Decimal -- the per-cycle-day
    divisor used to amortize a flat monthly amount (cost.py's monthly fee)
    across however long the cycle actually ran, including anchor-day
    clamping to a shorter month. total_seconds() is exact here since every
    BillingPeriod boundary is a local midnight, so the delta is always a
    whole number of seconds representable exactly in a float."""
    return decimal.Decimal(repr((period.end - period.start).total_seconds())) / decimal.Decimal(86400)


def _anchor_date(year: int, month: int, anchor_day: int) -> datetime.date:
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, min(anchor_day, last_day))


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    zero_based = (year * 12 + (month - 1)) + delta
    return zero_based // 12, zero_based % 12 + 1


def effective_billing_cycles_for_site(db: Session, site_id: int) -> list[BillingCycle]:
    stmt = select(BillingCycle).where(BillingCycle.site_id == site_id).order_by(BillingCycle.effective_from)
    return list(db.execute(stmt).scalars())


def _cycle_effective_at(cycles: list[BillingCycle], at: datetime.datetime) -> BillingCycle | None:
    for cyc in reversed(cycles):
        if as_utc(cyc.effective_from) <= at and (cyc.effective_to is None or as_utc(cyc.effective_to) > at):
            return cyc
    return None


def current_billing_period_start(
    cycles: list[BillingCycle], site_timezone: str, now: datetime.datetime
) -> datetime.datetime | None:
    """The start of whichever billing period `now` falls inside, or -- if a
    cycle is active but hasn't reached its first anchor day yet (the gap
    billing_periods_for_site leaves unmodeled between a cycle's
    effective_from and its first anchor date) -- the active cycle's own
    effective_from, so there's still a "since" to report the running cost
    from instead of nothing. None only when no cycle is active at all: none
    configured yet, the first cycle hasn't started, or a cycle ended without
    a successor covering `now`. Used for the dashboard's running "billing
    period cost so far" tile, the billing-cycle analogue of "today's cost so
    far"."""
    periods = billing_periods_for_site(cycles, site_timezone, now)
    if periods:
        current = periods[-1]
        if current.start <= now < current.end:
            return current.start
    active_cycle = _cycle_effective_at(cycles, now)
    if active_cycle is not None:
        return as_utc(active_cycle.effective_from)
    return None


def billing_periods_for_site(
    cycles: list[BillingCycle], site_timezone: str, until: datetime.datetime
) -> list[BillingPeriod]:
    """Every billing period from the site's first configured BillingCycle up
    to (and including, if already open) `until`, each period using
    whichever BillingCycle was effective at that period's own start — not
    whatever is configured now (spec: the non-retroactive rule already
    applied to DemandWindow/SiteTariff, extended here to BillingCycle
    periods).

    The first period starts on the first anchor-day on or after the
    earliest cycle's effective_from; a partial period before that anchor
    day is not modeled (billing is assumed to begin cleanly on an anchor
    day).

    Calendar math (which date is "the 18th of next month") is done in the
    site's local calendar throughout, since that's what anchor_day means —
    but every BillingPeriod.start/end returned is converted to UTC before
    being handed back. These values are written straight into
    DemandCharge.billing_period_start/end and used in UTC comparisons
    against DemandInterval.interval_start; SQLite silently drops tzinfo on
    write, so a local-tz-aware value passed through unconverted would be
    stored/compared using the wrong wall-clock numbers for any site not on
    UTC (see time_utils.as_utc's docstring for the same quirk)."""
    if not cycles:
        return []
    tz = zoneinfo.ZoneInfo(site_timezone)
    first_cycle = min(cycles, key=lambda c: as_utc(c.effective_from))
    first_effective_from = as_utc(first_cycle.effective_from)
    first_local = first_effective_from.astimezone(tz)
    year, month = first_local.year, first_local.month
    start_date = _anchor_date(year, month, first_cycle.anchor_day)
    if local_midnight(start_date, tz) < first_effective_from:
        year, month = _add_months(year, month, 1)
        start_date = _anchor_date(year, month, first_cycle.anchor_day)

    periods: list[BillingPeriod] = []
    local_cursor = local_midnight(start_date, tz)
    while local_cursor < until:
        cursor_year, cursor_month = local_cursor.year, local_cursor.month
        cyc = _cycle_effective_at(cycles, local_cursor)
        if cyc is None:
            break
        end_year, end_month = _add_months(cursor_year, cursor_month, cyc.cycle_months)
        end_date = _anchor_date(end_year, end_month, cyc.anchor_day)
        local_end = local_midnight(end_date, tz)
        periods.append(
            BillingPeriod(
                start=local_cursor.astimezone(datetime.timezone.utc),
                end=local_end.astimezone(datetime.timezone.utc),
                cycle=cyc,
            )
        )
        local_cursor = local_end
    return periods
