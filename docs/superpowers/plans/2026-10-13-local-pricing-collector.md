# Local pricing collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user with a fixed or time-of-use tariff enter rate windows in the webapp, and have a new `local-pricing-collector` turn them into price intervals, alongside a renamed `amber-pricing-collector`.

**Architecture:** Core-api stores rate windows per local pricing source in a new `local_rate_windows` table, validates them (no overlaps per channel), and hands them to the collector inside the existing `GET /sources/{id}/config`. The collector builds hour-capped `PriceIntervalIn` rows for the next 48 hours every 5 minutes and pushes them to the existing `POST /sources/{id}/prices`. Collectors find their sources through a new `provider` filter, and core-api rejects a request whose `X-Collector-Provider` header does not match the source.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, Alembic, SQLite, httpx, pytest, respx, React, TanStack Query, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-13-local-pricing-collector-design.md`

## Global Constraints

- Run all commands from the `wattwise/` directory (the git root is its parent). Core-api tests: `cd core-api && uv run pytest`. Collector tests: `cd <collector> && uv run pytest`. Webapp: `cd webapp && npm test` and `npm run lint`.
- Each collector is its own package and shares no code with the other. The local collector copies the client pattern from the Amber one.
- The env var stays `PRICING_COLLECTOR_KEY` for every pricing collector. Core-api's `pricing_collector_key` setting is not renamed.
- Channels are `buy` (import), `sell` (export), `controlled_load`. Rates are entered positive in c/kWh. The collector pushes `sell` negative, and `0` stays `0`.
- `start_time` is included and `end_time` is excluded. `end_time` earlier than `start_time` wraps past midnight. Equal times mean all day. Times are whole minutes.
- `months` are 1-12. `days_of_week` are Monday=0 to Sunday=6. Both describe the minute being priced.
- A price interval must not span more than 1440 minutes (`MAX_PRICE_INTERVAL_MINUTES`). The local collector caps intervals at 60 minutes.
- A site has at most one active pricing source. Core-api already enforces this.
- Response timestamp fields use `UtcDatetime`, never plain `datetime`.
- `httpx>=0.27,<1.0`, `pytest>=8.0,<9.0`, `respx>=0.21,<1.0`, `requires-python = ">=3.11"`, matching the Amber collector.
- Prose in code comments, docs and commit messages follows `.claude/rules/avoid-ai-writing.md`. Functions stay under 30 lines, with no `any` in TypeScript.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Inputs the spec implies that no obvious test would reach. Each has a test in the task named.

1. A new local source with no rate windows yet: config returns an empty list, the collector pushes nothing and does not error, the webapp shows empty tables and no warning banner. (Tasks 5, 6, 7)
2. Two windows that touch at a boundary, such as 00:00-07:00 and 07:00-22:00: they must save without an overlap error. (Task 4)
3. A window whose `effective_to` falls inside the 48-hour horizon: prices stop at that instant and the next window takes over. (Task 6)
4. A free-export `sell` rate of `0`: pushed as `0`, not `-0`. (Task 6)
5. Renaming a local source to a blank name: rejected with 422, so the Billing heading never becomes " Tariffs". (Task 5)

## File Structure

Core-api (`core-api/`):
- Create `core_api/models/local_rates.py`: the `LocalRateWindow` model.
- Create `alembic/versions/e5a1c3d7f902_add_local_rate_windows.py`: the migration.
- Create `core_api/local_rates.py`: pure overlap logic (`minutes_of_day`, `windows_overlap`, `find_overlap`).
- Create `core_api/routers/local_rates.py`: rate window CRUD.
- Modify `core_api/schemas.py`, `core_api/deps.py`, `core_api/routers/collector.py`, `core_api/routers/collector_config.py`, `core_api/routers/sources.py`, `core_api/models/__init__.py`, `core_api/main.py`.
- Tests: `tests/test_collector_provider.py`, `tests/test_local_rate_windows_model.py`, `tests/test_local_rates.py`, `tests/test_local_rate_windows_api.py`, plus additions to `tests/test_cost.py` and `tests/test_sources.py`.

Collectors:
- Rename `pricing-collector/` to `amber-pricing-collector/` (package `amber_pricing_collector`).
- Create `local-pricing-collector/` with `config.py`, `core_api_client.py`, `rates.py`, `scheduling.py`, `poll.py`, `main.py`, `Dockerfile`, `pyproject.toml` and tests.

Webapp (`webapp/src/`):
- Create `sources/useRateWindows.ts`, `sources/rateCoverage.ts`, `sources/RateWindowsPanel.tsx`, `billing/tariffHeading.ts` and tests.
- Modify `sources/useSources.ts`, `pages/SourcesPage.tsx`, `pages/BillingPage.tsx`, `docs/04-sources.md`, `docs/06-billing.md`, `docs/09-glossary.md`.

Root: `docker-compose.yml`, `README.md`, `.gitignore`, `.claude/agents/documentation.md`.

---

### Task 1: Rename pricing-collector to amber-pricing-collector

**Files:**
- Rename: `pricing-collector/` to `amber-pricing-collector/`, and its package `pricing_collector/` to `amber_pricing_collector/`
- Modify: `amber-pricing-collector/pyproject.toml`, `amber-pricing-collector/Dockerfile`, `amber-pricing-collector/uv.lock`, every `.py` file under it, `docker-compose.yml`, `README.md`, `.gitignore`, `.claude/agents/documentation.md`

**Interfaces:**
- Produces: package `amber_pricing_collector` with the same modules as before. Task 2 edits `amber_pricing_collector/core_api_client.py`.

- [ ] **Step 1: Rename the directory and package**

```bash
git mv pricing-collector amber-pricing-collector
git mv amber-pricing-collector/pricing_collector amber-pricing-collector/amber_pricing_collector
```

- [ ] **Step 2: Rewrite names inside the collector**

The lookbehind stops the substitution from matching `amber_pricing_collector` again, and the lookahead leaves `pricing_collector_key` alone.

```bash
find amber-pricing-collector -type f \( -name '*.py' -o -name 'Dockerfile' -o -name 'pyproject.toml' \) -not -path '*/.venv/*' -print0 \
  | xargs -0 perl -pi -e 's/(?<![\w-])pricing_collector(?!_key)/amber_pricing_collector/g; s/(?<![\w-])pricing-collector/amber-pricing-collector/g'
perl -pi -e 's/^name = "pricing-collector"/name = "amber-pricing-collector"/' amber-pricing-collector/uv.lock
```

- [ ] **Step 3: Rewrite names in the root files**

```bash
perl -pi -e 's/(?<![\w-])pricing_collector(?!_key)/amber_pricing_collector/g; s/(?<![\w-])pricing-collector/amber-pricing-collector/g' \
  docker-compose.yml README.md .gitignore .claude/agents/documentation.md
```

- [ ] **Step 4: Check nothing still uses the old name**

Run:
```bash
grep -rIn "pricing_collector\|pricing-collector" . --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.git --exclude-dir=superpowers \
  | grep -v "amber_pricing_collector\|amber-pricing-collector\|pricing_collector_key\|PRICING_COLLECTOR_KEY"
```
Expected: no output. (`docs/superpowers/` is excluded on purpose: specs and plans keep the old name.)

- [ ] **Step 5: Run the existing collector tests**

Run: `cd amber-pricing-collector && uv lock --check && uv sync && uv run pytest -q`
Expected: the lock check passes (if it reports a mismatch, run `uv lock` and re-check) and every test passes.

- [ ] **Step 6: Commit**

```bash
git add -A .
git commit -m "refactor: rename pricing-collector to amber-pricing-collector

Each vendor gets its own collector. The env var PRICING_COLLECTOR_KEY is unchanged.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Provider filter and provider header check

**Files:**
- Modify: `core-api/core_api/routers/collector.py:15-37`, `core-api/core_api/deps.py:142-156`
- Modify: `amber-pricing-collector/amber_pricing_collector/core_api_client.py`
- Test: `core-api/tests/test_collector_provider.py` (create), `amber-pricing-collector/tests/test_core_api_client.py`

**Interfaces:**
- Produces: `GET /collector/sources?kind=pricing&provider=<name>` returns only sources with that provider. Any request through `get_source_by_collector_key` may send `X-Collector-Provider`; a mismatch returns 401 `invalid_collector_key`. Task 6 sends both for `local`.

- [ ] **Step 1: Write the failing core-api tests**

Create `core-api/tests/test_collector_provider.py`:

```python
from __future__ import annotations

from core_api.models.sources import SourceKind
from tests.conftest import PRICING_COLLECTOR_KEY
from tests.test_collector_discovery import make_source


def _discover(client, **params):
    return client.get(
        "/api/v1/collector/sources",
        params={"kind": "pricing", **params},
        headers={"X-Collector-Key": PRICING_COLLECTOR_KEY},
    )


def test_discovery_filters_by_provider(client, app_and_db_url):
    _, db_url = app_and_db_url
    make_source(db_url, SourceKind.PRICING, "amber")
    local_id, _ = make_source(db_url, SourceKind.PRICING, "local")

    response = _discover(client, provider="local")

    assert response.status_code == 200, response.text
    assert [s["source_id"] for s in response.json()] == [local_id]


def test_discovery_without_provider_returns_every_source_of_the_kind(client, app_and_db_url):
    _, db_url = app_and_db_url
    amber_id, _ = make_source(db_url, SourceKind.PRICING, "amber")
    local_id, _ = make_source(db_url, SourceKind.PRICING, "local")

    response = _discover(client)

    assert sorted(s["source_id"] for s in response.json()) == sorted([amber_id, local_id])


def _config(client, source_id, **headers):
    return client.get(
        f"/api/v1/sources/{source_id}/config",
        headers={"X-Collector-Key": PRICING_COLLECTOR_KEY, **headers},
    )


def test_config_rejects_a_provider_header_that_does_not_match(client, app_and_db_url):
    _, db_url = app_and_db_url
    amber_id, _ = make_source(db_url, SourceKind.PRICING, "amber")

    response = _config(client, amber_id, **{"X-Collector-Provider": "local"})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_collector_key"


def test_config_accepts_a_matching_provider_header(client, app_and_db_url):
    _, db_url = app_and_db_url
    amber_id, _ = make_source(db_url, SourceKind.PRICING, "amber")

    assert _config(client, amber_id, **{"X-Collector-Provider": "amber"}).status_code == 200


def test_config_without_a_provider_header_still_works(client, app_and_db_url):
    _, db_url = app_and_db_url
    amber_id, _ = make_source(db_url, SourceKind.PRICING, "amber")

    assert _config(client, amber_id).status_code == 200
```

- [ ] **Step 2: Run them to see the failures**

Run: `cd core-api && uv run pytest tests/test_collector_provider.py -v`
Expected: `test_discovery_filters_by_provider` and `test_config_rejects_a_provider_header_that_does_not_match` FAIL. The other three pass.

- [ ] **Step 3: Add the provider filter**

In `core-api/core_api/routers/collector.py`, add the parameter and the filter:

```python
@router.get("/sources", response_model=list[CollectorSourceOut])
def list_collector_sources(
    kind: SourceKind,
    provider: str | None = None,
    x_collector_key: str = Header(...),
    db: Session = Depends(get_db_session),
) -> list[CollectorSourceOut]:
    if not check_collector_key(kind, x_collector_key):
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this kind")
    now = datetime.datetime.now(datetime.timezone.utc)
    query = select(Source).where(
        Source.kind == kind,
        Source.enabled.is_(True),
        or_(Source.effective_to.is_(None), Source.effective_to > now),
    )
    if provider is not None:
        query = query.where(Source.provider == provider)
    sources = db.execute(query).scalars().all()
    return [
        CollectorSourceOut(source_id=s.id, site_id=s.site_id, config=resolve_source_config(db, s))
        for s in sources
    ]
```

- [ ] **Step 4: Add the provider header check**

In `core-api/core_api/deps.py`, replace `get_source_by_collector_key`:

```python
def get_source_by_collector_key(
    source_id: int,
    x_collector_key: str = Header(...),
    x_collector_provider: str | None = Header(None),
    db: Session = Depends(get_db_session),
) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this source")
    if not check_collector_key(source.kind, x_collector_key):
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this source")
    # A collector that names its provider can only reach that provider's
    # sources, so one vendor's collector cannot touch another's data.
    if x_collector_provider is not None and x_collector_provider != source.provider:
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this source")
    if not source.enabled:
        raise AppError(403, "source_disabled", "This source is disabled")
    if source.effective_to is not None and source.effective_to <= datetime.datetime.now(datetime.timezone.utc):
        raise AppError(403, "source_expired", "This source's effective period has ended")
    return source
```

- [ ] **Step 5: Run the core-api tests**

Run: `cd core-api && uv run pytest tests/test_collector_provider.py tests/test_collector_discovery.py tests/test_deps.py -q`
Expected: all pass.

- [ ] **Step 6: Write the failing Amber client test**

In `amber-pricing-collector/tests/test_core_api_client.py`, change `test_fetch_collector_sources_sends_kind_and_collector_key` so it also asserts the new parameter and header. Open the test first; keep its existing assertions and add:

```python
    assert route.calls[0].request.url.params["provider"] == "amber"
    assert route.calls[0].request.headers["X-Collector-Provider"] == "amber"
```

Add a second test next to the one for `fetch_source_config` (line ~56), using that test's own route setup:

```python
    assert route.calls[0].request.headers["X-Collector-Provider"] == "amber"
```

Run: `cd amber-pricing-collector && uv run pytest tests/test_core_api_client.py -q`
Expected: FAIL (`KeyError` on the missing param or header).

- [ ] **Step 7: Send the provider from the Amber collector**

In `amber-pricing-collector/amber_pricing_collector/core_api_client.py`, add under `CoreApiError`:

