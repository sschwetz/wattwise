# Background Jobs: Demand Tariff Billing + Retention Rollup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the three in-process periodic jobs (DemandInterval derivation, DemandCharge estimation/finalization, retention rollup) that make the existing `DemandInterval`/`DemandCharge`/`*Rollup5m` schema live, plus the schema corrections the design spec identified.

**Architecture:** All logic lives under a new `core_api/jobs/` package as plain functions taking a `Session` and a `now: datetime` — fully unit-testable without a scheduler. A thin `core_api/scheduler.py` wraps them in APScheduler (`BackgroundScheduler`) and is started/stopped from `main.py`'s existing `lifespan`. Shared effective-dated-config lookups (tariff/window at a timestamp, billing-period walking) live in their own modules so Job 1 and Job 2 don't duplicate them.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, FastAPI, APScheduler 3.x (new dependency), pytest, `zoneinfo` (stdlib).

**Spec:** `docs/superpowers/specs/2026-10-06-background-jobs-design.md`

## Global Constraints

- `DemandInterval` is fixed at 5-minute granularity (matches `EnergyReadingRollup5m`'s convention).
- `rolling_sum_w` = sum of the slot's own zeroed-or-raw contribution plus the preceding `N − 1` slots' contributions, `N = measurement_interval_minutes / 5`.
- A slot's contribution is its real `raw_5min_avg_w` only if the slot falls inside a demand window; otherwise `0`.
- `DemandWindow.rate_c_per_kw_per_day` is **$/kW/day** (cents, per existing `Numeric` convention used elsewhere — see `charge_amount` below), not $/kWh.
- `charge_amount` (cents) = `peak_demand_kW × rate_c_per_kw_per_day × billing_period_days`; `billing_period_days` is computed on the fly, never persisted.
- All three jobs run in-process via APScheduler inside Core API — no new service.
- Every job is idempotent by construction (upsert on the natural/unique key); a crash mid-run or a missed run must self-heal on the next run.
- Non-retroactive rule: any lookup of "which `SiteTariff`/`DemandWindow`/`BillingCycle` applies" must use whatever was effective **at the timestamp in question**, never whatever is configured now.
- Raw retention is 1 year (`core_api.readings_query.RAW_RETENTION`) — reuse that constant, don't redefine it.
- SQLite silently drops `tzinfo` on write; any datetime read back from an ORM attribute must be re-attached to UTC before comparing against an aware value (see `core_api/time_utils.py` introduced in Task 2).
- **`DemandWindow.start_time`/`end_time`/`months`/`days_of_week` and `BillingCycle.anchor_day` are all site-*local* wall-clock concepts, never UTC.** Every instant (`EnergyReading.ts`, `DemandInterval.interval_start`, "now") is stored/passed as UTC; converting it to the site's local time via `at.astimezone(zoneinfo.ZoneInfo(site.timezone))` is mandatory immediately before any comparison against one of those fields, and the result must be converted back to UTC before it's written to or compared against any persisted column (`DemandCharge.billing_period_start`/`end`, `DemandInterval.interval_start`). Getting this backwards silently works for a UTC-timezone site and silently breaks for every other one — see Tasks 2–5's tests, which deliberately use a real `Australia/Sydney` test site rather than a UTC one, because a UTC test fixture can't distinguish "converts correctly" from "never converts at all."

## Review Focus

- A billing period with genuinely zero demand-window usage anywhere in it must produce `charge_amount == 0`, not a crash or a skipped row (Task 5).
- A `DemandWindow`'s day/time pattern boundary (`start_time`/`end_time` inclusive, month/day-of-week sets) must be evaluated correctly at the exact open/close instant **in the site's local timezone**, not off-by-one and not against the raw UTC clock (Task 4).
- Re-running Job 1 over an already-derived range must not double-count or change any already-written `rolling_sum_w` (Task 4).
- A site with no `SiteTariff`/`DemandWindow` configured at all must not crash either job — Job 1 must still derive `DemandInterval` rows (with `rolling_sum_w` effectively per-slot, no window), and Job 2 must skip billing periods for that site entirely rather than writing a bogus zero-rate charge (Tasks 4–5).
- Retention rollup must correctly separate `last_value` metrics (e.g. `battery_soc_pct`) from `average` metrics per bucket, and must never roll up or touch `PriceInterval` rows (Task 6).
- A site whose `BillingCycle.anchor_day`-based period boundary, once computed in local time, gets handed to a UTC-typed column or a UTC comparison must have that boundary converted to a UTC instant first, not passed through in its local `tzinfo` representation (Task 3) — the bug class this plan was specifically corrected for after review.

---

## Task 1: Schema correction — rename/drop billing columns

**Files:**
- Modify: `core-api/core_api/models/billing.py`
- Modify: `core-api/core_api/schemas.py`
- Create: `core-api/alembic/versions/a1c9f2e4b7d3_correct_demand_tariff_units.py`
- Modify: `core-api/tests/test_billing.py`
- Modify: `core-api/tests/test_schema_constraints.py`
- Modify: `core-api/tests/test_query.py`

**Interfaces:**
- Produces: `DemandWindow.rate_c_per_kw_per_day` (`Numeric(10,4)`, was `rate_c_per_kwh`), `DemandInterval.rolling_sum_w` (`Float`, was `avg_import_w`), `DemandCharge` without `demand_window_hours`. Every later task reads/writes these exact names.

This is a pure rename/drop with no new behavior, so there's no new test to write first — instead: update production code, then update the existing tests that reference the old names, then confirm the full suite is green.

- [ ] **Step 1: Rename/drop the columns in the model**

In `core-api/core_api/models/billing.py`:

```python
class DemandWindow(Base):
    __tablename__ = "demand_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_tariff_id: Mapped[int] = mapped_column(ForeignKey("site_tariffs.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    months: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    days_of_week: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    start_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    rate_c_per_kw_per_day: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    measurement_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)


class DemandInterval(Base):
    """Derived from EnergyReading.grid_import_w by a periodic job. PK is the
    natural key so re-running the job is a safe upsert.

    rolling_sum_w is the sum of the last N 5-minute slots' contributions
    (each zeroed unless the slot falls inside a demand window), not a single
    slot's average — see 'Granularity' in the background-jobs design spec."""

    __tablename__ = "demand_intervals"

    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), primary_key=True)
    interval_start: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    interval_end: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rolling_sum_w: Mapped[float] = mapped_column(Float, nullable=False)
    is_demand_window: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rate_applied: Mapped[decimal.Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)


class DemandCharge(Base):
    """One finalized row per site per billing period — an invoice line item,
    stable once written. References its peak DemandInterval by a composite FK
    on (site_id, peak_interval_start) rather than a surrogate id, since
    DemandInterval's PK is the natural key above. billing_period_days is
    computed on the fly from billing_period_start/end, never stored."""

    __tablename__ = "demand_charges"
    __table_args__ = (
        UniqueConstraint("site_id", "billing_period_start", name="uq_demand_charges_site_period"),
        ForeignKeyConstraint(
            ["site_id", "peak_interval_start"],
            ["demand_intervals.site_id", "demand_intervals.interval_start"],
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    billing_period_start: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    billing_period_end: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    peak_interval_start: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    peak_avg_import_w: Mapped[float] = mapped_column(Float, nullable=False)
    rate_applied: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    charge_amount: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

(`SiteTariff`/`BillingCycle` above them in the file are unchanged — leave as-is.)

- [ ] **Step 2: Write the Alembic migration**

Create `core-api/alembic/versions/a1c9f2e4b7d3_correct_demand_tariff_units.py`:

```python
"""correct demand tariff units: rename columns, drop demand_window_hours

Revision ID: a1c9f2e4b7d3
Revises: d7a583fd431c
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c9f2e4b7d3'
down_revision: Union[str, Sequence[str], None] = 'd7a583fd431c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('demand_windows') as batch_op:
        batch_op.alter_column(
            'rate_c_per_kwh', new_column_name='rate_c_per_kw_per_day', existing_type=sa.Numeric(10, 4)
        )
    with op.batch_alter_table('demand_intervals') as batch_op:
        batch_op.alter_column(
            'avg_import_w', new_column_name='rolling_sum_w', existing_type=sa.Float()
        )
    with op.batch_alter_table('demand_charges') as batch_op:
        batch_op.drop_column('demand_window_hours')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('demand_charges') as batch_op:
        batch_op.add_column(sa.Column('demand_window_hours', sa.Numeric(10, 4), nullable=False, server_default='0'))
    with op.batch_alter_table('demand_intervals') as batch_op:
        batch_op.alter_column(
            'rolling_sum_w', new_column_name='avg_import_w', existing_type=sa.Float()
        )
    with op.batch_alter_table('demand_windows') as batch_op:
        batch_op.alter_column(
            'rate_c_per_kw_per_day', new_column_name='rate_c_per_kwh', existing_type=sa.Numeric(10, 4)
        )
```

- [ ] **Step 3: Update the Pydantic schemas**

In `core-api/core_api/schemas.py`, change:

```python
class DemandWindowCreate(BaseModel):
    label: str
    months: list[int]
    days_of_week: list[int]
    start_time: datetime.time
    end_time: datetime.time
    rate_c_per_kw_per_day: decimal.Decimal
    measurement_interval_minutes: int = 30
```

and remove `demand_window_hours` from `DemandChargeOut`:

```python
class DemandChargeOut(BaseModel):
    id: int
    site_id: int
    billing_period_start: datetime.datetime
    billing_period_end: datetime.datetime
    peak_interval_start: datetime.datetime
    peak_avg_import_w: float
    rate_applied: decimal.Decimal
    charge_amount: decimal.Decimal
    is_estimated: bool

    model_config = {"from_attributes": True}
```

(`routers/billing.py` and `routers/query.py` need no changes — they pass fields through by name via `**payload.model_dump()` / `response_model`.)

- [ ] **Step 4: Update existing tests that reference the old names**

In `core-api/tests/test_billing.py`, change the `demand-windows` POST payload's key:

```python
            "rate_c_per_kw_per_day": "45.0",
```

In `core-api/tests/test_schema_constraints.py`:

```python
def make_demand_charge(db, site, peak_start, **overrides):
    fields = dict(
        site_id=site.id,
        billing_period_start=datetime.datetime(2026, 1, 1, tzinfo=UTC),
        billing_period_end=datetime.datetime(2026, 2, 1, tzinfo=UTC),
        peak_interval_start=peak_start,
        peak_avg_import_w=4000.0,
        rate_applied=12.5,
        charge_amount=100.0,
    )
    fields.update(overrides)
    charge = DemandCharge(**fields)
    db.add(charge)
    return charge
```

(removes the `demand_window_hours=2` line) and both `DemandInterval(... avg_import_w=4000.0 ...)` constructions become `DemandInterval(... rolling_sum_w=4000.0 ...)`.

In `core-api/tests/test_query.py`, the `DemandInterval(... avg_import_w=5000.0 ...)` becomes `rolling_sum_w=5000.0`, and the `DemandCharge(...)` construction drops its `demand_window_hours=decimal.Decimal("2"),` line.

- [ ] **Step 5: Run the full suite and confirm green**

Run: `cd core-api && uv run pytest -q`
Expected: all tests pass (this is a mechanical rename; no behavior changed).

- [ ] **Step 6: Commit**

```bash
git add core-api/core_api/models/billing.py core-api/core_api/schemas.py \
  core-api/alembic/versions/a1c9f2e4b7d3_correct_demand_tariff_units.py \
  core-api/tests/test_billing.py core-api/tests/test_schema_constraints.py core-api/tests/test_query.py
git commit -m "fix: correct demand tariff schema units (rate c/kW/day, drop stored window-hours)"
```

---

## Task 2: Shared effective-config lookups (`demand_config.py`) + `time_utils.as_utc`

**Files:**
- Create: `core-api/core_api/time_utils.py`
- Create: `core-api/core_api/jobs/__init__.py`
- Create: `core-api/core_api/jobs/demand_config.py`
- Create: `core-api/tests/jobs/__init__.py`
- Create: `core-api/tests/jobs/test_demand_config.py`
- Modify: `core-api/core_api/routers/sources.py` (consolidate its local `_as_utc` into the new shared helper — a third caller is about to need the same 6-line function, which crosses this codebase's own "duplicated more than twice" threshold)
- Modify: `core-api/tests/conftest.py` (add a shared `sydney_local` test helper — Tasks 2, 4, and 5 all need a non-UTC site to actually exercise local-time conversion, not a fixture that dodges it)

**Interfaces:**
- Produces: `as_utc(value: datetime.datetime) -> datetime.datetime`; `effective_site_tariff_at(db, site_id, at) -> SiteTariff | None`; `first_matching_demand_window(db, site_id, at, site_timezone) -> DemandWindow | None`; `any_configured_demand_window(db, site_id, at) -> DemandWindow | None`; `energy_source_ids_for_site(db, site_id) -> list[int]`; `pricing_source_ids_for_site(db, site_id) -> list[int]`; `price_interval_flags_demand(db, pricing_source_ids, bucket_start, bucket_end) -> bool`; `sydney_local(year, month, day, hour=0, minute=0) -> datetime.datetime` (test helper). Tasks 3–5 import all of these.
- Consumes: `DemandWindow.rate_c_per_kw_per_day` from Task 1.

**Why a real non-UTC site, not a UTC-timezone test fixture:** conftest's shared `site` fixture is `Australia/Sydney`, deliberately not UTC, so that every `DemandWindow` day/time pattern check in this plan's tests actually exercises `at.astimezone(ZoneInfo(site_timezone))`. A test site pinned to UTC would make that conversion a silent no-op — it would pass identically whether or not the conversion code was even called, hiding exactly the class of bug where a wall-clock demand-window boundary (defined in the site's local time, per the design spec) gets compared against the wrong instant. Every test below that checks a specific hour-of-day pattern match constructs its timestamps via `sydney_local(...)` (a UTC-represented instant computed from Sydney wall-clock components) rather than a bare `datetime.datetime(..., tzinfo=UTC)`.

- [ ] **Step 1: Write the failing tests**

First, add the shared helper to `core-api/tests/conftest.py`. Add `import zoneinfo` to its imports, and after the existing `UTC = datetime.timezone.utc` line at the bottom of the file, add:

```python
SYDNEY = zoneinfo.ZoneInfo("Australia/Sydney")


def sydney_local(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime.datetime:
    """The UTC instant corresponding to this Sydney local wall-clock time --
    lets a test express '3pm local' while still passing around the
    UTC-represented values these jobs expect (see core_api.time_utils.as_utc
    for why representation matters here, not just absolute instant: SQLite
    silently drops tzinfo on write, so a non-UTC-represented value would
    store/compare the wrong wall-clock numbers once it touches the DB)."""
    return datetime.datetime(year, month, day, hour, minute, tzinfo=SYDNEY).astimezone(UTC)
```

Then write the test file.

Create `core-api/tests/jobs/__init__.py` (empty file).

Create `core-api/tests/jobs/test_demand_config.py`:

```python
from __future__ import annotations

import datetime

from core_api.jobs.demand_config import (
    any_configured_demand_window,
    effective_site_tariff_at,
    energy_source_ids_for_site,
    first_matching_demand_window,
    pricing_source_ids_for_site,
    price_interval_flags_demand,
)
from core_api.models.billing import DemandWindow, SiteTariff
from core_api.models.pricing import PriceChannel, PriceInterval
from core_api.models.sources import Source, SourceKind
from core_api.time_utils import as_utc
from tests.conftest import UTC, sydney_local

# conftest's `site` fixture is Australia/Sydney -- deliberately kept (not
# overridden to UTC) so these tests exercise the real local<->UTC
# conversion in demand_config.py, not a no-op. AEDT (Sydney's southern
# summer offset) is UTC+11.


def make_tariff(db, site, effective_from, effective_to=None, rate="40.0"):
    tariff = SiteTariff(
        site_id=site.id, effective_from=effective_from, effective_to=effective_to,
        service_availability_c_per_day="80.0",
    )
    db.add(tariff)
    db.commit()
    return tariff


def make_window(db, tariff, label="Peak", months=None, days_of_week=None, start="15:00", end="21:00", rate="45.0", minutes=30):
    window = DemandWindow(
        site_tariff_id=tariff.id,
        label=label,
        months=months or list(range(1, 13)),
        days_of_week=days_of_week if days_of_week is not None else [0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time.fromisoformat(start),
        end_time=datetime.time.fromisoformat(end),
        rate_c_per_kw_per_day=rate,
        measurement_interval_minutes=minutes,
    )
    db.add(window)
    db.commit()
    return window


def test_as_utc_attaches_utc_to_naive_value():
    naive = datetime.datetime(2026, 1, 1, 12, 0)
    assert as_utc(naive) == datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_as_utc_leaves_aware_value_unchanged():
    aware = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert as_utc(aware) is aware or as_utc(aware) == aware


def test_effective_site_tariff_at_picks_non_retroactive_tariff(db, site):
    old = make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC), datetime.datetime(2026, 6, 1, tzinfo=UTC))
    new = make_tariff(db, site, datetime.datetime(2026, 6, 1, tzinfo=UTC), None)

    assert effective_site_tariff_at(db, site.id, datetime.datetime(2026, 3, 1, tzinfo=UTC)).id == old.id
    assert effective_site_tariff_at(db, site.id, datetime.datetime(2026, 9, 1, tzinfo=UTC)).id == new.id


def test_first_matching_demand_window_at_pattern_boundary_is_inclusive(db, site):
    """`at` is passed in as a UTC instant (as the job code always does); the
    window's 15:00-21:00 bounds are Sydney *local* time, so this only
    passes if first_matching_demand_window actually converts before
    comparing -- a UTC-timezone site fixture couldn't catch a regression
    here, since the conversion would be a no-op either way."""
    tariff = make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC))
    make_window(db, tariff, start="15:00", end="21:00")

    at_open = sydney_local(2026, 1, 5, 15, 0)
    at_close = sydney_local(2026, 1, 5, 21, 0)
    just_before = sydney_local(2026, 1, 5, 14, 59)

    assert first_matching_demand_window(db, site.id, at_open, site.timezone) is not None
    assert first_matching_demand_window(db, site.id, at_close, site.timezone) is not None
    assert first_matching_demand_window(db, site.id, just_before, site.timezone) is None


def test_first_matching_demand_window_rejects_wrong_month_or_day(db, site):
    tariff = make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC))
    make_window(db, tariff, months=[12, 1, 2], days_of_week=[0, 1, 2, 3, 4])

    # 2026-01-05 is a Monday in January (Sydney local date) -> matches.
    assert first_matching_demand_window(db, site.id, sydney_local(2026, 1, 5, 17, 0), site.timezone)
    # 2026-07-05 is in July -> wrong month.
    assert first_matching_demand_window(db, site.id, sydney_local(2026, 7, 5, 17, 0), site.timezone) is None
    # 2026-01-10 is a Saturday -> wrong day-of-week.
    assert first_matching_demand_window(db, site.id, sydney_local(2026, 1, 10, 17, 0), site.timezone) is None


def test_first_matching_demand_window_first_match_wins(db, site):
    tariff = make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC))
    first = make_window(db, tariff, label="First", rate="10.0")
    make_window(db, tariff, label="Second", rate="99.0")

    found = first_matching_demand_window(db, site.id, sydney_local(2026, 1, 5, 17, 0), site.timezone)
    assert found.id == first.id


def test_any_configured_demand_window_ignores_pattern(db, site):
    tariff = make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC))
    window = make_window(db, tariff, months=[6], days_of_week=[0])  # won't match a January Monday evening

    found = any_configured_demand_window(db, site.id, sydney_local(2026, 1, 5, 17, 0))
    assert found.id == window.id


def test_no_tariff_configured_returns_none(db, site):
    assert effective_site_tariff_at(db, site.id, datetime.datetime(2026, 1, 1, tzinfo=UTC)) is None
    assert first_matching_demand_window(db, site.id, datetime.datetime(2026, 1, 1, tzinfo=UTC), site.timezone) is None
    assert any_configured_demand_window(db, site.id, datetime.datetime(2026, 1, 1, tzinfo=UTC)) is None


def test_source_id_helpers_filter_by_kind(db, site):
    db.add(Source(site_id=site.id, kind=SourceKind.ENERGY, provider="envoy", config={}, collector_api_key_hash="e1", effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC)))
    db.add(Source(site_id=site.id, kind=SourceKind.PRICING, provider="amber", config={}, collector_api_key_hash="p1", effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC)))
    db.commit()

    assert len(energy_source_ids_for_site(db, site.id)) == 1
    assert len(pricing_source_ids_for_site(db, site.id)) == 1


def test_price_interval_flags_demand_true_when_covering_interval_exists(db, site):
    source = Source(site_id=site.id, kind=SourceKind.PRICING, provider="amber", config={}, collector_api_key_hash="p2", effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC))
    db.add(source)
    db.commit()
    start = datetime.datetime(2026, 1, 5, 17, 0, tzinfo=UTC)
    db.add(PriceInterval(
        pricing_source_id=source.id, channel=PriceChannel.DEMAND, interval_start=start,
        interval_end=start + datetime.timedelta(minutes=30), price="0.0", is_demand_window=True,
    ))
    db.commit()

    bucket_start = start
    bucket_end = start + datetime.timedelta(minutes=5)
    assert price_interval_flags_demand(db, [source.id], bucket_start, bucket_end) is True
    assert price_interval_flags_demand(db, [source.id], start - datetime.timedelta(minutes=5), start) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core-api && uv run pytest tests/jobs/test_demand_config.py -v`
Expected: `ModuleNotFoundError: No module named 'core_api.jobs'` (or `core_api.time_utils`).

- [ ] **Step 3: Consolidate `as_utc` into `core_api/time_utils.py`**

Create `core-api/core_api/time_utils.py`:

```python
from __future__ import annotations

import datetime


def as_utc(value: datetime.datetime) -> datetime.datetime:
    """Normalize a possibly-naive datetime to aware UTC. SQLite's DateTime
    column silently drops tzinfo on write, so a value read back naive was
    always stored as UTC — treat it as such here (consolidated from
    routers/sources.py's original _as_utc once a second caller needed the
    identical logic)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)
