# Export Adjustment Daily Allowance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an export adjustment window carry a daily kWh allowance that accumulates through the billing period, so only exports beyond `allowance * days elapsed` are adjusted, in both cost totals and the export price graphs.

**Architecture:** A new pure module (`export_allowance.py`) computes charged kWh from cumulative export and days elapsed. A DB-facing module (`export_allowance_data.py`) loads billing periods and grid export and turns them into cost deltas (for `cost.py`) and a marginal adjustment function (for the price endpoint). Nothing new is stored except one column on `export_adjustment_windows`.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, Alembic, pytest (core-api); React, TypeScript, vitest (webapp).

**Spec:** `docs/superpowers/specs/2026-10-21-export-allowance-design.md`

## Global Constraints

- Allowance field name is `daily_allowance_kwh`, `Numeric(10, 4)`, not null, default 0. 0 means no allowance and must behave exactly as today.
- Charged kWh at instant `t` is `max(0, E(t) - z * D(t))`. `E(t)` is grid export (`GRID_EXPORT_W`) inside the window's pattern from the billing period start. `D(t)` is local days elapsed in the period, counting the current day (day 1 gives 1).
- Allowance applies backwards: a later day's allowance can cancel an earlier overage. The balance resets at each billing period start, with no refund at the boundary.
- Sign convention is unchanged: a charge is a negative `adjustment_c_per_kwh`. A charge of `rate` on `ΔC` charged kWh adds `-rate * ΔC` to `cost_c`.
- The allowance counts grid export only. The gross cost method is out of scope.
- A window with an allowance but no billing period covering the instant behaves as if the allowance were 0.
- Price graphs show the marginal price: the adjustment applies to the next kWh when `E(before t) >= z * D(t)`. Future instants use `E` as of now.
- Prose and comments follow `.claude/rules/avoid-ai-writing.md`.
- Test commands run from `core-api/` with `.venv/bin/pytest`, and from `webapp/` with `npx vitest run`.

## Review Focus

- Allowance window with no billing cycle configured: charges every exported kWh like a plain window (Task 4 test).
- Query range starting mid-period: cost over a sub-range equals `rate * (C(end) - C(start))` (Task 4 test).
- Window covering only part of the day (10:00-15:00): exports outside the pattern do not use up the allowance (Task 4 test).
- Export minutes with no sell price still use up the allowance (Task 4 test).
- DST change inside a billing period: days elapsed counts local dates, not 24h blocks (Task 2 test).
- Export at the end of a period does not produce a refund or charge in the next period (Task 4 test).

## File Structure

- Modify `core-api/core_api/models/billing.py`: add column to `ExportAdjustmentWindow`.
- Create `core-api/alembic/versions/a7e2c9d4f1b8_add_daily_allowance_to_export_adjustment_windows.py`.
- Modify `core-api/core_api/schemas.py`: field on `ExportAdjustmentWindowCreate`.
- Create `core-api/core_api/export_allowance.py`: pure arithmetic.
- Create `core-api/core_api/export_allowance_data.py`: loads periods and export, builds cost deltas and the marginal adjustment function.
- Modify `core-api/core_api/jobs/demand_config.py`: `in_export_window_pattern`, `skip_allowance` parameter, remove `adjusted_export_price`.
- Modify `core-api/core_api/cost.py`: use the allowance path in `_minute_costs`.
- Modify `core-api/core_api/routers/query.py`: price endpoint uses the marginal adjustment.
- Modify `webapp/src/billing/useBilling.ts`, `webapp/src/pages/BillingPage.tsx`, `webapp/src/pages/BillingPage.test.tsx`.
- Modify `webapp/src/docs/07-billing.md`, `webapp/src/docs/10-glossary.md`.
- Tests: `core-api/tests/test_billing.py`, `core-api/tests/test_export_allowance.py` (new), `core-api/tests/test_export_allowance_data.py` (new), `core-api/tests/test_cost.py`, `core-api/tests/test_query.py`.

---

### Task 1: Column, migration, schema, API

**Files:**
- Modify: `core-api/core_api/models/billing.py` (class `ExportAdjustmentWindow`, after `adjustment_c_per_kwh`)
- Create: `core-api/alembic/versions/a7e2c9d4f1b8_add_daily_allowance_to_export_adjustment_windows.py`
- Modify: `core-api/core_api/schemas.py` (class `ExportAdjustmentWindowCreate`)
- Test: `core-api/tests/test_billing.py`

**Interfaces:**
- Produces: `ExportAdjustmentWindow.daily_allowance_kwh: Mapped[decimal.Decimal]`; API field `daily_allowance_kwh` (string like `"10.0000"` in responses, optional in requests, default 0, must be >= 0).

- [ ] **Step 1: Write the failing tests**

Add to `core-api/tests/test_billing.py` after `test_create_export_adjustment_window`:

```python
def test_create_export_adjustment_window_with_daily_allowance(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff = client.post(
        f"/api/v1/sites/{site_id}/tariffs",
        json={"effective_from": "2026-01-01T00:00:00Z", "service_availability_c_per_day": "80.5"},
        headers=auth(owner_id),
    )
    tariff_id = tariff.json()["id"]
    url = f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/export-adjustments"
    body = {
        "label": "Export fee",
        "months": [1],
        "days_of_week": [0],
        "start_time": "00:00:00",
        "end_time": "23:59:00",
        "adjustment_c_per_kwh": "-3.0",
    }

    with_allowance = client.post(url, json={**body, "daily_allowance_kwh": "10"}, headers=auth(owner_id))
    assert with_allowance.status_code == 201, with_allowance.text
    assert with_allowance.json()["daily_allowance_kwh"] == "10.0000"

    without = client.post(url, json=body, headers=auth(owner_id))
    assert without.status_code == 201, without.text
    assert without.json()["daily_allowance_kwh"] == "0.0000"

    negative = client.post(url, json={**body, "daily_allowance_kwh": "-1"}, headers=auth(owner_id))
    assert negative.status_code == 422
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_billing.py::test_create_export_adjustment_window_with_daily_allowance -v`
Expected: FAIL (`KeyError: 'daily_allowance_kwh'`).

