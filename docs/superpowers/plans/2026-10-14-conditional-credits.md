# Conditional Credits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a tariff carry conditional credits: on set days, if the average grid import between a:00 and b:00 is below y watts, credit z cents once for that day.

**Architecture:** A new `ConditionalCreditWindow` table hangs off `SiteTariff`, like `ExportAdjustmentWindow`. `compute_cost` in `cost.py` computes the credit on the fly from `grid_import_w` readings, as it does for the daily service charge. Nothing is stored per day. Two endpoints and a webapp list and form create and show the windows.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic on SQLite, pytest (core-api); React, TanStack Query, Vitest (webapp).

**Spec:** `docs/superpowers/specs/2026-10-14-conditional-credits-design.md`

## Global Constraints

- The test uses `grid_import_w` only, whatever the cost `method` (`net` or `gross`) is.
- `credit_c` and `max_avg_import_w` are `Numeric(10, 4)`. The credit is in cents, like every other money field.
- The credit applies when the average is strictly below `max_avg_import_w`.
- Window times are whole minutes with no timezone offset, and `start_time < end_time` (no wrap past midnight).
- A window needs a reading for every minute from its start up to, but not including, its end. One missing minute means no credit.
- No credit until the window's end has passed, compared with the real current time even when `prorate_fees=False`.
- The credit goes into `compute_cost` only, never `compute_cost_series`.
- No edit or delete endpoint. To change a credit, end the tariff and add a new one.
- Prose in docs, comments and commit messages follows `.claude/rules/avoid-ai-writing.md`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Python functions stay under 30 lines. No `any` in TypeScript.

## Review Focus

Inputs and conditions the spec implies that a person would hit. Each has a test in the task that owns the code.

1. **Zero-watt readings** count as readings. A day of 0 W import is the best case and must earn the credit (Task 2, `test_credit_applies_when_every_reading_is_zero_watts`).
2. **Daylight-saving days.** On the day clocks go back, a 01:00–04:00 window is 240 real minutes, not 180 (Task 2, `test_credit_window_on_dst_end_day_needs_every_real_minute`).
3. **Local date, not UTC date,** picks the weekday and month. 10:00–12:00 Sydney on a Monday is Sunday in UTC (Task 2, `test_credit_matches_weekday_and_month_in_site_local_time`).
4. **A site with no energy source** gets no credit and no error (Task 2, `test_credit_is_skipped_when_the_site_has_no_energy_source`).
5. **Times with seconds or an offset** are rejected with 422, since the minute count assumes whole minutes (Task 1, `test_create_conditional_credit_rejects_times_that_are_not_whole_minutes`).

---

## File Structure

| File | Change |
|---|---|
| `core-api/core_api/models/billing.py` | Add `ConditionalCreditWindow` |
| `core-api/alembic/versions/f3b7d2a9c814_add_conditional_credit_windows.py` | New migration |
| `core-api/core_api/schemas.py` | Add `ConditionalCreditWindowCreate` and `...Out` |
| `core-api/core_api/routers/billing.py` | Add POST and GET `/tariffs/{id}/conditional-credits` |
| `core-api/core_api/jobs/demand_config.py` | Add `conditional_credit_windows_by_tariff`, `credit_window_applies_on` |
| `core-api/core_api/cost.py` | Add credit rows to `compute_cost` |
| `core-api/tests/test_billing.py`, `core-api/tests/test_cost.py` | New tests |
| `webapp/src/billing/billingUi.ts` | New: shared helpers moved out of `BillingPage.tsx` |
| `webapp/src/billing/ConditionalCredits.tsx` + `.test.tsx` | New list and form |
| `webapp/src/billing/useBilling.ts` | New type and two hooks |
| `webapp/src/pages/BillingPage.tsx` | Import shared helpers, render the new component |
| `webapp/src/docs/06-billing.md`, `09-glossary.md` | Document the feature |

---

### Task 1: Model, migration, schemas and endpoints

**Files:**
- Modify: `core-api/core_api/models/billing.py` (after `ExportAdjustmentWindow`)
- Create: `core-api/alembic/versions/f3b7d2a9c814_add_conditional_credit_windows.py`
- Modify: `core-api/core_api/schemas.py` (after `ExportAdjustmentWindowOut`)
- Modify: `core-api/core_api/routers/billing.py`
- Test: `core-api/tests/test_billing.py`

**Interfaces:**
- Produces: `ConditionalCreditWindow` model with columns `id, site_tariff_id, label, months, days_of_week, start_time, end_time, max_avg_import_w (Decimal), credit_c (Decimal)`. Task 2 imports it from `core_api.models.billing`.
- Produces: `POST` and `GET /api/v1/sites/{site_id}/tariffs/{tariff_id}/conditional-credits`. Task 3 calls them.

- [ ] **Step 1: Install dependencies and confirm the baseline passes**

```bash
cd core-api && uv sync && uv run pytest tests/test_billing.py tests/test_bootstrap.py -q
```
Expected: all pass.

- [ ] **Step 2: Write the failing API tests**

Append to `core-api/tests/test_billing.py`:

```python
CREDIT_PAYLOAD = {
    "label": "Low evening import",
    "months": [1, 2, 3],
    "days_of_week": [0, 1, 2, 3, 4],
    "start_time": "17:00:00",
    "end_time": "21:00:00",
    "max_avg_import_w": "300",
    "credit_c": "50",
}


def _make_tariff(client, owner_id, site_id) -> int:
    response = client.post(
        f"/api/v1/sites/{site_id}/tariffs",
        json={"effective_from": "2026-01-01T00:00:00Z", "service_availability_c_per_day": "80.5"},
        headers=auth(owner_id),
    )
    return response.json()["id"]


def test_create_and_list_conditional_credit(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff_id = _make_tariff(client, owner_id, site_id)
    url = f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/conditional-credits"

    created = client.post(url, json=CREDIT_PAYLOAD, headers=auth(owner_id))
    assert created.status_code == 201, created.text
    assert created.json()["credit_c"] == "50.0000"
    assert created.json()["max_avg_import_w"] == "300.0000"

    listed = client.get(url, headers=auth(owner_id))
    assert [w["label"] for w in listed.json()] == ["Low evening import"]


def test_create_conditional_credit_for_tariff_from_another_site_is_404(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    other_owner_id, other_site_id = make_owner_and_site(db_url)
    tariff_id = _make_tariff(client, owner_id, site_id)

    response = client.post(
        f"/api/v1/sites/{other_site_id}/tariffs/{tariff_id}/conditional-credits",
        json=CREDIT_PAYLOAD,
        headers=auth(other_owner_id),
    )
    assert response.status_code == 404


import pytest


@pytest.mark.parametrize(
    "override",
    [
        {"start_time": "21:00:00", "end_time": "17:00:00"},
        {"start_time": "17:00:00", "end_time": "17:00:00"},
        {"days_of_week": []},
        {"months": []},
        {"months": [13]},
        {"days_of_week": [7]},
        {"max_avg_import_w": "0"},
        {"credit_c": "0"},
        {"credit_c": "-5"},
        {"label": ""},
    ],
)
def test_create_conditional_credit_rejects_invalid_input(client, app_and_db_url, override):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff_id = _make_tariff(client, owner_id, site_id)

    response = client.post(
        f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/conditional-credits",
        json={**CREDIT_PAYLOAD, **override},
        headers=auth(owner_id),
    )
    assert response.status_code == 422


@pytest.mark.parametrize("override", [{"start_time": "17:00:30"}, {"end_time": "21:00:00+10:00"}])
def test_create_conditional_credit_rejects_times_that_are_not_whole_minutes(client, app_and_db_url, override):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff_id = _make_tariff(client, owner_id, site_id)

    response = client.post(
        f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/conditional-credits",
        json={**CREDIT_PAYLOAD, **override},
        headers=auth(owner_id),
    )
    assert response.status_code == 422
```

Move the `import pytest` line to the top of the file with the other imports.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_billing.py -k conditional_credit -q`
Expected: FAIL (404 on the new URL, so the status assertions fail).

- [ ] **Step 4: Add the model**

In `core-api/core_api/models/billing.py`, after `ExportAdjustmentWindow`:

```python
class ConditionalCreditWindow(Base):
    """A flat credit earned on each matching day whose average grid import
    over [start_time, end_time) is strictly below max_avg_import_w. Same
    months/days-of-week/time-of-day pattern as ExportAdjustmentWindow,
    attached to a SiteTariff the same way. cost.py works it out on the fly
    and stacks every matching window; nothing is stored per day."""

    __tablename__ = "conditional_credit_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_tariff_id: Mapped[int] = mapped_column(ForeignKey("site_tariffs.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    months: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    days_of_week: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    start_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    max_avg_import_w: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    credit_c: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 4), nullable=False)