```

In `core-api/core_api/routers/sources.py`, remove the local `_as_utc` function and import the shared one instead:

```python
from core_api.deps import get_db_session, require_site_access, require_site_owner
from core_api.errors import AppError
from core_api.models.identity import Site
from core_api.models.sources import Source, SourceKind
from core_api.schemas import SourceCreate, SourceCreateOut, SourceOut, SourceUpdate
from core_api.time_utils import as_utc
```

and update its two call sites:

```python
    for existing in db.execute(query).scalars():
        existing_from = as_utc(existing.effective_from)
        existing_to = as_utc(existing.effective_to) if existing.effective_to else _FAR_FUTURE
```

(delete the now-unused local `_as_utc` def entirely).

- [ ] **Step 4: Write `core_api/jobs/__init__.py` and `core_api/jobs/demand_config.py`**

Create `core-api/core_api/jobs/__init__.py` (empty file).

Create `core-api/core_api/jobs/demand_config.py`:

```python
from __future__ import annotations

import datetime
import zoneinfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.models.billing import DemandWindow, SiteTariff
from core_api.models.pricing import PriceInterval
from core_api.models.sources import Source, SourceKind
from core_api.time_utils import as_utc


def effective_site_tariff_at(db: Session, site_id: int, at: datetime.datetime) -> SiteTariff | None:
    """The SiteTariff in force at `at` — non-retroactive: a later rate/window
    change must not reinterpret a past timestamp (spec: 'Job 1')."""
    stmt = select(SiteTariff).where(SiteTariff.site_id == site_id).order_by(SiteTariff.effective_from.desc())
    for tariff in db.execute(stmt).scalars():
        if as_utc(tariff.effective_from) <= at and (
            tariff.effective_to is None or as_utc(tariff.effective_to) > at
        ):
            return tariff
    return None