- [ ] **Step 3: Implement**

`models/billing.py`, in `ExportAdjustmentWindow` after `adjustment_c_per_kwh`:

```python
    # kWh of exports per day, accumulating through the billing period, that
    # are exempt from adjustment_c_per_kwh. 0 means every exported kWh is
    # adjusted. See export_allowance.py.
    daily_allowance_kwh: Mapped[decimal.Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=decimal.Decimal("0"), server_default="0"
    )
```

`schemas.py`, `ExportAdjustmentWindowCreate`:

```python
    adjustment_c_per_kwh: decimal.Decimal
    daily_allowance_kwh: decimal.Decimal = Field(default=decimal.Decimal("0"), ge=0, max_digits=10, decimal_places=4)
```

Migration:

```python
"""add daily_allowance_kwh to export_adjustment_windows

Revision ID: a7e2c9d4f1b8
Revises: b4d7e1a9c3f2
Create Date: 2026-10-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7e2c9d4f1b8'
down_revision: Union[str, Sequence[str], None] = 'b4d7e1a9c3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'export_adjustment_windows',
        sa.Column('daily_allowance_kwh', sa.Numeric(10, 4), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('export_adjustment_windows', 'daily_allowance_kwh')
```

- [ ] **Step 4: Run to verify it passes, and the migration chain is a single head**

Run: `.venv/bin/pytest tests/test_billing.py tests/test_schema_constraints.py -v && .venv/bin/alembic heads`
Expected: tests PASS; `alembic heads` prints only `a7e2c9d4f1b8 (head)`.

- [ ] **Step 5: Commit**

```bash
git add core-api/core_api/models/billing.py core-api/core_api/schemas.py core-api/alembic/versions/a7e2c9d4f1b8_add_daily_allowance_to_export_adjustment_windows.py core-api/tests/test_billing.py
git commit -m "feat(core-api): add daily_allowance_kwh to export adjustment windows"
```

---

### Task 2: Pure allowance arithmetic

**Files:**
- Create: `core-api/core_api/export_allowance.py`
- Test: `core-api/tests/test_export_allowance.py`

**Interfaces:**
- Produces:
  - `days_elapsed(period_start: datetime, at: datetime, tz: ZoneInfo) -> int`
  - `charged_kwh(exported_kwh: Decimal, allowance_kwh_per_day: Decimal, days: int) -> Decimal`
  - `charged_kwh_steps(allowance_kwh_per_day: Decimal, period_start: datetime, period_end: datetime, export_kwh_by_minute: dict[datetime, Decimal], tz: ZoneInfo) -> list[tuple[datetime, Decimal]]` (each tuple is a UTC instant and the charged kWh from that instant on; only instants where the value changes)
  - `charge_cost_deltas_c(rate_c_per_kwh: Decimal, steps) -> list[tuple[datetime, Decimal]]` (cost change in cents at each step)
  - `is_charging_next_kwh(allowance_kwh_per_day: Decimal, period_start: datetime, at: datetime, exported_before_kwh: Decimal, tz: ZoneInfo) -> bool`
  - `class CumulativeExport` with `__init__(export_kwh_by_minute)` and `before(at) -> Decimal` (total of minutes strictly before `at`)

- [ ] **Step 1: Write the failing tests**

Create `core-api/tests/test_export_allowance.py`:

```python
from __future__ import annotations

import datetime
import decimal

from core_api.export_allowance import (
    CumulativeExport,
    charge_cost_deltas_c,
    charged_kwh,
    charged_kwh_steps,
    days_elapsed,
    is_charging_next_kwh,
)
from tests.conftest import SYDNEY, sydney_local

D = decimal.Decimal
ONE_MINUTE = datetime.timedelta(minutes=1)


def test_days_elapsed_counts_the_current_day():
    start = sydney_local(2026, 1, 1)
    assert days_elapsed(start, sydney_local(2026, 1, 1, 12), SYDNEY) == 1
    assert days_elapsed(start, sydney_local(2026, 1, 3, 0, 0), SYDNEY) == 3


def test_days_elapsed_counts_local_dates_across_a_dst_change():
    # Sydney clocks go forward on 2026-10-04, so that day has 23 hours.
    start = sydney_local(2026, 10, 1)
    assert days_elapsed(start, sydney_local(2026, 10, 5, 0, 0), SYDNEY) == 5
    assert days_elapsed(start, sydney_local(2026, 10, 4, 23, 59), SYDNEY) == 4


def test_charged_kwh_never_goes_below_zero():
    assert charged_kwh(D("3"), D("1"), 2) == D("1")
    assert charged_kwh(D("1"), D("1"), 5) == D("0")


def test_steps_overage_is_cancelled_by_later_days():
    start = sydney_local(2026, 1, 1)
    end = sydney_local(2026, 2, 1)
    noon = sydney_local(2026, 1, 1, 12, 0)
    exports = {noon: D("1"), noon + ONE_MINUTE: D("1"), noon + 2 * ONE_MINUTE: D("1")}

    steps = charged_kwh_steps(D("1"), start, end, exports, SYDNEY)

    assert steps == [
        (noon + ONE_MINUTE, D("1")),
        (noon + 2 * ONE_MINUTE, D("2")),
        (sydney_local(2026, 1, 2), D("1")),
        (sydney_local(2026, 1, 3), D("0")),
    ]


def test_steps_ignore_exports_outside_the_period():
    start = sydney_local(2026, 2, 1)
    end = sydney_local(2026, 3, 1)
    exports = {sydney_local(2026, 1, 31, 12, 0): D("50")}

    assert charged_kwh_steps(D("1"), start, end, exports, SYDNEY) == []


def test_cost_deltas_turn_a_charge_into_positive_cost_and_a_refund_into_negative():
    t1 = sydney_local(2026, 1, 1, 12, 0)
    t2 = sydney_local(2026, 1, 2)
    steps = [(t1, D("2")), (t2, D("1"))]

    assert charge_cost_deltas_c(D("-10"), steps) == [(t1, D("20")), (t2, D("-10"))]


def test_cumulative_export_counts_only_minutes_before_the_instant():
    t0 = sydney_local(2026, 1, 1, 12, 0)
    cumulative = CumulativeExport({t0: D("1"), t0 + ONE_MINUTE: D("2")})

    assert cumulative.before(t0) == D("0")
    assert cumulative.before(t0 + datetime.timedelta(seconds=1)) == D("1")
    assert cumulative.before(t0 + datetime.timedelta(days=5)) == D("3")


def test_is_charging_next_kwh_flips_when_export_reaches_the_allowance():
    start = sydney_local(2026, 1, 1)
    at = sydney_local(2026, 1, 2, 12)  # day 2, so 2 kWh of allowance with z = 1
    assert is_charging_next_kwh(D("1"), start, at, D("1.9"), SYDNEY) is False
    assert is_charging_next_kwh(D("1"), start, at, D("2"), SYDNEY) is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_export_allowance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core_api.export_allowance'`.