```python
PROVIDER = "amber"


def _headers(collector_key: str) -> dict[str, str]:
    return {"X-Collector-Key": collector_key, "X-Collector-Provider": PROVIDER}
```

Replace every `headers={"X-Collector-Key": collector_key}` with `headers=_headers(collector_key)` (six call sites), and change the discovery params to:

```python
        params={"kind": "pricing", "provider": PROVIDER},
```

- [ ] **Step 8: Run the collector tests**

Run: `cd amber-pricing-collector && uv run pytest -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add -A .
git commit -m "feat: filter collector discovery by provider and check the provider header

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: LocalRateWindow model, migration and source delete

**Files:**
- Create: `core-api/core_api/models/local_rates.py`, `core-api/alembic/versions/e5a1c3d7f902_add_local_rate_windows.py`
- Modify: `core-api/core_api/models/__init__.py`, `core-api/core_api/routers/sources.py:174-193`
- Test: `core-api/tests/test_local_rate_windows_model.py` (create), `core-api/tests/test_sources.py`

**Interfaces:**
- Produces: `core_api.models.local_rates.LocalRateWindow` with columns `id, source_id, channel (PriceChannel), label, effective_from, effective_to, months, days_of_week, start_time, end_time, rate_c_per_kwh (Decimal)`. Tasks 4 and 5 use it. Deleting a source deletes its windows.

- [ ] **Step 1: Write the failing model tests**

Create `core-api/tests/test_local_rate_windows_model.py`:

```python
from __future__ import annotations

import datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from core_api.db import make_engine
from core_api.models.sources import Source, SourceKind

UTC = datetime.timezone.utc


def test_migration_creates_the_table(app_and_db_url):
    _, db_url = app_and_db_url
    inspector = sa.inspect(make_engine(db_url))

    assert "local_rate_windows" in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns("local_rate_windows")}
    assert columns == {
        "id", "source_id", "channel", "label", "effective_from", "effective_to",
        "months", "days_of_week", "start_time", "end_time", "rate_c_per_kwh",
    }