def _in_demand_window_pattern(window: DemandWindow, at_local: datetime.datetime) -> bool:
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
    at_local = at.astimezone(zoneinfo.ZoneInfo(site_timezone))
    stmt = select(DemandWindow).where(DemandWindow.site_tariff_id == tariff.id).order_by(DemandWindow.id)
    for window in db.execute(stmt).scalars():
        if _in_demand_window_pattern(window, at_local):
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
    return db.execute(stmt).scalars().first()


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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd core-api && uv run pytest tests/jobs/test_demand_config.py tests/test_sources.py -v`
Expected: all PASS (including the untouched `test_sources.py`, confirming the `as_utc` consolidation didn't break the existing overlap check).

- [ ] **Step 6: Commit**

```bash
git add core-api/core_api/time_utils.py core-api/core_api/jobs/ core-api/core_api/routers/sources.py core-api/tests/jobs/ core-api/tests/conftest.py
git commit -m "feat: add shared effective-dated demand-config lookups for the billing jobs"
```

---

## Task 3: Billing-period walking (`billing_periods.py`)

**Files:**
- Create: `core-api/core_api/jobs/billing_periods.py`
- Create: `core-api/tests/jobs/test_billing_periods.py`

**Interfaces:**
- Consumes: `as_utc` from Task 2.
- Produces: `BillingPeriod(start: datetime, end: datetime, cycle: BillingCycle)` dataclass; `effective_billing_cycles_for_site(db, site_id) -> list[BillingCycle]`; `billing_periods_for_site(cycles: list[BillingCycle], site_timezone: str, until: datetime) -> list[BillingPeriod]`. Task 5 calls both.

- [ ] **Step 1: Write the failing tests**

Create `core-api/tests/jobs/test_billing_periods.py`:

```python
from __future__ import annotations

import datetime

from core_api.jobs.billing_periods import billing_periods_for_site
from core_api.models.billing import BillingCycle
from tests.conftest import UTC


def cycle(site_id, effective_from, effective_to=None, cycle_months=1, anchor_day=1):
    return BillingCycle(
        site_id=site_id, effective_from=effective_from, effective_to=effective_to,
        cycle_months=cycle_months, anchor_day=anchor_day,
    )


def test_monthly_periods_from_anchor_day(site):
    cycles = [cycle(site.id, datetime.datetime(2026, 1, 18, tzinfo=UTC), anchor_day=18, cycle_months=1)]
    periods = billing_periods_for_site(cycles, "UTC", datetime.datetime(2026, 4, 1, tzinfo=UTC))

    starts = [p.start for p in periods]
    assert starts == [
        datetime.datetime(2026, 1, 18, tzinfo=UTC),
        datetime.datetime(2026, 2, 18, tzinfo=UTC),
        datetime.datetime(2026, 3, 18, tzinfo=UTC),
    ]
    assert periods[0].end == datetime.datetime(2026, 2, 18, tzinfo=UTC)


def test_quarterly_periods_step_three_months(site):
    cycles = [cycle(site.id, datetime.datetime(2026, 1, 1, tzinfo=UTC), anchor_day=1, cycle_months=3)]
    periods = billing_periods_for_site(cycles, "UTC", datetime.datetime(2026, 8, 1, tzinfo=UTC))

    assert [p.start for p in periods] == [
        datetime.datetime(2026, 1, 1, tzinfo=UTC),
        datetime.datetime(2026, 4, 1, tzinfo=UTC),
        datetime.datetime(2026, 7, 1, tzinfo=UTC),
    ]


def test_anchor_day_clamps_to_shorter_month(site):
    cycles = [cycle(site.id, datetime.datetime(2026, 1, 31, tzinfo=UTC), anchor_day=31, cycle_months=1)]
    periods = billing_periods_for_site(cycles, "UTC", datetime.datetime(2026, 4, 1, tzinfo=UTC))

    # February 2026 has 28 days -> clamped.
    assert periods[1].start == datetime.datetime(2026, 2, 28, tzinfo=UTC)