- [ ] **Step 3: Implement**

Create `core-api/core_api/export_allowance.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_export_allowance.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core-api/core_api/export_allowance.py core-api/tests/test_export_allowance.py
git commit -m "feat(core-api): add export allowance arithmetic"
```

---

### Task 3: Period and export loading, cost deltas, marginal adjustment

**Files:**
- Modify: `core-api/core_api/jobs/demand_config.py` (add `in_export_window_pattern`; add `skip_allowance` to `total_export_adjustment_c_per_kwh`)
- Create: `core-api/core_api/export_allowance_data.py`
- Test: `core-api/tests/test_export_allowance_data.py`

**Interfaces:**
- Consumes (Task 2): `CumulativeExport`, `charge_cost_deltas_c`, `charged_kwh_steps`, `is_charging_next_kwh`.
- Produces:
  - `in_export_window_pattern(window: ExportAdjustmentWindow, at_local: datetime) -> bool`
  - `total_export_adjustment_c_per_kwh(windows, at_local, skip_allowance: bool = False) -> Decimal` (skips windows with `daily_allowance_kwh > 0` when `skip_allowance`)
  - `allowance_periods(cycles, site_timezone, start, end) -> list[BillingPeriod]`
  - `period_containing(periods, at) -> BillingPeriod | None`
  - `periods_for_allowance_windows(db, site_id, windows_by_tariff, site_timezone, start, end) -> list[BillingPeriod]` (empty when no window has an allowance)
  - `allowance_cost_deltas(db, site_id, periods, start, end, site_timezone, tariffs, windows_by_tariff) -> list[tuple[datetime, Decimal]]` (cost deltas in cents, instants inside `[start, end)`)
  - `marginal_adjustment_fn(db, site_id, site_timezone, tariffs, windows_by_tariff, start, end, now) -> Callable[[datetime], Decimal]` (total `adjustment_c_per_kwh` for the next kWh exported at the instant)

- [ ] **Step 1: Write the failing tests**

Create `core-api/tests/test_export_allowance_data.py`:

```python
from __future__ import annotations

import datetime
import decimal

from core_api.export_allowance_data import allowance_periods, period_containing
from core_api.jobs.demand_config import total_export_adjustment_c_per_kwh
from core_api.models.billing import BillingCycle, ExportAdjustmentWindow
from tests.conftest import sydney_local

TZ_NAME = "Australia/Sydney"


def _cycle(effective_from, anchor_day=1) -> BillingCycle:
    return BillingCycle(site_id=1, effective_from=effective_from, cycle_months=1, anchor_day=anchor_day)


def _window(allowance: str, adjustment: str = "-3") -> ExportAdjustmentWindow:
    return ExportAdjustmentWindow(
        site_tariff_id=1,
        label="w",
        months=list(range(1, 13)),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time(0, 0),
        end_time=datetime.time(23, 59),
        adjustment_c_per_kwh=decimal.Decimal(adjustment),
        daily_allowance_kwh=decimal.Decimal(allowance),
    )


def test_allowance_periods_returns_the_period_overlapping_the_range():
    cycles = [_cycle(sydney_local(2026, 1, 1))]

    periods = allowance_periods(cycles, TZ_NAME, sydney_local(2026, 1, 15), sydney_local(2026, 1, 16))

    assert [(p.start, p.end) for p in periods] == [(sydney_local(2026, 1, 1), sydney_local(2026, 2, 1))]


def test_allowance_periods_includes_the_partial_period_before_the_first_anchor_day():
    cycles = [_cycle(sydney_local(2026, 1, 10))]

    periods = allowance_periods(cycles, TZ_NAME, sydney_local(2026, 1, 15), sydney_local(2026, 1, 16))

    assert [(p.start, p.end) for p in periods] == [(sydney_local(2026, 1, 1), sydney_local(2026, 2, 1))]


def test_allowance_periods_splits_a_range_that_crosses_a_boundary():
    cycles = [_cycle(sydney_local(2026, 1, 1))]

    periods = allowance_periods(cycles, TZ_NAME, sydney_local(2026, 1, 31), sydney_local(2026, 2, 2))

    assert [p.start for p in periods] == [sydney_local(2026, 1, 1), sydney_local(2026, 2, 1)]


def test_allowance_periods_is_empty_without_a_billing_cycle():
    assert allowance_periods([], TZ_NAME, sydney_local(2026, 1, 15), sydney_local(2026, 1, 16)) == []


def test_period_containing_finds_the_period_or_none():
    periods = allowance_periods([_cycle(sydney_local(2026, 1, 1))], TZ_NAME, sydney_local(2026, 1, 1), sydney_local(2026, 3, 1))

    assert period_containing(periods, sydney_local(2026, 2, 1)).start == sydney_local(2026, 2, 1)
    assert period_containing(periods, sydney_local(2025, 12, 31)) is None


def test_total_export_adjustment_skips_allowance_windows_on_request():
    at_local = sydney_local(2026, 1, 1, 12).astimezone(datetime.timezone.utc)
    windows = [_window("0", "5"), _window("10", "-3")]

    assert total_export_adjustment_c_per_kwh(windows, at_local) == decimal.Decimal("2")
    assert total_export_adjustment_c_per_kwh(windows, at_local, skip_allowance=True) == decimal.Decimal("5")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_export_allowance_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core_api.export_allowance_data'`.