def test_check_constraint_rejects_an_unknown_channel(db, site):
    source = Source(
        site_id=site.id, kind=SourceKind.PRICING, provider="local", name="Fixed", config={},
        effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()

    db.execute(
        sa.text(
            "INSERT INTO local_rate_windows (source_id, channel, label, effective_from, months, "
            "days_of_week, start_time, end_time, rate_c_per_kwh) "
            "VALUES (:s, 'bogus', 'x', '2026-01-01 00:00:00', '[1]', '[0]', '00:00:00', '00:00:00', 1)"
        ),
        {"s": source.id},
    )
    with pytest.raises(IntegrityError):
        db.commit()
```

- [ ] **Step 2: Run to see it fail**

Run: `cd core-api && uv run pytest tests/test_local_rate_windows_model.py -v`
Expected: FAIL (no such table).

- [ ] **Step 3: Add the model**

Create `core-api/core_api/models/local_rates.py`:

```python
from __future__ import annotations

import datetime
import decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Time
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from core_api.models.base import Base, enum_check, str_enum
from core_api.models.pricing import PriceChannel


class LocalRateWindow(Base):
    """One per-kWh rate on one channel for a recurring months/days/time-of-day
    pattern, owned by a `local` pricing source. The pattern shape matches
    DemandWindow, but the end time is excluded and equal start and end times
    mean all day. The rate is stored positive for every channel; the local
    collector flips the sign of `sell` when it pushes, matching Amber's
    negative-when-paid convention."""

    __tablename__ = "local_rate_windows"
    __table_args__ = (enum_check("channel", PriceChannel, "ck_local_rate_windows_channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    channel: Mapped[PriceChannel] = mapped_column(str_enum(PriceChannel, length=32), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    effective_from: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    months: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    days_of_week: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    start_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    rate_c_per_kwh: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 4), nullable=False)
```

In `core-api/core_api/models/__init__.py`, add `from core_api.models.local_rates import LocalRateWindow` after the `identity` import and add `"LocalRateWindow",` to `__all__`. (`Base.metadata` must know the table for `create_all` in the `db` fixture.)

- [ ] **Step 4: Add the migration**

Create `core-api/alembic/versions/e5a1c3d7f902_add_local_rate_windows.py`:

```python
"""add local_rate_windows

Revision ID: e5a1c3d7f902
Revises: c7e4a1f9b2d3
Create Date: 2026-10-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5a1c3d7f902'
down_revision: Union[str, Sequence[str], None] = 'c7e4a1f9b2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'local_rate_windows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=32), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('effective_from', sa.DateTime(timezone=True), nullable=False),
        sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('months', sa.JSON(), nullable=False),
        sa.Column('days_of_week', sa.JSON(), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('rate_c_per_kwh', sa.Numeric(12, 4), nullable=False),
        sa.CheckConstraint(
            "channel IN ('buy', 'sell', 'controlled_load')", name='ck_local_rate_windows_channel'
        ),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_local_rate_windows_source_id'), 'local_rate_windows', ['source_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_local_rate_windows_source_id'), table_name='local_rate_windows')
    op.drop_table('local_rate_windows')
```

- [ ] **Step 5: Run the model tests**

Run: `cd core-api && uv run pytest tests/test_local_rate_windows_model.py tests/test_bootstrap.py -q`
Expected: pass. Then check the model and migration agree: `uv run alembic -x db_url=sqlite:///$(mktemp -d)/m.db upgrade head` is not needed; instead run `uv run pytest tests/test_schema_constraints.py -q` and expect pass.

- [ ] **Step 6: Write the failing delete tests**

Append to `core-api/tests/test_sources.py` (reuse that file's `make_owner_and_site`, `auth` and fixtures; read `test_delete_source_with_no_readings_succeeds` at line 86 and `test_delete_source_with_readings_rejected` at line 102 first and copy their setup):

```python
def test_delete_local_source_also_deletes_its_rate_windows(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "amber", "config": {}},
        headers=auth(owner_id),
    ).json()
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    session.add(
        LocalRateWindow(
            source_id=source["id"], channel=PriceChannel.BUY, label="Flat",
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            months=[1], days_of_week=[0], start_time=datetime.time(0, 0), end_time=datetime.time(0, 0),
            rate_c_per_kwh=decimal.Decimal("30"),
        )
    )
    session.commit()
    session.close()

    response = client.delete(f"/api/v1/sites/{site_id}/sources/{source['id']}", headers=auth(owner_id))

    assert response.status_code == 204, response.text
    session = make_session_factory(engine)()
    assert session.execute(sa.select(sa.func.count()).select_from(LocalRateWindow)).scalar_one() == 0
    session.close()
```

Add the imports this needs at the top of the file if missing: `datetime`, `decimal`, `sqlalchemy as sa`, `make_engine`, `make_session_factory` from `core_api.db`, `PriceChannel` from `core_api.models.pricing`, `LocalRateWindow` from `core_api.models.local_rates`. (The test uses an `amber` source because the window table does not check the provider. The `local` provider arrives in Task 5.)

Run: `cd core-api && uv run pytest tests/test_sources.py -k rate_windows -v`
Expected: FAIL with 409 `source_has_readings` (the foreign key blocks the delete).

- [ ] **Step 7: Delete the windows with the source**

In `core-api/core_api/routers/sources.py`, import `delete` from `sqlalchemy` (change the existing import to `from sqlalchemy import delete, select`) and `from core_api.models.local_rates import LocalRateWindow`. In `delete_source`, replace `db.delete(source)` with:

```python
    # Rate windows go in the same transaction: if readings still block the
    # delete, the IntegrityError below rolls the window delete back too.
    db.execute(delete(LocalRateWindow).where(LocalRateWindow.source_id == source.id))
    db.delete(source)
```

- [ ] **Step 8: Run the source tests**

Run: `cd core-api && uv run pytest tests/test_sources.py -q`
Expected: all pass, including `test_delete_source_with_readings_rejected`.

- [ ] **Step 9: Commit**

```bash
git add -A .
git commit -m "feat: add the local_rate_windows table

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Overlap logic

**Files:**
- Create: `core-api/core_api/local_rates.py`
- Test: `core-api/tests/test_local_rates.py` (create)

**Interfaces:**
- Produces, used by Task 5:
  - `minutes_of_day(start: datetime.time, end: datetime.time) -> frozenset[int]`
  - `windows_overlap(a: RateWindowLike, b: RateWindowLike) -> bool`
  - `find_overlap(candidate: RateWindowLike, existing: Iterable[W]) -> W | None`
  - `RateWindowLike` is a Protocol with `effective_from, effective_to, months, days_of_week, start_time, end_time`.

- [ ] **Step 1: Write the failing tests**

Create `core-api/tests/test_local_rates.py`:

```python
from __future__ import annotations

import datetime
from dataclasses import dataclass

from core_api.local_rates import find_overlap, minutes_of_day, windows_overlap

UTC = datetime.timezone.utc
T = datetime.time


@dataclass
class W:
    effective_from: datetime.datetime = datetime.datetime(2026, 1, 1, tzinfo=UTC)
    effective_to: datetime.datetime | None = None
    months: tuple[int, ...] = tuple(range(1, 13))
    days_of_week: tuple[int, ...] = tuple(range(7))
    start_time: datetime.time = T(0, 0)
    end_time: datetime.time = T(0, 0)


def test_equal_times_mean_all_day():
    assert minutes_of_day(T(0, 0), T(0, 0)) == frozenset(range(1440))


def test_end_time_is_excluded():
    assert minutes_of_day(T(7, 0), T(7, 2)) == frozenset({420, 421})


def test_end_before_start_wraps_past_midnight():
    wrapped = minutes_of_day(T(23, 58), T(0, 2))
    assert wrapped == frozenset({1438, 1439, 0, 1})


def test_windows_touching_at_a_boundary_do_not_overlap():
    night = W(start_time=T(0, 0), end_time=T(7, 0))
    day = W(start_time=T(7, 0), end_time=T(22, 0))
    assert not windows_overlap(night, day)


def test_wrapping_window_overlaps_the_early_morning_window():
    overnight = W(start_time=T(22, 0), end_time=T(7, 0))
    early = W(start_time=T(6, 0), end_time=T(8, 0))
    assert windows_overlap(overnight, early)


def test_disjoint_months_do_not_overlap():
    summer = W(months=(12, 1, 2))
    winter = W(months=(6, 7, 8))
    assert not windows_overlap(summer, winter)


def test_disjoint_days_do_not_overlap():
    weekday = W(days_of_week=(0, 1, 2, 3, 4))
    weekend = W(days_of_week=(5, 6))
    assert not windows_overlap(weekday, weekend)


def test_a_closed_window_does_not_overlap_one_that_starts_when_it_ends():
    old = W(effective_to=datetime.datetime(2026, 7, 1, tzinfo=UTC))
    new = W(effective_from=datetime.datetime(2026, 7, 1, tzinfo=UTC))
    assert not windows_overlap(old, new)


def test_an_open_ended_window_overlaps_a_later_one():
    current = W()
    later = W(effective_from=datetime.datetime(2027, 1, 1, tzinfo=UTC))
    assert windows_overlap(current, later)


def test_find_overlap_returns_the_clashing_window():
    flat = W()
    peak = W(start_time=T(7, 0), end_time=T(9, 0))
    assert find_overlap(peak, [flat]) is flat


def test_find_overlap_returns_none_when_clear():
    night = W(start_time=T(0, 0), end_time=T(7, 0))
    day = W(start_time=T(7, 0), end_time=T(22, 0))
    assert find_overlap(day, [night]) is None
```

- [ ] **Step 2: Run to see it fail**

Run: `cd core-api && uv run pytest tests/test_local_rates.py -v`
Expected: FAIL (`ModuleNotFoundError: core_api.local_rates`).

- [ ] **Step 3: Implement**

Create `core-api/core_api/local_rates.py`:

```python
from __future__ import annotations

import datetime
from collections.abc import Iterable, Sequence
from typing import Protocol, TypeVar

from core_api.time_utils import as_utc

DAY_MINUTES = 24 * 60
_FAR_FUTURE = datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)


class RateWindowLike(Protocol):
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None
    months: Sequence[int]
    days_of_week: Sequence[int]
    start_time: datetime.time
    end_time: datetime.time


W = TypeVar("W", bound=RateWindowLike)


def minutes_of_day(start: datetime.time, end: datetime.time) -> frozenset[int]:
    """The minutes of the local day a window covers. The start is included and
    the end is excluded. An end earlier than the start wraps past midnight, and
    equal times mean the whole day."""
    start_minute = start.hour * 60 + start.minute
    end_minute = end.hour * 60 + end.minute
    if start_minute == end_minute:
        return frozenset(range(DAY_MINUTES))
    if start_minute < end_minute:
        return frozenset(range(start_minute, end_minute))
    return frozenset(range(start_minute, DAY_MINUTES)) | frozenset(range(end_minute))


def _span(window: RateWindowLike) -> tuple[datetime.datetime, datetime.datetime]:
    end = as_utc(window.effective_to) if window.effective_to else _FAR_FUTURE
    return as_utc(window.effective_from), end


def windows_overlap(a: RateWindowLike, b: RateWindowLike) -> bool:
    """True when some minute could match both windows. The caller compares
    windows on the same channel only."""
    a_from, a_to = _span(a)
    b_from, b_to = _span(b)
    return (
        a_from < b_to
        and b_from < a_to
        and bool(set(a.months) & set(b.months))
        and bool(set(a.days_of_week) & set(b.days_of_week))
        and bool(minutes_of_day(a.start_time, a.end_time) & minutes_of_day(b.start_time, b.end_time))
    )


def find_overlap(candidate: RateWindowLike, existing: Iterable[W]) -> W | None:
    for window in existing:
        if windows_overlap(candidate, window):
            return window
    return None
```

- [ ] **Step 4: Run the tests**

Run: `cd core-api && uv run pytest tests/test_local_rates.py -v`
Expected: all 11 pass.

- [ ] **Step 5: Commit**

```bash
git add -A .
git commit -m "feat: add overlap checks for local rate windows

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Rate window API, local source rules and config delivery

**Files:**
- Create: `core-api/core_api/routers/local_rates.py`
- Modify: `core-api/core_api/schemas.py` (add schemas, extend `SourceCreate`), `core-api/core_api/main.py`, `core-api/core_api/routers/collector_config.py`, `core-api/core_api/routers/sources.py` (`update_source`)
- Test: `core-api/tests/test_local_rate_windows_api.py` (create), `core-api/tests/test_cost.py`

**Interfaces:**
- Consumes: `LocalRateWindow` (Task 3), `find_overlap` (Task 4).
- Produces:
  - `POST/GET /sites/{site_id}/sources/{source_id}/rate-windows`, `PATCH/DELETE .../rate-windows/{window_id}`. Body for POST and PATCH is `LocalRateWindowCreate` (PATCH replaces every field). Response is `LocalRateWindowOut`.
  - `GET /sources/{id}/config` for a `local` source returns `{"timezone": "<site tz>", "rate_windows": [{"channel", "effective_from", "effective_to", "months", "days_of_week", "start_time", "end_time", "rate_c_per_kwh"}]}`. Times are `HH:MM:SS`, timestamps are ISO UTC, `rate_c_per_kwh` is a string. Task 6 reads exactly this.
  - Creating a `local` source requires `kind=pricing` and a name of 1-100 characters.

- [ ] **Step 1: Write the failing API tests**

Create `core-api/tests/test_local_rate_windows_api.py`:

```python
from __future__ import annotations

import pytest

from core_api.models.sources import SourceKind
from tests.conftest import PRICING_COLLECTOR_KEY
from tests.test_billing import auth, make_owner_and_site
from tests.test_collector_discovery import make_source

FLAT = {
    "channel": "buy",
    "label": "Flat",
    "effective_from": "2026-01-01T00:00:00Z",
    "months": list(range(1, 13)),
    "days_of_week": list(range(7)),
    "start_time": "00:00:00",
    "end_time": "00:00:00",
    "rate_c_per_kwh": "32.5",
}


def _window(**overrides) -> dict:
    return {**FLAT, **overrides}


@pytest.fixture()
def local_source(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    response = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "local", "name": "Origin Fixed"},
        headers=auth(owner_id),
    )
    assert response.status_code == 201, response.text
    return {"owner": owner_id, "site": site_id, "source": response.json()["id"], "db_url": db_url}


def _url(ctx, suffix="") -> str:
    return f"/api/v1/sites/{ctx['site']}/sources/{ctx['source']}/rate-windows{suffix}"


def test_create_and_list_a_window(client, local_source):
    created = client.post(_url(local_source), json=_window(), headers=auth(local_source["owner"]))
    assert created.status_code == 201, created.text
    assert created.json()["rate_c_per_kwh"] == "32.5000"
    assert created.json()["effective_from"].endswith("Z") or created.json()["effective_from"].endswith("+00:00")

    listed = client.get(_url(local_source), headers=auth(local_source["owner"]))
    assert [w["label"] for w in listed.json()] == ["Flat"]


def test_adjacent_windows_save_without_an_overlap_error(client, local_source):
    headers = auth(local_source["owner"])
    night = _window(label="Off-peak", end_time="07:00:00", rate_c_per_kwh="18")
    day = _window(label="Peak", start_time="07:00:00", end_time="00:00:00", rate_c_per_kwh="40")
    assert client.post(_url(local_source), json=night, headers=headers).status_code == 201
    assert client.post(_url(local_source), json=day, headers=headers).status_code == 201


def test_an_overlapping_window_is_rejected_and_names_the_clash(client, local_source):
    headers = auth(local_source["owner"])
    client.post(_url(local_source), json=_window(), headers=headers)

    response = client.post(_url(local_source), json=_window(label="Extra", start_time="07:00:00", end_time="09:00:00"), headers=headers)

    assert response.status_code == 422
    assert response.json()["code"] == "rate_window_overlap"
    assert '"Flat"' in response.json()["message"]


def test_the_same_hours_on_another_channel_do_not_clash(client, local_source):
    headers = auth(local_source["owner"])
    client.post(_url(local_source), json=_window(), headers=headers)
    export = _window(channel="sell", label="Feed-in", rate_c_per_kwh="5")
    assert client.post(_url(local_source), json=export, headers=headers).status_code == 201


@pytest.mark.parametrize(
    "bad",
    [
        {"months": [13]},
        {"days_of_week": [7]},
        {"months": []},
        {"start_time": "07:00:30"},
        {"rate_c_per_kwh": "-1"},
        {"effective_to": "2025-01-01T00:00:00Z"},
        {"label": ""},
    ],
)
def test_invalid_windows_are_rejected(client, local_source, bad):
    response = client.post(_url(local_source), json=_window(**bad), headers=auth(local_source["owner"]))
    assert response.status_code == 422


def test_patch_replaces_a_window_and_ignores_itself_in_the_overlap_check(client, local_source):
    headers = auth(local_source["owner"])
    window_id = client.post(_url(local_source), json=_window(), headers=headers).json()["id"]

    response = client.patch(_url(local_source, f"/{window_id}"), json=_window(rate_c_per_kwh="35"), headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["rate_c_per_kwh"] == "35.0000"


def test_patch_that_would_overlap_another_window_is_rejected(client, local_source):
    headers = auth(local_source["owner"])
    client.post(_url(local_source), json=_window(end_time="07:00:00"), headers=headers)
    day_id = client.post(
        _url(local_source), json=_window(label="Day", start_time="07:00:00", end_time="00:00:00"), headers=headers
    ).json()["id"]

    response = client.patch(_url(local_source, f"/{day_id}"), json=_window(label="Day", start_time="06:00:00"), headers=headers)

    assert response.status_code == 422


def test_delete_a_window(client, local_source):
    headers = auth(local_source["owner"])
    window_id = client.post(_url(local_source), json=_window(), headers=headers).json()["id"]

    assert client.delete(_url(local_source, f"/{window_id}"), headers=headers).status_code == 204
    assert client.get(_url(local_source), headers=headers).json() == []


def test_windows_cannot_be_added_to_a_non_local_source(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    amber = client.post(
        f"/api/v1/sites/{site_id}/sources", json={"kind": "pricing", "provider": "amber"}, headers=auth(owner_id)
    ).json()

    response = client.post(
        f"/api/v1/sites/{site_id}/sources/{amber['id']}/rate-windows", json=_window(), headers=auth(owner_id)
    )

    assert response.status_code == 422
    assert response.json()["code"] == "not_a_local_source"


def test_only_the_site_owner_can_add_windows(client, local_source, app_and_db_url):
    _, db_url = app_and_db_url
    other_owner, _ = make_owner_and_site(db_url)
    response = client.post(_url(local_source), json=_window(), headers=auth(other_owner))
    assert response.status_code == 403


def test_a_local_source_needs_a_name(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    for body in ({"kind": "pricing", "provider": "local"}, {"kind": "pricing", "provider": "local", "name": "  "}):
        response = client.post(f"/api/v1/sites/{site_id}/sources", json=body, headers=auth(owner_id))
        assert response.status_code == 422


def test_the_local_provider_is_only_for_pricing_sources(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    response = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "weather", "provider": "local", "name": "x"},
        headers=auth(owner_id),
    )
    assert response.status_code == 422


def test_renaming_a_local_source_to_a_blank_name_is_rejected(client, local_source):
    response = client.patch(
        f"/api/v1/sites/{local_source['site']}/sources/{local_source['source']}",
        json={"name": "   "},
        headers=auth(local_source["owner"]),
    )
    assert response.status_code == 422


def test_config_for_a_local_source_with_no_windows_is_empty(client, local_source):
    response = client.get(
        f"/api/v1/sources/{local_source['source']}/config",
        headers={"X-Collector-Key": PRICING_COLLECTOR_KEY, "X-Collector-Provider": "local"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["config"] == {"timezone": "Australia/Sydney", "rate_windows": []}


def test_config_for_a_local_source_lists_its_windows(client, local_source):
    client.post(_url(local_source), json=_window(), headers=auth(local_source["owner"]))

    response = client.get(
        f"/api/v1/sources/{local_source['source']}/config",
        headers={"X-Collector-Key": PRICING_COLLECTOR_KEY, "X-Collector-Provider": "local"},
    )

    assert response.json()["config"]["rate_windows"] == [
        {
            "channel": "buy",
            "effective_from": "2026-01-01T00:00:00+00:00",
            "effective_to": None,
            "months": list(range(1, 13)),
            "days_of_week": list(range(7)),
            "start_time": "00:00:00",
            "end_time": "00:00:00",
            "rate_c_per_kwh": "32.5000",
        }
    ]


def test_amber_config_has_no_rate_windows(client, app_and_db_url):
    _, db_url = app_and_db_url
    amber_id, _ = make_source(db_url, SourceKind.PRICING, "amber")
    response = client.get(f"/api/v1/sources/{amber_id}/config", headers={"X-Collector-Key": PRICING_COLLECTOR_KEY})
    assert "rate_windows" not in response.json()["config"]
```

- [ ] **Step 2: Run to see them fail**

Run: `cd core-api && uv run pytest tests/test_local_rate_windows_api.py -q`
Expected: most FAIL (404 on the new routes, 201 on a nameless local source).

- [ ] **Step 3: Add the schemas**

In `core-api/core_api/schemas.py`, replace `SourceCreate` with:

```python
class SourceCreate(BaseModel):
    kind: SourceKind
    provider: str
    config: dict = {}
    name: str | None = None

    @model_validator(mode="after")
    def _local_provider_rules(self) -> SourceCreate:
        if self.provider != "local":
            return self
        if self.kind != SourceKind.PRICING:
            raise ValueError("The local provider is only available for pricing sources")
        name = (self.name or "").strip()
        if not name or len(name) > MAX_TARIFF_NAME_LENGTH:
            raise ValueError(f"A local tariff needs a name of 1-{MAX_TARIFF_NAME_LENGTH} characters")
        self.name = name
        return self
```

Add `MAX_TARIFF_NAME_LENGTH = 100` under `MAX_PRICE_INTERVAL_MINUTES`. Then add after `MonthlyFeeDiscountWindowOut`:

```python
class LocalRateWindowCreate(BaseModel):
    channel: PriceChannel
    label: str = Field(min_length=1, max_length=100)
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None = None
    months: list[int] = Field(min_length=1)
    days_of_week: list[int] = Field(min_length=1)
    start_time: datetime.time
    end_time: datetime.time
    rate_c_per_kwh: decimal.Decimal = Field(ge=0, max_digits=12, decimal_places=4)

    @model_validator(mode="after")
    def _check_pattern(self) -> LocalRateWindowCreate:
        if any(month < 1 or month > 12 for month in self.months):
            raise ValueError("months must be between 1 and 12")
        if any(day < 0 or day > 6 for day in self.days_of_week):
            raise ValueError("days_of_week must be between 0 (Monday) and 6 (Sunday)")
        if any(t.second or t.microsecond for t in (self.start_time, self.end_time)):
            raise ValueError("start_time and end_time must be whole minutes")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self


class LocalRateWindowOut(LocalRateWindowCreate):
    id: int
    source_id: int

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Add the router**

Create `core-api/core_api/routers/local_rates.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, require_site_access, require_site_owner
from core_api.errors import AppError
from core_api.local_rates import find_overlap
from core_api.models.identity import Site
from core_api.models.local_rates import LocalRateWindow
from core_api.models.sources import Source, SourceKind
from core_api.schemas import LocalRateWindowCreate, LocalRateWindowOut

router = APIRouter(prefix="/sites/{site_id}/sources/{source_id}/rate-windows", tags=["local-rates"])


def _get_local_source(db: Session, site: Site, source_id: int) -> Source:
    source = db.get(Source, source_id)
    if source is None or source.site_id != site.id:
        raise AppError(404, "not_found", f"Source {source_id} not found")
    if source.kind != SourceKind.PRICING or source.provider != "local":
        raise AppError(422, "not_a_local_source", "Rate windows can only be added to a local pricing source")
    return source


def _get_window(db: Session, source: Source, window_id: int) -> LocalRateWindow:
    window = db.get(LocalRateWindow, window_id)
    if window is None or window.source_id != source.id:
        raise AppError(404, "not_found", f"Rate window {window_id} not found")
    return window


def _reject_overlap(
    db: Session, source: Source, payload: LocalRateWindowCreate, exclude_id: int | None = None
) -> None:
    same_channel = db.execute(
        select(LocalRateWindow).where(
            LocalRateWindow.source_id == source.id, LocalRateWindow.channel == payload.channel
        )
    ).scalars().all()
    clash = find_overlap(payload, [w for w in same_channel if w.id != exclude_id])
    if clash is not None:
        raise AppError(
            422,
            "rate_window_overlap",
            f'This window overlaps "{clash.label}" (id {clash.id}) on the same channel',
        )


@router.post("", response_model=LocalRateWindowOut, status_code=status.HTTP_201_CREATED)
def create_rate_window(
    source_id: int,
    payload: LocalRateWindowCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> LocalRateWindow:
    source = _get_local_source(db, site, source_id)
    _reject_overlap(db, source, payload)
    window = LocalRateWindow(source_id=source.id, **payload.model_dump())
    db.add(window)
    db.commit()
    db.refresh(window)
    return window


@router.get("", response_model=list[LocalRateWindowOut])
def list_rate_windows(
    source_id: int,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[LocalRateWindow]:
    source = _get_local_source(db, site, source_id)
    query = (
        select(LocalRateWindow)
        .where(LocalRateWindow.source_id == source.id)
        .order_by(LocalRateWindow.channel, LocalRateWindow.effective_from, LocalRateWindow.start_time)
    )
    return db.execute(query).scalars().all()


@router.patch("/{window_id}", response_model=LocalRateWindowOut)
def replace_rate_window(
    source_id: int,
    window_id: int,
    payload: LocalRateWindowCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> LocalRateWindow:
    """Replaces every field. Sending the full window keeps the overlap check
    simple: the merged result is exactly the payload."""
    source = _get_local_source(db, site, source_id)
    window = _get_window(db, source, window_id)
    _reject_overlap(db, source, payload, exclude_id=window.id)
    for field, value in payload.model_dump().items():
        setattr(window, field, value)
    db.commit()
    db.refresh(window)
    return window


@router.delete("/{window_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rate_window(
    source_id: int,
    window_id: int,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> None:
    source = _get_local_source(db, site, source_id)
    db.delete(_get_window(db, source, window_id))
    db.commit()
```

In `core-api/core_api/main.py`, add `from core_api.routers.local_rates import router as local_rates_router` next to the other router imports and `app.include_router(local_rates_router, prefix=api_prefix)` after the `sources_router` line.

- [ ] **Step 5: Reject blank names on rename**

In `core-api/core_api/routers/sources.py`, in `update_source`, replace the name branch:

```python
    if payload.name is not None:
        if source.provider == "local" and not payload.name.strip():
            raise AppError(422, "name_required", "A local tariff needs a name")
        source.name = payload.name.strip() if source.provider == "local" else payload.name
```

- [ ] **Step 6: Deliver the windows in the source config**

In `core-api/core_api/routers/collector_config.py`, add imports `from sqlalchemy import select`, `from core_api.models.local_rates import LocalRateWindow`, `from core_api.time_utils import as_utc`, and replace `resolve_source_config`:

```python
def _rate_window_config(window: LocalRateWindow) -> dict:
    return {
        "channel": window.channel.value,
        "effective_from": as_utc(window.effective_from).isoformat(),
        "effective_to": as_utc(window.effective_to).isoformat() if window.effective_to else None,
        "months": window.months,
        "days_of_week": window.days_of_week,
        "start_time": window.start_time.isoformat(),
        "end_time": window.end_time.isoformat(),
        "rate_c_per_kwh": str(window.rate_c_per_kwh),
    }


def _local_config(db: Session, source: Source, config: dict) -> None:
    site = db.get(Site, source.site_id)
    windows = db.execute(
        select(LocalRateWindow).where(LocalRateWindow.source_id == source.id).order_by(LocalRateWindow.id)
    ).scalars().all()
    config["timezone"] = site.timezone
    config["rate_windows"] = [_rate_window_config(w) for w in windows]


def resolve_source_config(db: Session, source: Source) -> dict:
    config = dict(source.config or {})
    if source.provider == "amber":
        site = db.get(Site, source.site_id)
        owner = db.get(User, site.owner_user_id)
        config["amber_api_key"] = owner.amber_api_key
    elif source.provider == "local":
        _local_config(db, source, config)
    return config
```

- [ ] **Step 7: Run the API tests**

Run: `cd core-api && uv run pytest tests/test_local_rate_windows_api.py -q`
Expected: all pass.

- [ ] **Step 8: Add the cost test**

In `core-api/tests/test_cost.py`, change `_make_pricing_source(db, site)` to take a provider:

```python
def _make_pricing_source(db, site, provider="amber") -> Source:
    source = Source(
        site_id=site.id,
        kind=SourceKind.PRICING,
        provider=provider,
        config={},
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
```
(leave the rest of the function as it is) and add after `test_compute_cost_net_method`:

```python
def test_compute_cost_with_a_local_source_adds_the_negative_sell_price(db, site):
    """The local collector pushes `sell` negative, like Amber, so cost math is
    identical for both providers."""
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site, provider="local")
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=300.0))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.BUY, value=decimal.Decimal("30.0")))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-10.0")))
    db.commit()

    result = compute_cost(
        db, site.id, ts, ts + datetime.timedelta(minutes=1), method="net", site_timezone=site.timezone
    )

    assert result.total_cost_c == decimal.Decimal("0.25")
```

- [ ] **Step 9: Run the whole core-api suite**

Run: `cd core-api && uv run pytest -q`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add -A .
git commit -m "feat: add the local rate window API and deliver windows in source config

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: local-pricing-collector

**Files (all under `local-pricing-collector/`):**
- Create: `pyproject.toml`, `Dockerfile`, `local_pricing_collector/__init__.py`, `config.py`, `core_api_client.py`, `rates.py`, `scheduling.py`, `poll.py`, `main.py`, `tests/__init__.py`, `tests/test_config.py`, `tests/test_core_api_client.py`, `tests/test_rates.py`, `tests/test_poll.py`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: the config shape from Task 5, `GET /collector/sources?kind=pricing&provider=local`, `POST /sources/{id}/prices` (202, body `{"intervals": [{"channel","interval_start","interval_end","price"}]}`).
- Produces:
  - `config.Settings(core_api_url: str, collector_key: str, horizon_hours: int)` and `get_settings()`.
  - `core_api_client.fetch_collector_sources(base_url, collector_key) -> list[int]`, `fetch_source_config(base_url, source_id, collector_key) -> dict`, `push_price_intervals(base_url, source_id, collector_key, intervals, *, timeout=60)`, `CoreApiError`.
  - `rates.parse_windows(raw: list[dict]) -> list[RateWindow]`, `rates.price_at(windows, channel, at_utc, tz) -> Decimal | None`, `rates.build_intervals(windows, tz, now, horizon_hours) -> list[dict]`.
  - `poll.sync_one_source(settings, source_id) -> None` (never raises) and `poll.run_forever(settings) -> None`.

- [ ] **Step 1: Scaffold the package**

Create `local-pricing-collector/pyproject.toml`:

```toml
[project]
name = "local-pricing-collector"
version = "0.1.0"
description = "Turns locally defined rate windows into price intervals for fixed-tariff sites."
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27,<1.0",
    "tzdata",
]

[dependency-groups]
dev = [
    "pytest>=8.0,<9.0",
    "respx>=0.21,<1.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["local_pricing_collector"]
```

Create `local-pricing-collector/Dockerfile`:

```dockerfile
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY local_pricing_collector ./local_pricing_collector
RUN uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1

CMD ["/app/.venv/bin/python", "-m", "local_pricing_collector.main"]
```

Create empty `local-pricing-collector/local_pricing_collector/__init__.py` and `local-pricing-collector/tests/__init__.py`. Then:

Run: `cd local-pricing-collector && uv lock && uv sync`
Expected: creates `uv.lock` and `.venv`.

- [ ] **Step 2: Write the config tests and code**

Create `local-pricing-collector/tests/test_config.py`:

```python
from __future__ import annotations

import pytest

from local_pricing_collector.config import get_settings


def test_reads_url_key_and_default_horizon(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://core-api:8000/")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.delenv("LOCAL_HORIZON_HOURS", raising=False)

    settings = get_settings()

    assert settings.core_api_url == "http://core-api:8000"
    assert settings.collector_key == "k"
    assert settings.horizon_hours == 48


def test_horizon_can_be_set(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://x")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.setenv("LOCAL_HORIZON_HOURS", "24")
    assert get_settings().horizon_hours == 24


def test_missing_url_or_key_raises(monkeypatch):
    monkeypatch.delenv("CORE_API_URL", raising=False)
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    with pytest.raises(RuntimeError):
        get_settings()


def test_a_non_numeric_horizon_raises(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://x")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.setenv("LOCAL_HORIZON_HOURS", "soon")
    with pytest.raises(RuntimeError):
        get_settings()
```

Create `local-pricing-collector/local_pricing_collector/config.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_HORIZON_HOURS = 48


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    collector_key: str
    horizon_hours: int


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    collector_key = os.environ.get("COLLECTOR_KEY")
    if not core_api_url or not collector_key:
        raise RuntimeError("CORE_API_URL and COLLECTOR_KEY must both be set")
    raw_horizon = os.environ.get("LOCAL_HORIZON_HOURS", str(DEFAULT_HORIZON_HOURS))
    try:
        horizon_hours = int(raw_horizon)
    except ValueError as exc:
        raise RuntimeError(f"LOCAL_HORIZON_HOURS must be a whole number, got {raw_horizon!r}") from exc
    return Settings(core_api_url=core_api_url.rstrip("/"), collector_key=collector_key, horizon_hours=horizon_hours)
```

Run: `cd local-pricing-collector && uv run pytest tests/test_config.py -q`
Expected: pass.

- [ ] **Step 3: Write the client tests and code**

Create `local-pricing-collector/tests/test_core_api_client.py`:

```python
from __future__ import annotations

import json

import httpx
import pytest
import respx

from local_pricing_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_source_config,
    push_price_intervals,
)

BASE_URL = "http://core-api:8000"


@respx.mock
def test_fetch_collector_sources_asks_for_local_pricing_sources():
    route = respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(
        return_value=httpx.Response(200, json=[{"source_id": 4, "site_id": 1, "config": {}}])
    )

    assert fetch_collector_sources(BASE_URL, "k") == [4]
    request = route.calls[0].request
    assert request.url.params["kind"] == "pricing"
    assert request.url.params["provider"] == "local"
    assert request.headers["X-Collector-Key"] == "k"
    assert request.headers["X-Collector-Provider"] == "local"


@respx.mock
def test_fetch_collector_sources_raises_on_non_200():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(return_value=httpx.Response(401, text="no"))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "k")


@respx.mock
def test_fetch_collector_sources_wraps_connection_errors():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "k")


@respx.mock
def test_fetch_source_config_returns_the_config_and_sends_the_provider():
    route = respx.get(f"{BASE_URL}/api/v1/sources/4/config").mock(
        return_value=httpx.Response(200, json={"config": {"timezone": "UTC", "rate_windows": []}})
    )

    assert fetch_source_config(BASE_URL, 4, "k") == {"timezone": "UTC", "rate_windows": []}
    assert route.calls[0].request.headers["X-Collector-Provider"] == "local"


@respx.mock
def test_push_price_intervals_posts_the_batch():
    route = respx.post(f"{BASE_URL}/api/v1/sources/4/prices").mock(return_value=httpx.Response(202, json={}))
    intervals = [{"channel": "buy", "interval_start": "a", "interval_end": "b", "price": "1"}]

    push_price_intervals(BASE_URL, 4, "k", intervals)

    assert route.calls[0].request.headers["X-Collector-Provider"] == "local"
    assert json.loads(route.calls[0].request.content) == {"intervals": intervals}


@respx.mock
def test_push_price_intervals_raises_on_failure():
    respx.post(f"{BASE_URL}/api/v1/sources/4/prices").mock(return_value=httpx.Response(422, text="bad"))
    with pytest.raises(CoreApiError):
        push_price_intervals(BASE_URL, 4, "k", [])
```

Create `local-pricing-collector/local_pricing_collector/core_api_client.py`:

```python
from __future__ import annotations

import httpx

PROVIDER = "local"


class CoreApiError(Exception):
    """Raised on any failure talking to Core API: a non-success response or a
    transport failure. Callers only catch this one type."""


def _headers(collector_key: str) -> dict[str, str]:
    return {"X-Collector-Key": collector_key, "X-Collector-Provider": PROVIDER}


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return httpx.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise CoreApiError(f"Failed to reach Core API: {exc}") from exc


def fetch_collector_sources(base_url: str, collector_key: str) -> list[int]:
    response = _request(
        "GET",
        f"{base_url}/api/v1/collector/sources",
        params={"kind": "pricing", "provider": PROVIDER},
        headers=_headers(collector_key),
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch collector sources: {response.status_code} {response.text}")
    return [entry["source_id"] for entry in response.json()]


def fetch_source_config(base_url: str, source_id: int, collector_key: str) -> dict:
    response = _request(
        "GET", f"{base_url}/api/v1/sources/{source_id}/config", headers=_headers(collector_key), timeout=10
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch source config: {response.status_code} {response.text}")
    return response.json()["config"]


def push_price_intervals(
    base_url: str, source_id: int, collector_key: str, intervals: list[dict], *, timeout: float = 60
) -> None:
    """The server writes one row per covered minute, so a 48-hour push is a few
    thousand rows per channel. The timeout is longer than the 10 seconds used
    for small reads."""
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/prices",
        headers=_headers(collector_key),
        json={"intervals": intervals},
        timeout=timeout,
    )
    if response.status_code != 202:
        raise CoreApiError(f"Failed to push price intervals: {response.status_code} {response.text}")
```

Run: `cd local-pricing-collector && uv run pytest tests/test_core_api_client.py -q`
Expected: pass.

- [ ] **Step 4: Write the failing rate tests**

Create `local-pricing-collector/tests/test_rates.py`:

```python
from __future__ import annotations

import datetime
import decimal
import logging
import zoneinfo

from local_pricing_collector.rates import build_intervals, parse_windows, price_at

UTC = datetime.timezone.utc
SYDNEY = zoneinfo.ZoneInfo("Australia/Sydney")
D = decimal.Decimal


def window(**overrides) -> dict:
    base = {
        "channel": "buy",
        "effective_from": "2026-01-01T00:00:00+00:00",
        "effective_to": None,
        "months": list(range(1, 13)),
        "days_of_week": list(range(7)),
        "start_time": "00:00:00",
        "end_time": "00:00:00",
        "rate_c_per_kwh": "30.0000",
    }
    return {**base, **overrides}


def at(*args) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=UTC)


def test_a_flat_window_prices_every_minute():
    windows = parse_windows([window()])
    assert price_at(windows, "buy", at(2026, 6, 15, 3, 7), UTC) == D("30.0000")


def test_other_channels_have_no_price():
    windows = parse_windows([window()])
    assert price_at(windows, "sell", at(2026, 6, 15, 3, 7), UTC) is None


def test_the_end_time_is_excluded():
    windows = parse_windows([window(start_time="07:00:00", end_time="09:00:00")])
    assert price_at(windows, "buy", at(2026, 6, 15, 8, 59), UTC) == D("30.0000")
    assert price_at(windows, "buy", at(2026, 6, 15, 9, 0), UTC) is None


def test_an_overnight_window_wraps_past_midnight():
    windows = parse_windows([window(start_time="22:00:00", end_time="07:00:00")])
    assert price_at(windows, "buy", at(2026, 6, 15, 23, 0), UTC) is not None
    assert price_at(windows, "buy", at(2026, 6, 15, 6, 59), UTC) is not None
    assert price_at(windows, "buy", at(2026, 6, 15, 7, 0), UTC) is None


def test_months_and_days_filter_the_window():
    windows = parse_windows([window(months=[12], days_of_week=[0])])
    assert price_at(windows, "buy", at(2026, 12, 14, 12, 0), UTC) is not None  # Monday in December
    assert price_at(windows, "buy", at(2026, 12, 15, 12, 0), UTC) is None  # Tuesday
    assert price_at(windows, "buy", at(2026, 11, 16, 12, 0), UTC) is None  # Monday in November


def test_the_window_is_read_in_the_site_time_zone():
    windows = parse_windows([window(start_time="07:00:00", end_time="09:00:00")])
    # 21:30Z on 14 June is 07:30 on 15 June in Sydney (UTC+10).
    assert price_at(windows, "buy", at(2026, 6, 14, 21, 30), SYDNEY) is not None
    assert price_at(windows, "buy", at(2026, 6, 14, 21, 30), UTC) is None


def test_effective_dates_bound_the_window():
    windows = parse_windows(
        [window(effective_from="2026-07-01T00:00:00+00:00", effective_to="2026-08-01T00:00:00+00:00")]
    )
    assert price_at(windows, "buy", at(2026, 6, 30, 23, 59), UTC) is None
    assert price_at(windows, "buy", at(2026, 7, 1, 0, 0), UTC) is not None
    assert price_at(windows, "buy", at(2026, 7, 31, 23, 59), UTC) is not None
    assert price_at(windows, "buy", at(2026, 8, 1, 0, 0), UTC) is None


def test_a_malformed_window_is_skipped_and_logged(caplog):
    with caplog.at_level(logging.ERROR):
        windows = parse_windows([{"channel": "buy"}, window()])
    assert len(windows) == 1
    assert "Skipping malformed rate window" in caplog.text


def test_no_windows_builds_no_intervals():
    assert build_intervals([], UTC, at(2026, 6, 15, 10, 30), 48) == []


def test_a_flat_tariff_builds_one_interval_per_hour_from_the_top_of_the_hour():
    windows = parse_windows([window()])

    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 30), 3)

    assert [(i["interval_start"], i["interval_end"]) for i in intervals] == [
        ("2026-06-15T10:00:00+00:00", "2026-06-15T11:00:00+00:00"),
        ("2026-06-15T11:00:00+00:00", "2026-06-15T12:00:00+00:00"),
        ("2026-06-15T12:00:00+00:00", "2026-06-15T13:00:00+00:00"),
    ]
    assert {i["channel"] for i in intervals} == {"buy"}
    assert {i["price"] for i in intervals} == {"30.0000"}


def test_interval_starts_do_not_move_within_an_hour():
    windows = parse_windows([window()])
    first = build_intervals(windows, UTC, at(2026, 6, 15, 10, 5), 2)
    later = build_intervals(windows, UTC, at(2026, 6, 15, 10, 55), 2)
    assert first == later


def test_an_interval_ends_where_the_rate_changes():
    windows = parse_windows(
        [
            window(label="a", start_time="00:00:00", end_time="10:30:00", rate_c_per_kwh="18.0000"),
            window(label="b", start_time="10:30:00", end_time="00:00:00", rate_c_per_kwh="40.0000"),
        ]
    )

    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 0), 2)

    assert [(i["interval_start"], i["interval_end"], i["price"]) for i in intervals] == [
        ("2026-06-15T10:00:00+00:00", "2026-06-15T10:30:00+00:00", "18.0000"),
        ("2026-06-15T10:30:00+00:00", "2026-06-15T11:00:00+00:00", "40.0000"),
        ("2026-06-15T11:00:00+00:00", "2026-06-15T12:00:00+00:00", "40.0000"),
    ]


def test_a_gap_produces_no_interval():
    windows = parse_windows([window(start_time="10:00:00", end_time="10:30:00")])

    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 0), 2)

    assert [(i["interval_start"], i["interval_end"]) for i in intervals] == [
        ("2026-06-15T10:00:00+00:00", "2026-06-15T10:30:00+00:00"),
    ]


def test_sell_is_pushed_negative():
    windows = parse_windows([window(channel="sell", rate_c_per_kwh="5.0000")])
    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 0), 1)
    assert [i["price"] for i in intervals] == ["-5.0000"]


def test_a_free_sell_rate_is_pushed_as_zero_not_negative_zero():
    windows = parse_windows([window(channel="sell", rate_c_per_kwh="0")])
    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 0), 1)
    assert [i["price"] for i in intervals] == ["0"]


def test_buy_and_controlled_load_are_pushed_as_entered():
    windows = parse_windows(
        [window(channel="buy", rate_c_per_kwh="30"), window(channel="controlled_load", rate_c_per_kwh="12")]
    )
    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 0), 1)
    assert {(i["channel"], i["price"]) for i in intervals} == {("buy", "30"), ("controlled_load", "12")}


def test_a_window_that_ends_inside_the_horizon_stops_pricing_at_that_instant():
    windows = parse_windows(
        [
            window(label="old", effective_to="2026-06-15T10:30:00+00:00", rate_c_per_kwh="30.0000"),
            window(label="new", effective_from="2026-06-15T10:30:00+00:00", rate_c_per_kwh="35.0000"),
        ]
    )

    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 0), 1)

    assert [(i["interval_start"], i["interval_end"], i["price"]) for i in intervals] == [
        ("2026-06-15T10:00:00+00:00", "2026-06-15T10:30:00+00:00", "30.0000"),
        ("2026-06-15T10:30:00+00:00", "2026-06-15T11:00:00+00:00", "35.0000"),
    ]


def test_a_daylight_saving_change_moves_the_peak_start_in_utc():
    """Sydney moves to daylight saving at 02:00 on 4 October 2026. Local 07:00
    is 21:00Z the day before on 3 October, and 20:00Z after the change."""
    windows = parse_windows(
        [
            window(label="peak", start_time="07:00:00", end_time="22:00:00", rate_c_per_kwh="40.0000"),
            window(label="off-peak", start_time="22:00:00", end_time="07:00:00", rate_c_per_kwh="20.0000"),
        ]
    )

    intervals = build_intervals(windows, SYDNEY, at(2026, 10, 3, 0, 0), 24)

    peak_starts = [i["interval_start"] for i in intervals if i["price"] == "40.0000"]
    off_peak_ends = [i["interval_end"] for i in intervals if i["price"] == "20.0000"]
    assert "2026-10-03T20:00:00+00:00" in peak_starts
    assert "2026-10-03T20:00:00+00:00" in off_peak_ends
    assert "2026-10-03T21:00:00+00:00" not in off_peak_ends
```

Run: `cd local-pricing-collector && uv run pytest tests/test_rates.py -q`
Expected: FAIL (`ModuleNotFoundError: local_pricing_collector.rates`).

- [ ] **Step 5: Implement rates**

Create `local-pricing-collector/local_pricing_collector/rates.py`:

```python
from __future__ import annotations

import datetime
import decimal
import logging
import zoneinfo
from dataclasses import dataclass

logger = logging.getLogger("local_pricing_collector")

CHANNELS = ("buy", "sell", "controlled_load")
DAY_MINUTES = 24 * 60
ONE_MINUTE = datetime.timedelta(minutes=1)


@dataclass(frozen=True)
class RateWindow:
    channel: str
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None
    months: frozenset[int]
    days_of_week: frozenset[int]
    minutes: frozenset[int]
    rate_c_per_kwh: decimal.Decimal


def _minutes_of_day(start: datetime.time, end: datetime.time) -> frozenset[int]:
    """Start included, end excluded. An end before the start wraps past
    midnight, and equal times mean the whole day."""
    start_minute = start.hour * 60 + start.minute
    end_minute = end.hour * 60 + end.minute
    if start_minute == end_minute:
        return frozenset(range(DAY_MINUTES))
    if start_minute < end_minute:
        return frozenset(range(start_minute, end_minute))
    return frozenset(range(start_minute, DAY_MINUTES)) | frozenset(range(end_minute))


def _parse_window(raw: dict) -> RateWindow:
    effective_to = raw["effective_to"]
    return RateWindow(
        channel=raw["channel"],
        effective_from=datetime.datetime.fromisoformat(raw["effective_from"]),
        effective_to=datetime.datetime.fromisoformat(effective_to) if effective_to else None,
        months=frozenset(raw["months"]),
        days_of_week=frozenset(raw["days_of_week"]),
        minutes=_minutes_of_day(
            datetime.time.fromisoformat(raw["start_time"]), datetime.time.fromisoformat(raw["end_time"])
        ),
        rate_c_per_kwh=decimal.Decimal(raw["rate_c_per_kwh"]),
    )


def parse_windows(raw_windows: list[dict]) -> list[RateWindow]:
    """Skips a malformed window and logs it, so one bad row does not stop the
    rest of the tariff from being priced."""
    windows = []
    for raw in raw_windows:
        try:
            windows.append(_parse_window(raw))
        except (KeyError, ValueError, decimal.InvalidOperation):
            logger.exception("Skipping malformed rate window: %r", raw)
    return windows


def _applies(window: RateWindow, at_utc: datetime.datetime, local: datetime.datetime) -> bool:
    return (
        window.effective_from <= at_utc
        and (window.effective_to is None or at_utc < window.effective_to)
        and local.month in window.months
        and local.weekday() in window.days_of_week
        and local.hour * 60 + local.minute in window.minutes
    )


def price_at(
    windows: list[RateWindow], channel: str, at_utc: datetime.datetime, tz: zoneinfo.ZoneInfo
) -> decimal.Decimal | None:
    """The rate in c/kWh for this channel at this instant, or None when no
    window covers it. Core-api rejects overlapping windows, so at most one
    matches; the first one wins if that ever changes."""
    local = at_utc.astimezone(tz)
    for window in windows:
        if window.channel == channel and _applies(window, at_utc, local):
            return window.rate_c_per_kwh
    return None


def _signed(channel: str, rate: decimal.Decimal) -> decimal.Decimal:
    """Amber stores the sell price negative when the customer is paid, and the
    cost math adds it, so local sell rates are pushed with the same sign."""
    return -rate if channel == "sell" and rate else rate


def _interval(
    channel: str, start: datetime.datetime, end: datetime.datetime, rate: decimal.Decimal
) -> dict:
    return {
        "channel": channel,
        "interval_start": start.isoformat(),
        "interval_end": end.isoformat(),
        "price": str(_signed(channel, rate)),
    }


def _channel_intervals(
    windows: list[RateWindow],
    channel: str,
    tz: zoneinfo.ZoneInfo,
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[dict]:
    """Walks minute by minute and closes an interval at each top of the hour
    and wherever the rate changes. A minute with no rate closes the interval
    and opens nothing."""
    intervals = []
    run_start, run_rate = start, price_at(windows, channel, start, tz)
    minute = start + ONE_MINUTE
    while minute <= end:
        rate = price_at(windows, channel, minute, tz) if minute < end else None
        if minute == end or minute.minute == 0 or rate != run_rate:
            if run_rate is not None:
                intervals.append(_interval(channel, run_start, minute, run_rate))
            run_start, run_rate = minute, rate
        minute += ONE_MINUTE
    return intervals


def build_intervals(
    windows: list[RateWindow], tz: zoneinfo.ZoneInfo, now: datetime.datetime, horizon_hours: int
) -> list[dict]:
    """Price intervals from the top of the current UTC hour for horizon_hours.
    Starting on the hour keeps every interval's start time the same across
    pushes, so repeating a push overwrites rows instead of adding them."""
    start = now.astimezone(datetime.timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(hours=horizon_hours)
    intervals: list[dict] = []
    for channel in CHANNELS:
        if any(w.channel == channel for w in windows):
            intervals.extend(_channel_intervals(windows, channel, tz, start, end))
    return intervals
```

- [ ] **Step 6: Run the rate tests**

Run: `cd local-pricing-collector && uv run pytest tests/test_rates.py -q`
Expected: all pass. If `test_a_daylight_saving_change_moves_the_peak_start_in_utc` fails, print `peak_starts` and `off_peak_ends` and check the arithmetic against the docstring before touching the code: `build_intervals` is meant to convert each UTC minute to Sydney local time, so a wrong result points at `price_at`, not the test.

- [ ] **Step 7: Write the failing poll tests**

Create `local-pricing-collector/tests/test_poll.py`:

```python
from __future__ import annotations

import datetime

from local_pricing_collector import poll
from local_pricing_collector.config import Settings
from local_pricing_collector.core_api_client import CoreApiError
from local_pricing_collector.scheduling import next_aligned_tick

SETTINGS = Settings(core_api_url="http://core-api:8000", collector_key="k", horizon_hours=2)
NOW = datetime.datetime(2026, 6, 15, 10, 30, tzinfo=datetime.timezone.utc)
CONFIG = {
    "timezone": "UTC",
    "rate_windows": [
        {
            "channel": "buy",
            "effective_from": "2026-01-01T00:00:00+00:00",
            "effective_to": None,
            "months": list(range(1, 13)),
            "days_of_week": list(range(7)),
            "start_time": "00:00:00",
            "end_time": "00:00:00",
            "rate_c_per_kwh": "30.0000",
        }
    ],
}


def test_sync_pushes_the_built_intervals(monkeypatch):
    pushed = []
    monkeypatch.setattr(poll, "fetch_source_config", lambda url, source_id, key: CONFIG)
    monkeypatch.setattr(
        poll, "push_price_intervals", lambda url, source_id, key, intervals, **kw: pushed.append((source_id, intervals))
    )

    poll.sync_one_source(SETTINGS, 7, now=NOW)

    assert pushed[0][0] == 7
    assert len(pushed[0][1]) == 2


def test_a_source_with_no_windows_pushes_nothing(monkeypatch):
    pushed = []
    monkeypatch.setattr(poll, "fetch_source_config", lambda url, source_id, key: {"timezone": "UTC", "rate_windows": []})
    monkeypatch.setattr(poll, "push_price_intervals", lambda *a, **kw: pushed.append(a))

    poll.sync_one_source(SETTINGS, 7, now=NOW)

    assert pushed == []


def test_a_config_fetch_failure_is_logged_and_does_not_raise(monkeypatch, caplog):
    def boom(url, source_id, key):
        raise CoreApiError("down")

    monkeypatch.setattr(poll, "fetch_source_config", boom)

    poll.sync_one_source(SETTINGS, 7, now=NOW)

    assert "Failed to fetch config" in caplog.text


def test_an_unknown_time_zone_is_logged_and_does_not_raise(monkeypatch, caplog):
    monkeypatch.setattr(poll, "fetch_source_config", lambda url, source_id, key: {**CONFIG, "timezone": "Mars/Base"})
    monkeypatch.setattr(poll, "push_price_intervals", lambda *a, **kw: None)

    poll.sync_one_source(SETTINGS, 7, now=NOW)

    assert "Failed to read config" in caplog.text


def test_a_push_failure_is_logged_and_does_not_raise(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise CoreApiError("422")

    monkeypatch.setattr(poll, "fetch_source_config", lambda url, source_id, key: CONFIG)
    monkeypatch.setattr(poll, "push_price_intervals", boom)

    poll.sync_one_source(SETTINGS, 7, now=NOW)

    assert "Failed to push" in caplog.text


def test_next_aligned_tick_is_the_next_five_minute_boundary_plus_buffer():
    now = datetime.datetime(2026, 6, 15, 10, 31, 0, tzinfo=datetime.timezone.utc)
    assert next_aligned_tick(now) == datetime.datetime(2026, 6, 15, 10, 35, 5, tzinfo=datetime.timezone.utc)


def test_next_aligned_tick_is_always_after_now():
    now = datetime.datetime(2026, 6, 15, 10, 35, 5, tzinfo=datetime.timezone.utc)
    assert next_aligned_tick(now) > now
```

Run: `cd local-pricing-collector && uv run pytest tests/test_poll.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 8: Implement scheduling, poll and main**

Create `local-pricing-collector/local_pricing_collector/scheduling.py`:

```python
from __future__ import annotations

import datetime


def next_aligned_tick(
    now: datetime.datetime, *, interval_minutes: int = 5, buffer_seconds: int = 5
) -> datetime.datetime:
    """The next wall-clock boundary that interval_minutes divides the hour
    into, plus a short buffer. Always strictly after `now`."""
    minute_start = now.replace(second=0, microsecond=0)
    boundary = minute_start - datetime.timedelta(minutes=minute_start.minute % interval_minutes)
    candidate = boundary + datetime.timedelta(seconds=buffer_seconds)
    if candidate <= now:
        candidate += datetime.timedelta(minutes=interval_minutes)
    return candidate
```

Create `local-pricing-collector/local_pricing_collector/poll.py`:

```python
from __future__ import annotations

import datetime
import logging
import multiprocessing
import time
import zoneinfo

from local_pricing_collector.config import Settings
from local_pricing_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_source_config,
    push_price_intervals,
)
from local_pricing_collector.rates import build_intervals, parse_windows
from local_pricing_collector.scheduling import next_aligned_tick

logger = logging.getLogger("local_pricing_collector")

CHILD_JOIN_TIMEOUT_SECONDS = 120


def sync_one_source(settings: Settings, source_id: int, *, now: datetime.datetime | None = None) -> None:
    """One-shot: read this source's rate windows and push the next
    horizon_hours of prices. Never raises, so one source's failure cannot stop
    the others. There is no retry: the next tick runs it again."""
    try:
        config = fetch_source_config(settings.core_api_url, source_id, settings.collector_key)
    except CoreApiError:
        logger.exception("Failed to fetch config for pricing source %s this cycle", source_id)
        return
    try:
        tz = zoneinfo.ZoneInfo(config["timezone"])
        windows = parse_windows(config["rate_windows"])
    except (KeyError, zoneinfo.ZoneInfoNotFoundError, ValueError):
        logger.exception("Failed to read config for pricing source %s this cycle", source_id)
        return

    now = now or datetime.datetime.now(datetime.timezone.utc)
    intervals = build_intervals(windows, tz, now, settings.horizon_hours)
    if not intervals:
        return
    try:
        push_price_intervals(settings.core_api_url, source_id, settings.collector_key, intervals)
    except CoreApiError:
        logger.exception("Failed to push price intervals for pricing source %s this cycle", source_id)


def _run_tick(settings: Settings) -> None:
    try:
        source_ids = fetch_collector_sources(settings.core_api_url, settings.collector_key)
    except CoreApiError:
        logger.exception("Failed to refresh the local pricing source list this tick; skipping")
        return
    children = [multiprocessing.Process(target=sync_one_source, args=(settings, sid)) for sid in source_ids]
    for child in children:
        child.start()
    for child in children:
        child.join(timeout=CHILD_JOIN_TIMEOUT_SECONDS)
        if child.is_alive():
            logger.error("Local pricing source process did not finish in time; terminating")
            child.terminate()
            child.join()


def run_forever(settings: Settings) -> None:
    """Every 5 minutes, fork one process per local source. This process stays
    single-threaded so the forks are safe."""
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            time.sleep(max((next_aligned_tick(now) - now).total_seconds(), 0))
            _run_tick(settings)
        except Exception:
            logger.exception("Unexpected error in the local pricing loop; continuing")
```

Create `local-pricing-collector/local_pricing_collector/main.py`:

```python
from __future__ import annotations

import logging

from local_pricing_collector.config import get_settings
from local_pricing_collector.poll import run_forever


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run_forever(get_settings())


if __name__ == "__main__":
    main()
```

- [ ] **Step 9: Run the whole collector suite**

Run: `cd local-pricing-collector && uv run pytest -q`
Expected: all pass.

- [ ] **Step 10: Add the compose service**

In `docker-compose.yml`, after the `amber-pricing-collector` service, add:

```yaml
  local-pricing-collector:
    build: ./local-pricing-collector
    environment:
      CORE_API_URL: http://core-api:8000
      COLLECTOR_KEY: ${PRICING_COLLECTOR_KEY:?set a long random secret, shared with core-api's own PRICING_COLLECTOR_KEY}
    depends_on:
      core-api:
        condition: service_healthy
    networks: *app-network
```

Run: `grep -c "local-pricing-collector" docker-compose.yml`
Expected: `2` (service name and build path).

- [ ] **Step 11: Commit**

```bash
git add -A .
git commit -m "feat: add local-pricing-collector

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Webapp rate windows panel and local source form

**Files:**
- Create: `webapp/src/sources/useRateWindows.ts`, `webapp/src/sources/rateCoverage.ts`, `webapp/src/sources/rateCoverage.test.ts`, `webapp/src/sources/RateWindowsPanel.tsx`, `webapp/src/sources/RateWindowsPanel.test.tsx`
- Modify: `webapp/src/sources/useSources.ts`, `webapp/src/pages/SourcesPage.tsx`, `webapp/src/pages/SourcesPage.test.tsx`

**Interfaces:**
- Consumes: the Task 5 endpoints. Rates arrive as decimal strings.
- Produces:
  - `RateChannel = "buy" | "sell" | "controlled_load"`, `RateWindow`, `RateWindowInput`
  - hooks `useRateWindows(siteId, sourceId)`, `useCreateRateWindow(siteId, sourceId)`, `useReplaceRateWindow(siteId, sourceId)` (variables `{ windowId, input }`), `useDeleteRateWindow(siteId, sourceId)`
  - `hasCoverageGap(windows: RateWindow[], now?: Date): boolean`
  - `<RateWindowsPanel siteId sourceId />`
  - `Source` gains `effective_from: string` and `effective_to?: string | null`.

- [ ] **Step 1: Write the failing coverage tests**

Create `webapp/src/sources/rateCoverage.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { hasCoverageGap } from "./rateCoverage";
import type { RateWindow } from "./useRateWindows";

const NOW = new Date("2026-06-15T00:00:00Z");

function win(overrides: Partial<RateWindow> = {}): RateWindow {
  return {
    id: 1,
    source_id: 1,
    channel: "buy",
    label: "Flat",
    effective_from: "2026-01-01T00:00:00Z",
    effective_to: null,
    months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    days_of_week: [0, 1, 2, 3, 4, 5, 6],
    start_time: "00:00:00",
    end_time: "00:00:00",
    rate_c_per_kwh: "30.0000",
    ...overrides,
  };
}

describe("hasCoverageGap", () => {
  it("is false when there are no windows", () => {
    expect(hasCoverageGap([], NOW)).toBe(false);
  });

  it("is false for an all-day window on every day", () => {
    expect(hasCoverageGap([win()], NOW)).toBe(false);
  });

  it("is true when only part of the day is covered", () => {
    expect(hasCoverageGap([win({ start_time: "07:00:00", end_time: "22:00:00" })], NOW)).toBe(true);
  });

  it("is false when two windows that touch at a boundary cover the day", () => {
    const night = win({ id: 1, start_time: "00:00:00", end_time: "07:00:00" });
    const day = win({ id: 2, start_time: "07:00:00", end_time: "00:00:00" });
    expect(hasCoverageGap([night, day], NOW)).toBe(false);
  });

  it("counts an overnight window", () => {
    const overnight = win({ id: 1, start_time: "22:00:00", end_time: "07:00:00" });
    const daytime = win({ id: 2, start_time: "07:00:00", end_time: "22:00:00" });
    expect(hasCoverageGap([overnight, daytime], NOW)).toBe(false);
  });

  it("is true when some days of the week are missing", () => {
    expect(hasCoverageGap([win({ days_of_week: [0, 1, 2, 3, 4] })], NOW)).toBe(true);
  });

  it("is true when some months are missing", () => {
    expect(hasCoverageGap([win({ months: [1, 2, 3] })], NOW)).toBe(true);
  });

  it("ignores windows that have already ended", () => {
    const ended = win({ effective_to: "2026-06-01T00:00:00Z" });
    expect(hasCoverageGap([ended], NOW)).toBe(false);
  });
});
```

Run: `cd webapp && npx vitest run src/sources/rateCoverage.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 2: Add the hooks and types**

Create `webapp/src/sources/useRateWindows.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export type RateChannel = "buy" | "sell" | "controlled_load";

export interface RateWindow {
  id: number;
  source_id: number;
  channel: RateChannel;
  label: string;
  effective_from: string;
  effective_to: string | null;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  // Serialized by core-api as a decimal string, not a JSON number.
  rate_c_per_kwh: string;
}

export interface RateWindowInput {
  channel: RateChannel;
  label: string;
  effective_from: string;
  effective_to: string | null;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  rate_c_per_kwh: number;
}

function basePath(siteId: number, sourceId: number): string {
  return `/api/v1/sites/${siteId}/sources/${sourceId}/rate-windows`;
}

function queryKey(siteId: number, sourceId: number) {
  return ["rate-windows", siteId, sourceId];
}

export function useRateWindows(siteId: number, sourceId: number) {
  return useQuery({
    queryKey: queryKey(siteId, sourceId),
    queryFn: () => apiFetch<RateWindow[]>(basePath(siteId, sourceId)),
  });
}

export function useCreateRateWindow(siteId: number, sourceId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: RateWindowInput) =>
      apiFetch<RateWindow>(basePath(siteId, sourceId), { method: "POST", body: JSON.stringify(input) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKey(siteId, sourceId) }),
  });
}

export function useReplaceRateWindow(siteId: number, sourceId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ windowId, input }: { windowId: number; input: RateWindowInput }) =>
      apiFetch<RateWindow>(`${basePath(siteId, sourceId)}/${windowId}`, {
        method: "PATCH",
        body: JSON.stringify(input),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKey(siteId, sourceId) }),
  });
}

export function useDeleteRateWindow(siteId: number, sourceId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (windowId: number) =>
      apiFetch<void>(`${basePath(siteId, sourceId)}/${windowId}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKey(siteId, sourceId) }),
  });
}
```

In `webapp/src/sources/useSources.ts`, extend `Source`:

```ts
export interface Source {
  id: number;
  site_id: number;
  kind: string;
  provider: string;
  name: string | null;
  enabled: boolean;
  effective_from?: string;
  effective_to?: string | null;
  config: Record<string, string | boolean>;
}
```

- [ ] **Step 3: Implement the coverage check**

Create `webapp/src/sources/rateCoverage.ts`:

```ts
import type { RateChannel, RateWindow } from "./useRateWindows";

const DAY_MINUTES = 24 * 60;
const CHANNELS: RateChannel[] = ["buy", "sell", "controlled_load"];

function toMinutes(time: string): number {
  const [hours, minutes] = time.split(":").map(Number);
  return hours * 60 + minutes;
}

/** The minutes of the day a window covers: start included, end excluded,
 * an end before the start wraps past midnight, equal times mean all day. */
function minutesOfDay(window: RateWindow): number[] {
  const start = toMinutes(window.start_time);
  const end = toMinutes(window.end_time);
  const all = (from: number, to: number) => Array.from({ length: to - from }, (_, i) => from + i);
  if (start === end) return all(0, DAY_MINUTES);
  if (start < end) return all(start, end);
  return [...all(start, DAY_MINUTES), ...all(0, end)];
}

function isCurrent(window: RateWindow, now: Date): boolean {
  return window.effective_to === null || new Date(window.effective_to) > now;
}

function channelHasGap(windows: RateWindow[]): boolean {
  const covered = new Uint8Array(12 * 7 * DAY_MINUTES);
  for (const window of windows) {
    const minutes = minutesOfDay(window);
    for (const month of window.months) {
      for (const day of window.days_of_week) {
        const base = ((month - 1) * 7 + day) * DAY_MINUTES;
        for (const minute of minutes) covered[base + minute] = 1;
      }
    }
  }
  return covered.includes(0);
}

/** True when a channel that has at least one current window leaves some month,
 * weekday and time of day uncovered. A channel with no windows is not a gap:
 * a tariff may have no controlled load or no feed-in. */
export function hasCoverageGap(windows: RateWindow[], now: Date = new Date()): boolean {
  const current = windows.filter((w) => isCurrent(w, now));
  return CHANNELS.some((channel) => {
    const forChannel = current.filter((w) => w.channel === channel);
    return forChannel.length > 0 && channelHasGap(forChannel);
  });
}
```

Run: `cd webapp && npx vitest run src/sources/rateCoverage.test.ts`
Expected: pass.

- [ ] **Step 4: Write the failing panel tests**

Create `webapp/src/sources/RateWindowsPanel.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { RateWindowsPanel } from "./RateWindowsPanel";
import type { RateWindow } from "./useRateWindows";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RateWindowsPanel siteId={7} sourceId={3} />
    </QueryClientProvider>,
  );
}

function win(overrides: Partial<RateWindow>): RateWindow {
  return {
    id: 1,
    source_id: 3,
    channel: "buy",
    label: "Peak",
    effective_from: "2026-01-01T00:00:00Z",
    effective_to: null,
    months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    days_of_week: [0, 1, 2, 3, 4, 5, 6],
    start_time: "00:00:00",
    end_time: "00:00:00",
    rate_c_per_kwh: "32.5000",
    ...overrides,
  };
}

describe("RateWindowsPanel", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows empty Import, Export and Controlled load tables and no warning for a new source", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]);
    renderPanel();

    expect(await screen.findByRole("heading", { name: "Import" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Export (feed-in)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Controlled load" })).toBeInTheDocument();
    expect(screen.queryByText(/not covered/i)).not.toBeInTheDocument();
  });

  it("lists a window under its channel with the rate", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      win({ channel: "sell", label: "Feed-in", rate_c_per_kwh: "5.0000" }),
    ]);
    renderPanel();

    const exportSection = (await screen.findByRole("heading", { name: "Export (feed-in)" })).closest("section")!;
    expect(within(exportSection).getByText("Feed-in")).toBeInTheDocument();
    expect(within(exportSection).getByText(/5c\/kWh/)).toBeInTheDocument();
  });

  it("warns about uncovered time", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([win({ start_time: "07:00:00", end_time: "22:00:00" })]);
    renderPanel();

    expect(await screen.findByText(/not covered/i)).toBeInTheDocument();
  });

  it("adds a flat import window with sensible defaults", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]).mockResolvedValueOnce(win({})).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("heading", { name: "Import" });
    await user.type(screen.getByLabelText("Label"), "Flat");
    await user.type(screen.getByLabelText("Rate (c/kWh)"), "32.5");
    await user.click(screen.getByRole("button", { name: "Add window" }));

    const post = vi.mocked(apiFetch).mock.calls[1];
    expect(post[0]).toBe("/api/v1/sites/7/sources/3/rate-windows");
    const body = JSON.parse((post[1] as RequestInit).body as string);
    expect(body).toMatchObject({
      channel: "buy",
      label: "Flat",
      months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
      days_of_week: [0, 1, 2, 3, 4, 5, 6],
      start_time: "00:00:00",
      end_time: "00:00:00",
      rate_c_per_kwh: 32.5,
      effective_to: null,
    });
  });

  it("shows the server's message when a window overlaps", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockRejectedValueOnce(new Error('This window overlaps "Flat" (id 1) on the same channel'));
    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("heading", { name: "Import" });
    await user.type(screen.getByLabelText("Label"), "Peak");
    await user.type(screen.getByLabelText("Rate (c/kWh)"), "40");
    await user.click(screen.getByRole("button", { name: "Add window" }));

    expect(await screen.findByRole("alert")).toHaveTextContent('overlaps "Flat"');
  });

  it("ends a window by sending it back with an end date", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([win({})]).mockResolvedValueOnce(win({})).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("button", { name: "End Peak" }));

    const patch = vi.mocked(apiFetch).mock.calls[1];
    expect(patch[0]).toBe("/api/v1/sites/7/sources/3/rate-windows/1");
    const body = JSON.parse((patch[1] as RequestInit).body as string);
    expect(body.effective_to).not.toBeNull();
    expect(body.rate_c_per_kwh).toBe(32.5);
  });

  it("deletes a window", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([win({})]).mockResolvedValueOnce(undefined).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("button", { name: "Delete Peak" }));

    expect(vi.mocked(apiFetch).mock.calls[1][0]).toBe("/api/v1/sites/7/sources/3/rate-windows/1");
    expect((vi.mocked(apiFetch).mock.calls[1][1] as RequestInit).method).toBe("DELETE");
  });
});
```

Run: `cd webapp && npx vitest run src/sources/RateWindowsPanel.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 5: Implement the panel**

Create `webapp/src/sources/RateWindowsPanel.tsx`:

```tsx
import { type FormEvent, useMemo, useState } from "react";
import { hasCoverageGap } from "./rateCoverage";
import {
  type RateChannel,
  type RateWindow,
  type RateWindowInput,
  useCreateRateWindow,
  useDeleteRateWindow,
  useRateWindows,
  useReplaceRateWindow,
} from "./useRateWindows";

const ALL_MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const CHANNEL_SECTIONS: { channel: RateChannel; title: string }[] = [
  { channel: "buy", title: "Import" },
  { channel: "sell", title: "Export (feed-in)" },
  { channel: "controlled_load", title: "Controlled load" },
];

const inputClass =
  "rounded-md border border-line bg-surface px-2 py-1 text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60";
const errorClass = "rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger";
const linkButtonClass = "text-ink-muted underline hover:text-ink disabled:opacity-50";

function describePattern(window: RateWindow): string {
  const months = window.months.length === 12 ? "all year" : window.months.map((m) => MONTH_NAMES[m - 1]).join(", ");
  const days = window.days_of_week.length === 7 ? "every day" : window.days_of_week.map((d) => DAY_NAMES[d]).join(", ");
  const allDay = window.start_time === window.end_time;
  const times = allDay ? "all day" : `${window.start_time.slice(0, 5)}–${window.end_time.slice(0, 5)}`;
  return `${times}, ${days}, ${months}`;
}

function formatRate(rate: string): string {
  return `${Number(rate)}c/kWh`;
}

function toInput(window: RateWindow, overrides: Partial<RateWindowInput>): RateWindowInput {
  return {
    channel: window.channel,
    label: window.label,
    effective_from: window.effective_from,
    effective_to: window.effective_to,
    months: window.months,
    days_of_week: window.days_of_week,
    start_time: window.start_time,
    end_time: window.end_time,
    rate_c_per_kwh: Number(window.rate_c_per_kwh),
    ...overrides,
  };
}

interface WindowActions {
  onEnd: (window: RateWindow) => void;
  onDelete: (window: RateWindow) => void;
  isPending: boolean;
}

function ChannelSection({ title, windows, actions }: { title: string; windows: RateWindow[]; actions: WindowActions }) {
  return (
    <section className="space-y-1">
      <h4 className="text-sm font-semibold text-ink">{title}</h4>
      {windows.length === 0 ? (
        <p className="text-sm text-ink-muted">No windows.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {windows.map((window) => (
            <li key={window.id} className="flex justify-between gap-2 rounded-md border border-line px-2 py-1">
              <span className="text-ink">
                <span className="font-medium">{window.label}</span> — {formatRate(window.rate_c_per_kwh)} —{" "}
                {describePattern(window)}
                {window.effective_to && (
                  <span className="text-ink-muted"> (ended {new Date(window.effective_to).toLocaleDateString()})</span>
                )}
              </span>
              <span className="flex shrink-0 gap-3">
                {window.effective_to === null && (
                  <button
                    aria-label={`End ${window.label}`}
                    onClick={() => actions.onEnd(window)}
                    disabled={actions.isPending}
                    className={linkButtonClass}
                  >
                    End
                  </button>
                )}
                <button
                  aria-label={`Delete ${window.label}`}
                  onClick={() => actions.onDelete(window)}
                  disabled={actions.isPending}
                  className={linkButtonClass}
                >
                  Delete
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CheckboxGroup({
  legend,
  options,
  selected,
  onChange,
}: {
  legend: string;
  options: { value: number; label: string }[];
  selected: number[];
  onChange: (selected: number[]) => void;
}) {
  function toggle(value: number) {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value].sort((a, b) => a - b));
  }
  return (
    <fieldset className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-ink-muted">
      <legend>{legend}</legend>
      {options.map((option) => (
        <label key={option.value} className="flex items-center gap-1">
          <input type="checkbox" checked={selected.includes(option.value)} onChange={() => toggle(option.value)} />
          {option.label}
        </label>
      ))}
    </fieldset>
  );
}

function AddWindowForm({ onAdd, isPending, error }: { onAdd: (input: RateWindowInput) => void; isPending: boolean; error: string | null }) {
  const [channel, setChannel] = useState<RateChannel>("buy");
  const [label, setLabel] = useState("");
  const [rate, setRate] = useState("");
  const [months, setMonths] = useState(ALL_MONTHS);
  const [days, setDays] = useState(ALL_DAYS);
  const [start, setStart] = useState("00:00");
  const [end, setEnd] = useState("00:00");
  const [from, setFrom] = useState(new Date().toISOString().slice(0, 10));

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    onAdd({
      channel,
      label: label.trim(),
      effective_from: new Date(`${from}T00:00:00`).toISOString(),
      effective_to: null,
      months,
      days_of_week: days,
      start_time: `${start}:00`,
      end_time: `${end}:00`,
      rate_c_per_kwh: Number(rate),
    });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2 border-t border-line pt-3 text-sm">
      {error && (
        <p role="alert" className={errorClass}>
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-ink-muted">
          Direction
          <select aria-label="Direction" value={channel} onChange={(e) => setChannel(e.target.value as RateChannel)} className={inputClass}>
            {CHANNEL_SECTIONS.map((s) => (
              <option key={s.channel} value={s.channel}>
                {s.title}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-ink-muted">
          Label
          <input aria-label="Label" value={label} onChange={(e) => setLabel(e.target.value)} required autoComplete="off" className={inputClass} />
        </label>
        <label className="flex flex-col text-ink-muted">
          Rate (c/kWh)
          <input aria-label="Rate (c/kWh)" type="number" step="0.0001" min="0" value={rate} onChange={(e) => setRate(e.target.value)} required className={inputClass} />
        </label>
        <label className="flex flex-col text-ink-muted">
          From
          <input aria-label="From" type="date" value={from} onChange={(e) => setFrom(e.target.value)} required className={inputClass} />
        </label>
        <label className="flex flex-col text-ink-muted">
          Start
          <input aria-label="Start" type="time" value={start} onChange={(e) => setStart(e.target.value)} required className={inputClass} />
        </label>
        <label className="flex flex-col text-ink-muted">
          End
          <input aria-label="End" type="time" value={end} onChange={(e) => setEnd(e.target.value)} required className={inputClass} />
        </label>
      </div>
      <p className="text-xs text-ink-muted">Start and end at the same time cover the whole day. An end before the start runs past midnight.</p>
      <CheckboxGroup legend="Months" options={MONTH_NAMES.map((name, i) => ({ value: i + 1, label: name }))} selected={months} onChange={setMonths} />
      <CheckboxGroup legend="Days" options={DAY_NAMES.map((name, i) => ({ value: i, label: name }))} selected={days} onChange={setDays} />
      <button
        type="submit"
        disabled={isPending || months.length === 0 || days.length === 0}
        className="rounded-md bg-solar px-3 py-1 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
      >
        Add window
      </button>
    </form>
  );
}

export function RateWindowsPanel({ siteId, sourceId }: { siteId: number; sourceId: number }) {
  const { data: windows } = useRateWindows(siteId, sourceId);
  const createWindow = useCreateRateWindow(siteId, sourceId);
  const replaceWindow = useReplaceRateWindow(siteId, sourceId);
  const deleteWindow = useDeleteRateWindow(siteId, sourceId);
  const gap = useMemo(() => hasCoverageGap(windows ?? []), [windows]);

  const actions: WindowActions = {
    onEnd: (window) =>
      replaceWindow.mutate({ windowId: window.id, input: toInput(window, { effective_to: new Date().toISOString() }) }),
    onDelete: (window) => deleteWindow.mutate(window.id),
    isPending: replaceWindow.isPending || deleteWindow.isPending,
  };
  const failure = [createWindow, replaceWindow, deleteWindow].find((m) => m.isError)?.error;

  return (
    <div className="space-y-3 border-t border-line p-3">
      <h3 className="font-display text-base font-semibold text-ink">Rate windows</h3>
      {gap && (
        <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-sm text-ink">
          Some months, days or times are not covered by a window. Those minutes will have no price.
        </p>
      )}
      {CHANNEL_SECTIONS.map(({ channel, title }) => (
        <ChannelSection key={channel} title={title} windows={(windows ?? []).filter((w) => w.channel === channel)} actions={actions} />
      ))}
      <AddWindowForm
        onAdd={(input) => createWindow.mutate(input)}
        isPending={createWindow.isPending}
        error={createWindow.isError ? createWindow.error.message : null}
      />
      {failure && !createWindow.isError && (
        <p role="alert" className={errorClass}>
          {failure instanceof Error ? failure.message : "That change failed. Please try again."}
        </p>
      )}
    </div>
  );
}
```

Run: `cd webapp && npx vitest run src/sources`
Expected: pass.

- [ ] **Step 6: Write the failing Sources page tests**

Append to `webapp/src/pages/SourcesPage.test.tsx` inside `describe("SourcesPage", ...)` (read the existing "creates a source" test first and match its interaction style):

```tsx
  it("offers the local provider for pricing sources and requires a tariff name", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("No sources yet.");

    await user.selectOptions(screen.getByLabelText("Kind"), "pricing");
    await user.selectOptions(screen.getByLabelText("Provider"), "local");

    expect(screen.getByLabelText("Tariff name")).toBeRequired();
    expect(screen.queryByLabelText("Label (optional)")).not.toBeInTheDocument();
  });

  it("shows the rate windows panel for a local source instead of a config form", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([
        { id: 5, site_id: 7, kind: "pricing", provider: "local", name: "Origin Fixed", enabled: true, config: {} },
      ])
      .mockResolvedValue([]);
    renderPage();

    expect(await screen.findByRole("heading", { name: "Rate windows" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit config" })).not.toBeInTheDocument();
  });
```

Run: `cd webapp && npx vitest run src/pages/SourcesPage.test.tsx`
Expected: the two new tests FAIL.

- [ ] **Step 7: Update the Sources page**

In `webapp/src/pages/SourcesPage.tsx`:

1. Import the panel: `import { RateWindowsPanel } from "../sources/RateWindowsPanel";`
2. Change the pricing providers: `pricing: ["amber", "local"],`
3. In `CreateSourceForm`, replace the label input with a name that depends on the provider:

```tsx
        {provider === "local" ? (
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Tariff name"
            aria-label="Tariff name"
            required
            maxLength={100}
            autoComplete="off"
            className="rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
          />
        ) : (
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Label (optional)"
            aria-label="Label (optional)"
            autoComplete="off"
            className="rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
          />
        )}
```

4. In `SourceRow`, add `const isLocal = source.provider === "local";`, wrap the "Edit config" button in `{!isLocal && (...)}`, and after the `editingConfig` block add:

```tsx
      {isLocal && <RateWindowsPanel siteId={source.site_id} sourceId={source.id} />}
```

- [ ] **Step 8: Run the webapp checks**

Run: `cd webapp && npm test && npm run lint && npx tsc -b`
Expected: all pass with no type errors.

- [ ] **Step 9: Commit**

```bash
git add -A .
git commit -m "feat(webapp): add local tariff sources and the rate windows panel

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Billing page tariff heading

**Files:**
- Create: `webapp/src/billing/tariffHeading.ts`, `webapp/src/billing/tariffHeading.test.ts`
- Modify: `webapp/src/pages/BillingPage.tsx:403-431`, `webapp/src/pages/BillingPage.test.tsx`

**Interfaces:**
- Consumes: `Source` and `useSources` (Task 7 added the optional dates).
- Produces: `tariffHeading(sources: Source[] | undefined, now?: Date): string`.

- [ ] **Step 1: Write the failing heading tests**

Create `webapp/src/billing/tariffHeading.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { Source } from "../sources/useSources";
import { tariffHeading } from "./tariffHeading";

const NOW = new Date("2026-06-15T00:00:00Z");

function source(overrides: Partial<Source>): Source {
  return {
    id: 1,
    site_id: 7,
    kind: "pricing",
    provider: "amber",
    name: null,
    enabled: true,
    effective_to: null,
    config: {},
    ...overrides,
  };
}

describe("tariffHeading", () => {
  it("is Tariffs before the sources load", () => {
    expect(tariffHeading(undefined, NOW)).toBe("Tariffs");
  });

  it("is Tariffs with no pricing source", () => {
    expect(tariffHeading([source({ kind: "energy", provider: "envoy" })], NOW)).toBe("Tariffs");
  });

  it("is Amber Tariffs for an Amber source, whatever its label", () => {
    expect(tariffHeading([source({ name: "Home" })], NOW)).toBe("Amber Tariffs");
  });

  it("uses the tariff name for a local source", () => {
    expect(tariffHeading([source({ provider: "local", name: "Origin Fixed" })], NOW)).toBe("Origin Fixed Tariffs");
  });

  it("falls back to Tariffs for a local source with a blank name", () => {
    expect(tariffHeading([source({ provider: "local", name: "  " })], NOW)).toBe("Tariffs");
  });

  it("ignores a pricing source that has ended", () => {
    const ended = source({ provider: "local", name: "Old", effective_to: "2026-01-01T00:00:00Z" });
    expect(tariffHeading([ended], NOW)).toBe("Tariffs");
  });

  it("ignores a disabled pricing source", () => {
    expect(tariffHeading([source({ enabled: false })], NOW)).toBe("Tariffs");
  });

  it("uses the active source when an ended one is also listed", () => {
    const ended = source({ id: 1, provider: "amber", effective_to: "2026-01-01T00:00:00Z" });
    const current = source({ id: 2, provider: "local", name: "Origin Fixed" });
    expect(tariffHeading([ended, current], NOW)).toBe("Origin Fixed Tariffs");
  });
});
```

Run: `cd webapp && npx vitest run src/billing/tariffHeading.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 2: Implement**

Create `webapp/src/billing/tariffHeading.ts`:

```ts
import type { Source } from "../sources/useSources";

const DEFAULT_HEADING = "Tariffs";

function isActivePricingSource(source: Source, now: Date): boolean {
  return (
    source.kind === "pricing" &&
    source.enabled &&
    (source.effective_to == null || new Date(source.effective_to) > now)
  );
}

/** The Billing section heading, named after whoever supplies the site's
 * prices. A site has at most one active pricing source. */
export function tariffHeading(sources: Source[] | undefined, now: Date = new Date()): string {
  const active = sources?.find((source) => isActivePricingSource(source, now));
  if (!active) return DEFAULT_HEADING;
  if (active.provider === "amber") return "Amber Tariffs";
  const name = active.name?.trim();
  return name ? `${name} Tariffs` : DEFAULT_HEADING;
}
```

Run: `cd webapp && npx vitest run src/billing/tariffHeading.test.ts`
Expected: pass.

- [ ] **Step 3: Write the failing page test**

Read the top of `webapp/src/pages/BillingPage.test.tsx` (`renderPage`, `emptyListsFetch`) and how existing tests route `apiFetch` by path. Add a test in the same style:

```tsx
  it("names the tariff section after the site's pricing source", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/sources") {
        return Promise.resolve([
          { id: 5, site_id: 7, kind: "pricing", provider: "local", name: "Origin Fixed", enabled: true, effective_to: null, config: {} },
        ]);
      }
      return emptyListsFetch(path);
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "Origin Fixed Tariffs" })).toBeInTheDocument();
  });

  it("falls back to Tariffs with no pricing source", async () => {
    vi.mocked(apiFetch).mockImplementation(emptyListsFetch);
    renderPage();

    expect(await screen.findByRole("heading", { name: "Tariffs" })).toBeInTheDocument();
  });
```

Run: `cd webapp && npx vitest run src/pages/BillingPage.test.tsx`
Expected: the first new test FAILS.

- [ ] **Step 4: Use the heading**

In `webapp/src/pages/BillingPage.tsx`, import `useSources` from `../sources/useSources` and `tariffHeading` from `../billing/tariffHeading`. In `TariffSection`, add `const { data: sources } = useSources(siteId);` with the other hooks and change the heading line to:

```tsx
      <h2 className="font-display text-lg font-semibold text-ink">{tariffHeading(sources)}</h2>
```

- [ ] **Step 5: Run the webapp checks**

Run: `cd webapp && npm test && npm run lint && npx tsc -b`
Expected: all pass. Existing Billing tests that mock every `apiFetch` call with an empty list still pass because `tariffHeading` receives `[]` and returns "Tariffs".

- [ ] **Step 6: Commit**

```bash
git add -A .
git commit -m "feat(webapp): name the Billing tariff section after the pricing source

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Docs and README

**Files:**
- Modify: `webapp/src/docs/04-sources.md`, `webapp/src/docs/06-billing.md`, `webapp/src/docs/09-glossary.md`, `README.md`, `.claude/agents/documentation.md`

**Interfaces:** none. The webapp's `docs.test.ts` and `links.test.ts` check chapter structure and links, so run them.

- [ ] **Step 1: Document the local source**

In `webapp/src/docs/04-sources.md`, add this section before `## Connecting a WeatherLink Live station`:

```markdown
## Setting up a fixed tariff

Use this when your supplier charges fixed or time-of-use rates and has no price API. You type the rates in, and WattWise prices every minute from them.

1. Open **Sources** and choose **pricing** as the kind and **local** as the provider.
2. Type a **Tariff name**, such as "Origin Fixed". The Billing page uses it as its heading, so this one reads "Origin Fixed Tariffs".
3. Select **Create source**. A **Rate windows** panel appears under the source.

A site can have one pricing source at a time. Close or delete the Amber source first if you are switching.

### Rate windows

Each window is one rate for one direction:

| Direction | What it prices |
|---|---|
| **Import** | Energy you buy from the grid. |
| **Export (feed-in)** | Energy you sell to the grid. Type the rate as a positive number. WattWise stores it the same way it stores Amber's feed-in prices. |
| **Controlled load** | Energy on a separate controlled-load circuit. |

Fill in the **Label**, the **Rate (c/kWh)**, the date it starts (**From**), a **Start** and **End** time, and tick the months and days it applies to. The start time is included and the end time is not, so a window that ends at 07:00 and one that starts at 07:00 do not overlap. Equal start and end times cover the whole day, which is how you enter a flat rate. An end time before the start time runs past midnight, so 22:00 to 07:00 is overnight. Months and days refer to the minute being priced.

Windows on the same direction cannot overlap. If you try, WattWise names the window that is in the way.

If some months, days or times are not covered, a notice says so. Those minutes have no price and show as missing in your cost figures.

### Changing a rate

Select **End** on the old window, then add a new window that starts on the day the rate changes. Cost for earlier dates keeps the old rate. Deleting a window stops new prices from it. Prices already stored stay as they are.

The collector pushes prices for the next 48 hours every 5 minutes, so a new window reaches the dashboard within about 5 minutes.
```

- [ ] **Step 2: Update the billing and glossary chapters**

In `webapp/src/docs/06-billing.md`, in the `## Tariffs` section, add one sentence after its opening paragraph: `The section heading names your price supplier: "Amber Tariffs" with an Amber source, "Origin Fixed Tariffs" with a local source called Origin Fixed, and "Tariffs" with neither. The supply charge and monthly fee below apply to every kind of pricing source. Per-kWh rates for a local source are set in [Sources](04-sources.md#setting-up-a-fixed-tariff).`

In `webapp/src/docs/09-glossary.md`, change the **Provider** row to: `The brand or system behind a source: `envoy`, `amber`, `local` (rates you type in) or `weatherlink_live`.`

- [ ] **Step 3: Update the README and the documentation agent**

In `README.md`:
- Add a row below the `amber-pricing-collector/` row: `| `local-pricing-collector/` | Turns rate windows entered in the webapp into price intervals, for suppliers with a fixed tariff. |`
- In the run instructions near the line that mentions `amber_pricing_collector.main`, add `local_pricing_collector.main` and say the local collector also uses `PRICING_COLLECTOR_KEY`.
- Add one sentence under the services table: `To add a collector for another supplier, create a directory like amber-pricing-collector, use its own provider name in the discovery request (provider=<name>) and send the X-Collector-Provider header.`

In `.claude/agents/documentation.md` line 8, change the sub-project list to name `amber-pricing-collector` and `local-pricing-collector` (and say "six sub-projects" if it says five; check the count against the list).

- [ ] **Step 4: Run the checks**

Run: `cd webapp && npm test && npm run lint`
Expected: all pass, including `docs.test.ts` and `links.test.ts`.

Then run the writing-style scan from `.claude/rules/avoid-ai-writing.md` over the sentences you added: no banned vocabulary, no "serves as", no trailing "-ing" clauses.

- [ ] **Step 5: Run every suite once**

```bash
(cd core-api && uv run pytest -q)
(cd amber-pricing-collector && uv run pytest -q)
(cd local-pricing-collector && uv run pytest -q)
(cd webapp && npm test && npm run lint && npx tsc -b)
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A .
git commit -m "docs: document local fixed tariffs and the collector split

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage**

| Spec section | Task |
|---|---|
| Rename | 1 |
| Provider filter, provider header | 2 |
| `local_rate_windows` table, delete with source | 3 |
| Overlap validation, gaps allowed | 4 (logic), 5 (422), 7 (banner) |
| Endpoints, config delivery, name required, blank rename | 5 |
| Collector: 5-minute tick, hour-capped intervals, sell sign, per-source processes, DST | 6 |
| Webapp provider choice, panel, direction labels, uncovered-time banner | 7 |
| Billing heading | 8 |
| Help page and README | 9 |
| Cost test with a local source | 5 (step 8) |
| Amber collector tests pass after the rename | 1 |

**Differences from the spec text:** the spec's PATCH is implemented as a full replace (Task 5 explains why). The webapp ends a window by sending it back with `effective_to` set. Core-api owner-only access is the existing `require_site_owner` behavior.

**Type consistency:** the config keys written in Task 5 step 6 (`timezone`, `rate_windows`, and the eight window fields) are the keys read in Task 6 `rates._parse_window` and `poll.sync_one_source`. `RateWindowInput` in Task 7 matches `LocalRateWindowCreate` in Task 5 field for field. `fetch_collector_sources` returns `list[int]` in Task 6 and `poll._run_tick` uses it that way.