def test_cycle_change_mid_sequence_uses_cycle_effective_at_each_periods_start(site):
    monthly = cycle(site.id, datetime.datetime(2026, 1, 1, tzinfo=UTC), datetime.datetime(2026, 3, 1, tzinfo=UTC), cycle_months=1, anchor_day=1)
    quarterly = cycle(site.id, datetime.datetime(2026, 3, 1, tzinfo=UTC), None, cycle_months=3, anchor_day=1)
    periods = billing_periods_for_site([monthly, quarterly], "UTC", datetime.datetime(2026, 9, 1, tzinfo=UTC))

    assert [p.start for p in periods] == [
        datetime.datetime(2026, 1, 1, tzinfo=UTC),
        datetime.datetime(2026, 2, 1, tzinfo=UTC),
        datetime.datetime(2026, 3, 1, tzinfo=UTC),
        datetime.datetime(2026, 6, 1, tzinfo=UTC),
    ]
    assert periods[2].cycle is quarterly  # the switch takes effect for the period starting at the switch


def test_no_cycles_returns_no_periods():
    assert billing_periods_for_site([], "UTC", datetime.datetime(2026, 1, 1, tzinfo=UTC)) == []


def test_until_in_the_past_returns_no_periods(site):
    cycles = [cycle(site.id, datetime.datetime(2026, 6, 1, tzinfo=UTC), anchor_day=1, cycle_months=1)]
    periods = billing_periods_for_site(cycles, "UTC", datetime.datetime(2026, 1, 1, tzinfo=UTC))
    assert periods == []


def test_non_utc_site_timezone_returns_utc_instants_not_local(site):
    """Calendar math (which date is 'the 1st of next month') must happen in
    the site's local calendar, but every returned boundary must be a UTC
    instant -- these values get written straight into
    DemandCharge.billing_period_start/end and compared against
    DemandInterval.interval_start (stored UTC), and SQLite silently drops
    tzinfo on write. Asserting only value equality wouldn't catch a
    regression here: an aware datetime still in the site's local tzinfo
    compares equal (by absolute instant) to its UTC equivalent in plain
    Python, even though writing the local-tz one to SQLite would silently
    store the wrong wall-clock numbers. So this test asserts tzinfo itself."""
    # 2025-12-31 13:00 UTC == 2026-01-01 00:00 AEDT (Sydney, UTC+11 in southern summer).
    sydney_jan1_midnight_utc = datetime.datetime(2025, 12, 31, 13, 0, tzinfo=UTC)
    cycles = [cycle(site.id, sydney_jan1_midnight_utc, anchor_day=1, cycle_months=1)]
    periods = billing_periods_for_site(cycles, "Australia/Sydney", datetime.datetime(2026, 2, 1, tzinfo=UTC))

    assert periods[0].start == sydney_jan1_midnight_utc
    assert periods[0].start.tzinfo == UTC
    # 2026-02-01 00:00 AEDT == 2026-01-31 13:00 UTC.
    assert periods[0].end == datetime.datetime(2026, 1, 31, 13, 0, tzinfo=UTC)
    assert periods[0].end.tzinfo == UTC
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core-api && uv run pytest tests/jobs/test_billing_periods.py -v`
Expected: `ModuleNotFoundError: No module named 'core_api.jobs.billing_periods'`.

- [ ] **Step 3: Write `core_api/jobs/billing_periods.py`**

```python
from __future__ import annotations

import calendar
import dataclasses
import datetime
import zoneinfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.models.billing import BillingCycle
from core_api.time_utils import as_utc


@dataclasses.dataclass(frozen=True)
class BillingPeriod:
    start: datetime.datetime
    end: datetime.datetime
    cycle: BillingCycle


def _anchor_date(year: int, month: int, anchor_day: int) -> datetime.date:
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, min(anchor_day, last_day))


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    zero_based = (year * 12 + (month - 1)) + delta
    return zero_based // 12, zero_based % 12 + 1


def _local_midnight(day: datetime.date, tz: zoneinfo.ZoneInfo) -> datetime.datetime:
    return datetime.datetime(day.year, day.month, day.day, tzinfo=tz)


def effective_billing_cycles_for_site(db: Session, site_id: int) -> list[BillingCycle]:
    stmt = select(BillingCycle).where(BillingCycle.site_id == site_id).order_by(BillingCycle.effective_from)
    return list(db.execute(stmt).scalars())


def _cycle_effective_at(cycles: list[BillingCycle], at: datetime.datetime) -> BillingCycle | None:
    chosen = None
    for cyc in cycles:
        if as_utc(cyc.effective_from) <= at and (cyc.effective_to is None or as_utc(cyc.effective_to) > at):
            chosen = cyc
    return chosen


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
    if _local_midnight(start_date, tz) < first_effective_from:
        year, month = _add_months(year, month, 1)
        start_date = _anchor_date(year, month, first_cycle.anchor_day)

    periods: list[BillingPeriod] = []
    local_cursor = _local_midnight(start_date, tz)
    cursor_year, cursor_month = year, month
    while local_cursor <= until:
        cyc = _cycle_effective_at(cycles, local_cursor)
        if cyc is None:
            break
        end_year, end_month = _add_months(cursor_year, cursor_month, cyc.cycle_months)
        end_date = _anchor_date(end_year, end_month, cyc.anchor_day)
        local_end = _local_midnight(end_date, tz)
        periods.append(
            BillingPeriod(
                start=local_cursor.astimezone(datetime.timezone.utc),
                end=local_end.astimezone(datetime.timezone.utc),
                cycle=cyc,
            )
        )
        local_cursor, cursor_year, cursor_month = local_end, end_year, end_month
    return periods
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd core-api && uv run pytest tests/jobs/test_billing_periods.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core-api/core_api/jobs/billing_periods.py core-api/tests/jobs/test_billing_periods.py
git commit -m "feat: add billing-period walking for effective-dated BillingCycle configs"
```

---

## Task 4: Job 1 — DemandInterval derivation

**Files:**
- Create: `core-api/core_api/jobs/time_buckets.py`
- Create: `core-api/core_api/jobs/demand_interval_job.py`
- Create: `core-api/tests/jobs/test_demand_interval_job.py`

**Interfaces:**
- Consumes: `first_matching_demand_window`, `price_interval_flags_demand`, `energy_source_ids_for_site`, `pricing_source_ids_for_site` from Task 2; `as_utc` from Task 2; `sydney_local` (test helper) from Task 2's `tests/conftest.py` change.
- Produces: `BUCKET_WIDTH: timedelta`, `floor_to_5min(ts) -> datetime`; `run_demand_interval_job(db: Session, now: datetime) -> None`. Task 5 reuses `BUCKET_WIDTH`.

- [ ] **Step 1: Write the failing tests**

Create `core-api/tests/jobs/test_demand_interval_job.py`:

```python
from __future__ import annotations

import datetime

from sqlalchemy import select

from core_api.jobs.demand_interval_job import run_demand_interval_job
from core_api.models.billing import DemandInterval, DemandWindow, SiteTariff
from core_api.models.readings import EnergyMetric, EnergyReading
from core_api.models.sources import Source, SourceKind
from tests.conftest import UTC, sydney_local

# conftest's `site` fixture is Australia/Sydney -- kept as-is (not
# overridden to UTC) so the two boundary tests below actually exercise
# Job 1's local<->UTC conversion instead of a no-op; see Task 2's note on
# why a UTC test fixture would hide that class of bug.