- [ ] **Step 3: Implement**

`jobs/demand_config.py`: replace `total_export_adjustment_c_per_kwh` and add the public pattern wrapper after it.

```python
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
        if _in_time_pattern(window, at_local):
            total += window.adjustment_c_per_kwh
    return total
```

Create `core-api/core_api/export_allowance_data.py`:

```python
"""Database side of the export allowance: billing periods, grid export, and
the two results the rest of the app needs -- cost deltas for cost.py and a
marginal adjustment function for the price endpoint. The arithmetic itself is
in export_allowance.py."""

from __future__ import annotations

import datetime
import decimal
from typing import Callable

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
)
from core_api.models.billing import BillingCycle, ExportAdjustmentWindow, SiteTariff
from core_api.models.readings import EnergyMetric, EnergyReading
from core_api.models.sources import Source, SourceKind
from core_api.time_utils import as_utc, site_tz

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
        if tariff is not None and tariff.id == tariff_id and in_export_window_pattern(window, ts.astimezone(tz)):
            counted[ts] = kwh
    return counted


def allowance_cost_deltas(
    db: Session,
    site_id: int,
    periods: list[BillingPeriod],
    start: datetime.datetime,
    end: datetime.datetime,
    site_timezone: str,
    tariffs: list[SiteTariff],
    windows_by_tariff: dict[int, list[ExportAdjustmentWindow]],
) -> list[tuple[datetime.datetime, decimal.Decimal]]:
    """Cost changes in cents, at instants inside [start, end), from windows
    with an allowance. Exports are read from each period's start so a range
    that begins mid-period still sees the exports before it."""
    tz = site_tz(site_timezone)
    start_utc, end_utc = as_utc(start), as_utc(end)
    deltas: list[tuple[datetime.datetime, decimal.Decimal]] = []
    for period in periods:
        exports = _grid_export_kwh_by_minute(db, site_id, period.start, min(period.end, end_utc))
        for tariff_id, windows in windows_by_tariff.items():
            for window in (w for w in windows if w.daily_allowance_kwh > 0):
                counted = _window_export_kwh(window, tariff_id, tariffs, exports, site_timezone)
                steps = charged_kwh_steps(window.daily_allowance_kwh, period.start, period.end, counted, tz)
                for ts, delta in charge_cost_deltas_c(window.adjustment_c_per_kwh, steps):
                    if start_utc <= ts < end_utc:
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
) -> dict[tuple[datetime.datetime, int], CumulativeExport]:
    """Per (period start, window id): the window's exports up to `now`."""
    cumulative = {}
    for period in periods:
        exports = _grid_export_kwh_by_minute(db, site_id, period.start, min(period.end, as_utc(now)))
        for tariff_id, windows in windows_by_tariff.items():
            for window in (w for w in windows if w.daily_allowance_kwh > 0):
                counted = _window_export_kwh(window, tariff_id, tariffs, exports, site_timezone)
                cumulative[(period.start, window.id)] = CumulativeExport(counted)
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
            exported = cumulative[(period.start, window.id)].before(at_utc)
            if in_export_window_pattern(window, at_local) and is_charging_next_kwh(
                window.daily_allowance_kwh, period.start, at_utc, exported, tz
            ):
                total += window.adjustment_c_per_kwh
        return total

    return adjustment_at
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_export_allowance_data.py tests/test_cost.py -v`
Expected: all PASS (test_cost is included to confirm `total_export_adjustment_c_per_kwh` still behaves the same with `skip_allowance` off; windows built in `test_cost.py` get `daily_allowance_kwh` 0 from the column default).

- [ ] **Step 5: Commit**

```bash
git add core-api/core_api/jobs/demand_config.py core-api/core_api/export_allowance_data.py core-api/tests/test_export_allowance_data.py
git commit -m "feat(core-api): load billing periods and export for the allowance"
```

---

### Task 4: Cost integration

**Files:**
- Modify: `core-api/core_api/cost.py` (`_minute_costs`, imports)
- Test: `core-api/tests/test_cost.py`

**Interfaces:**
- Consumes (Task 3): `periods_for_allowance_windows`, `period_containing`, `allowance_cost_deltas`, `total_export_adjustment_c_per_kwh(..., skip_allowance)`.
- Produces: `compute_cost` and `compute_cost_series` include allowance charges and refunds.

- [ ] **Step 1: Write the failing tests**

In `core-api/tests/test_cost.py`, change the `_make_export_adjustment` helper to take `daily_allowance_kwh`:

```python
def _make_export_adjustment(
    db, tariff, adjustment_c_per_kwh="5.0", months=None, days_of_week=None, start="00:00", end="23:59",
    daily_allowance_kwh="0",
) -> ExportAdjustmentWindow:
    window = ExportAdjustmentWindow(
        site_tariff_id=tariff.id,
        label="Test adjustment",
        months=months or list(range(1, 13)),
        days_of_week=days_of_week if days_of_week is not None else [0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time.fromisoformat(start),
        end_time=datetime.time.fromisoformat(end),
        adjustment_c_per_kwh=decimal.Decimal(adjustment_c_per_kwh),
        daily_allowance_kwh=decimal.Decimal(daily_allowance_kwh),
    )
    db.add(window)
    db.commit()
    return window
```

Add these after `test_compute_cost_series_includes_export_adjustment`:

```python
def _add_export_kwh(db, energy_source, pricing_source, ts, kwh="1", with_price=True):
    """One minute of grid export totalling `kwh` (60000 W for one minute is 1 kWh)."""
    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=float(kwh) * 60000))
    if with_price:
        db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("0")))
    db.commit()


def _allowance_setup(db, site, adjustment="-10", allowance="1", start="00:00", end="23:59", with_cycle=True):
    """A Sydney billing period of 1 Jan - 1 Feb 2026 and one export window
    charging 10c/kWh beyond 1 kWh/day (unless overridden)."""
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    if with_cycle:
        _make_billing_cycle(db, site, sydney_local(2026, 1, 1))
    tariff = _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0")
    _make_export_adjustment(
        db, tariff, adjustment_c_per_kwh=adjustment, daily_allowance_kwh=allowance, start=start, end=end
    )
    return energy_source, pricing_source


def _three_kwh_on_day_one(db, energy_source, pricing_source):
    noon = sydney_local(2026, 1, 1, 12, 0)
    for minute in range(3):
        _add_export_kwh(db, energy_source, pricing_source, noon + datetime.timedelta(minutes=minute))


def _cost(db, site, start, end) -> decimal.Decimal:
    return compute_cost(db, site.id, start, end, method="net", site_timezone=site.timezone).total_cost_c


def test_allowance_overage_is_charged_then_cancelled_by_later_days(db, site):
    energy_source, pricing_source = _allowance_setup(db, site)
    _three_kwh_on_day_one(db, energy_source, pricing_source)
    start = sydney_local(2026, 1, 1)

    # Day 1: 3 kWh exported, 1 kWh allowed -> 2 kWh charged at 10c.
    assert _cost(db, site, start, sydney_local(2026, 1, 2)) == decimal.Decimal("20")
    # Day 2 adds 1 kWh of allowance, which cancels 1 kWh of the day-1 charge.
    assert _cost(db, site, start, sydney_local(2026, 1, 3)) == decimal.Decimal("10")
    # Day 3 cancels the rest.
    assert _cost(db, site, start, sydney_local(2026, 1, 4)) == decimal.Decimal("0")


def test_allowance_cost_over_a_sub_range_is_the_difference_of_its_ends(db, site):
    energy_source, pricing_source = _allowance_setup(db, site)
    _three_kwh_on_day_one(db, energy_source, pricing_source)

    # Charged kWh is 1 just before 12:02 and 1 at the start of 3 Jan: no change.
    assert _cost(db, site, sydney_local(2026, 1, 1, 12, 2), sydney_local(2026, 1, 3)) == decimal.Decimal("0")


def test_allowance_balance_resets_at_the_next_billing_period(db, site):
    energy_source, pricing_source = _allowance_setup(db, site)
    last_day = sydney_local(2026, 1, 31, 12, 0)
    for minute in range(40):
        _add_export_kwh(db, energy_source, pricing_source, last_day + datetime.timedelta(minutes=minute))

    # 40 kWh against 31 days of allowance leaves 9 kWh charged, and the
    # new period on 1 Feb neither refunds nor repeats it.
    assert _cost(db, site, sydney_local(2026, 1, 31), sydney_local(2026, 2, 3)) == decimal.Decimal("90")


def test_allowance_window_without_a_billing_cycle_adjusts_every_kwh(db, site):
    energy_source, pricing_source = _allowance_setup(db, site, with_cycle=False)
    _add_export_kwh(db, energy_source, pricing_source, sydney_local(2026, 1, 1, 12, 0))

    assert _cost(db, site, sydney_local(2026, 1, 1), sydney_local(2026, 1, 2)) == decimal.Decimal("10")


def test_allowance_only_counts_exports_inside_the_window_pattern(db, site):
    energy_source, pricing_source = _allowance_setup(db, site, start="10:00", end="15:00")
    noon = sydney_local(2026, 1, 1, 12, 0)
    _add_export_kwh(db, energy_source, pricing_source, noon)
    _add_export_kwh(db, energy_source, pricing_source, noon + datetime.timedelta(minutes=1))
    _add_export_kwh(db, energy_source, pricing_source, sydney_local(2026, 1, 1, 20, 0))

    # 2 kWh inside the window, 1 allowed -> 1 kWh charged. The 20:00 export is outside it.
    assert _cost(db, site, sydney_local(2026, 1, 1), sydney_local(2026, 1, 2)) == decimal.Decimal("10")


def test_allowance_counts_export_minutes_that_have_no_sell_price(db, site):
    energy_source, pricing_source = _allowance_setup(db, site)
    noon = sydney_local(2026, 1, 1, 12, 0)
    for minute in range(2):
        _add_export_kwh(db, energy_source, pricing_source, noon + datetime.timedelta(minutes=minute), with_price=False)

    result = compute_cost(
        db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 1, 2), method="net", site_timezone=site.timezone
    )

    assert result.minutes_missing_price == 2
    assert result.total_cost_c == decimal.Decimal("10")


def test_compute_cost_series_shows_the_charge_and_the_midnight_refund(db, site):
    energy_source, pricing_source = _allowance_setup(db, site)
    _three_kwh_on_day_one(db, energy_source, pricing_source)

    points = compute_cost_series(
        db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 1, 3), method="net", bucket="hour",
        site_timezone=site.timezone,
    )

    assert [(p.bucket_start, p.cost_c) for p in points] == [
        (datetime.datetime(2026, 1, 1, 1, 0, tzinfo=UTC), decimal.Decimal("20")),
        (datetime.datetime(2026, 1, 1, 13, 0, tzinfo=UTC), decimal.Decimal("-10")),
    ]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cost.py -k allowance -v`