```

- [ ] **Step 5: Add the migration**

Confirm the current head first: `cd core-api && uv run alembic heads`. Expected: `a9c3e5f17b24 (head)`. If it differs, use that id as `down_revision`.

Create `core-api/alembic/versions/f3b7d2a9c814_add_conditional_credit_windows.py`:

```python
"""add conditional_credit_windows

Revision ID: f3b7d2a9c814
Revises: a9c3e5f17b24
Create Date: 2026-10-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3b7d2a9c814'
down_revision: Union[str, Sequence[str], None] = 'a9c3e5f17b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'conditional_credit_windows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('site_tariff_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('months', sa.JSON(), nullable=False),
        sa.Column('days_of_week', sa.JSON(), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('max_avg_import_w', sa.Numeric(10, 4), nullable=False),
        sa.Column('credit_c', sa.Numeric(10, 4), nullable=False),
        sa.ForeignKeyConstraint(['site_tariff_id'], ['site_tariffs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_conditional_credit_windows_site_tariff_id'),
        'conditional_credit_windows',
        ['site_tariff_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_conditional_credit_windows_site_tariff_id'), table_name='conditional_credit_windows')
    op.drop_table('conditional_credit_windows')
```

- [ ] **Step 6: Add the schemas**

In `core-api/core_api/schemas.py`, after `ExportAdjustmentWindowOut`:

```python
class ConditionalCreditWindowCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    months: list[int] = Field(min_length=1)
    days_of_week: list[int] = Field(min_length=1)
    start_time: datetime.time
    end_time: datetime.time
    max_avg_import_w: decimal.Decimal = Field(gt=0, max_digits=10, decimal_places=4)
    credit_c: decimal.Decimal = Field(gt=0, max_digits=10, decimal_places=4)

    @model_validator(mode="after")
    def _check_pattern(self) -> ConditionalCreditWindowCreate:
        if any(month < 1 or month > 12 for month in self.months):
            raise ValueError("months must be between 1 and 12")
        if any(day < 0 or day > 6 for day in self.days_of_week):
            raise ValueError("days_of_week must be between 0 (Monday) and 6 (Sunday)")
        # cost.py counts one reading per minute, and SQLite drops an offset.
        if any(t.tzinfo is not None for t in (self.start_time, self.end_time)):
            raise ValueError("start_time and end_time must not carry a timezone offset")
        if any(t.second or t.microsecond for t in (self.start_time, self.end_time)):
            raise ValueError("start_time and end_time must be whole minutes")
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class ConditionalCreditWindowOut(ConditionalCreditWindowCreate):
    id: int
    site_tariff_id: int

    model_config = {"from_attributes": True}
```

- [ ] **Step 7: Add the endpoints**

In `core-api/core_api/routers/billing.py`, add `ConditionalCreditWindow` to the `core_api.models.billing` import and `ConditionalCreditWindowCreate`, `ConditionalCreditWindowOut` to the `core_api.schemas` import (keep both lists alphabetical). Then, after `list_export_adjustment_windows`:

```python
@router.post(
    "/tariffs/{tariff_id}/conditional-credits",
    response_model=ConditionalCreditWindowOut,
    status_code=status.HTTP_201_CREATED,
)
def create_conditional_credit_window(
    tariff_id: int,
    payload: ConditionalCreditWindowCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> ConditionalCreditWindow:
    return _create_tariff_child(db, site, tariff_id, ConditionalCreditWindow, payload)


@router.get("/tariffs/{tariff_id}/conditional-credits", response_model=list[ConditionalCreditWindowOut])
def list_conditional_credit_windows(
    tariff_id: int,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[ConditionalCreditWindow]:
    return _list_tariff_children(db, site, tariff_id, ConditionalCreditWindow)
```

Also add `ConditionalCreditWindow` to the docstring list in `_create_tariff_child` ("every window type (DemandWindow, ExportAdjustmentWindow, ...)").

- [ ] **Step 8: Run the tests**

Run: `cd core-api && uv run pytest tests/test_billing.py tests/test_bootstrap.py tests/test_schema_constraints.py -q`
Expected: all pass. `test_bootstrap` fails if the migration and model disagree.

- [ ] **Step 9: Commit**

```bash
git add core-api
git commit -m "feat(core-api): add conditional credit windows to tariffs

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Credit calculation in compute_cost

**Files:**
- Modify: `core-api/core_api/jobs/demand_config.py` (after `total_export_adjustment_c_per_kwh`)
- Modify: `core-api/core_api/cost.py`
- Test: `core-api/tests/test_cost.py`

**Interfaces:**
- Consumes: `ConditionalCreditWindow` from Task 1.
- Produces in `demand_config.py`:
  - `conditional_credit_windows_by_tariff(db: Session, site_id: int) -> dict[int, list[ConditionalCreditWindow]]`
  - `credit_window_applies_on(window: ConditionalCreditWindow, local_date: datetime.date) -> bool`
- Produces in `cost.py`: `_window_average_import_w(db, energy_source_ids: list[int], start_utc, end_utc) -> decimal.Decimal | None` and `_conditional_credit_rows(db, site_id, days, site_timezone, tariffs, real_now_utc) -> list[_MinuteCost]`.

- [ ] **Step 1: Write the failing tests**

In `core-api/tests/test_cost.py`, add `ConditionalCreditWindow` to the `core_api.models.billing` import, then add after `_make_pricing_source`:

```python
def _make_credit(
    db, tariff, credit_c="50", max_avg_import_w="300", months=None, days_of_week=None, start="10:00", end="12:00"
) -> ConditionalCreditWindow:
    window = ConditionalCreditWindow(
        site_tariff_id=tariff.id,
        label="Low import credit",
        months=months or list(range(1, 13)),
        days_of_week=days_of_week if days_of_week is not None else [0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time.fromisoformat(start),
        end_time=datetime.time.fromisoformat(end),
        max_avg_import_w=decimal.Decimal(max_avg_import_w),
        credit_c=decimal.Decimal(credit_c),
    )
    db.add(window)
    db.commit()
    return window


def _add_readings(db, source, start, minutes, watts, metric=EnergyMetric.GRID_IMPORT_W, skip_minute=None):
    for i in range(minutes):
        if i == skip_minute:
            continue
        db.add(
            EnergyReading(
                source_id=source.id, ts=start + datetime.timedelta(minutes=i), metric=metric, value=float(watts)
            )
        )
    db.commit()


# 2026-01-07 is a Wednesday (weekday 2); Sydney is UTC+11 in January.
CREDIT_DAY = (2026, 1, 7)
AFTER_WINDOW = sydney_local(2026, 1, 7, 13, 0)


def _credit_total(db, site, now=AFTER_WINDOW, start=None, end=None, method="net"):
    return compute_cost(
        db,
        site.id,
        start or sydney_local(*CREDIT_DAY),
        end or sydney_local(2026, 1, 8),
        method=method,
        site_timezone=site.timezone,
        now=now,
    ).total_cost_c


def _credit_setup(db, site, watts=200, minutes=120, **credit_kwargs):
    """A zero-service-charge tariff with one 10:00-12:00 credit, and `minutes`
    of grid import at `watts` from 10:00 on CREDIT_DAY."""
    source = _make_energy_source(db, site)
    tariff = _make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC), service_c_per_day="0")
    _make_credit(db, tariff, **credit_kwargs)
    _add_readings(db, source, sydney_local(*CREDIT_DAY, 10, 0), minutes, watts)
    return source, tariff
```

Then add the tests at the end of the file:

```python
def test_credit_applies_when_average_import_is_below_the_threshold(db, site):
    _credit_setup(db, site, watts=200)

    assert _credit_total(db, site) == decimal.Decimal("-50")


def test_no_credit_when_average_import_equals_the_threshold(db, site):
    _credit_setup(db, site, watts=300)

    assert _credit_total(db, site) == decimal.Decimal("0")


def test_credit_uses_the_average_not_every_minute(db, site):
    source, _ = _credit_setup(db, site, watts=100, minutes=60)
    _add_readings(db, source, sydney_local(*CREDIT_DAY, 11, 0), 60, 450)  # average 275 W

    assert _credit_total(db, site) == decimal.Decimal("-50")


def test_no_credit_when_one_minute_has_no_reading(db, site):
    _credit_setup(db, site, watts=200, minutes=119)

    assert _credit_total(db, site) == decimal.Decimal("0")


def test_credit_applies_when_every_reading_is_zero_watts(db, site):
    _credit_setup(db, site, watts=0)

    assert _credit_total(db, site) == decimal.Decimal("-50")


def test_no_credit_before_the_window_has_ended(db, site):
    _credit_setup(db, site, watts=200)

    assert _credit_total(db, site, now=sydney_local(2026, 1, 7, 11, 0)) == decimal.Decimal("0")


def test_credit_counts_once_the_window_end_is_reached(db, site):
    _credit_setup(db, site, watts=200)

    assert _credit_total(db, site, now=sydney_local(2026, 1, 7, 12, 0)) == decimal.Decimal("-50")


def test_no_credit_on_a_day_that_does_not_match(db, site):
    _credit_setup(db, site, watts=200, days_of_week=[0])  # Monday only

    assert _credit_total(db, site) == decimal.Decimal("0")


def test_no_credit_in_a_month_that_does_not_match(db, site):
    _credit_setup(db, site, watts=200, months=[2])

    assert _credit_total(db, site) == decimal.Decimal("0")


def test_credit_matches_weekday_and_month_in_site_local_time(db, site):
    """10:00-12:00 Sydney on Monday 2026-02-02 is Sunday 2026-02-01 in UTC."""
    source = _make_energy_source(db, site)
    tariff = _make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC), service_c_per_day="0")
    _make_credit(db, tariff, months=[2], days_of_week=[0])
    _add_readings(db, source, sydney_local(2026, 2, 2, 10, 0), 120, 200)

    total = _credit_total(
        db, site, now=sydney_local(2026, 2, 2, 13, 0), start=sydney_local(2026, 2, 2), end=sydney_local(2026, 2, 3)
    )

    assert total == decimal.Decimal("-50")


def test_two_matching_credits_both_apply(db, site):
    _, tariff = _credit_setup(db, site, watts=200)
    _make_credit(db, tariff, credit_c="30", max_avg_import_w="250")

    assert _credit_total(db, site) == decimal.Decimal("-80")


def test_credit_comes_from_the_tariff_in_force_that_day(db, site):
    source = _make_energy_source(db, site)
    old = _make_tariff(
        db, site, datetime.datetime(2025, 12, 1, tzinfo=UTC), effective_to=sydney_local(*CREDIT_DAY), service_c_per_day="0"
    )
    new = _make_tariff(db, site, sydney_local(*CREDIT_DAY), service_c_per_day="0")
    _make_credit(db, old, credit_c="50")
    _make_credit(db, new, credit_c="30")
    _add_readings(db, source, sydney_local(*CREDIT_DAY, 10, 0), 120, 200)

    assert _credit_total(db, site) == decimal.Decimal("-30")


def test_credit_is_the_same_under_net_and_gross(db, site):
    source, _ = _credit_setup(db, site, watts=200)
    _add_readings(db, source, sydney_local(*CREDIT_DAY, 10, 0), 120, 5000, metric=EnergyMetric.CONSUMPTION_W)

    assert _credit_total(db, site, method="net") == decimal.Decimal("-50")
    assert _credit_total(db, site, method="gross") == decimal.Decimal("-50")


def test_credit_is_found_when_the_range_ends_before_the_window(db, site):
    _credit_setup(db, site, watts=200)

    total = _credit_total(db, site, end=sydney_local(2026, 1, 7, 9, 0))

    assert total == decimal.Decimal("-50")


def test_credit_is_skipped_when_the_site_has_no_energy_source(db, site):
    tariff = _make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC), service_c_per_day="0")
    _make_credit(db, tariff)

    assert _credit_total(db, site) == decimal.Decimal("0")


def test_credit_window_on_dst_end_day_needs_every_real_minute(db, site):
    """Sydney clocks go back on 2026-04-05 (03:00 AEDT -> 02:00 AEST), so
    01:00-04:00 local is 240 real minutes."""
    source = _make_energy_source(db, site)
    tariff = _make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC), service_c_per_day="0")
    _make_credit(db, tariff, start="01:00", end="04:00")
    start = sydney_local(2026, 4, 5, 1, 0)
    now = sydney_local(2026, 4, 5, 6, 0)
    day_start, day_end = sydney_local(2026, 4, 5), sydney_local(2026, 4, 6)

    _add_readings(db, source, start, 180, 200)  # the 180 minutes a 3-hour window would need
    assert _credit_total(db, site, now=now, start=day_start, end=day_end) == decimal.Decimal("0")

    _add_readings(db, source, start + datetime.timedelta(minutes=180), 60, 200)
    assert _credit_total(db, site, now=now, start=day_start, end=day_end) == decimal.Decimal("-50")


def test_series_leaves_the_credit_out(db, site):
    _credit_setup(db, site, watts=200)

    points = compute_cost_series(
        db,
        site.id,
        sydney_local(*CREDIT_DAY),
        sydney_local(2026, 1, 8),
        method="net",
        bucket="hour",
        site_timezone=site.timezone,
    )

    assert all(p.cost_c >= 0 for p in points)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_cost.py -k "credit" -q`
Expected: FAIL. The ones expecting `-50` get `0`. The ones expecting `0` already pass, which is fine.

- [ ] **Step 3: Add the helpers to `demand_config.py`**

Add `ConditionalCreditWindow` to the `core_api.models.billing` import, then after `total_export_adjustment_c_per_kwh`:

```python
def conditional_credit_windows_by_tariff(db: Session, site_id: int) -> dict[int, list[ConditionalCreditWindow]]:
    return _windows_by_tariff(db, site_id, ConditionalCreditWindow)


def credit_window_applies_on(window: ConditionalCreditWindow, local_date: datetime.date) -> bool:
    """Does `local_date` (a calendar day in the site's time zone) fall in the
    window's months and days of week? The time of day is not checked here:
    the credit looks at the whole window on that day, not at one instant."""
    return local_date.month in window.months and local_date.weekday() in window.days_of_week
```

- [ ] **Step 4: Add the calculation to `cost.py`**

Add `conditional_credit_windows_by_tariff` and `credit_window_applies_on` to the `core_api.jobs.demand_config` import list (alphabetical). After `_monthly_fee_rows`, add:

```python
def _window_average_import_w(
    db: Session, energy_source_ids: list[int], start_utc: datetime.datetime, end_utc: datetime.datetime
) -> decimal.Decimal | None:
    """Mean grid_import_w over the whole minutes of [start_utc, end_utc), or
    None when any of those minutes has no reading -- a collector outage must
    not look like a quiet window. Zero-watt readings count as readings."""
    expected_minutes = int((end_utc - start_utc).total_seconds() // 60)
    readings = _energy_by_ts(db, energy_source_ids, EnergyMetric.GRID_IMPORT_W, start_utc, end_utc)
    if expected_minutes <= 0 or len(readings) < expected_minutes:
        return None
    return sum(readings.values(), decimal.Decimal("0")) / len(readings)


def _conditional_credit_rows(
    db: Session,
    site_id: int,
    days: list[tuple[datetime.datetime, datetime.datetime]],
    site_timezone: str,
    tariffs: list[SiteTariff],
    real_now_utc: datetime.datetime,
) -> list[_MinuteCost]:
    """One negative row per matching ConditionalCreditWindow on each local day
    (days resolved by the caller, as for _service_charge_rows) whose average
    grid import over [start_time, end_time) is strictly below max_avg_import_w.
    The window's bounds are local wall-clock times converted to UTC, so a DST
    day has its real length. A window that has not ended by `real_now_utc`
    earns nothing yet. Windows stack. Used only by compute_cost's total,
    never compute_cost_series, for the same reason as the daily fees."""
    windows_by_tariff = conditional_credit_windows_by_tariff(db, site_id)
    if not days or not windows_by_tariff:
        return []
    tz = site_tz(site_timezone)
    energy_source_ids = _source_ids(db, site_id, SourceKind.ENERGY)
    rows = []
    for midnight, _next_midnight in days:
        tariff = tariff_effective_at(tariffs, midnight)
        if tariff is None:
            continue
        local_date = midnight.astimezone(tz).date()
        for window in windows_by_tariff.get(tariff.id, []):
            if not credit_window_applies_on(window, local_date):
                continue
            start_utc = datetime.datetime.combine(local_date, window.start_time, tzinfo=tz).astimezone(datetime.timezone.utc)
            end_utc = datetime.datetime.combine(local_date, window.end_time, tzinfo=tz).astimezone(datetime.timezone.utc)
            if end_utc > real_now_utc:
                continue
            average_w = _window_average_import_w(db, energy_source_ids, start_utc, end_utc)
            if average_w is not None and average_w < window.max_avg_import_w:
                rows.append(
                    _MinuteCost(
                        ts=end_utc,
                        cost_c=-window.credit_c,
                        import_kwh=decimal.Decimal("0"),
                        export_kwh=decimal.Decimal("0"),
                    )
                )
    return rows
```

- [ ] **Step 5: Call it from `compute_cost`**

Replace the `now_utc = ...` line in `compute_cost`:

```python
    real_now_utc = as_utc(now) if now is not None else datetime.datetime.now(datetime.timezone.utc)
    now_utc = real_now_utc if prorate_fees else None
```

After the `_monthly_fee_rows` loop, add:

```python
    for credit in _conditional_credit_rows(db, site_id, days, site_timezone, tariffs, real_now_utc):
        total_cost_c += credit.cost_c
```

Add a sentence to `compute_cost`'s docstring: "Conditional credits always compare their window end with the real current time, whatever `prorate_fees` is."

- [ ] **Step 6: Run the tests**

Run: `cd core-api && uv run pytest -q`
Expected: all pass, including the existing cost tests (the `now_utc` change keeps their behavior).

- [ ] **Step 7: Check function length and commit**

`_conditional_credit_rows` is the longest function. If it is over 30 lines, extract the two `combine(...)` lines into `_local_window_bounds_utc(local_date, window, tz) -> tuple[datetime.datetime, datetime.datetime]` and re-run the tests.

```bash
git add core-api
git commit -m "feat(core-api): apply conditional credits in compute_cost

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Webapp list and form

**Files:**
- Create: `webapp/src/billing/billingUi.ts`
- Create: `webapp/src/billing/ConditionalCredits.tsx`
- Create: `webapp/src/billing/ConditionalCredits.test.tsx`
- Modify: `webapp/src/billing/useBilling.ts`
- Modify: `webapp/src/pages/BillingPage.tsx`

**Interfaces:**
- Consumes: the two endpoints from Task 1.
- Produces in `useBilling.ts`: `ConditionalCreditWindow`, `useConditionalCreditWindows(siteId, tariffId)`, `useCreateConditionalCreditWindow(siteId, tariffId)`.
- Produces in `ConditionalCredits.tsx`: `ConditionalCreditList({ siteId, tariffId })` and `ConditionalCreditForm({ siteId, tariffId })`.
- Produces in `billingUi.ts`: `inputClass`, `primaryButtonClass`, `errorClass`, `formatMonths`, `formatDaysOfWeek`, `formatCents`, `formatTime`, `parseIntList` (moved unchanged from `BillingPage.tsx`).

- [ ] **Step 1: Install dependencies and confirm the baseline**

```bash
cd webapp && npm ci && npm test -- --run && npm run lint
```
Expected: pass.

- [ ] **Step 2: Move the shared helpers out of `BillingPage.tsx`**

Create `webapp/src/billing/billingUi.ts` holding these, cut from `BillingPage.tsx` and exported: `inputClass`, `primaryButtonClass`, `errorClass`, `MONTH_NAMES`, `DAY_NAMES`, `formatMonths`, `formatDaysOfWeek`, `formatCents`, `formatTime`, `parseIntList`. Keep their bodies identical. In `BillingPage.tsx`, delete the originals and add:

```ts
import {
  errorClass,
  formatCents,
  formatDaysOfWeek,
  formatMonths,
  formatTime,
  inputClass,
  parseIntList,
  primaryButtonClass,
} from "../billing/billingUi";
```

`MONTH_NAMES` and `DAY_NAMES` are only used by the two format functions, so they stay private in `billingUi.ts` (not exported). Leave `endLinkClass`, `disabledClass`, `isActive`, `isExpired`, `formatSignedCents`, `formatCentsAsDollars` and `dollarsToCents` where they are.

Run: `cd webapp && npm run build && npm run lint && npm test -- --run`
Expected: pass. This step changes no behavior.

- [ ] **Step 3: Write the failing component tests**

Create `webapp/src/billing/ConditionalCredits.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { ConditionalCreditForm, ConditionalCreditList } from "./ConditionalCredits";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const PATH = "/api/v1/sites/7/tariffs/3/conditional-credits";

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("ConditionalCredits", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("lists a credit with its threshold, amount and pattern", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      {
        id: 1,
        site_tariff_id: 3,
        label: "Low evening import",
        months: [1, 2, 3],
        days_of_week: [0, 1, 2, 3, 4],
        start_time: "17:00:00",
        end_time: "21:00:00",
        max_avg_import_w: "300.0000",
        credit_c: "50.0000",
      },
    ]);
    renderWithClient(<ConditionalCreditList siteId={7} tariffId={3} />);

    expect(await screen.findByText("Low evening import")).toBeInTheDocument();
    expect(screen.getByText(/50\.00c credit/)).toBeInTheDocument();
    expect(screen.getByText(/17:00–21:00/)).toBeInTheDocument();
    expect(screen.getByText(/average import below 300 W/i)).toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledWith(PATH);
  });

  it("posts the parsed form values", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({});
    renderWithClient(<ConditionalCreditForm siteId={7} tariffId={3} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Credit label"), "Low evening import");
    await user.type(screen.getByLabelText("Credit months"), "1, 2,3");
    await user.type(screen.getByLabelText("Credit days of week"), "0,1,2,3,4");
    await user.type(screen.getByLabelText("Credit start time"), "17:00");
    await user.type(screen.getByLabelText("Credit end time"), "21:00");
    await user.type(screen.getByLabelText("Average import below (W)"), "300");
    await user.type(screen.getByLabelText("Credit (cents)"), "50");
    await user.click(screen.getByRole("button", { name: "Add conditional credit" }));

    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path, init] = vi.mocked(apiFetch).mock.calls[0];
    expect(path).toBe(PATH);
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      label: "Low evening import",
      months: [1, 2, 3],
      days_of_week: [0, 1, 2, 3, 4],
      start_time: "17:00",
      end_time: "21:00",
      max_avg_import_w: 300,
      credit_c: 50,
    });
  });

  it("shows the API error message when the create fails", async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("start_time must be before end_time"));
    renderWithClient(<ConditionalCreditForm siteId={7} tariffId={3} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Credit label"), "x");
    await user.type(screen.getByLabelText("Credit months"), "1");
    await user.type(screen.getByLabelText("Credit days of week"), "0");
    await user.type(screen.getByLabelText("Credit start time"), "21:00");
    await user.type(screen.getByLabelText("Credit end time"), "17:00");
    await user.type(screen.getByLabelText("Average import below (W)"), "300");
    await user.type(screen.getByLabelText("Credit (cents)"), "50");
    await user.click(screen.getByRole("button", { name: "Add conditional credit" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("start_time must be before end_time");
  });
});
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd webapp && npm test -- --run src/billing/ConditionalCredits.test.tsx`
Expected: FAIL (module `./ConditionalCredits` not found).

- [ ] **Step 5: Add the hooks**

In `webapp/src/billing/useBilling.ts`, after the `ExportAdjustmentWindow` interface:

```ts
export interface ConditionalCreditWindow {
  id: number;
  site_tariff_id: number;
  label: string;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  // Serialized by core-api as decimal strings, not JSON numbers.
  max_avg_import_w: string;
  credit_c: string;
}
```

After `CreateExportAdjustmentWindowInput`:

```ts
interface CreateConditionalCreditWindowInput {
  label: string;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  max_avg_import_w: number;
  credit_c: number;
}
```

After `useCreateExportAdjustmentWindow`:

```ts
export function useConditionalCreditWindows(siteId: number, tariffId: number) {
  return useQuery({
    queryKey: ["conditional-credits", siteId, tariffId],
    queryFn: () =>
      apiFetch<ConditionalCreditWindow[]>(`/api/v1/sites/${siteId}/tariffs/${tariffId}/conditional-credits`),
  });
}

export function useCreateConditionalCreditWindow(siteId: number, tariffId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateConditionalCreditWindowInput) =>
      apiFetch<ConditionalCreditWindow>(`/api/v1/sites/${siteId}/tariffs/${tariffId}/conditional-credits`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conditional-credits", siteId, tariffId] });
    },
  });
}
```

- [ ] **Step 6: Add the component**

Create `webapp/src/billing/ConditionalCredits.tsx`:

```tsx
import { type FormEvent, useState } from "react";
import {
  errorClass,
  formatCents,
  formatDaysOfWeek,
  formatMonths,
  formatTime,
  inputClass,
  parseIntList,
  primaryButtonClass,
} from "./billingUi";
import { useConditionalCreditWindows, useCreateConditionalCreditWindow } from "./useBilling";

interface TariffProps {
  siteId: number;
  tariffId: number;
}

export function ConditionalCreditList({ siteId, tariffId }: TariffProps) {
  const { data: windows } = useConditionalCreditWindows(siteId, tariffId);

  return (
    <ul className="space-y-2">
      {(windows ?? []).map((w) => (
        <li key={w.id} className="space-y-0.5 rounded-md border border-line/60 px-3 py-1.5">
          <div className="flex items-baseline justify-between gap-2 text-sm">
            <span className="text-ink">{w.label}</span>
            <span className="font-display tabular-nums text-ink">{formatCents(w.credit_c)}c credit</span>
          </div>
          <p className="text-xs text-ink-muted">
            Average import below {Number(w.max_avg_import_w)} W · {formatTime(w.start_time)}–{formatTime(w.end_time)}{" "}
            · {formatDaysOfWeek(w.days_of_week)} · {formatMonths(w.months)}
          </p>
        </li>
      ))}
    </ul>
  );
}

export function ConditionalCreditForm({ siteId, tariffId }: TariffProps) {
  const [label, setLabel] = useState("");
  const [months, setMonths] = useState("");
  const [daysOfWeek, setDaysOfWeek] = useState("");
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [maxAvgImportW, setMaxAvgImportW] = useState("");
  const [creditC, setCreditC] = useState("");
  const createWindow = useCreateConditionalCreditWindow(siteId, tariffId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createWindow.mutate({
      label,
      months: parseIntList(months),
      days_of_week: parseIntList(daysOfWeek),
      start_time: startTime,
      end_time: endTime,
      max_avg_import_w: Number(maxAvgImportW),
      credit_c: Number(creditC),
    });
  }

  return (
    <div className="ml-2 space-y-2 border-l-2 border-line pl-4">
      {createWindow.isError && (
        <p role="alert" className={errorClass}>
          {createWindow.error instanceof Error
            ? createWindow.error.message
            : "Failed to create conditional credit. Please try again."}
        </p>
      )}
      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Credit label"
          aria-label="Credit label"
          className={inputClass}
          required
        />
        <input
          value={months}
          onChange={(e) => setMonths(e.target.value)}
          placeholder="Credit months (e.g. 12,1,2)"
          aria-label="Credit months"
          inputMode="numeric"
          className={inputClass}
          required
        />
        <input
          value={daysOfWeek}
          onChange={(e) => setDaysOfWeek(e.target.value)}
          placeholder="Credit days of week (0=Mon .. 6=Sun)"
          aria-label="Credit days of week"
          inputMode="numeric"
          className={inputClass}
          required
        />
        <input
          value={startTime}
          onChange={(e) => setStartTime(e.target.value)}
          placeholder="Credit start time (HH:MM)"
          aria-label="Credit start time"
          className={inputClass}
          required
        />
        <input
          value={endTime}
          onChange={(e) => setEndTime(e.target.value)}
          placeholder="Credit end time (HH:MM)"
          aria-label="Credit end time"
          className={inputClass}
          required
        />
        <input
          value={maxAvgImportW}
          onChange={(e) => setMaxAvgImportW(e.target.value)}
          placeholder="Average import below (W)"
          aria-label="Average import below (W)"
          inputMode="decimal"
          className={inputClass}
          required
        />
        <input
          value={creditC}
          onChange={(e) => setCreditC(e.target.value)}
          placeholder="Credit (cents)"
          aria-label="Credit (cents)"
          inputMode="decimal"
          className={inputClass}
          required
        />
        <button type="submit" disabled={createWindow.isPending} className={primaryButtonClass}>
          Add conditional credit
        </button>
      </form>
    </div>
  );
}
```

- [ ] **Step 7: Render it on the Billing page**

In `BillingPage.tsx`, add `import { ConditionalCreditForm, ConditionalCreditList } from "../billing/ConditionalCredits";`. In the active-tariff block, add the list after `ExportAdjustmentWindowsList` and the form after `ExportAdjustmentWindowForm`:

```tsx
<ConditionalCreditList siteId={siteId} tariffId={tariff.id} />
```
```tsx
<ConditionalCreditForm siteId={siteId} tariffId={tariff.id} />
```

In the expanded ended-tariff block, add only the list after `ExportAdjustmentWindowsList`.

- [ ] **Step 8: Run the checks**

Run: `cd webapp && npm test -- --run && npm run lint && npm run build`
Expected: all pass, no `any`, no unused imports.

- [ ] **Step 9: Commit**

```bash
git add webapp
git commit -m "feat(webapp): add conditional credits to the tariff card

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Help docs

**Files:**
- Modify: `webapp/src/docs/06-billing.md`
- Modify: `webapp/src/docs/09-glossary.md`

`webapp/src/help/docs.test.ts` fails on a broken link or anchor, so the links below are checked by the test.

- [ ] **Step 1: Update the cost list and the chart sentence**

In `06-billing.md`, after item 5 of "How the cost is calculated", add:

```markdown
6. **Conditional credits**: a [conditional credit](#conditional-credits) subtracts a fixed amount for each day your average grid import in its time window stays below its limit.
```

Change "include all five parts" to "include all six parts", and "plus the demand charge, so fixed fees do not show up as a spike at midnight" to "plus the demand charge, so fixed fees and conditional credits do not show up as a spike at one instant".

- [ ] **Step 2: Update the other mentions**

- "Before you start": add the bullet `- Any credit for keeping grid import low during set hours: its hours, days, months, power limit and amount in cents.`
- Under "## Tariffs", change "demand windows, export adjustments and fee discounts on this page" to "demand windows, export adjustments, conditional credits and fee discounts on this page".
- In the "Changing a tariff" note, change "Demand windows, export adjustments and fee discounts belong to one tariff." to "Demand windows, export adjustments, conditional credits and fee discounts belong to one tariff."

- [ ] **Step 3: Add the section**

After the "Export adjustments" section, before "Monthly fee discounts":

```markdown
### Conditional credits

A **conditional credit** takes a fixed amount off your bill on each day that your average grid import, between two times of day, stays below a limit. Use it for a retailer offer such as "keep your evening import under 300 W and get 50 cents".

| Field | What to enter |
|-------|---------------|
| **Credit label** | A name. |
| **Credit months**, **Credit days of week**, **Credit start time**, **Credit end time** | Same format as a demand window. The end time must be later than the start time, so a window cannot cross midnight. |
| **Average import below (W)** | The limit in watts. |
| **Credit (cents)** | The amount for each qualifying day. |

Click **Add conditional credit**. On each matching day, WattWise averages your grid import from the start time up to the end time. If the average is below the limit, that day earns the credit.

- The test uses power drawn from the grid only. Power you export does not offset it, and it does not change with the [net or gross](05-dashboard.md#net-and-gross-cost) setting.
- Equal to the limit does not qualify.
- A day earns nothing until its end time has passed.
- If any minute in the window has no reading, for example because a collector was offline, that day earns nothing.
- If several credits qualify on the same day, they add together.
- Credits lower the **Cost** total and the billing period tiles. They do not appear on the history chart.
```

- [ ] **Step 4: Add the glossary entry**

In `09-glossary.md`, between **Collector** and **Consumption**:

```markdown
| **Conditional credit** | A fixed amount taken off your bill on days when your average grid import in a set time window stays below a limit. See [Conditional credits](06-billing.md#conditional-credits). |
```

- [ ] **Step 5: Run the docs test**

Run: `cd webapp && npm test -- --run src/help`
Expected: pass, including link and anchor checks.

- [ ] **Step 6: Commit**

```bash
git add webapp/src/docs
git commit -m "docs(help): document conditional credits

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] `cd core-api && uv run pytest -q` passes.
- [ ] `cd webapp && npm test -- --run && npm run lint && npm run build` passes.
- [ ] `uv run alembic upgrade head` on a scratch database ends at `f3b7d2a9c814`, and `alembic downgrade -1` then `upgrade head` works.
- [ ] Run `/simplify` over the branch diff, as `CLAUDE.md` requires, before presenting the result.