def make_energy_source(db, site):
    source = Source(
        site_id=site.id, kind=SourceKind.ENERGY, provider="envoy", config={},
        collector_api_key_hash="e1", effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def add_reading(db, source, ts, value):
    db.add(EnergyReading(source_id=source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=value))


def make_tariff_and_window(db, site, start="15:00", end="21:00", minutes=30, rate="45.0"):
    tariff = SiteTariff(
        site_id=site.id, effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
        service_availability_c_per_day="80.0",
    )
    db.add(tariff)
    db.commit()
    window = DemandWindow(
        site_tariff_id=tariff.id, label="Peak", months=list(range(1, 13)), days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time.fromisoformat(start), end_time=datetime.time.fromisoformat(end),
        rate_c_per_kw_per_day=rate, measurement_interval_minutes=minutes,
    )
    db.add(window)
    db.commit()
    return tariff, window


def test_rolling_sum_zeroes_slots_outside_window_and_sums_n_slots_inside(db, site):
    """Six slots (N=6 for a 30-min window) ending right at window close:
    the two slots before window-open contribute 0, the four inside
    contribute their real average. Timestamps are expressed via
    sydney_local, since the window's 15:00-21:00 bounds are Sydney *local*
    time — using bare UTC timestamps here would only pass by accident of
    local-time arithmetic never actually running on a non-UTC site."""
    source = make_energy_source(db, site)
    make_tariff_and_window(db, site, start="15:00", end="21:00", minutes=30)

    # Slots at 14:50, 14:55 local are outside the window; 15:00..15:15 local are inside.
    for minute, value in [(50, 1000.0), (55, 1000.0), (0, 2000.0), (5, 2000.0), (10, 2000.0), (15, 2000.0)]:
        hour = 14 if minute >= 50 else 15
        add_reading(db, source, sydney_local(2026, 1, 5, hour, minute), value)
    db.commit()

    run_demand_interval_job(db, now=sydney_local(2026, 1, 5, 15, 25))

    closing_slot = db.get(DemandInterval, (site.id, sydney_local(2026, 1, 5, 15, 15)))
    assert closing_slot is not None
    assert closing_slot.rolling_sum_w == 4 * 2000.0  # the two pre-window slots contributed 0
    assert closing_slot.is_demand_window is True
    assert closing_slot.rate_applied == 45.0


def test_slot_exactly_at_window_open_and_close_boundary_is_included(db, site):
    source = make_energy_source(db, site)
    make_tariff_and_window(db, site, start="15:00", end="21:00", minutes=5)

    at_open = sydney_local(2026, 1, 5, 15, 0)
    at_close = sydney_local(2026, 1, 5, 21, 0)
    add_reading(db, source, at_open, 3000.0)
    add_reading(db, source, at_close, 3000.0)
    db.commit()

    run_demand_interval_job(db, now=at_close + datetime.timedelta(minutes=10))

    at_open_row = db.get(DemandInterval, (site.id, at_open))
    at_close_row = db.get(DemandInterval, (site.id, at_close))
    assert at_open_row.is_demand_window is True
    assert at_close_row.is_demand_window is True


def test_window_change_mid_range_uses_window_effective_at_each_slots_own_timestamp(db, site):
    source = make_energy_source(db, site)
    old_tariff = SiteTariff(
        site_id=site.id, effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
        effective_to=datetime.datetime(2026, 1, 5, 12, tzinfo=UTC), service_availability_c_per_day="80.0",
    )
    db.add(old_tariff)
    db.commit()
    db.add(DemandWindow(
        site_tariff_id=old_tariff.id, label="Old", months=list(range(1, 13)), days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time(0, 0), end_time=datetime.time(23, 59), rate_c_per_kw_per_day="10.0",
        measurement_interval_minutes=5,
    ))
    new_tariff = SiteTariff(
        site_id=site.id, effective_from=datetime.datetime(2026, 1, 5, 12, tzinfo=UTC), service_availability_c_per_day="80.0",
    )
    db.add(new_tariff)
    db.commit()
    db.add(DemandWindow(
        site_tariff_id=new_tariff.id, label="New", months=list(range(1, 13)), days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time(0, 0), end_time=datetime.time(23, 59), rate_c_per_kw_per_day="99.0",
        measurement_interval_minutes=5,
    ))
    db.commit()

    before_switch = datetime.datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    after_switch = datetime.datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    add_reading(db, source, before_switch, 1000.0)
    add_reading(db, source, after_switch, 1000.0)
    db.commit()

    run_demand_interval_job(db, now=after_switch + datetime.timedelta(minutes=10))

    before_row = db.get(DemandInterval, (site.id, before_switch))
    after_row = db.get(DemandInterval, (site.id, after_switch))
    assert before_row.rate_applied == 10.0
    assert after_row.rate_applied == 99.0


def test_rerun_over_already_derived_range_is_idempotent(db, site):
    source = make_energy_source(db, site)
    make_tariff_and_window(db, site)
    day = datetime.datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    add_reading(db, source, day, 2500.0)
    db.commit()

    now = day + datetime.timedelta(minutes=10)
    run_demand_interval_job(db, now=now)
    first_pass = list(db.execute(select(DemandInterval).where(DemandInterval.site_id == site.id)).scalars())

    run_demand_interval_job(db, now=now)
    second_pass = list(db.execute(select(DemandInterval).where(DemandInterval.site_id == site.id)).scalars())

    assert len(first_pass) == len(second_pass)
    assert {(r.interval_start, r.rolling_sum_w) for r in first_pass} == {(r.interval_start, r.rolling_sum_w) for r in second_pass}


def test_catch_up_resumes_from_latest_already_derived_bucket(db, site):
    source = make_energy_source(db, site)
    make_tariff_and_window(db, site)
    already_derived = datetime.datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    db.add(DemandInterval(
        site_id=site.id, interval_start=already_derived, interval_end=already_derived + datetime.timedelta(minutes=5),
        rolling_sum_w=0.0, is_demand_window=False,
    ))
    # An EnergyReading far earlier than `already_derived` must NOT be (re)processed.
    add_reading(db, source, already_derived - datetime.timedelta(days=1), 9999.0)
    add_reading(db, source, already_derived + datetime.timedelta(minutes=5), 500.0)
    db.commit()

    run_demand_interval_job(db, now=already_derived + datetime.timedelta(minutes=15))

    rows = list(db.execute(select(DemandInterval).where(DemandInterval.site_id == site.id)).scalars())
    assert already_derived - datetime.timedelta(days=1) not in {r.interval_start for r in rows}
    assert any(r.interval_start == already_derived + datetime.timedelta(minutes=5) for r in rows)


def test_no_demand_window_configured_still_derives_intervals_with_rolling_sum_of_one(db, site):
    source = make_energy_source(db, site)
    ts = datetime.datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    add_reading(db, source, ts, 1200.0)
    db.commit()

    run_demand_interval_job(db, now=ts + datetime.timedelta(minutes=10))

    row = db.get(DemandInterval, (site.id, ts))
    assert row is not None
    assert row.is_demand_window is False
    assert row.rolling_sum_w == 0.0
    assert row.rate_applied is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core-api && uv run pytest tests/jobs/test_demand_interval_job.py -v`
Expected: `ModuleNotFoundError: No module named 'core_api.jobs.demand_interval_job'`.

- [ ] **Step 3: Write `core_api/jobs/time_buckets.py`**

```python
from __future__ import annotations

import datetime

BUCKET_WIDTH = datetime.timedelta(minutes=5)


def floor_to_5min(ts: datetime.datetime) -> datetime.datetime:
    floored_minute = ts.minute - (ts.minute % 5)
    return ts.replace(minute=floored_minute, second=0, microsecond=0)
```

- [ ] **Step 4: Write `core_api/jobs/demand_interval_job.py`**

```python
from __future__ import annotations

import dataclasses
import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core_api.jobs.demand_config import (
    energy_source_ids_for_site,
    first_matching_demand_window,
    price_interval_flags_demand,
    pricing_source_ids_for_site,
)
from core_api.jobs.time_buckets import BUCKET_WIDTH, floor_to_5min
from core_api.models.billing import DemandInterval, DemandWindow
from core_api.models.identity import Site
from core_api.models.readings import EnergyMetric, EnergyReading
from core_api.time_utils import as_utc


@dataclasses.dataclass(frozen=True)
class _SlotInfo:
    raw_avg_w: float
    is_flagged: bool
    window: DemandWindow | None


def run_demand_interval_job(db: Session, now: datetime.datetime) -> None:
    for site in db.execute(select(Site)).scalars():
        _derive_for_site(db, site, now)
    db.commit()


def _raw_avg_w(db: Session, energy_source_ids: list[int], slot_start: datetime.datetime, slot_end: datetime.datetime) -> float:
    avg_w = db.execute(
        select(func.avg(EnergyReading.value)).where(
            EnergyReading.source_id.in_(energy_source_ids),
            EnergyReading.metric == EnergyMetric.GRID_IMPORT_W,
            EnergyReading.ts >= slot_start,
            EnergyReading.ts < slot_end,
        )
    ).scalar()
    return float(avg_w) if avg_w is not None else 0.0


def _slot_info(
    db: Session, site: Site, energy_source_ids: list[int], pricing_source_ids: list[int], slot_start: datetime.datetime
) -> _SlotInfo:
    slot_end = slot_start + BUCKET_WIDTH
    raw = _raw_avg_w(db, energy_source_ids, slot_start, slot_end)
    window = first_matching_demand_window(db, site.id, slot_start, site.timezone)
    is_flagged = window is not None or price_interval_flags_demand(db, pricing_source_ids, slot_start, slot_end)
    return _SlotInfo(raw_avg_w=raw, is_flagged=is_flagged, window=window)


def _derive_for_site(db: Session, site: Site, now: datetime.datetime) -> None:
    energy_source_ids = energy_source_ids_for_site(db, site.id)
    if not energy_source_ids:
        return
    pricing_source_ids = pricing_source_ids_for_site(db, site.id)

    latest = db.execute(
        select(func.max(DemandInterval.interval_start)).where(DemandInterval.site_id == site.id)
    ).scalar()
    if latest is not None:
        bucket_start = floor_to_5min(as_utc(latest)) + BUCKET_WIDTH
    else:
        earliest = db.execute(
            select(func.min(EnergyReading.ts)).where(EnergyReading.source_id.in_(energy_source_ids))
        ).scalar()
        if earliest is None:
            return
        bucket_start = floor_to_5min(as_utc(earliest))

    last_allowed_start = floor_to_5min(now - BUCKET_WIDTH)

    info_cache: dict[datetime.datetime, _SlotInfo] = {}

    def info_at(slot_start: datetime.datetime) -> _SlotInfo:
        if slot_start not in info_cache:
            info_cache[slot_start] = _slot_info(db, site, energy_source_ids, pricing_source_ids, slot_start)
        return info_cache[slot_start]

    def contribution_at(slot_start: datetime.datetime) -> float:
        info = info_at(slot_start)
        return info.raw_avg_w if info.is_flagged else 0.0

    while bucket_start <= last_allowed_start:
        bucket_end = bucket_start + BUCKET_WIDTH
        info = info_at(bucket_start)
        rolling_n = (info.window.measurement_interval_minutes // 5) if info.window is not None else 1
        slots = [bucket_start - i * BUCKET_WIDTH for i in range(rolling_n)]
        rolling_sum_w = sum(contribution_at(slot) for slot in slots)
        rate_applied = info.window.rate_c_per_kw_per_day if (info.window is not None and info.is_flagged) else None

        stmt = sqlite_insert(DemandInterval).values(
            site_id=site.id,
            interval_start=bucket_start,
            interval_end=bucket_end,
            rolling_sum_w=rolling_sum_w,
            is_demand_window=info.is_flagged,
            rate_applied=rate_applied,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["site_id", "interval_start"],
            set_={
                "interval_end": stmt.excluded.interval_end,
                "rolling_sum_w": stmt.excluded.rolling_sum_w,
                "is_demand_window": stmt.excluded.is_demand_window,
                "rate_applied": stmt.excluded.rate_applied,
            },
        )
        db.execute(stmt)
        bucket_start = bucket_end
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd core-api && uv run pytest tests/jobs/test_demand_interval_job.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add core-api/core_api/jobs/time_buckets.py core-api/core_api/jobs/demand_interval_job.py core-api/tests/jobs/test_demand_interval_job.py
git commit -m "feat: derive DemandInterval rolling sums every 5 minutes (Job 1)"
```

---

## Task 5: Job 2 — DemandCharge estimation/finalization

**Files:**
- Create: `core-api/core_api/jobs/demand_charge_job.py`
- Create: `core-api/tests/jobs/test_demand_charge_job.py`

**Interfaces:**
- Consumes: `effective_billing_cycles_for_site`, `billing_periods_for_site`, `BillingPeriod` from Task 3; `any_configured_demand_window`, `first_matching_demand_window`, `as_utc` from Task 2; `BUCKET_WIDTH` from Task 4; `sydney_local` (test helper) from Task 2's `tests/conftest.py` change.
- Produces: `run_demand_charge_job(db: Session, now: datetime) -> None`. Consumed by Task 7's scheduler.

- [ ] **Step 1: Write the failing tests**

Create `core-api/tests/jobs/test_demand_charge_job.py`:

```python
from __future__ import annotations

import datetime
import zoneinfo

from sqlalchemy import select

from core_api.jobs.demand_charge_job import run_demand_charge_job
from core_api.models.billing import BillingCycle, DemandCharge, DemandInterval, DemandWindow, SiteTariff
from tests.conftest import sydney_local

# conftest's `site` fixture is Australia/Sydney -- kept as-is (not
# overridden to UTC), both because billing_periods_for_site must be
# exercised against a real local calendar (see Task 3's non_utc_site_
# timezone test) and because the 15:00-21:00 DemandWindow pattern below is
# Sydney local time, same reasoning as Task 2/4.


def make_billing_cycle(db, site, effective_from, cycle_months=1, anchor_day=1):
    cyc = BillingCycle(site_id=site.id, effective_from=effective_from, cycle_months=cycle_months, anchor_day=anchor_day)
    db.add(cyc)
    db.commit()
    return cyc


def make_tariff_and_window(db, site, effective_from, rate="45.0", minutes=30):
    tariff = SiteTariff(site_id=site.id, effective_from=effective_from, service_availability_c_per_day="80.0")
    db.add(tariff)
    db.commit()
    window = DemandWindow(
        site_tariff_id=tariff.id, label="Peak", months=list(range(1, 13)), days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time(15, 0), end_time=datetime.time(21, 0), rate_c_per_kw_per_day=rate,
        measurement_interval_minutes=minutes,
    )
    db.add(window)
    db.commit()
    return tariff, window


def add_demand_intervals_for_period(db, site, period_start, period_end, in_window_w):
    """Fills a billing period with one DemandInterval per 5-min slot: every
    slot inside 15:00-21:00 *local* gets rolling_sum_w=in_window_w and a
    rate; every other slot is zeroed, exactly like Job 1 would produce.
    period_start/end are UTC instants, so each slot is converted to the
    site's local time before the pattern check -- comparing slot.time()
    directly (UTC wall-clock) against the window's local bounds would be
    wrong for any site not on UTC."""
    tz = zoneinfo.ZoneInfo(site.timezone)
    slot = period_start
    while slot < period_end:
        inside = datetime.time(15, 0) <= slot.astimezone(tz).time() <= datetime.time(21, 0)
        db.add(DemandInterval(
            site_id=site.id, interval_start=slot, interval_end=slot + datetime.timedelta(minutes=5),
            rolling_sum_w=in_window_w if inside else 0.0, is_demand_window=inside,
            rate_applied="45.0" if inside else None,
        ))
        slot += datetime.timedelta(minutes=5)
    db.commit()


def test_zero_demand_window_usage_gives_exactly_zero_charge(db, site):
    period_start = sydney_local(2026, 1, 1)
    period_end = sydney_local(2026, 2, 1)
    make_billing_cycle(db, site, period_start)
    make_tariff_and_window(db, site, period_start)
    add_demand_intervals_for_period(db, site, period_start, period_start + datetime.timedelta(hours=1), in_window_w=0.0)

    run_demand_charge_job(db, now=period_end + datetime.timedelta(days=1))

    charge = db.execute(
        select(DemandCharge).where(DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period_start)
    ).scalar_one()
    assert charge.charge_amount == 0


def test_gaps_in_energy_reading_coverage_mark_is_estimated_true(db, site):
    period_start = sydney_local(2026, 1, 1)
    period_end = sydney_local(2026, 2, 1)
    make_billing_cycle(db, site, period_start)
    make_tariff_and_window(db, site, period_start)
    # Only 1 hour of DemandInterval rows exist out of the whole month -> a gap.
    add_demand_intervals_for_period(db, site, period_start, period_start + datetime.timedelta(hours=1), in_window_w=3000.0)

    run_demand_charge_job(db, now=period_end + datetime.timedelta(days=1))

    charge = db.execute(
        select(DemandCharge).where(DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period_start)
    ).scalar_one()
    assert charge.is_estimated is True


def test_period_finalizes_on_the_run_after_it_closes(db, site):
    period_start = sydney_local(2026, 1, 1)
    period_end = sydney_local(2026, 2, 1)
    make_billing_cycle(db, site, period_start)
    make_tariff_and_window(db, site, period_start)
    add_demand_intervals_for_period(db, site, period_start, period_end, in_window_w=3000.0)

    # First run: period still open.
    run_demand_charge_job(db, now=period_start + datetime.timedelta(days=5))
    mid_run = db.execute(
        select(DemandCharge).where(DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period_start)
    ).scalar_one()
    assert mid_run.is_estimated is True

    # Second run: period has closed, full coverage.
    run_demand_charge_job(db, now=period_end + datetime.timedelta(days=1))
    final_run = db.execute(
        select(DemandCharge).where(DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period_start)
    ).scalar_one()
    assert final_run.is_estimated is False
    assert final_run.charge_amount > 0


def test_finalized_charge_is_not_recomputed_on_a_later_run(db, site):
    period_start = sydney_local(2026, 1, 1)
    period_end = sydney_local(2026, 2, 1)
    make_billing_cycle(db, site, period_start)
    make_tariff_and_window(db, site, period_start)
    add_demand_intervals_for_period(db, site, period_start, period_end, in_window_w=3000.0)
    run_demand_charge_job(db, now=period_end + datetime.timedelta(days=1))
    finalized = db.execute(
        select(DemandCharge).where(DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period_start)
    ).scalar_one()
    original_amount = finalized.charge_amount

    # A new, much higher peak appears after finalization -- must NOT change the stable row.
    db.add(DemandInterval(
        site_id=site.id, interval_start=period_start + datetime.timedelta(minutes=5),
        interval_end=period_start + datetime.timedelta(minutes=10), rolling_sum_w=999999.0,
        is_demand_window=True, rate_applied="45.0",
    ))
    db.commit()
    run_demand_charge_job(db, now=period_end + datetime.timedelta(days=2))

    unchanged = db.execute(
        select(DemandCharge).where(DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period_start)
    ).scalar_one()
    assert unchanged.charge_amount == original_amount


def test_site_with_no_demand_tariff_configured_gets_no_demand_charges(db, site):
    period_start = sydney_local(2026, 1, 1)
    make_billing_cycle(db, site, period_start)
    # No SiteTariff/DemandWindow at all.
    run_demand_charge_job(db, now=sydney_local(2026, 2, 2))

    charges = list(db.execute(select(DemandCharge).where(DemandCharge.site_id == site.id)).scalars())
    assert charges == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core-api && uv run pytest tests/jobs/test_demand_charge_job.py -v`
Expected: `ModuleNotFoundError: No module named 'core_api.jobs.demand_charge_job'`.

- [ ] **Step 3: Write `core_api/jobs/demand_charge_job.py`**

```python
from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core_api.jobs.billing_periods import billing_periods_for_site, effective_billing_cycles_for_site
from core_api.jobs.demand_config import any_configured_demand_window, first_matching_demand_window
from core_api.jobs.time_buckets import BUCKET_WIDTH
from core_api.models.billing import DemandCharge, DemandInterval
from core_api.models.identity import Site
from core_api.time_utils import as_utc


def run_demand_charge_job(db: Session, now: datetime.datetime) -> None:
    for site in db.execute(select(Site)).scalars():
        _finalize_for_site(db, site, now)
    db.commit()


def _finalize_for_site(db: Session, site: Site, now: datetime.datetime) -> None:
    cycles = effective_billing_cycles_for_site(db, site.id)
    for period in billing_periods_for_site(cycles, site.timezone, now):
        existing = db.execute(
            select(DemandCharge).where(
                DemandCharge.site_id == site.id, DemandCharge.billing_period_start == period.start
            )
        ).scalar_one_or_none()
        if existing is not None and not existing.is_estimated:
            continue  # finalized -- stable once written
        _upsert_charge_for_period(db, site, period.start, period.end, now)


def _upsert_charge_for_period(
    db: Session, site: Site, period_start: datetime.datetime, period_end: datetime.datetime, now: datetime.datetime
) -> None:
    configured_window = any_configured_demand_window(db, site.id, period_start)
    if configured_window is None:
        return  # no demand tariff in force for this period -- nothing to charge

    peak_row = db.execute(
        select(DemandInterval)
        .where(
            DemandInterval.site_id == site.id,
            DemandInterval.interval_start >= period_start,
            DemandInterval.interval_start < period_end,
        )
        .order_by(DemandInterval.rolling_sum_w.desc(), DemandInterval.interval_start.asc())
        .limit(1)
    ).scalar_one_or_none()
    if peak_row is None:
        return  # Job 1 hasn't derived any slots for this period yet -- catch up next run

    expected_slots = (period_end - period_start) // BUCKET_WIDTH
    actual_slots = db.execute(
        select(func.count()).select_from(DemandInterval).where(
            DemandInterval.site_id == site.id,
            DemandInterval.interval_start >= period_start,
            DemandInterval.interval_start < period_end,
        )
    ).scalar()
    has_gaps = actual_slots < expected_slots

    peak_interval_start = as_utc(peak_row.interval_start)
    window = first_matching_demand_window(db, site.id, peak_interval_start, site.timezone) or configured_window
    n = window.measurement_interval_minutes // 5

    peak_avg_import_w = peak_row.rolling_sum_w / n
    peak_demand_kw = peak_avg_import_w / 1000
    rate_c_per_kw_per_day = peak_row.rate_applied if peak_row.rate_applied is not None else window.rate_c_per_kw_per_day
    billing_period_days = (period_end - period_start).total_seconds() / 86400
    charge_amount_c = peak_demand_kw * float(rate_c_per_kw_per_day) * billing_period_days

    is_estimated = (period_end > now) or has_gaps

    stmt = sqlite_insert(DemandCharge).values(
        site_id=site.id,
        billing_period_start=period_start,
        billing_period_end=period_end,
        peak_interval_start=peak_interval_start,
        peak_avg_import_w=peak_avg_import_w,
        rate_applied=rate_c_per_kw_per_day,
        charge_amount=charge_amount_c,
        is_estimated=is_estimated,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["site_id", "billing_period_start"],
        set_={
            "billing_period_end": stmt.excluded.billing_period_end,
            "peak_interval_start": stmt.excluded.peak_interval_start,
            "peak_avg_import_w": stmt.excluded.peak_avg_import_w,
            "rate_applied": stmt.excluded.rate_applied,
            "charge_amount": stmt.excluded.charge_amount,
            "is_estimated": stmt.excluded.is_estimated,
        },
    )
    db.execute(stmt)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd core-api && uv run pytest tests/jobs/test_demand_charge_job.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core-api/core_api/jobs/demand_charge_job.py core-api/tests/jobs/test_demand_charge_job.py
git commit -m "feat: estimate and finalize DemandCharge per billing period (Job 2)"
```

---

## Task 6: Job 3 — Retention rollup

**Files:**
- Modify: `core-api/core_api/models/pricing.py` (add `PRICE_CHANNEL_AGGREGATION`)
- Modify: `core-api/core_api/models/__init__.py` (export it)
- Create: `core-api/core_api/jobs/retention_rollup_job.py`
- Create: `core-api/tests/jobs/test_retention_rollup_job.py`

**Interfaces:**
- Consumes: `floor_to_5min` from Task 4; `RAW_RETENTION` from existing `core_api.readings_query`; `ENERGY_METRIC_AGGREGATION`/`WEATHER_METRIC_AGGREGATION`/`AggregationMethod` from existing `core_api.models.readings`.
- Produces: `PRICE_CHANNEL_AGGREGATION: dict[PriceChannel, AggregationMethod]`; `run_retention_rollup_job(db: Session, now: datetime) -> None`. Consumed by Task 7's scheduler.

- [ ] **Step 1: Write the failing tests**

Create `core-api/tests/jobs/test_retention_rollup_job.py`:

```python
from __future__ import annotations

import datetime

from sqlalchemy import select

from core_api.jobs.retention_rollup_job import run_retention_rollup_job
from core_api.models.pricing import PriceChannel, PriceReading, PriceReadingRollup5m
from core_api.models.readings import EnergyMetric, EnergyReading, EnergyReadingRollup5m
from core_api.models.sources import Source, SourceKind
from core_api.readings_query import RAW_RETENTION
from tests.conftest import UTC


def make_energy_source(db, site):
    source = Source(
        site_id=site.id, kind=SourceKind.ENERGY, provider="envoy", config={},
        collector_api_key_hash="e1", effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def make_pricing_source(db, site):
    source = Source(
        site_id=site.id, kind=SourceKind.PRICING, provider="amber", config={},
        collector_api_key_hash="p1", effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def test_average_metric_rolls_up_to_mean_and_deletes_raw_rows(db, site):
    source = make_energy_source(db, site)
    old_ts = datetime.datetime.now(UTC) - RAW_RETENTION - datetime.timedelta(days=10)
    bucket = old_ts.replace(minute=old_ts.minute - old_ts.minute % 5, second=0, microsecond=0)
    db.add(EnergyReading(source_id=source.id, ts=bucket, metric=EnergyMetric.PRODUCTION_W, value=100.0))
    db.add(EnergyReading(source_id=source.id, ts=bucket + datetime.timedelta(minutes=1), metric=EnergyMetric.PRODUCTION_W, value=300.0))
    db.commit()

    run_retention_rollup_job(db, now=datetime.datetime.now(UTC))

    rolled = db.execute(
        select(EnergyReadingRollup5m).where(EnergyReadingRollup5m.source_id == source.id)
    ).scalars().all()
    assert len(rolled) == 1
    assert rolled[0].value == 200.0
    remaining_raw = db.execute(select(EnergyReading).where(EnergyReading.source_id == source.id)).scalars().all()
    assert remaining_raw == []


def test_last_value_metric_rolls_up_to_latest_reading_not_average(db, site):
    source = make_energy_source(db, site)
    old_ts = datetime.datetime.now(UTC) - RAW_RETENTION - datetime.timedelta(days=10)
    bucket = old_ts.replace(minute=old_ts.minute - old_ts.minute % 5, second=0, microsecond=0)
    db.add(EnergyReading(source_id=source.id, ts=bucket, metric=EnergyMetric.BATTERY_SOC_PCT, value=40.0))
    db.add(EnergyReading(source_id=source.id, ts=bucket + datetime.timedelta(minutes=3), metric=EnergyMetric.BATTERY_SOC_PCT, value=55.0))
    db.commit()

    run_retention_rollup_job(db, now=datetime.datetime.now(UTC))

    rolled = db.execute(
        select(EnergyReadingRollup5m).where(EnergyReadingRollup5m.source_id == source.id)
    ).scalars().all()
    assert len(rolled) == 1
    assert rolled[0].value == 55.0  # the later reading, not the average of 40/55


def test_rows_newer_than_cutoff_are_untouched(db, site):
    source = make_energy_source(db, site)
    recent_ts = datetime.datetime.now(UTC) - datetime.timedelta(days=1)
    db.add(EnergyReading(source_id=source.id, ts=recent_ts, metric=EnergyMetric.PRODUCTION_W, value=100.0))
    db.commit()

    run_retention_rollup_job(db, now=datetime.datetime.now(UTC))

    remaining_raw = db.execute(select(EnergyReading).where(EnergyReading.source_id == source.id)).scalars().all()
    assert len(remaining_raw) == 1
    rolled = db.execute(select(EnergyReadingRollup5m).where(EnergyReadingRollup5m.source_id == source.id)).scalars().all()
    assert rolled == []


def test_price_reading_rolls_up_by_channel_average(db, site):
    source = make_pricing_source(db, site)
    old_ts = datetime.datetime.now(UTC) - RAW_RETENTION - datetime.timedelta(days=10)
    bucket = old_ts.replace(minute=old_ts.minute - old_ts.minute % 5, second=0, microsecond=0)
    db.add(PriceReading(pricing_source_id=source.id, ts=bucket, channel=PriceChannel.BUY, value="20.0"))
    db.add(PriceReading(pricing_source_id=source.id, ts=bucket + datetime.timedelta(minutes=2), channel=PriceChannel.BUY, value="30.0"))
    db.commit()

    run_retention_rollup_job(db, now=datetime.datetime.now(UTC))

    rolled = db.execute(
        select(PriceReadingRollup5m).where(PriceReadingRollup5m.pricing_source_id == source.id)
    ).scalars().all()
    assert len(rolled) == 1
    assert rolled[0].value == 25.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core-api && uv run pytest tests/jobs/test_retention_rollup_job.py -v`
Expected: `ModuleNotFoundError: No module named 'core_api.jobs.retention_rollup_job'`.

- [ ] **Step 3: Add `PRICE_CHANNEL_AGGREGATION` to `core_api/models/pricing.py`**

Add near the top of `core-api/core_api/models/pricing.py`, after the existing imports:

```python
from core_api.models.readings import AggregationMethod
```

and after the `PriceChannel` class definition:

```python
# All price channels are continuous values (no state-type price channel
# exists), so all average today -- mirrors ENERGY_METRIC_AGGREGATION /
# WEATHER_METRIC_AGGREGATION's lookup-table pattern (spec: 'Retention &
# rollup').
PRICE_CHANNEL_AGGREGATION: dict[PriceChannel, AggregationMethod] = {
    channel: AggregationMethod.AVERAGE for channel in PriceChannel
}
```

In `core-api/core_api/models/__init__.py`, add `PRICE_CHANNEL_AGGREGATION` to the `core_api.models.pricing` import line and to `__all__`.

- [ ] **Step 4: Write `core_api/jobs/retention_rollup_job.py`**

```python
from __future__ import annotations

import collections
import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core_api.jobs.time_buckets import floor_to_5min
from core_api.models.pricing import PRICE_CHANNEL_AGGREGATION, PriceReading, PriceReadingRollup5m
from core_api.models.readings import (
    ENERGY_METRIC_AGGREGATION,
    WEATHER_METRIC_AGGREGATION,
    AggregationMethod,
    EnergyReading,
    EnergyReadingRollup5m,
    WeatherReading,
    WeatherReadingRollup5m,
)
from core_api.readings_query import RAW_RETENTION
from core_api.time_utils import as_utc


def run_retention_rollup_job(db: Session, now: datetime.datetime) -> None:
    cutoff = now - RAW_RETENTION
    _rollup_and_delete(db, EnergyReading, EnergyReadingRollup5m, "source_id", "metric", ENERGY_METRIC_AGGREGATION, cutoff)
    _rollup_and_delete(db, WeatherReading, WeatherReadingRollup5m, "source_id", "metric", WEATHER_METRIC_AGGREGATION, cutoff)
    _rollup_and_delete(
        db, PriceReading, PriceReadingRollup5m, "pricing_source_id", "channel", PRICE_CHANNEL_AGGREGATION, cutoff
    )
    db.commit()


def _rollup_and_delete(
    db: Session,
    model_raw: type,
    model_rollup: type,
    key_column_name: str,
    discriminator_column_name: str,
    aggregation_map: dict,
    cutoff: datetime.datetime,
) -> None:
    rows = db.execute(select(model_raw).where(model_raw.ts < cutoff)).scalars().all()
    if not rows:
        return

    buckets: dict[tuple, list] = collections.defaultdict(list)
    for row in rows:
        key_value = getattr(row, key_column_name)
        discriminator_value = getattr(row, discriminator_column_name)
        bucket_ts = floor_to_5min(as_utc(row.ts))
        buckets[(key_value, bucket_ts, discriminator_value)].append(row)

    for (key_value, bucket_ts, discriminator_value), group in buckets.items():
        method = aggregation_map[discriminator_value]
        if method == AggregationMethod.AVERAGE:
            value = sum(row.value for row in group) / len(group)
        else:
            value = max(group, key=lambda row: row.ts).value

        stmt = sqlite_insert(model_rollup).values(
            **{
                key_column_name: key_value,
                "ts": bucket_ts,
                discriminator_column_name: discriminator_value,
                "value": value,
            }
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[key_column_name, "ts", discriminator_column_name],
            set_={"value": stmt.excluded.value},
        )
        db.execute(stmt)

    db.execute(delete(model_raw).where(model_raw.ts < cutoff))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd core-api && uv run pytest tests/jobs/test_retention_rollup_job.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the full suite**

Run: `cd core-api && uv run pytest -q`
Expected: all PASS (confirms the `models/pricing.py` and `models/__init__.py` edits didn't break anything importing those modules).

- [ ] **Step 7: Commit**

```bash
git add core-api/core_api/models/pricing.py core-api/core_api/models/__init__.py \
  core-api/core_api/jobs/retention_rollup_job.py core-api/tests/jobs/test_retention_rollup_job.py
git commit -m "feat: roll up and delete raw readings older than retention (Job 3)"
```

---

## Task 7: APScheduler wiring

**Files:**
- Modify: `core-api/pyproject.toml` (add `apscheduler` dependency)
- Create: `core-api/core_api/scheduler.py`
- Modify: `core-api/core_api/config.py` (add `scheduler_enabled`)
- Modify: `core-api/core_api/main.py` (start/stop the scheduler in `lifespan`)
- Create: `core-api/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `run_demand_interval_job` (Task 4), `run_demand_charge_job` (Task 5), `run_retention_rollup_job` (Task 6).
- Produces: `build_scheduler(session_factory: sessionmaker) -> BackgroundScheduler` with job ids `"demand_interval"`, `"demand_charge"`, `"retention_rollup"`.

- [ ] **Step 1: Add the APScheduler dependency**

Run: `cd core-api && uv add "apscheduler>=3.10,<4.0"`
Expected: `pyproject.toml` and `uv.lock` are updated; `uv run python -c "import apscheduler"` succeeds.

- [ ] **Step 2: Write the failing tests**

Create `core-api/tests/test_scheduler.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from core_api.main import create_app


def test_scheduler_starts_and_registers_all_three_jobs(app_and_db_url, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    app, db_url = app_and_db_url
    app = create_app(database_url=db_url)
    with TestClient(app) as client:
        scheduler = client.app.state.scheduler
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert job_ids == {"demand_interval", "demand_charge", "retention_rollup"}
        assert scheduler.running is True


def test_scheduler_disabled_via_env_var(app_and_db_url, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    app, db_url = app_and_db_url
    app = create_app(database_url=db_url)
    with TestClient(app) as client:
        assert getattr(client.app.state, "scheduler", None) is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_scheduler.py -v`
Expected: `ModuleNotFoundError: No module named 'core_api.scheduler'`.

- [ ] **Step 4: Write `core_api/scheduler.py`**

```python
from __future__ import annotations

import datetime
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session, sessionmaker

from core_api.jobs.demand_charge_job import run_demand_charge_job
from core_api.jobs.demand_interval_job import run_demand_interval_job
from core_api.jobs.retention_rollup_job import run_retention_rollup_job


def _run_with_session(session_factory: sessionmaker[Session], job_fn: Callable[[Session, datetime.datetime], None]) -> None:
    db = session_factory()
    try:
        job_fn(db, datetime.datetime.now(datetime.timezone.utc))
    finally:
        db.close()


def build_scheduler(session_factory: sessionmaker[Session]) -> BackgroundScheduler:
    """One in-process scheduler for all three periodic jobs -- no new
    service is justified for lightweight periodic tasks at this scale
    (spec: Purpose)."""
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: _run_with_session(session_factory, run_demand_interval_job),
        trigger=IntervalTrigger(minutes=5),
        id="demand_interval",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        lambda: _run_with_session(session_factory, run_demand_charge_job),
        trigger=CronTrigger(hour=0, minute=10),
        id="demand_charge",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        lambda: _run_with_session(session_factory, run_retention_rollup_job),
        trigger=CronTrigger(hour=0, minute=20),
        id="retention_rollup",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
```

- [ ] **Step 5: Add `scheduler_enabled` to `core_api/config.py`**

```python
@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str
    jwt_expiry_hours: int
    smtp_host: str
    smtp_port: int
    smtp_from: str
    smtp_user: str | None
    smtp_password: str | None
    frontend_base_url: str
    scheduler_enabled: bool


def get_settings() -> Settings:
    jwt_secret = os.environ.get("JWT_SECRET")
    if not jwt_secret:
        raise RuntimeError(
            "JWT_SECRET environment variable must be set (no insecure default is provided)."
        )
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "sqlite:///./core_api.db"),
        jwt_secret=jwt_secret,
        jwt_expiry_hours=int(os.environ.get("JWT_EXPIRY_HOURS", "24")),
        smtp_host=os.environ.get("SMTP_HOST", "mail-relay"),
        smtp_port=int(os.environ.get("SMTP_PORT", "25")),
        smtp_from=os.environ.get("SMTP_FROM", "noreply@localhost"),
        smtp_user=os.environ.get("SMTP_USER") or None,
        smtp_password=os.environ.get("SMTP_PASSWORD") or None,
        frontend_base_url=os.environ.get("FRONTEND_BASE_URL", "http://localhost:5173"),
        scheduler_enabled=os.environ.get("SCHEDULER_ENABLED", "true").lower() not in ("0", "false", "no"),
    )
```

- [ ] **Step 6: Wire the scheduler into `core_api/main.py`'s lifespan**

```python
from core_api.config import get_settings
from core_api.db import make_engine, make_session_factory
from core_api.email_sender import EmailSender, SmtpEmailSender
from core_api.errors import install_error_handlers
from core_api.scheduler import build_scheduler
```

and inside `create_app`:

```python
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.session_factory = session_factory
        scheduler = None
        if get_settings().scheduler_enabled:
            scheduler = build_scheduler(session_factory)
            scheduler.start()
        app.state.scheduler = scheduler
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        engine.dispose()
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_scheduler.py -v`
Expected: all PASS.

- [ ] **Step 8: Run the full suite**

Run: `cd core-api && uv run pytest -q`
Expected: all PASS — in particular, every test using the plain `client` fixture (`TestClient(app)` without a `with` block) must still pass, since `lifespan` (and therefore the scheduler) never runs for those.

- [ ] **Step 9: Commit**

```bash
git add core-api/pyproject.toml core-api/uv.lock core-api/core_api/scheduler.py \
  core-api/core_api/config.py core-api/core_api/main.py core-api/tests/test_scheduler.py
git commit -m "feat: run the three billing/retention jobs in-process via APScheduler"
```