Expected: FAIL. With no allowance logic, the window charges every exported kWh (`60` instead of `20`, and so on).

- [ ] **Step 3: Implement**

In `cost.py`, extend the imports:

```python
from core_api.export_allowance_data import allowance_cost_deltas, period_containing, periods_for_allowance_windows
```

Add this helper above `_minute_costs`:

```python
def _plain_export_adjustment_c_per_kwh(
    tariffs: list[SiteTariff],
    windows_by_tariff: dict[int, list],
    minute_utc: datetime.datetime,
    tz: zoneinfo.ZoneInfo,
    periods: list,
) -> decimal.Decimal:
    """Adjustment for windows charged per exported kWh. Windows with an
    allowance are left out when a billing period covers the minute, since
    allowance_cost_deltas charges them instead."""
    tariff = tariff_effective_at(tariffs, minute_utc)
    if tariff is None:
        return decimal.Decimal("0")
    windows = windows_by_tariff.get(tariff.id, [])
    skip_allowance = period_containing(periods, minute_utc) is not None
    return total_export_adjustment_c_per_kwh(windows, minute_utc.astimezone(tz), skip_allowance=skip_allowance)
```

In `_minute_costs`, after `export_windows_by_tariff = ...` and `tz = ...`, add:

```python
    periods = periods_for_allowance_windows(
        db, site_id, export_windows_by_tariff, site_timezone, start, end
    )
```

Replace the block that computes `adjustment_c_per_kwh` inside `if export_w:`:

```python
        if export_w:
            adjustment_c_per_kwh = _plain_export_adjustment_c_per_kwh(
                tariffs, export_windows_by_tariff, minute_utc, tz, periods
            )
```

(keep the existing sign-convention comment and the `cost_c += export_kwh * (sell_price - adjustment_c_per_kwh)` line). Before `return rows, minutes_missing_price`, add:

```python
    zero = decimal.Decimal("0")
    for ts, delta_c in allowance_cost_deltas(
        db, site_id, periods, start, end, site_timezone, tariffs, export_windows_by_tariff
    ):
        rows.append(_MinuteCost(ts=ts, cost_c=delta_c, import_kwh=zero, export_kwh=zero))
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cost.py tests/test_export_allowance.py tests/test_export_allowance_data.py -v`
Expected: all PASS, including the existing export adjustment tests.

- [ ] **Step 5: Commit**

```bash
git add core-api/core_api/cost.py core-api/tests/test_cost.py
git commit -m "feat(core-api): charge export beyond the daily allowance in cost"
```

---

### Task 5: Price graph integration

**Files:**
- Modify: `core-api/core_api/routers/query.py` (`_displayed_price_fn`, `get_prices`, imports)
- Modify: `core-api/core_api/jobs/demand_config.py` (delete `adjusted_export_price`)
- Test: `core-api/tests/test_query.py`

**Interfaces:**
- Consumes (Task 3): `marginal_adjustment_fn`.
- Produces: `/prices?channel=sell` returns the marginal price for windows with an allowance.

- [ ] **Step 1: Write the failing tests**

In `core-api/tests/test_query.py`, add imports `BillingCycle` (from `core_api.models.billing`) and `EnergyMetric, EnergyReading` (from `core_api.models.readings`) if they are not imported yet. Add after `test_get_prices_buy_channel_is_unaffected_by_export_adjustment`:

```python
def seed_allowance_window(db_url: str, exported_kwh: int) -> tuple[int, int]:
    """A UTC site with a 100-year billing period starting 2020-01-01 (so the
    test cannot straddle a period boundary), one all-day window charging 3c/kWh
    beyond 0.001 kWh/day (about 2.5 kWh accumulated by now), and `exported_kwh` kWh of
    grid export 3 hours ago. Sell price readings at 1 hour ago and 1 hour ahead
    are -10c."""
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="pq-allow-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    effective_from = datetime.datetime(2020, 1, 1, tzinfo=UTC)
    pricing = Source(site_id=site.id, kind=SourceKind.PRICING, provider="amber", config={}, effective_from=effective_from)
    energy = Source(site_id=site.id, kind=SourceKind.ENERGY, provider="envoy", config={}, effective_from=effective_from)
    session.add_all([pricing, energy])
    session.add(BillingCycle(site_id=site.id, effective_from=effective_from, cycle_months=1200, anchor_day=1))
    tariff = SiteTariff(site_id=site.id, effective_from=effective_from, service_availability_c_per_day=decimal.Decimal("0"))
    session.add(tariff)
    session.commit()
    session.add(
        ExportAdjustmentWindow(
            site_tariff_id=tariff.id,
            label="Export fee",
            months=list(range(1, 13)),
            days_of_week=[0, 1, 2, 3, 4, 5, 6],
            start_time=datetime.time(0, 0),
            end_time=datetime.time.max,
            adjustment_c_per_kwh=decimal.Decimal("-3"),
            daily_allowance_kwh=decimal.Decimal("0.001"),
        )
    )
    now = datetime.datetime.now(UTC).replace(second=0, microsecond=0)
    for minute in range(exported_kwh):
        ts = now - datetime.timedelta(hours=3) + datetime.timedelta(minutes=minute)
        session.add(EnergyReading(source_id=energy.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=60000.0))
    for offset_hours in (-1, 1):
        ts = now + datetime.timedelta(hours=offset_hours)
        session.add(PriceReading(pricing_source_id=pricing.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-10.0")))
    session.commit()
    ids = (owner.id, site.id)
    session.close()
    return ids


def _get_sell_prices(client, owner_id, site_id) -> list[float]:
    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/prices",
        params={
            "channel": "sell",
            "start": (now - datetime.timedelta(hours=2)).isoformat(),
            "end": (now + datetime.timedelta(hours=2)).isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    return [row["value"] for row in response.json()]


def test_get_prices_sell_channel_applies_charge_once_the_allowance_is_used_up(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_allowance_window(db_url, exported_kwh=5)

    # 5 kWh exported is past the allowance so far. Both the past reading and
    # the future one (which assumes no further export) show -10 - (-3) = -7.
    assert _get_sell_prices(client, owner_id, site_id) == [-7.0, -7.0]


def test_get_prices_sell_channel_shows_the_raw_price_while_the_allowance_lasts(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_allowance_window(db_url, exported_kwh=1)

    assert _get_sell_prices(client, owner_id, site_id) == [-10.0, -10.0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_query.py -k allowance -v`
Expected: FAIL. The current code charges every window unconditionally, so the second test gets `[-7.0, -7.0]`.

- [ ] **Step 3: Implement**

`routers/query.py`: in the import from `core_api.jobs.demand_config`, remove `adjusted_export_price`. Add `from core_api.export_allowance_data import marginal_adjustment_fn`. Replace `_displayed_price_fn` and update its caller:

```python
def _displayed_price_fn(
    db: Session, site: Site, channel: PriceChannel, start: datetime.datetime, end: datetime.datetime
) -> Callable[[PriceReading], decimal.Decimal]:
    """The price the dashboard shows for a reading. The export price (e.g. the
    "today's pricing" chart) must reflect any configured incentive/disincentive,
    the same as the $ cost totals already do -- not just the raw Amber feed.

    Amber stores the feed-in price with its raw sign (negative when paid), so
    the adjustment is subtracted: a positive incentive pushes the price further
    negative (more revenue) and a negative disincentive pulls it back up. For a
    window with a daily allowance this is the marginal price: the adjustment
    shows only once exports so far have used up the allowance so far."""
    if channel != PriceChannel.SELL:
        return lambda r: decimal.Decimal(str(r.value))
    tariffs = site_tariffs_for_site(db, site.id)
    windows_by_tariff = export_adjustment_windows_by_tariff(db, site.id)
    now = datetime.datetime.now(datetime.timezone.utc)
    adjustment_at = marginal_adjustment_fn(db, site.id, site.timezone, tariffs, windows_by_tariff, start, end, now)
    return lambda r: decimal.Decimal(str(r.value)) - adjustment_at(r.ts)
```

In `get_prices`: `value_of = _displayed_price_fn(db, site, channel, start, end)`.

`jobs/demand_config.py`: delete `adjusted_export_price` (its only caller was `_displayed_price_fn`). If `site_tz` or `tariff_effective_at` become unused imports there, leave them: both are still used elsewhere in the module.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest -q`
Expected: the whole core-api suite PASSES, including the two earlier export adjustment price tests.

- [ ] **Step 5: Commit**

```bash
git add core-api/core_api/routers/query.py core-api/core_api/jobs/demand_config.py core-api/tests/test_query.py
git commit -m "feat(core-api): show the marginal export price for allowance windows"
```

---

### Task 6: Billing page and docs

**Files:**
- Modify: `webapp/src/billing/useBilling.ts` (types)
- Modify: `webapp/src/pages/BillingPage.tsx` (`ExportAdjustmentWindowsList`, `ExportAdjustmentWindowForm`)
- Modify: `webapp/src/pages/BillingPage.test.tsx`
- Modify: `webapp/src/docs/07-billing.md`, `webapp/src/docs/10-glossary.md`

**Interfaces:**
- Consumes (Task 1): API field `daily_allowance_kwh`.

- [ ] **Step 1: Write the failing tests**

In `BillingPage.test.tsx`: add `daily_allowance_kwh: "0.0000",` to the `EXPORT_ADJUSTMENT` constant, and add `daily_allowance_kwh: 0,` to the `toEqual` body of "creates an export adjustment window under an existing tariff". Add these tests after "renders a disincentive export adjustment window's negative rate without a double sign":

```tsx
  it("renders an export adjustment window's daily allowance", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/export-adjustments") {
        return Promise.resolve([
          { ...EXPORT_ADJUSTMENT, label: "Export fee", adjustment_c_per_kwh: "-3", daily_allowance_kwh: "10.0000" },
        ]);
      }
      if (path === "/api/v1/sites/7/billing-cycle") {
        return Promise.resolve([{ id: 1, site_id: 7, effective_from: "2026-01-01T00:00:00Z", effective_to: null, cycle_months: 1, anchor_day: 1 }]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    const item = await screen.findByText("Export fee");
    expect(item.closest("li")).toHaveTextContent(/10 kWh\/day allowance/);
    expect(screen.queryByText(/allowance is ignored/)).not.toBeInTheDocument();
  });

  it("warns that an allowance is ignored while no billing cycle is set", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/export-adjustments") {
        return Promise.resolve([{ ...EXPORT_ADJUSTMENT, daily_allowance_kwh: "10.0000" }]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    expect(await screen.findByText(/allowance is ignored until you set a billing cycle/i)).toBeInTheDocument();
  });

  it("sends the daily allowance when creating an export adjustment window", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    await userEvent.type(screen.getByPlaceholderText("Adjustment label"), "Export fee");
    await userEvent.type(screen.getByPlaceholderText("Export months (e.g. 12,1,2)"), "1");
    await userEvent.type(screen.getByPlaceholderText("Export days of week (0=Mon .. 6=Sun)"), "0");
    await userEvent.type(screen.getByPlaceholderText("Export start time (HH:MM)"), "00:00");
    await userEvent.type(screen.getByPlaceholderText("Export end time (HH:MM)"), "23:59");
    await userEvent.type(screen.getByPlaceholderText("Adjustment (+incentive / -disincentive, cents/kWh)"), "-3");
    await userEvent.type(screen.getByPlaceholderText("Daily allowance (kWh, 0 = none)"), "10");
    await userEvent.click(screen.getByText("Add export adjustment"));
    const postCall = vi
      .mocked(apiFetch)
      .mock.calls.find(
        ([path, options]) =>
          path === "/api/v1/sites/7/tariffs/1/export-adjustments" && options?.method === "POST",
      );
    expect(JSON.parse(String(postCall?.[1]?.body)).daily_allowance_kwh).toBe(10);
  });
```

- [ ] **Step 2: Run to verify it fails**

Run (from `webapp/`): `npx vitest run src/pages/BillingPage.test.tsx`
Expected: FAIL on the three new tests and the updated create test.

- [ ] **Step 3: Implement**

`useBilling.ts`: in `ExportAdjustmentWindow` add after `adjustment_c_per_kwh: string;`:

```ts
  // Decimal string. kWh of exports per day, accumulating through the billing
  // period, that are exempt from the adjustment. "0.0000" means no allowance.
  daily_allowance_kwh: string;
```

In `CreateExportAdjustmentWindowInput` add `daily_allowance_kwh: number;`.

`BillingPage.tsx`, `ExportAdjustmentWindowsList` (add `const { data: cycles } = useBillingCycles(siteId);` to the component, and render the allowance and note):

```tsx
function ExportAdjustmentWindowsList({ siteId, tariffId }: { siteId: number; tariffId: number }) {
  const { data: windows } = useExportAdjustmentWindows(siteId, tariffId);
  const { data: cycles } = useBillingCycles(siteId);
  const noBillingCycle = cycles !== undefined && cycles.length === 0;

  return (
    <ul className="space-y-2">
      {(windows ?? []).map((w) => {
        const allowance = Number(w.daily_allowance_kwh);
        return (
          <li key={w.id} className="space-y-0.5 rounded-md border border-line/60 px-3 py-1.5">
            <div className="flex items-baseline justify-between gap-2 text-sm">
              <span className="text-ink">{w.label}</span>
              <span className="font-display tabular-nums text-ink">{formatSignedCents(w.adjustment_c_per_kwh)}c/kWh</span>
            </div>
            <p className="text-xs text-ink-muted">
              {formatTime(w.start_time)}–{formatTime(w.end_time)} · {formatDaysOfWeek(w.days_of_week)} ·{" "}
              {formatMonths(w.months)}
              {allowance > 0 && ` · ${allowance} kWh/day allowance`}
            </p>
            {allowance > 0 && noBillingCycle && (
              <p className="text-xs text-ink-muted">
                The allowance is ignored until you set a billing cycle, so every exported kWh is adjusted.
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
```

`ExportAdjustmentWindowForm`: add state and input, and send the value.

```tsx
  const [allowance, setAllowance] = useState("");
```

in `createWindow.mutate({...})` add `daily_allowance_kwh: Number(allowance || "0"),` after `adjustment_c_per_kwh`, and add this input after the Adjustment input:

```tsx
        <input
          value={allowance}
          onChange={(e) => setAllowance(e.target.value)}
          placeholder="Daily allowance (kWh, 0 = none)"
          aria-label="Daily allowance"
          inputMode="decimal"
          className={inputClass}
        />
```

`07-billing.md`: replace the `Adjustment` row and the sentence after the table in the **Export adjustments** section with:

```markdown
| **Adjustment** | Cents per kWh. Positive is an incentive, negative is a disincentive. |
| **Daily allowance** | kWh of exports per day that are not adjusted. Leave it at 0 if there is none. |

Click **Add export adjustment**. If several adjustments cover the same minute, they add together.

#### Daily allowance

Some export tariffs charge a fee per kWh exported but include a free amount each day, for example "10 kWh a day free, then 3c/kWh". Enter `-3` as the adjustment and `10` as the daily allowance.

The allowance builds up through the billing period. On day 3 you have 30 kWh of allowance in total, whatever you exported on days 1 and 2. Allowance you have not used also counts backwards: if you export 30 kWh on day 1 you are charged for 20 kWh, and that charge drops to 10 kWh on day 2 and to 0 on day 3 if you export nothing more. The count starts again at the start of each [billing cycle](#billing-cycle).

- Only exports inside the adjustment's months, days and times use up the allowance.
- The allowance counts grid export, the same as the [net](06-dashboard.md#net-and-gross-cost) cost.
- Without a billing cycle the allowance is ignored and every exported kWh is adjusted.
- On the export price graphs the adjustment appears only once your exports so far have used up the allowance so far. For times in the future the graph assumes you export nothing more.
```

Check the heading anchor for the billing cycle section in `07-billing.md` (`grep -n "^##" webapp/src/docs/07-billing.md`) and change `#billing-cycle` to match it.

`10-glossary.md`: add a row after **Export adjustment**:

```markdown
| **Daily allowance** | Part of an [export adjustment](07-billing.md#export-adjustments): kWh of exports per day, building up through the billing cycle, that are not adjusted. |
```

- [ ] **Step 4: Run to verify it passes**

Run (from `webapp/`): `npx vitest run && npx tsc -b && npx oxlint`
Expected: all tests PASS, no type errors, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add webapp/src/billing/useBilling.ts webapp/src/pages/BillingPage.tsx webapp/src/pages/BillingPage.test.tsx webapp/src/docs/07-billing.md webapp/src/docs/10-glossary.md
git commit -m "feat(webapp): add a daily allowance to export adjustments"
```
