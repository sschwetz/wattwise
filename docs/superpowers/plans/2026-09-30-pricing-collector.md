# Pricing Collector (Amber Electric) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Pricing collector (sub-project 3): a standalone `pricing-collector` project that polls Amber Electric's public API for one or more sites' prices and pushes them into Core API, plus the small set of Core API extensions this needs.

**Architecture:** Five Core API tasks add a per-user Amber credential, a server-side config merge, and three new collector-facing endpoints supporting historical backfill. Nine collector tasks build a standalone project with one master process running two independent schedules: a 5-minute wall-clock-aligned live poll that forks one child process per configured site (via `multiprocessing.Process`, a real OS `fork()`), and a 12-hourly inline sequential backfill job that closes gaps in price history.

**Tech Stack:** Python 3.12, `uv`, FastAPI + SQLAlchemy 2.0 + Alembic (Core API side), `httpx` + `respx` for the collector, `pytest`, Docker.

**Spec:** `enphase-data/docs/superpowers/specs/2026-09-30-pricing-collector-design.md`

## Global Constraints

- Amber base URL `https://api.amber.com.au/v1`, auth header `Authorization: Bearer {amber_api_key}` — entirely separate credential from Core API's `X-Collector-Key`.
- `channelType` → `channel` mapping: `general`→`buy`, `feedIn`→`sell`, `controlledLoad`→`controlled_load`. `PriceChannel.DEMAND` is never populated by this collector.
- `perKwh` → `price`: direct, no conversion (already cents/kWh, matching Core API's `cost.py` usage).
- `startTime`/`endTime` → `interval_start`/`interval_end`: direct passthrough, including the 1-second boundary stagger.
- `tariffInformation` is never read. `is_demand_window` is always omitted (schema default `false`).
- Live poll pushes `ActualInterval`, `CurrentInterval`, and `ForecastInterval` entries (all map identically); the historical/backfill endpoint only ever returns `ActualInterval`.
- Amber's historical endpoint (`GET /sites/{id}/prices`) caps `startDate`/`endDate` span at 7 days per call and only accepts whole calendar dates.
- `PRICING_SOURCES` env var: JSON array of `{"source_id": int, "collector_key": str}`, one entry per site. `CORE_API_URL` shared across all sites. No Amber-specific env vars — `amber_site_id`/`amber_api_key` both come from each source's `GET /sources/{id}/config`.
- Live poll: sleep until 15 seconds past the next UTC `:00/:05/.../:55` boundary, then fork one child per configured source via `multiprocessing.Process`. `.join(timeout=60)` each child; `.terminate()` + reap any still alive past that.
- Each live-poll child is one-shot (config-fetch → Amber fetch → map → push → exit), no internal retry loop.
- Backfill runs every 12 hours, inline in the master process, sequentially per site — never forked.
- The Core API HTTP client wraps every `httpx.HTTPError` into one `CoreApiError` type from its first commit (same lesson carried from the Envoy/Weather collectors).
- Mapping isolates individual malformed/unrecognized-channel intervals (logged, skipped) without discarding the rest of the batch.
- `User.amber_api_key`: new nullable `str` column, via Alembic migration.
- `GET /sources/{id}/config`: unchanged shape/behavior for every provider except `amber`, where the response's `config` dict gets `amber_api_key` merged in from the site owner's `User` row.
- `PriceGapMarker(pricing_source_id, channel, gap_date)`, PK on all three columns — records a calendar day confirmed to have no data for that channel, so it's never re-reported as a gap.

## Review Focus

- A gap date-range Amber genuinely has no data for (e.g. before the site was Amber-connected) must get a `PriceGapMarker` row and never be re-reported as a gap on a later run — not just skipped for one cycle.
- A `ForecastInterval` pushed now, then overwritten by the real `ActualInterval` once it settles (same `interval_start`), must end up with only the actual's price stored — the upsert-on-natural-key behavior must actually hold across a forecast→actual transition, not just within one push.
- An unrecognized `channelType` (a hypothetical future Amber addition) in either the live-poll or backfill response must be isolated — skip that one entry, keep the rest of the batch.
- A brand-new site with zero readings anywhere yet (`site-data-floor` returns `null`) must not crash the backfill job — it should no-op that cycle, not raise.
- One site's forked child process crashing during the live poll must not prevent the other configured sites' children from completing that same tick — real process-level isolation, not just isolation assumed from the design.

---

## Task 1: Core API — `User.amber_api_key`

**Files:**
- Modify: `core_api/core_api/models/identity.py`
- Create: `core_api/alembic/versions/b8e0a7d45d94_add_amber_api_key_to_users.py`
- Test: `core_api/tests/test_bootstrap.py` (extend)

**Interfaces:**
- Produces: `User.amber_api_key: str | None` — a nullable column later tasks read via `db.get(User, ...)`.

- [ ] **Step 1: Write the failing test**

Add to `core_api/tests/test_bootstrap.py`:

```python
def test_amber_api_key_column_exists_and_defaults_null(db):
    from core_api.models.identity import User

    u = User(email="amber-user@example.com", password_hash="hash")
    db.add(u)
    db.commit()
    db.refresh(u)
    assert u.amber_api_key is None

    u.amber_api_key = "psk_example_token"
    db.commit()
    db.refresh(u)
    assert u.amber_api_key == "psk_example_token"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && uv run pytest tests/test_bootstrap.py::test_amber_api_key_column_exists_and_defaults_null -v`
Expected: FAIL — `AttributeError: 'User' object has no attribute 'amber_api_key'`.

- [ ] **Step 3: Add the column to the model**

In `core_api/core_api/models/identity.py`, add to `User` (after `password_hash`):

```python
    amber_api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

- [ ] **Step 4: Write the migration**

Create `core_api/alembic/versions/b8e0a7d45d94_add_amber_api_key_to_users.py`:

```python
"""add amber_api_key to users

Revision ID: b8e0a7d45d94
Revises: 835fc75bd640
Create Date: 2026-09-30 13:44:32.380919

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8e0a7d45d94'
down_revision: Union[str, Sequence[str], None] = '835fc75bd640'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('users', sa.Column('amber_api_key', sa.String(length=255), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('users', 'amber_api_key')
    # ### end Alembic commands ###
```

This is the exact output of `uv run alembic revision --autogenerate -m "add amber_api_key to users"` against the current head (`835fc75bd640`) with the Step 3 model change in place — verified during plan authoring. Running autogenerate again should produce an empty diff; if it doesn't, the model and migration have drifted and the migration file should be regenerated instead of hand-edited further.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd core-api && uv run pytest tests/test_bootstrap.py::test_amber_api_key_column_exists_and_defaults_null -v`
Expected: PASS. (The `db` fixture uses `Base.metadata.create_all`, so this test passes as soon as the model column exists — it doesn't exercise the migration file directly. Step 6 covers that.)

- [ ] **Step 6: Verify the migration applies cleanly**

Run: `cd core-api && uv run pytest -q`
Expected: all tests pass — the `client` fixture uses `run_migrations()` (real Alembic) for every test, so a broken migration file would show up as widespread failures here, not just in this task's own test.

- [ ] **Step 7: Commit**

```bash
git add core_api/models/identity.py alembic/versions/b8e0a7d45d94_add_amber_api_key_to_users.py tests/test_bootstrap.py
git commit -m "Add User.amber_api_key column"
```

---

## Task 2: Core API — merge owner's Amber key into the config endpoint

**Files:**
- Modify: `core_api/core_api/routers/collector_config.py`
- Test: `core_api/tests/test_collector_config.py` (extend)

**Interfaces:**
- Consumes: `User.amber_api_key` (Task 1).
- Produces: `GET /sources/{id}/config` returns `{"config": {**source.config, "amber_api_key": owner.amber_api_key}}` when `source.provider == "amber"`; unchanged for every other provider.

- [ ] **Step 1: Write the failing tests**

Add to `core_api/tests/test_collector_config.py`:

```python
def make_pricing_source(db_url: str, amber_api_key: str | None, config: dict | None = None) -> tuple[int, str]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email=f"cc-amber-owner-{secrets.token_hex(4)}@example.com", password_hash="x", amber_api_key=amber_api_key)
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    raw_key = secrets.token_urlsafe(16)
    source = Source(
        site_id=site.id,
        kind=SourceKind.PRICING,
        provider="amber",
        config=config or {"amber_site_id": "example-site-id"},
        collector_api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        effective_from=datetime.now(timezone.utc),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id, raw_key


def test_get_source_config_merges_owner_amber_key_for_amber_provider(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_pricing_source(db_url, amber_api_key="psk_owner_token")
    response = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": raw_key})
    assert response.status_code == 200, response.text
    assert response.json()["config"] == {"amber_site_id": "example-site-id", "amber_api_key": "psk_owner_token"}


def test_get_source_config_does_not_merge_key_for_non_amber_provider(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_energy_source(db_url, {"host": "envoy.local", "token": "abc"})
    response = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": raw_key})
    assert response.status_code == 200, response.text
    assert response.json()["config"] == {"host": "envoy.local", "token": "abc"}
    assert "amber_api_key" not in response.json()["config"]


def test_get_source_config_merges_null_when_owner_has_no_amber_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_pricing_source(db_url, amber_api_key=None)
    response = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": raw_key})
    assert response.status_code == 200, response.text
    assert response.json()["config"]["amber_api_key"] is None
```

Add these imports to the top of `core_api/tests/test_collector_config.py` (alongside the existing ones):

```python
from core_api.models.sources import SourceKind
```

(`User`, `Site`, `Source`, `hashlib`, `secrets`, `datetime`, `timezone`, `make_engine`, `make_session_factory` are already imported in this file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_collector_config.py -k amber -v`
Expected: FAIL — `test_get_source_config_merges_owner_amber_key_for_amber_provider` and `test_get_source_config_merges_null_when_owner_has_no_amber_key` fail with `KeyError: 'amber_api_key'` (or an assertion mismatch); `test_get_source_config_does_not_merge_key_for_non_amber_provider` passes already (nothing to change for that path) — that's expected, it's here to pin the no-regression case.

- [ ] **Step 3: Implement the merge**

Replace `core_api/core_api/routers/collector_config.py`'s imports and `get_source_config` with:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, get_source_by_collector_key
from core_api.models.identity import Site, User
from core_api.models.sources import Source
from core_api.schemas import DiscoveredSourceConfig, SourceConfigOut

router = APIRouter(prefix="/sources/{source_id}", tags=["collector-config"])


@router.get("/config", response_model=SourceConfigOut)
def get_source_config(
    source: Source = Depends(get_source_by_collector_key), db: Session = Depends(get_db_session)
) -> SourceConfigOut:
    config = dict(source.config or {})
    if source.provider == "amber":
        site = db.get(Site, source.site_id)
        owner = db.get(User, site.owner_user_id)
        config["amber_api_key"] = owner.amber_api_key
    return SourceConfigOut(config=config)


@router.patch("/discovered-config", status_code=status.HTTP_204_NO_CONTENT)
def report_discovered_config(
    payload: DiscoveredSourceConfig,
    source: Source = Depends(get_source_by_collector_key),
    db: Session = Depends(get_db_session),
) -> None:
    source.config = {**(source.config or {}), "serial_num": payload.serial_num}
    db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_collector_config.py -v`
Expected: PASS (all tests in this file, including the 4 pre-existing ones — 7 total).

- [ ] **Step 5: Run full suite, then commit**

```bash
cd core-api
uv run pytest -q
git add core_api/routers/collector_config.py tests/test_collector_config.py
git commit -m "Merge site owner's Amber API key into the config endpoint"
```

---

## Task 3: Core API — `PriceGapMarker` table + gap-marker endpoint

**Files:**
- Modify: `core_api/core_api/models/pricing.py`
- Modify: `core_api/core_api/models/__init__.py`
- Modify: `core_api/core_api/schemas.py`
- Create: `core_api/alembic/versions/1395c4a4134a_add_price_gap_markers.py`
- Create: `core_api/core_api/routers/pricing_backfill.py`
- Modify: `core_api/core_api/main.py`
- Test: `core_api/tests/test_pricing_backfill.py`

**Interfaces:**
- Produces: `PriceGapMarker` model; `POST /sources/{id}/prices/gap-markers` with body `{"channel": ..., "gap_date": "YYYY-MM-DD"}` → `204`.

- [ ] **Step 1: Write the failing test**

Create `core_api/tests/test_pricing_backfill.py`:

```python
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, User
from core_api.models.sources import Source, SourceKind


def make_pricing_source(db_url: str, config: dict | None = None) -> tuple[int, str]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email=f"pb-owner-{secrets.token_hex(4)}@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    raw_key = secrets.token_urlsafe(16)
    source = Source(
        site_id=site.id,
        kind=SourceKind.PRICING,
        provider="amber",
        config=config or {"amber_site_id": "example-site-id"},
        collector_api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        effective_from=datetime.now(timezone.utc),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id, raw_key


def test_create_gap_marker_is_idempotent(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_pricing_source(db_url)
    payload = {"channel": "buy", "gap_date": "2025-03-01"}
    headers = {"X-Collector-Key": raw_key}

    first = client.post(f"/api/v1/sources/{source_id}/prices/gap-markers", json=payload, headers=headers)
    assert first.status_code == 204, first.text
    second = client.post(f"/api/v1/sources/{source_id}/prices/gap-markers", json=payload, headers=headers)
    assert second.status_code == 204, second.text

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.pricing import PriceGapMarker

    markers = session.query(PriceGapMarker).all()
    session.close()
    assert len(markers) == 1


def test_create_gap_marker_wrong_key_rejected(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, _raw_key = make_pricing_source(db_url)
    response = client.post(
        f"/api/v1/sources/{source_id}/prices/gap-markers",
        json={"channel": "buy", "gap_date": "2025-03-01"},
        headers={"X-Collector-Key": "wrong"},
    )
    assert response.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && uv run pytest tests/test_pricing_backfill.py -v`
Expected: FAIL — `404 Not Found` (no such route yet), so `assert first.status_code == 204` fails with `assert 404 == 204`.

- [ ] **Step 3: Add the model**

In `core_api/core_api/models/pricing.py`, add after `PriceReadingRollup5m`:

```python
class PriceGapMarker(Base):
    """Records a calendar day the pricing provider has confirmed has no data
    for a channel, so the backfill job stops asking about it forever."""

    __tablename__ = "price_gap_markers"
    __table_args__ = (enum_check("channel", PriceChannel, "ck_price_gap_markers_channel"),)

    pricing_source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    channel: Mapped[PriceChannel] = mapped_column(str_enum(PriceChannel, length=32), primary_key=True)
    gap_date: Mapped[datetime.date] = mapped_column(primary_key=True)
```

- [ ] **Step 4: Write the migration**

Create `core_api/alembic/versions/1395c4a4134a_add_price_gap_markers.py`:

```python
"""add price gap markers

Revision ID: 1395c4a4134a
Revises: b8e0a7d45d94
Create Date: 2026-09-30 13:44:52.679018

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1395c4a4134a'
down_revision: Union[str, Sequence[str], None] = 'b8e0a7d45d94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('price_gap_markers',
    sa.Column('pricing_source_id', sa.Integer(), nullable=False),
    sa.Column('channel', sa.Enum('buy', 'sell', 'controlled_load', 'demand', name='pricechannel', native_enum=False, length=32), nullable=False),
    sa.Column('gap_date', sa.Date(), nullable=False),
    sa.CheckConstraint("channel IN ('buy', 'sell', 'controlled_load', 'demand')", name='ck_price_gap_markers_channel'),
    sa.ForeignKeyConstraint(['pricing_source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('pricing_source_id', 'channel', 'gap_date')
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('price_gap_markers')
    # ### end Alembic commands ###
```

This is the exact output of `uv run alembic revision --autogenerate -m "add price gap markers"` against head `b8e0a7d45d94` with the Step 3 model in place — verified during plan authoring, same caveat as Task 1 Step 4 about re-running autogenerate to confirm an empty diff.

- [ ] **Step 5: Export the model and add the schema**

In `core_api/core_api/models/__init__.py`, change the pricing import line to:

```python
from core_api.models.pricing import PriceChannel, PriceGapMarker, PriceInterval, PriceReading, PriceReadingRollup5m
```

and add `"PriceGapMarker",` to `__all__` (next to `"PriceReadingRollup5m"`).

In `core_api/core_api/schemas.py`, add after `PriceIntervalBatch`:

```python
class GapMarkerCreate(BaseModel):
    channel: PriceChannel
    gap_date: datetime.date
```

- [ ] **Step 6: Create the router**

Create `core_api/core_api/routers/pricing_backfill.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, require_source_kind
from core_api.models.pricing import PriceGapMarker
from core_api.models.sources import Source, SourceKind
from core_api.schemas import GapMarkerCreate

router = APIRouter(prefix="/sources/{source_id}/prices", tags=["pricing-backfill"])


@router.post("/gap-markers", status_code=status.HTTP_204_NO_CONTENT)
def create_gap_marker(
    payload: GapMarkerCreate,
    source: Source = Depends(require_source_kind(SourceKind.PRICING)),
    db: Session = Depends(get_db_session),
) -> None:
    stmt = sqlite_insert(PriceGapMarker).values(
        pricing_source_id=source.id, channel=payload.channel, gap_date=payload.gap_date
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["pricing_source_id", "channel", "gap_date"])
    db.execute(stmt)
    db.commit()
```

- [ ] **Step 7: Register the router**

In `core_api/core_api/main.py`, add the import:

```python
from core_api.routers.pricing_backfill import router as pricing_backfill_router
```

and register it alongside the others:

```python
    app.include_router(pricing_backfill_router, prefix=api_prefix)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_pricing_backfill.py -v`
Expected: PASS (2 tests).

- [ ] **Step 9: Run full suite, then commit**

```bash
cd core-api
uv run pytest -q
git add core_api/models/pricing.py core_api/models/__init__.py core_api/schemas.py \
        alembic/versions/1395c4a4134a_add_price_gap_markers.py \
        core_api/routers/pricing_backfill.py core_api/main.py tests/test_pricing_backfill.py
git commit -m "Add PriceGapMarker table and gap-marker reporting endpoint"
```

---

## Task 4: Core API — `GET /sources/{id}/site-data-floor`

**Files:**
- Modify: `core_api/core_api/routers/pricing_backfill.py`
- Modify: `core_api/core_api/schemas.py`
- Test: `core_api/tests/test_pricing_backfill.py` (extend)

**Interfaces:**
- Consumes: `EnergyReading`, `WeatherReading`, `PriceReading` (existing models), `Source` (existing).
- Produces: `GET /sources/{id}/site-data-floor` → `{"floor_ts": "<iso datetime>" | null}`.

- [ ] **Step 1: Write the failing tests**

Add to `core_api/tests/test_pricing_backfill.py`:

```python
def make_energy_source_for_site(db_url: str, site_id: int) -> int:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    raw_key = secrets.token_urlsafe(16)
    source = Source(
        site_id=site_id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        collector_api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        effective_from=datetime.now(timezone.utc),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id


def test_site_data_floor_is_null_when_site_has_no_readings_anywhere(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_pricing_source(db_url)
    response = client.get(f"/api/v1/sources/{source_id}/site-data-floor", headers={"X-Collector-Key": raw_key})
    assert response.status_code == 200, response.text
    assert response.json() == {"floor_ts": None}


def test_site_data_floor_is_earliest_reading_across_any_source_kind(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_pricing_source(db_url)

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    pricing_source = session.get(Source, source_id)
    energy_source_id = make_energy_source_for_site(db_url, pricing_source.site_id)

    from core_api.models.readings import EnergyMetric, EnergyReading
    from core_api.models.pricing import PriceChannel, PriceReading

    session.add(EnergyReading(
        source_id=energy_source_id,
        ts=datetime(2025, 1, 1, tzinfo=timezone.utc),
        metric=EnergyMetric.PRODUCTION_W,
        value=100.0,
    ))
    session.add(PriceReading(
        pricing_source_id=source_id,
        ts=datetime(2025, 6, 1, tzinfo=timezone.utc),
        channel=PriceChannel.BUY,
        value=25.0,
    ))
    session.commit()
    session.close()

    response = client.get(f"/api/v1/sources/{source_id}/site-data-floor", headers={"X-Collector-Key": raw_key})
    assert response.status_code == 200, response.text
    assert response.json()["floor_ts"].startswith("2025-01-01")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_pricing_backfill.py -k site_data_floor -v`
Expected: FAIL — `404 Not Found` (no such route yet).

- [ ] **Step 3: Add the schema and endpoint**

In `core_api/core_api/schemas.py`, add after `GapMarkerCreate`:

```python
class SiteDataFloorOut(BaseModel):
    floor_ts: datetime.datetime | None
```

In `core_api/core_api/routers/pricing_backfill.py`, add these imports:

```python
from sqlalchemy import func, select

from core_api.deps import get_source_by_collector_key
from core_api.models.readings import EnergyReading, WeatherReading
from core_api.models.pricing import PriceReading
from core_api.schemas import SiteDataFloorOut
```

and this endpoint (registered under `/sources/{source_id}`, not `/sources/{source_id}/prices`, so add it to a second router in the same file):

```python
site_router = APIRouter(prefix="/sources/{source_id}", tags=["pricing-backfill"])


@site_router.get("/site-data-floor", response_model=SiteDataFloorOut)
def get_site_data_floor(
    source: Source = Depends(get_source_by_collector_key), db: Session = Depends(get_db_session)
) -> SiteDataFloorOut:
    source_ids = list(db.execute(select(Source.id).where(Source.site_id == source.site_id)).scalars().all())
    if not source_ids:
        return SiteDataFloorOut(floor_ts=None)

    candidates = []
    for model, key_col in (
        (EnergyReading, EnergyReading.source_id),
        (WeatherReading, WeatherReading.source_id),
        (PriceReading, PriceReading.pricing_source_id),
    ):
        earliest = db.execute(select(func.min(model.ts)).where(key_col.in_(source_ids))).scalar()
        if earliest is not None:
            candidates.append(earliest)

    if not candidates:
        return SiteDataFloorOut(floor_ts=None)
    return SiteDataFloorOut(floor_ts=min(candidates))
```

Then register the second router in `core_api/core_api/main.py`:

```python
from core_api.routers.pricing_backfill import router as pricing_backfill_router
from core_api.routers.pricing_backfill import site_router as pricing_site_data_router
```

```python
    app.include_router(pricing_backfill_router, prefix=api_prefix)
    app.include_router(pricing_site_data_router, prefix=api_prefix)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_pricing_backfill.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run full suite, then commit**

```bash
cd core-api
uv run pytest -q
git add core_api/routers/pricing_backfill.py core_api/schemas.py core_api/main.py tests/test_pricing_backfill.py
git commit -m "Add GET /sources/{id}/site-data-floor endpoint"
```

---

## Task 5: Core API — `GET /sources/{id}/prices/gaps`

**Files:**
- Create: `core_api/core_api/pricing_gaps.py`
- Test: `core_api/tests/test_pricing_gaps.py`
- Modify: `core_api/core_api/routers/pricing_backfill.py`
- Test: `core_api/tests/test_pricing_backfill.py` (extend)

**Interfaces:**
- Consumes: `PriceInterval`, `PriceGapMarker` (Task 3).
- Produces: `missing_days_in_range(since_date, until_date, covered_days, marked_days) -> list[date]`, `coalesce_missing_days(missing_days) -> list[tuple[date, date]]`; `GET /sources/{id}/prices/gaps?since=<iso datetime>` → `{"gaps": [{"channel": ..., "start_date": ..., "end_date": ...}, ...]}`.

- [ ] **Step 1: Write the failing tests for the pure gap-computation logic**

Create `core_api/tests/test_pricing_gaps.py`:

```python
from __future__ import annotations

import datetime

from core_api.pricing_gaps import coalesce_missing_days, missing_days_in_range


def d(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def test_missing_days_excludes_covered_and_marked_days():
    covered = {d("2025-01-02")}
    marked = {d("2025-01-03")}
    missing = missing_days_in_range(d("2025-01-01"), d("2025-01-04"), covered, marked)
    assert missing == [d("2025-01-01"), d("2025-01-04")]


def test_missing_days_returns_empty_when_fully_covered():
    covered = {d("2025-01-01"), d("2025-01-02")}
    missing = missing_days_in_range(d("2025-01-01"), d("2025-01-02"), covered, set())
    assert missing == []


def test_coalesce_groups_consecutive_days_into_one_run():
    days = [d("2025-01-01"), d("2025-01-02"), d("2025-01-03")]
    assert coalesce_missing_days(days) == [(d("2025-01-01"), d("2025-01-03"))]


def test_coalesce_splits_non_consecutive_days_into_separate_runs():
    days = [d("2025-01-01"), d("2025-01-05")]
    assert coalesce_missing_days(days) == [(d("2025-01-01"), d("2025-01-01")), (d("2025-01-05"), d("2025-01-05"))]


def test_coalesce_of_empty_list_is_empty():
    assert coalesce_missing_days([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_pricing_gaps.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core_api.pricing_gaps'`.

- [ ] **Step 3: Implement the pure logic**

Create `core_api/core_api/pricing_gaps.py`:

```python
from __future__ import annotations

import datetime


def missing_days_in_range(
    since_date: datetime.date,
    until_date: datetime.date,
    covered_days: set[datetime.date],
    marked_days: set[datetime.date],
) -> list[datetime.date]:
    """Every calendar day in [since_date, until_date] that has neither real
    PriceInterval coverage nor a confirmed-unavailable PriceGapMarker."""
    missing = []
    day = since_date
    while day <= until_date:
        if day not in covered_days and day not in marked_days:
            missing.append(day)
        day += datetime.timedelta(days=1)
    return missing


def coalesce_missing_days(missing_days: list[datetime.date]) -> list[tuple[datetime.date, datetime.date]]:
    """Group a sorted list of missing calendar days into contiguous
    (start, end) runs, so the caller can turn each run directly into a
    chunked historical-API request range."""
    if not missing_days:
        return []
    runs = []
    run_start = missing_days[0]
    run_end = missing_days[0]
    for day in missing_days[1:]:
        if day == run_end + datetime.timedelta(days=1):
            run_end = day
        else:
            runs.append((run_start, run_end))
            run_start = day
            run_end = day
    runs.append((run_start, run_end))
    return runs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_pricing_gaps.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Write the failing endpoint tests**

Add to `core_api/tests/test_pricing_backfill.py`:

```python
def test_prices_gaps_returns_empty_when_no_intervals_ever_ingested(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_pricing_source(db_url)
    response = client.get(
        f"/api/v1/sources/{source_id}/prices/gaps",
        params={"since": "2025-01-01T00:00:00Z"},
        headers={"X-Collector-Key": raw_key},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"gaps": []}


def test_prices_gaps_reports_uncovered_days_for_known_channel(client, app_and_db_url):
    # Uses dates relative to the real "today" (rather than freezing time) so
    # the test stays correct regardless of when the suite runs: day 0 is
    # covered by a real PriceInterval, day 1 is explicitly marked
    # unavailable, and days 2..N (up to today) are genuine gaps.
    from datetime import timedelta

    _, db_url = app_and_db_url
    source_id, raw_key = make_pricing_source(db_url)

    today = datetime.now(timezone.utc).date()
    since_date = today - timedelta(days=4)
    covered_day = since_date
    marked_day = since_date + timedelta(days=1)
    first_gap_day = since_date + timedelta(days=2)

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.pricing import PriceChannel, PriceGapMarker, PriceInterval

    covered_start = datetime(covered_day.year, covered_day.month, covered_day.day, tzinfo=timezone.utc)
    session.add(PriceInterval(
        pricing_source_id=source_id,
        channel=PriceChannel.BUY,
        interval_start=covered_start,
        interval_end=covered_start + timedelta(minutes=5),
        price=25,
    ))
    session.add(PriceGapMarker(pricing_source_id=source_id, channel=PriceChannel.BUY, gap_date=marked_day))
    session.commit()
    session.close()

    since_dt = datetime(since_date.year, since_date.month, since_date.day, tzinfo=timezone.utc)
    response = client.get(
        f"/api/v1/sources/{source_id}/prices/gaps",
        params={"since": since_dt.isoformat()},
        headers={"X-Collector-Key": raw_key},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "gaps": [{"channel": "buy", "start_date": first_gap_day.isoformat(), "end_date": today.isoformat()}]
    }
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_pricing_backfill.py -k prices_gaps -v`
Expected: FAIL — `404 Not Found` for both (no such route yet).

- [ ] **Step 7: Implement the endpoint**

In `core_api/core_api/routers/pricing_backfill.py`, add these imports:

```python
import datetime

from core_api.models.pricing import PriceChannel, PriceGapMarker, PriceInterval
from core_api.pricing_gaps import coalesce_missing_days, missing_days_in_range
from core_api.schemas import UtcDatetime
```

and this endpoint on the `router` (the `/sources/{source_id}/prices`-prefixed one, alongside `create_gap_marker`):

```python
@router.get("/gaps")
def get_price_gaps(
    since: UtcDatetime,
    source: Source = Depends(require_source_kind(SourceKind.PRICING)),
    db: Session = Depends(get_db_session),
) -> dict:
    since_date = since.date()
    today = datetime.datetime.now(datetime.timezone.utc).date()

    channels = list(
        db.execute(
            select(PriceInterval.channel).where(PriceInterval.pricing_source_id == source.id).distinct()
        ).scalars()
    )

    gaps = []
    for channel in channels:
        covered_days = {
            ts.date()
            for ts in db.execute(
                select(PriceInterval.interval_start).where(
                    PriceInterval.pricing_source_id == source.id,
                    PriceInterval.channel == channel,
                    PriceInterval.interval_start >= since,
                )
            ).scalars()
        }
        marked_days = set(
            db.execute(
                select(PriceGapMarker.gap_date).where(
                    PriceGapMarker.pricing_source_id == source.id,
                    PriceGapMarker.channel == channel,
                )
            ).scalars()
        )
        missing = missing_days_in_range(since_date, today, covered_days, marked_days)
        for run_start, run_end in coalesce_missing_days(missing):
            gaps.append({"channel": channel.value, "start_date": run_start.isoformat(), "end_date": run_end.isoformat()})

    return {"gaps": gaps}
```

Also add `from sqlalchemy import select` if not already present in this file (it is, from Task 4's `site-data-floor` endpoint).

The Step 5 test computes its date range relative to the real "today" (rather than freezing time), so the expected gap range stays correct no matter when the suite actually runs.

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_pricing_backfill.py -v`
Expected: PASS (6 tests: the 4 from Tasks 3-4 plus these 2).

- [ ] **Step 9: Pin the forecast-overwritten-by-actual upsert behavior**

The live poller pushes `ForecastInterval` entries now, trusting that the real `ActualInterval` will later overwrite them at the same `interval_start` (Review Focus item 2). Core API's existing `_upsert_price_interval`/`on_conflict_do_update` already does this — this step is a regression test pinning that it actually holds when the *price itself changes* between pushes (the existing `test_ingest_price_interval_retry_is_idempotent` only proves re-pushing identical data is safe, not that a changed price overwrites cleanly).

Add to `core_api/tests/test_ingestion.py`:

```python
def test_ingest_price_interval_forecast_overwritten_by_actual(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_pricing_source(db_url)
    headers = {"X-Collector-Key": raw_key}
    interval_common = {
        "channel": "buy",
        "interval_start": "2026-01-01T00:00:00Z",
        "interval_end": "2026-01-01T00:05:00Z",
    }

    forecast_batch = {"intervals": [{**interval_common, "price": "6.35178"}]}
    client.post(f"/api/v1/sources/{source_id}/prices", json=forecast_batch, headers=headers)

    actual_batch = {"intervals": [{**interval_common, "price": "7.37398"}]}
    response = client.post(f"/api/v1/sources/{source_id}/prices", json=actual_batch, headers=headers)
    assert response.status_code == 202, response.text

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.pricing import PriceInterval, PriceReading

    intervals = session.query(PriceInterval).all()
    readings = session.query(PriceReading).all()
    session.close()
    assert len(intervals) == 1
    assert str(intervals[0].price) == "7.3740"  # the actual's price, not the forecast's
    assert len(readings) == 5  # still one row per covered minute, not 10 (no duplicate fan-out)
    assert all(str(r.value) == "7.3740" for r in readings)
```

(`make_pricing_source` is already defined earlier in `test_ingestion.py`, from the existing `test_ingest_price_interval_*` tests.)

Run: `cd core-api && uv run pytest tests/test_ingestion.py -k forecast_overwritten -v`
Expected: PASS immediately — this pins existing behavior (no production code change), so there's no RED step here; it's a regression test for a mechanism this collector newly depends on, not a new feature.

- [ ] **Step 10: Run full suite, then commit**

```bash
cd core-api
uv run pytest -q
git add core_api/pricing_gaps.py core_api/routers/pricing_backfill.py tests/test_pricing_gaps.py \
        tests/test_pricing_backfill.py tests/test_ingestion.py
git commit -m "Add GET /sources/{id}/prices/gaps endpoint"
```

This completes the Core API side. Tasks 6+ build the standalone `pricing-collector` project.

---

## Task 6: Scaffold + config.py

**Files:**
- Create: `pricing-collector/pyproject.toml`
- Create: `pricing-collector/.env.example`
- Create: `pricing-collector/pricing_collector/__init__.py`
- Create: `pricing-collector/pricing_collector/config.py`
- Test: `pricing-collector/tests/__init__.py`
- Test: `pricing-collector/tests/test_config.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class PricingSource:
      source_id: int
      collector_key: str

  @dataclass(frozen=True)
  class Settings:
      core_api_url: str
      sources: list[PricingSource]

  def get_settings() -> Settings: ...
  ```

- [ ] **Step 1: Create the project scaffold**

Create `pricing-collector/pyproject.toml`:

```toml
[project]
name = "pricing-collector"
version = "0.1.0"
description = "Polls Amber Electric's API and pushes prices into Core API (sub-project 3)."
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27,<1.0",
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
packages = ["pricing_collector"]
```

Create `pricing-collector/.env.example`:

```
CORE_API_URL=http://localhost:8000
PRICING_SOURCES=[{"source_id": 1, "collector_key": "changeme"}]
```

Create empty `pricing-collector/pricing_collector/__init__.py` and `pricing-collector/tests/__init__.py`.

Run `cd pricing-collector && uv sync` to generate `uv.lock` and the `.venv`.

- [ ] **Step 2: Write the failing tests**

Create `pricing-collector/tests/test_config.py`:

```python
from __future__ import annotations

import pytest

from pricing_collector.config import PricingSource, get_settings


def test_get_settings_parses_multiple_sources(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000/")
    monkeypatch.setenv(
        "PRICING_SOURCES",
        '[{"source_id": 1, "collector_key": "key-one"}, {"source_id": 2, "collector_key": "key-two"}]',
    )
    settings = get_settings()
    assert settings.core_api_url == "http://localhost:8000"  # trailing slash stripped
    assert settings.sources == [
        PricingSource(source_id=1, collector_key="key-one"),
        PricingSource(source_id=2, collector_key="key-two"),
    ]


def test_get_settings_raises_when_core_api_url_missing(monkeypatch):
    monkeypatch.delenv("CORE_API_URL", raising=False)
    monkeypatch.setenv("PRICING_SOURCES", '[{"source_id": 1, "collector_key": "key"}]')
    with pytest.raises(RuntimeError):
        get_settings()


def test_get_settings_raises_when_pricing_sources_missing(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.delenv("PRICING_SOURCES", raising=False)
    with pytest.raises(RuntimeError):
        get_settings()


def test_get_settings_raises_on_malformed_json(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("PRICING_SOURCES", "not json")
    with pytest.raises(RuntimeError):
        get_settings()


def test_get_settings_raises_on_empty_array(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("PRICING_SOURCES", "[]")
    with pytest.raises(RuntimeError):
        get_settings()


def test_get_settings_raises_on_entry_missing_required_key(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("PRICING_SOURCES", '[{"source_id": 1}]')
    with pytest.raises(RuntimeError):
        get_settings()
```

- [ ] **Step 2b: Run tests to verify they fail**

Run: `cd pricing-collector && uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pricing_collector.config'`.

- [ ] **Step 3: Implement `pricing_collector/config.py`**

```python
from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PricingSource:
    source_id: int
    collector_key: str


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    sources: list[PricingSource]


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    sources_json = os.environ.get("PRICING_SOURCES")
    if not core_api_url or not sources_json:
        raise RuntimeError("CORE_API_URL and PRICING_SOURCES must both be set")

    try:
        raw_sources = json.loads(sources_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"PRICING_SOURCES is not valid JSON: {exc}") from exc

    if not isinstance(raw_sources, list) or not raw_sources:
        raise RuntimeError("PRICING_SOURCES must be a non-empty JSON array")

    sources = []
    for entry in raw_sources:
        try:
            sources.append(PricingSource(source_id=int(entry["source_id"]), collector_key=str(entry["collector_key"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid entry in PRICING_SOURCES: {entry!r} ({exc})") from exc

    return Settings(core_api_url=core_api_url.rstrip("/"), sources=sources)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pricing-collector && uv run pytest tests/test_config.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run full suite, then commit**

```bash
cd pricing-collector
uv run pytest -q
git add pyproject.toml uv.lock .env.example pricing_collector/__init__.py pricing_collector/config.py \
        tests/__init__.py tests/test_config.py
git commit -m "Scaffold pricing-collector project with multi-site env-var settings"
```

---

## Task 7: Wall-clock-aligned scheduling

**Files:**
- Create: `pricing-collector/pricing_collector/scheduling.py`
- Test: `pricing-collector/tests/test_scheduling.py`

**Interfaces:**
- Produces: `next_aligned_tick(now: datetime.datetime, *, interval_minutes: int = 5, buffer_seconds: int = 15) -> datetime.datetime`.

- [ ] **Step 1: Write the failing tests**

Create `pricing-collector/tests/test_scheduling.py`:

```python
from __future__ import annotations

import datetime

from pricing_collector.scheduling import next_aligned_tick

UTC = datetime.timezone.utc


def dt(h: int, m: int, s: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 9, 30, h, m, s, tzinfo=UTC)


def test_well_before_next_boundary():
    assert next_aligned_tick(dt(10, 2, 0)) == dt(10, 5, 15)


def test_within_buffer_window_of_current_boundary():
    # Already at the :05 minute, but the 15s buffer hasn't elapsed yet --
    # this cycle's tick is still pending, not skipped.
    assert next_aligned_tick(dt(10, 5, 5)) == dt(10, 5, 15)


def test_just_after_buffer_elapsed_skips_to_next_boundary():
    assert next_aligned_tick(dt(10, 5, 16)) == dt(10, 10, 15)


def test_late_in_the_interval_rounds_to_next_boundary():
    assert next_aligned_tick(dt(10, 4, 59)) == dt(10, 5, 15)


def test_handles_hour_rollover():
    assert next_aligned_tick(dt(10, 57, 0)) == dt(11, 0, 15)


def test_result_is_always_strictly_after_now():
    now = dt(10, 5, 15)  # exactly on a would-be tick
    result = next_aligned_tick(now)
    assert result > now
    assert result == dt(10, 10, 15)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pricing-collector && uv run pytest tests/test_scheduling.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pricing_collector.scheduling'`.

- [ ] **Step 3: Implement `pricing_collector/scheduling.py`**

```python
from __future__ import annotations

import datetime


def next_aligned_tick(
    now: datetime.datetime, *, interval_minutes: int = 5, buffer_seconds: int = 15
) -> datetime.datetime:
    """The next wall-clock :00/:05/.../:55-style boundary (whatever
    interval_minutes divides the hour into), plus buffer_seconds -- Amber
    settles a new interval right at the boundary, so a short buffer avoids
    polling before it's actually published. Always strictly after `now`."""
    epoch_minute = now.replace(second=0, microsecond=0)
    minutes_since_boundary = epoch_minute.minute % interval_minutes
    boundary = epoch_minute - datetime.timedelta(minutes=minutes_since_boundary)
    candidate = boundary + datetime.timedelta(seconds=buffer_seconds)
    if candidate <= now:
        candidate += datetime.timedelta(minutes=interval_minutes)
    return candidate
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pricing-collector && uv run pytest tests/test_scheduling.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run full suite, then commit**

```bash
cd pricing-collector
uv run pytest -q
git add pricing_collector/scheduling.py tests/test_scheduling.py
git commit -m "Add wall-clock-aligned tick scheduling"
```

---

## Task 8: Mapping — Amber interval → Core API `PriceIntervalIn`

**Files:**
- Create: `pricing-collector/pricing_collector/mapping.py`
- Create: `pricing-collector/tests/fixtures/prices_current.json`
- Test: `pricing-collector/tests/test_mapping.py`

**Interfaces:**
- Produces: `CHANNEL_MAP: dict[str, str]`, `map_price_intervals(intervals: list[dict]) -> list[dict]`.

- [ ] **Step 1: Create the fixture**

Create `pricing-collector/tests/fixtures/prices_current.json` (real Amber interval shapes, no site-identifying fields — Amber's interval objects never include the site ID or NMI):

```json
[
    {
        "type": "ActualInterval",
        "date": "2026-09-30",
        "duration": 5,
        "startTime": "2026-09-29T23:55:01Z",
        "endTime": "2026-09-30T00:00:00Z",
        "nemTime": "2026-09-30T10:00:00+10:00",
        "perKwh": 7.37398,
        "renewables": 95.35075983951165,
        "spotPerKwh": 0.99067,
        "channelType": "general",
        "spikeStatus": "none",
        "tariffInformation": {"period": "offPeak", "season": "default", "demandWindow": false},
        "descriptor": "extremelyLow"
    },
    {
        "type": "CurrentInterval",
        "date": "2026-09-30",
        "duration": 5,
        "startTime": "2026-09-30T00:00:01Z",
        "endTime": "2026-09-30T00:05:00Z",
        "nemTime": "2026-09-30T10:05:00+10:00",
        "perKwh": 7.16779,
        "renewables": 90.119,
        "spotPerKwh": 0.79106,
        "channelType": "general",
        "spikeStatus": "none",
        "tariffInformation": {"period": "offPeak", "season": "default", "demandWindow": false},
        "descriptor": "extremelyLow",
        "estimate": false
    },
    {
        "type": "ForecastInterval",
        "date": "2026-09-30",
        "duration": 5,
        "startTime": "2026-09-30T00:05:01Z",
        "endTime": "2026-09-30T00:10:00Z",
        "nemTime": "2026-09-30T10:10:00+10:00",
        "perKwh": 6.35178,
        "renewables": 95.98940277005903,
        "spotPerKwh": 0.00111,
        "channelType": "general",
        "spikeStatus": "none",
        "tariffInformation": {"period": "offPeak", "season": "default", "demandWindow": false},
        "descriptor": "extremelyLow",
        "advancedPrice": {"low": 6.34166, "predicted": 7.02336, "high": 7.95185}
    },
    {
        "type": "ActualInterval",
        "date": "2026-09-30",
        "duration": 5,
        "startTime": "2026-09-29T23:55:01Z",
        "endTime": "2026-09-30T00:00:00Z",
        "nemTime": "2026-09-30T10:00:00+10:00",
        "perKwh": -0.93032,
        "renewables": 95.35075983951165,
        "spotPerKwh": 0.99067,
        "channelType": "feedIn",
        "spikeStatus": "none",
        "descriptor": "low"
    },
    {
        "type": "CurrentInterval",
        "date": "2026-09-30",
        "duration": 5,
        "startTime": "2026-09-30T00:00:01Z",
        "endTime": "2026-09-30T00:05:00Z",
        "nemTime": "2026-09-30T10:05:00+10:00",
        "perKwh": 0.48913,
        "renewables": 90.119,
        "spotPerKwh": 0.79106,
        "channelType": "feedIn",
        "spikeStatus": "none",
        "descriptor": "low",
        "estimate": false
    }
]
```

- [ ] **Step 2: Write the failing tests**

Create `pricing-collector/tests/test_mapping.py`:

```python
from __future__ import annotations

import json
import pathlib

from pricing_collector.mapping import map_price_intervals

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())


def test_maps_general_to_buy_and_feedin_to_sell():
    mapped = map_price_intervals(load_fixture("prices_current.json"))
    channels = {m["channel"] for m in mapped}
    assert channels == {"buy", "sell"}


def test_maps_price_and_interval_bounds_directly():
    mapped = map_price_intervals(load_fixture("prices_current.json"))
    first = next(m for m in mapped if m["channel"] == "buy" and m["interval_start"] == "2026-09-29T23:55:01Z")
    assert first["price"] == "7.37398"
    assert first["interval_end"] == "2026-09-30T00:00:00Z"


def test_actual_current_and_forecast_all_included():
    mapped = map_price_intervals(load_fixture("prices_current.json"))
    # 3 general (actual/current/forecast) + 2 feedIn (actual/current) = 5
    assert len(mapped) == 5


def test_is_demand_window_never_present_in_output():
    mapped = map_price_intervals(load_fixture("prices_current.json"))
    assert all("is_demand_window" not in m for m in mapped)


def test_unrecognized_channel_type_is_skipped_not_fatal():
    intervals = load_fixture("prices_current.json") + [
        {
            "channelType": "someFutureChannel",
            "startTime": "2026-09-30T00:10:01Z",
            "endTime": "2026-09-30T00:15:00Z",
            "perKwh": 5.0,
        }
    ]
    mapped = map_price_intervals(intervals)
    assert len(mapped) == 5  # the unrecognized entry is dropped, the rest still map


def test_malformed_interval_missing_field_is_skipped_not_fatal():
    intervals = load_fixture("prices_current.json") + [
        {"channelType": "general", "startTime": "2026-09-30T00:10:01Z"}  # missing endTime, perKwh
    ]
    mapped = map_price_intervals(intervals)
    assert len(mapped) == 5


def test_controlled_load_maps_correctly():
    intervals = [
        {
            "channelType": "controlledLoad",
            "startTime": "2026-09-30T00:00:01Z",
            "endTime": "2026-09-30T00:05:00Z",
            "perKwh": 12.3,
        }
    ]
    mapped = map_price_intervals(intervals)
    assert mapped == [{"channel": "controlled_load", "interval_start": "2026-09-30T00:00:01Z", "interval_end": "2026-09-30T00:05:00Z", "price": "12.3"}]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd pricing-collector && uv run pytest tests/test_mapping.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pricing_collector.mapping'`.

- [ ] **Step 4: Implement `pricing_collector/mapping.py`**

```python
from __future__ import annotations

import logging

logger = logging.getLogger("pricing_collector")

CHANNEL_MAP = {"general": "buy", "feedIn": "sell", "controlledLoad": "controlled_load"}


def map_price_intervals(intervals: list[dict]) -> list[dict]:
    """Maps every Amber interval entry to a Core API PriceIntervalIn dict.
    ActualInterval/CurrentInterval/ForecastInterval all map identically --
    inclusion is decided by which Amber endpoint was called, not by this
    function. `tariffInformation`/`is_demand_window` are never read (demand-
    window determination is entirely Core API's own SiteTariff/DemandWindow
    concern). price is kept as a string (Amber's own JSON number, restring-
    ified) rather than a float or Decimal, matching how Core API's own tests
    send it -- avoiding float precision issues without adding a Decimal
    dependency to the wire format."""
    mapped = []
    for interval in intervals:
        try:
            channel_type = interval["channelType"]
            if channel_type not in CHANNEL_MAP:
                logger.warning("Skipping price interval with unrecognized channelType: %r", channel_type)
                continue
            mapped.append(
                {
                    "channel": CHANNEL_MAP[channel_type],
                    "interval_start": interval["startTime"],
                    "interval_end": interval["endTime"],
                    "price": str(interval["perKwh"]),
                }
            )
        except KeyError:
            logger.exception("Skipping malformed price interval: %r", interval)
    return mapped
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd pricing-collector && uv run pytest tests/test_mapping.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Run full suite, then commit**

```bash
cd pricing-collector
uv run pytest -q
git add pricing_collector/mapping.py tests/fixtures/prices_current.json tests/test_mapping.py
git commit -m "Add Amber interval to PriceIntervalIn mapping"
```

---

## Task 9: Amber HTTP client

**Files:**
- Create: `pricing-collector/pricing_collector/amber_client.py`
- Test: `pricing-collector/tests/test_amber_client.py`

**Interfaces:**
- Produces: `fetch_current_prices(amber_site_id: str, amber_api_key: str, *, previous: int = 1, next_count: int = 12) -> list[dict]`, `fetch_historical_prices(amber_site_id: str, amber_api_key: str, start_date: datetime.date, end_date: datetime.date, *, resolution: int = 5) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

Create `pricing-collector/tests/test_amber_client.py`:

```python
from __future__ import annotations

import datetime

import httpx
import pytest
import respx

from pricing_collector.amber_client import fetch_current_prices, fetch_historical_prices

AMBER_SITE_ID = "example-site-id"
AMBER_API_KEY = "psk_example_token"


@respx.mock
def test_fetch_current_prices_sends_bearer_auth_and_params():
    route = respx.get(f"https://api.amber.com.au/v1/sites/{AMBER_SITE_ID}/prices/current").mock(
        return_value=httpx.Response(200, json=[{"channelType": "general"}])
    )
    result = fetch_current_prices(AMBER_SITE_ID, AMBER_API_KEY, previous=1, next_count=12)
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {AMBER_API_KEY}"
    assert route.calls[0].request.url.params["previous"] == "1"
    assert route.calls[0].request.url.params["next"] == "12"
    assert result == [{"channelType": "general"}]


@respx.mock
def test_fetch_current_prices_raises_on_http_error_status():
    respx.get(f"https://api.amber.com.au/v1/sites/{AMBER_SITE_ID}/prices/current").mock(
        return_value=httpx.Response(401)
    )
    with pytest.raises(httpx.HTTPStatusError):
        fetch_current_prices(AMBER_SITE_ID, AMBER_API_KEY)


@respx.mock
def test_fetch_historical_prices_sends_date_range_and_resolution():
    route = respx.get(f"https://api.amber.com.au/v1/sites/{AMBER_SITE_ID}/prices").mock(
        return_value=httpx.Response(200, json=[{"channelType": "general"}])
    )
    result = fetch_historical_prices(
        AMBER_SITE_ID, AMBER_API_KEY, datetime.date(2025, 3, 1), datetime.date(2025, 3, 7), resolution=5
    )
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {AMBER_API_KEY}"
    assert route.calls[0].request.url.params["startDate"] == "2025-03-01"
    assert route.calls[0].request.url.params["endDate"] == "2025-03-07"
    assert route.calls[0].request.url.params["resolution"] == "5"
    assert result == [{"channelType": "general"}]


@respx.mock
def test_fetch_historical_prices_raises_on_http_error_status():
    respx.get(f"https://api.amber.com.au/v1/sites/{AMBER_SITE_ID}/prices").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_historical_prices(AMBER_SITE_ID, AMBER_API_KEY, datetime.date(2025, 3, 1), datetime.date(2025, 3, 7))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pricing-collector && uv run pytest tests/test_amber_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pricing_collector.amber_client'`.

- [ ] **Step 3: Implement `pricing_collector/amber_client.py`**

```python
from __future__ import annotations

import datetime

import httpx

AMBER_BASE_URL = "https://api.amber.com.au/v1"


def fetch_current_prices(
    amber_site_id: str, amber_api_key: str, *, previous: int = 1, next_count: int = 12
) -> list[dict]:
    response = httpx.get(
        f"{AMBER_BASE_URL}/sites/{amber_site_id}/prices/current",
        params={"previous": previous, "next": next_count},
        headers={"Authorization": f"Bearer {amber_api_key}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def fetch_historical_prices(
    amber_site_id: str,
    amber_api_key: str,
    start_date: datetime.date,
    end_date: datetime.date,
    *,
    resolution: int = 5,
) -> list[dict]:
    response = httpx.get(
        f"{AMBER_BASE_URL}/sites/{amber_site_id}/prices",
        params={"startDate": start_date.isoformat(), "endDate": end_date.isoformat(), "resolution": resolution},
        headers={"Authorization": f"Bearer {amber_api_key}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pricing-collector && uv run pytest tests/test_amber_client.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run full suite, then commit**

```bash
cd pricing-collector
uv run pytest -q
git add pricing_collector/amber_client.py tests/test_amber_client.py
git commit -m "Add Amber Electric HTTP client"
```

---

## Task 10: Core API HTTP client (transport-error wrapping built in from the start)

**Files:**
- Create: `pricing-collector/pricing_collector/core_api_client.py`
- Test: `pricing-collector/tests/test_core_api_client.py`

**Interfaces:**
- Produces: `CoreApiError`, `fetch_source_config(base_url, source_id, collector_key) -> dict`, `push_price_intervals(base_url, source_id, collector_key, intervals: list[dict]) -> None`, `fetch_site_data_floor(base_url, source_id, collector_key) -> datetime.datetime | None`, `fetch_price_gaps(base_url, source_id, collector_key, since: datetime.datetime) -> list[dict]`, `report_gap_marker(base_url, source_id, collector_key, channel: str, gap_date: datetime.date) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `pricing-collector/tests/test_core_api_client.py`:

```python
from __future__ import annotations

import datetime

import httpx
import pytest
import respx

from pricing_collector.core_api_client import (
    CoreApiError,
    fetch_price_gaps,
    fetch_site_data_floor,
    fetch_source_config,
    push_price_intervals,
    report_gap_marker,
)

BASE_URL = "http://localhost:8000"


@respx.mock
def test_fetch_source_config_sends_collector_key_header():
    route = respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(
        return_value=httpx.Response(200, json={"config": {"amber_site_id": "example-site-id"}})
    )
    result = fetch_source_config(BASE_URL, 1, "my-key")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    assert result == {"amber_site_id": "example-site-id"}


@respx.mock
def test_fetch_source_config_raises_core_api_error_on_connection_failure():
    respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        fetch_source_config(BASE_URL, 1, "my-key")


@respx.mock
def test_fetch_source_config_raises_on_non_200():
    respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(return_value=httpx.Response(401))
    with pytest.raises(CoreApiError):
        fetch_source_config(BASE_URL, 1, "wrong-key")


@respx.mock
def test_push_price_intervals_sends_batch():
    route = respx.post(f"{BASE_URL}/api/v1/sources/1/prices").mock(return_value=httpx.Response(202, json={"ingested": 1}))
    push_price_intervals(BASE_URL, 1, "my-key", [{"channel": "buy", "interval_start": "2026-01-01T00:00:00Z", "interval_end": "2026-01-01T00:05:00Z", "price": "25.0"}])
    import json as _json

    body = _json.loads(route.calls[0].request.content)
    assert body == {"intervals": [{"channel": "buy", "interval_start": "2026-01-01T00:00:00Z", "interval_end": "2026-01-01T00:05:00Z", "price": "25.0"}]}


@respx.mock
def test_push_price_intervals_raises_on_non_202():
    respx.post(f"{BASE_URL}/api/v1/sources/1/prices").mock(return_value=httpx.Response(422))
    with pytest.raises(CoreApiError):
        push_price_intervals(BASE_URL, 1, "my-key", [])


@respx.mock
def test_push_price_intervals_raises_core_api_error_on_connection_failure():
    respx.post(f"{BASE_URL}/api/v1/sources/1/prices").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        push_price_intervals(BASE_URL, 1, "my-key", [])


@respx.mock
def test_fetch_site_data_floor_parses_iso_datetime():
    respx.get(f"{BASE_URL}/api/v1/sources/1/site-data-floor").mock(
        return_value=httpx.Response(200, json={"floor_ts": "2025-01-01T00:00:00+00:00"})
    )
    result = fetch_site_data_floor(BASE_URL, 1, "my-key")
    assert result == datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)


@respx.mock
def test_fetch_site_data_floor_returns_none_when_null():
    respx.get(f"{BASE_URL}/api/v1/sources/1/site-data-floor").mock(return_value=httpx.Response(200, json={"floor_ts": None}))
    assert fetch_site_data_floor(BASE_URL, 1, "my-key") is None


@respx.mock
def test_fetch_site_data_floor_raises_core_api_error_on_connection_failure():
    respx.get(f"{BASE_URL}/api/v1/sources/1/site-data-floor").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        fetch_site_data_floor(BASE_URL, 1, "my-key")


@respx.mock
def test_fetch_price_gaps_sends_since_param_and_parses_response():
    since = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    route = respx.get(f"{BASE_URL}/api/v1/sources/1/prices/gaps").mock(
        return_value=httpx.Response(200, json={"gaps": [{"channel": "buy", "start_date": "2025-01-01", "end_date": "2025-01-03"}]})
    )
    result = fetch_price_gaps(BASE_URL, 1, "my-key", since)
    assert route.calls[0].request.url.params["since"] == "2025-01-01T00:00:00+00:00"
    assert result == [{"channel": "buy", "start_date": "2025-01-01", "end_date": "2025-01-03"}]


@respx.mock
def test_fetch_price_gaps_raises_core_api_error_on_connection_failure():
    respx.get(f"{BASE_URL}/api/v1/sources/1/prices/gaps").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        fetch_price_gaps(BASE_URL, 1, "my-key", datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc))


@respx.mock
def test_report_gap_marker_sends_channel_and_date():
    route = respx.post(f"{BASE_URL}/api/v1/sources/1/prices/gap-markers").mock(return_value=httpx.Response(204))
    report_gap_marker(BASE_URL, 1, "my-key", "buy", datetime.date(2025, 3, 1))
    import json as _json

    body = _json.loads(route.calls[0].request.content)
    assert body == {"channel": "buy", "gap_date": "2025-03-01"}


@respx.mock
def test_report_gap_marker_raises_core_api_error_on_connection_failure():
    respx.post(f"{BASE_URL}/api/v1/sources/1/prices/gap-markers").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        report_gap_marker(BASE_URL, 1, "my-key", "buy", datetime.date(2025, 3, 1))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pricing-collector && uv run pytest tests/test_core_api_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pricing_collector.core_api_client'`.

- [ ] **Step 3: Implement `pricing_collector/core_api_client.py`**

```python
from __future__ import annotations

import datetime

import httpx


class CoreApiError(Exception):
    """Raised on any failure talking to Core API -- a non-success response or
    a transport failure (connection refused, timeout, DNS, etc.). Callers
    only ever need to catch this one type, never httpx's own exceptions."""


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return httpx.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise CoreApiError(f"Failed to reach Core API: {exc}") from exc


def fetch_source_config(base_url: str, source_id: int, collector_key: str) -> dict:
    response = _request(
        "GET", f"{base_url}/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": collector_key}, timeout=10
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch source config: {response.status_code} {response.text}")
    return response.json()["config"]


def push_price_intervals(base_url: str, source_id: int, collector_key: str, intervals: list[dict]) -> None:
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/prices",
        headers={"X-Collector-Key": collector_key},
        json={"intervals": intervals},
        timeout=10,
    )
    if response.status_code != 202:
        raise CoreApiError(f"Failed to push price intervals: {response.status_code} {response.text}")


def fetch_site_data_floor(base_url: str, source_id: int, collector_key: str) -> datetime.datetime | None:
    response = _request(
        "GET",
        f"{base_url}/api/v1/sources/{source_id}/site-data-floor",
        headers={"X-Collector-Key": collector_key},
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch site-data floor: {response.status_code} {response.text}")
    floor_ts = response.json()["floor_ts"]
    return datetime.datetime.fromisoformat(floor_ts) if floor_ts else None


def fetch_price_gaps(base_url: str, source_id: int, collector_key: str, since: datetime.datetime) -> list[dict]:
    response = _request(
        "GET",
        f"{base_url}/api/v1/sources/{source_id}/prices/gaps",
        params={"since": since.isoformat()},
        headers={"X-Collector-Key": collector_key},
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch price gaps: {response.status_code} {response.text}")
    return response.json()["gaps"]


def report_gap_marker(base_url: str, source_id: int, collector_key: str, channel: str, gap_date: datetime.date) -> None:
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/prices/gap-markers",
        headers={"X-Collector-Key": collector_key},
        json={"channel": channel, "gap_date": gap_date.isoformat()},
        timeout=10,
    )
    if response.status_code != 204:
        raise CoreApiError(f"Failed to report gap marker: {response.status_code} {response.text}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pricing-collector && uv run pytest tests/test_core_api_client.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Run full suite, then commit**

```bash
cd pricing-collector
uv run pytest -q
git add pricing_collector/core_api_client.py tests/test_core_api_client.py
git commit -m "Add Core API HTTP client with transport-error wrapping"
```

---

## Task 11: Live poll — one-shot child + fork-per-tick master loop

**Files:**
- Create: `pricing-collector/pricing_collector/live_poll.py`
- Test: `pricing-collector/tests/test_live_poll.py`

**Interfaces:**
- Consumes: everything from Tasks 6-10.
- Produces: `poll_one_site(core_api_url: str, source_id: int, collector_key: str) -> None`, `run_live_poll_forever(core_api_url: str, sources: list[PricingSource]) -> None`, `CHILD_JOIN_TIMEOUT_SECONDS = 60`.

- [ ] **Step 1: Write the failing tests for `poll_one_site`**

Create `pricing-collector/tests/test_live_poll.py`:

```python
from __future__ import annotations

import datetime

import pytest

from pricing_collector import live_poll
from pricing_collector.config import PricingSource
from pricing_collector.core_api_client import CoreApiError

BASE_URL = "http://localhost:8000"


def test_poll_one_site_pushes_mapped_intervals_on_success(monkeypatch):
    monkeypatch.setattr(
        live_poll, "fetch_source_config", lambda base_url, sid, key: {"amber_site_id": "example-site-id", "amber_api_key": "psk_token"}
    )
    monkeypatch.setattr(
        live_poll,
        "fetch_current_prices",
        lambda site_id, api_key, **kwargs: [
            {"channelType": "general", "startTime": "2026-01-01T00:00:01Z", "endTime": "2026-01-01T00:05:00Z", "perKwh": 25.0}
        ],
    )
    pushed = []
    monkeypatch.setattr(live_poll, "push_price_intervals", lambda base_url, sid, key, intervals: pushed.append(intervals))

    live_poll.poll_one_site(BASE_URL, 1, "collector-key")

    assert len(pushed) == 1
    assert pushed[0] == [{"channel": "buy", "interval_start": "2026-01-01T00:00:01Z", "interval_end": "2026-01-01T00:05:00Z", "price": "25.0"}]


def test_poll_one_site_skips_when_config_fetch_fails(monkeypatch, caplog):
    def _raise(*a, **k):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(live_poll, "fetch_source_config", _raise)
    pushed = []
    monkeypatch.setattr(live_poll, "push_price_intervals", lambda *a, **k: pushed.append(a))

    with caplog.at_level("ERROR"):
        live_poll.poll_one_site(BASE_URL, 1, "collector-key")  # must not raise

    assert pushed == []


def test_poll_one_site_skips_when_amber_fetch_fails(monkeypatch, caplog):
    monkeypatch.setattr(live_poll, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})

    def _raise(*a, **k):
        raise OSError("network unreachable")

    monkeypatch.setattr(live_poll, "fetch_current_prices", _raise)
    pushed = []
    monkeypatch.setattr(live_poll, "push_price_intervals", lambda *a, **k: pushed.append(a))

    with caplog.at_level("ERROR"):
        live_poll.poll_one_site(BASE_URL, 1, "collector-key")  # must not raise

    assert pushed == []


def test_poll_one_site_logs_but_does_not_raise_when_push_fails(monkeypatch, caplog):
    monkeypatch.setattr(live_poll, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    monkeypatch.setattr(
        live_poll,
        "fetch_current_prices",
        lambda *a, **k: [{"channelType": "general", "startTime": "2026-01-01T00:00:01Z", "endTime": "2026-01-01T00:05:00Z", "perKwh": 25.0}],
    )

    def _raise(*a, **k):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(live_poll, "push_price_intervals", _raise)

    with caplog.at_level("ERROR"):
        live_poll.poll_one_site(BASE_URL, 1, "collector-key")  # must not raise


def test_poll_one_site_pushes_nothing_when_all_intervals_unmappable(monkeypatch):
    monkeypatch.setattr(live_poll, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    monkeypatch.setattr(live_poll, "fetch_current_prices", lambda *a, **k: [{"channelType": "unknownType"}])
    pushed = []
    monkeypatch.setattr(live_poll, "push_price_intervals", lambda *a, **k: pushed.append(a))

    live_poll.poll_one_site(BASE_URL, 1, "collector-key")

    assert pushed == []  # never calls push with an empty batch
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pricing-collector && uv run pytest tests/test_live_poll.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pricing_collector.live_poll'`.

- [ ] **Step 3: Implement `poll_one_site`**

Create `pricing-collector/pricing_collector/live_poll.py`:

```python
from __future__ import annotations

import datetime
import logging
import multiprocessing
import time

from pricing_collector.amber_client import fetch_current_prices
from pricing_collector.config import PricingSource
from pricing_collector.core_api_client import CoreApiError, fetch_source_config, push_price_intervals
from pricing_collector.mapping import map_price_intervals
from pricing_collector.scheduling import next_aligned_tick

logger = logging.getLogger("pricing_collector")

CHILD_JOIN_TIMEOUT_SECONDS = 60


def poll_one_site(core_api_url: str, source_id: int, collector_key: str) -> None:
    """One-shot: fetch this site's config, poll Amber, map, push, exit. No
    internal retry -- the next aligned tick spawns a fresh child regardless,
    so retry happens via the scheduler rather than an indefinite-retry loop."""
    try:
        config = fetch_source_config(core_api_url, source_id, collector_key)
        amber_site_id = config["amber_site_id"]
        amber_api_key = config["amber_api_key"]
    except Exception:
        logger.exception("Failed to fetch config for pricing source %s this cycle", source_id)
        return

    try:
        raw_intervals = fetch_current_prices(amber_site_id, amber_api_key)
    except Exception:
        logger.exception("Failed to fetch current prices from Amber for source %s this cycle", source_id)
        return

    mapped = map_price_intervals(raw_intervals)
    if not mapped:
        return

    try:
        push_price_intervals(core_api_url, source_id, collector_key, mapped)
    except CoreApiError:
        logger.exception("Failed to push price intervals to Core API for source %s this cycle", source_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pricing-collector && uv run pytest tests/test_live_poll.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Write the failing tests for `run_live_poll_forever`**

Add to `pricing-collector/tests/test_live_poll.py`:

```python
class FakeProcess:
    """Stands in for multiprocessing.Process so tests don't actually fork --
    records what it was constructed with and what was called on it."""

    instances: list["FakeProcess"] = []

    def __init__(self, target, args):
        self.target = target
        self.args = args
        self.started = False
        self.joined_with_timeout = None
        self.terminated = False
        FakeProcess.instances.append(self)

    def start(self):
        self.started = True

    def join(self, timeout=None):
        self.joined_with_timeout = timeout

    def is_alive(self):
        return False


def test_run_live_poll_forever_forks_one_child_per_source(monkeypatch):
    FakeProcess.instances = []
    monkeypatch.setattr(live_poll.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now: now)  # tick immediately

    sources = [PricingSource(source_id=1, collector_key="k1"), PricingSource(source_id=2, collector_key="k2")]

    calls = {"count": 0}

    def _fake_sleep_then_stop(seconds):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep_then_stop)

    with pytest.raises(SystemExit):
        live_poll.run_live_poll_forever(BASE_URL, sources)

    assert len(FakeProcess.instances) == 2
    assert {p.args[1] for p in FakeProcess.instances} == {1, 2}
    assert all(p.started for p in FakeProcess.instances)
    assert all(p.joined_with_timeout == live_poll.CHILD_JOIN_TIMEOUT_SECONDS for p in FakeProcess.instances)


def test_run_live_poll_forever_terminates_child_that_does_not_join_in_time(monkeypatch):
    class StuckProcess(FakeProcess):
        def is_alive(self):
            return not self.terminated

        def terminate(self):
            self.terminated = True

    FakeProcess.instances = []
    monkeypatch.setattr(live_poll.multiprocessing, "Process", StuckProcess)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now: now)

    calls = {"count": 0}

    def _fake_sleep_then_stop(seconds):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep_then_stop)

    with pytest.raises(SystemExit):
        live_poll.run_live_poll_forever(BASE_URL, [PricingSource(source_id=1, collector_key="k1")])

    assert FakeProcess.instances[0].terminated is True


def test_run_live_poll_forever_survives_unexpected_exception(monkeypatch):
    def _raise_process(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(live_poll.multiprocessing, "Process", _raise_process)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now: now)

    calls = {"count": 0}

    def _fake_sleep_then_stop(seconds):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep_then_stop)

    with pytest.raises(SystemExit):
        live_poll.run_live_poll_forever(BASE_URL, [PricingSource(source_id=1, collector_key="k1")])

    assert calls["count"] == 2  # the loop survived the RuntimeError and reached the second sleep
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd pricing-collector && uv run pytest tests/test_live_poll.py -k run_live_poll_forever -v`
Expected: FAIL — `AttributeError: module 'pricing_collector.live_poll' has no attribute 'run_live_poll_forever'`.

- [ ] **Step 7: Implement `run_live_poll_forever`**

Add to `pricing-collector/pricing_collector/live_poll.py`:

```python
def run_live_poll_forever(core_api_url: str, sources: list[PricingSource]) -> None:
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            tick = next_aligned_tick(now)
            time.sleep(max((tick - now).total_seconds(), 0))

            children = [
                multiprocessing.Process(target=poll_one_site, args=(core_api_url, s.source_id, s.collector_key))
                for s in sources
            ]
            for child in children:
                child.start()
            for child in children:
                child.join(timeout=CHILD_JOIN_TIMEOUT_SECONDS)
                if child.is_alive():
                    logger.error("Pricing source child process did not finish in time; terminating")
                    child.terminate()
                    child.join()
        except Exception:
            logger.exception("Unexpected error in live-poll scheduling loop; continuing")
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd pricing-collector && uv run pytest tests/test_live_poll.py -v`
Expected: PASS (8 tests).

- [ ] **Step 9: Run full suite, then commit**

```bash
cd pricing-collector
uv run pytest -q
git add pricing_collector/live_poll.py tests/test_live_poll.py
git commit -m "Add fork-per-tick live poll with wall-clock-aligned scheduling"
```

---

## Task 12: Backfill — gap-driven historical catch-up

**Files:**
- Create: `pricing-collector/pricing_collector/backfill.py`
- Test: `pricing-collector/tests/test_backfill.py`

**Interfaces:**
- Consumes: everything from Tasks 6-10.
- Produces: `backfill_one_site(core_api_url: str, source_id: int, collector_key: str) -> None`, `run_backfill_forever(core_api_url: str, sources: list[PricingSource]) -> None`, `BACKFILL_INTERVAL_SECONDS = 12 * 60 * 60`.

- [ ] **Step 1: Write the failing tests for the chunking helper**

Create `pricing-collector/tests/test_backfill.py`:

```python
from __future__ import annotations

import datetime

import pytest

from pricing_collector import backfill
from pricing_collector.config import PricingSource
from pricing_collector.core_api_client import CoreApiError

BASE_URL = "http://localhost:8000"


def test_chunk_date_range_splits_into_max_7_day_chunks():
    chunks = backfill._chunk_date_range(datetime.date(2025, 1, 1), datetime.date(2025, 1, 20))
    assert chunks == [
        (datetime.date(2025, 1, 1), datetime.date(2025, 1, 7)),
        (datetime.date(2025, 1, 8), datetime.date(2025, 1, 14)),
        (datetime.date(2025, 1, 15), datetime.date(2025, 1, 20)),
    ]


def test_chunk_date_range_single_day():
    assert backfill._chunk_date_range(datetime.date(2025, 1, 1), datetime.date(2025, 1, 1)) == [
        (datetime.date(2025, 1, 1), datetime.date(2025, 1, 1))
    ]


def test_chunk_date_range_exactly_7_days():
    assert backfill._chunk_date_range(datetime.date(2025, 1, 1), datetime.date(2025, 1, 7)) == [
        (datetime.date(2025, 1, 1), datetime.date(2025, 1, 7))
    ]


def test_days_in_range_inclusive():
    assert backfill._days_in_range(datetime.date(2025, 1, 1), datetime.date(2025, 1, 3)) == [
        datetime.date(2025, 1, 1),
        datetime.date(2025, 1, 2),
        datetime.date(2025, 1, 3),
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pricing-collector && uv run pytest tests/test_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pricing_collector.backfill'`.

- [ ] **Step 3: Implement the chunking helpers**

Create `pricing-collector/pricing_collector/backfill.py`:

```python
from __future__ import annotations

import datetime
import logging
import time

from pricing_collector.amber_client import fetch_historical_prices
from pricing_collector.config import PricingSource
from pricing_collector.core_api_client import (
    CoreApiError,
    fetch_price_gaps,
    fetch_site_data_floor,
    fetch_source_config,
    push_price_intervals,
    report_gap_marker,
)
from pricing_collector.mapping import CHANNEL_MAP, map_price_intervals

logger = logging.getLogger("pricing_collector")

MAX_CHUNK_DAYS = 7
BACKFILL_INTERVAL_SECONDS = 12 * 60 * 60


def _chunk_date_range(start_date: datetime.date, end_date: datetime.date) -> list[tuple[datetime.date, datetime.date]]:
    """Amber's historical endpoint caps startDate/endDate span at 7 days."""
    chunks = []
    chunk_start = start_date
    while chunk_start <= end_date:
        chunk_end = min(chunk_start + datetime.timedelta(days=MAX_CHUNK_DAYS - 1), end_date)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + datetime.timedelta(days=1)
    return chunks


def _days_in_range(start_date: datetime.date, end_date: datetime.date) -> list[datetime.date]:
    days = []
    day = start_date
    while day <= end_date:
        days.append(day)
        day += datetime.timedelta(days=1)
    return days
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pricing-collector && uv run pytest tests/test_backfill.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Write the failing tests for `backfill_one_site`**

Add to `pricing-collector/tests/test_backfill.py`:

```python
def test_backfill_one_site_does_nothing_when_floor_is_none(monkeypatch):
    monkeypatch.setattr(backfill, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    monkeypatch.setattr(backfill, "fetch_site_data_floor", lambda *a, **k: None)
    gaps_called = []
    monkeypatch.setattr(backfill, "fetch_price_gaps", lambda *a, **k: gaps_called.append(a))

    backfill.backfill_one_site(BASE_URL, 1, "collector-key")  # must not raise

    assert gaps_called == []


def test_backfill_one_site_fetches_gap_range_and_pushes(monkeypatch):
    monkeypatch.setattr(backfill, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    monkeypatch.setattr(
        backfill, "fetch_site_data_floor", lambda *a, **k: datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    )
    monkeypatch.setattr(
        backfill, "fetch_price_gaps", lambda *a, **k: [{"channel": "buy", "start_date": "2025-01-01", "end_date": "2025-01-02"}]
    )
    monkeypatch.setattr(
        backfill,
        "fetch_historical_prices",
        lambda site_id, api_key, start, end, **kwargs: [
            {"channelType": "general", "startTime": f"{start.isoformat()}T00:00:01Z", "endTime": f"{start.isoformat()}T00:05:00Z", "perKwh": 20.0}
        ],
    )
    pushed = []
    monkeypatch.setattr(backfill, "push_price_intervals", lambda base_url, sid, key, intervals: pushed.append(intervals))
    markers = []
    monkeypatch.setattr(backfill, "report_gap_marker", lambda *a, **k: markers.append(a))

    backfill.backfill_one_site(BASE_URL, 1, "collector-key")

    assert len(pushed) == 1
    assert pushed[0][0]["channel"] == "buy"
    assert markers == []  # the channel was present, nothing to mark


def test_backfill_one_site_marks_gap_when_amber_confirms_no_data(monkeypatch):
    monkeypatch.setattr(backfill, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    monkeypatch.setattr(
        backfill, "fetch_site_data_floor", lambda *a, **k: datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    )
    monkeypatch.setattr(
        backfill, "fetch_price_gaps", lambda *a, **k: [{"channel": "buy", "start_date": "2025-01-01", "end_date": "2025-01-02"}]
    )
    monkeypatch.setattr(backfill, "fetch_historical_prices", lambda *a, **k: [])  # Amber has nothing at all
    pushed = []
    monkeypatch.setattr(backfill, "push_price_intervals", lambda *a, **k: pushed.append(a))
    markers = []
    monkeypatch.setattr(backfill, "report_gap_marker", lambda base_url, sid, key, channel, gap_date: markers.append((channel, gap_date)))

    backfill.backfill_one_site(BASE_URL, 1, "collector-key")

    assert pushed == []
    assert markers == [("buy", datetime.date(2025, 1, 1)), ("buy", datetime.date(2025, 1, 2))]


def test_backfill_one_site_isolates_one_gap_failure_from_the_next(monkeypatch, caplog):
    monkeypatch.setattr(backfill, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    monkeypatch.setattr(
        backfill, "fetch_site_data_floor", lambda *a, **k: datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    )
    monkeypatch.setattr(
        backfill,
        "fetch_price_gaps",
        lambda *a, **k: [
            {"channel": "buy", "start_date": "2025-01-01", "end_date": "2025-01-01"},
            {"channel": "sell", "start_date": "2025-01-01", "end_date": "2025-01-01"},
        ],
    )

    call_count = {"n": 0}

    def _flaky_fetch_historical(site_id, api_key, start, end, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("network unreachable")
        return [{"channelType": "feedIn", "startTime": "2025-01-01T00:00:01Z", "endTime": "2025-01-01T00:05:00Z", "perKwh": 5.0}]

    monkeypatch.setattr(backfill, "fetch_historical_prices", _flaky_fetch_historical)
    pushed = []
    monkeypatch.setattr(backfill, "push_price_intervals", lambda base_url, sid, key, intervals: pushed.append(intervals))
    monkeypatch.setattr(backfill, "report_gap_marker", lambda *a, **k: None)

    with caplog.at_level("ERROR"):
        backfill.backfill_one_site(BASE_URL, 1, "collector-key")  # must not raise

    assert len(pushed) == 1  # the second (sell) gap still got processed
    assert pushed[0][0]["channel"] == "sell"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd pricing-collector && uv run pytest tests/test_backfill.py -k backfill_one_site -v`
Expected: FAIL — `AttributeError: module 'pricing_collector.backfill' has no attribute 'backfill_one_site'`.

- [ ] **Step 7: Implement `backfill_one_site`**

Add to `pricing-collector/pricing_collector/backfill.py`:

```python
def _backfill_one_gap(
    core_api_url: str, source_id: int, collector_key: str, amber_site_id: str, amber_api_key: str, gap: dict
) -> None:
    channel = gap["channel"]
    start_date = datetime.date.fromisoformat(gap["start_date"])
    end_date = datetime.date.fromisoformat(gap["end_date"])

    for chunk_start, chunk_end in _chunk_date_range(start_date, end_date):
        raw_intervals = fetch_historical_prices(amber_site_id, amber_api_key, chunk_start, chunk_end)

        mapped = map_price_intervals(raw_intervals)
        if mapped:
            push_price_intervals(core_api_url, source_id, collector_key, mapped)

        target_channel_present = any(CHANNEL_MAP.get(i.get("channelType")) == channel for i in raw_intervals)
        if not target_channel_present:
            for day in _days_in_range(chunk_start, chunk_end):
                report_gap_marker(core_api_url, source_id, collector_key, channel, day)


def backfill_one_site(core_api_url: str, source_id: int, collector_key: str) -> None:
    try:
        config = fetch_source_config(core_api_url, source_id, collector_key)
        amber_site_id = config["amber_site_id"]
        amber_api_key = config["amber_api_key"]
    except Exception:
        logger.exception("Failed to fetch config for pricing source %s during backfill", source_id)
        return

    try:
        floor_ts = fetch_site_data_floor(core_api_url, source_id, collector_key)
    except CoreApiError:
        logger.exception("Failed to fetch site-data floor for source %s during backfill", source_id)
        return
    if floor_ts is None:
        return  # brand-new site, nothing to backfill against yet

    try:
        gaps = fetch_price_gaps(core_api_url, source_id, collector_key, floor_ts)
    except CoreApiError:
        logger.exception("Failed to fetch price gaps for source %s during backfill", source_id)
        return

    for gap in gaps:
        try:
            _backfill_one_gap(core_api_url, source_id, collector_key, amber_site_id, amber_api_key, gap)
        except Exception:
            logger.exception("Failed to backfill gap %r for source %s; will retry next cycle", gap, source_id)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd pricing-collector && uv run pytest tests/test_backfill.py -v`
Expected: PASS (8 tests).

- [ ] **Step 9: Write the failing test for `run_backfill_forever`**

Add to `pricing-collector/tests/test_backfill.py`:

```python
def test_run_backfill_forever_visits_every_site_each_cycle(monkeypatch):
    visited = []
    monkeypatch.setattr(backfill, "backfill_one_site", lambda base_url, sid, key: visited.append(sid))

    calls = {"n": 0}

    def _fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] >= 1:
            raise SystemExit

    monkeypatch.setattr(backfill.time, "sleep", _fake_sleep)

    sources = [PricingSource(source_id=1, collector_key="k1"), PricingSource(source_id=2, collector_key="k2")]
    with pytest.raises(SystemExit):
        backfill.run_backfill_forever(BASE_URL, sources)

    assert visited == [1, 2]


def test_run_backfill_forever_isolates_one_site_failure_from_the_next(monkeypatch):
    visited = []

    def _backfill_one_site(base_url, source_id, key):
        if source_id == 1:
            raise RuntimeError("boom")
        visited.append(source_id)

    monkeypatch.setattr(backfill, "backfill_one_site", _backfill_one_site)

    calls = {"n": 0}

    def _fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] >= 1:
            raise SystemExit

    monkeypatch.setattr(backfill.time, "sleep", _fake_sleep)

    sources = [PricingSource(source_id=1, collector_key="k1"), PricingSource(source_id=2, collector_key="k2")]
    with pytest.raises(SystemExit):
        backfill.run_backfill_forever(BASE_URL, sources)

    assert visited == [2]  # site 1's crash didn't stop site 2 from running
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `cd pricing-collector && uv run pytest tests/test_backfill.py -k run_backfill_forever -v`
Expected: FAIL — `AttributeError: module 'pricing_collector.backfill' has no attribute 'run_backfill_forever'`.

- [ ] **Step 11: Implement `run_backfill_forever`**

Add to `pricing-collector/pricing_collector/backfill.py`:

```python
def run_backfill_forever(core_api_url: str, sources: list[PricingSource]) -> None:
    while True:
        for s in sources:
            try:
                backfill_one_site(core_api_url, s.source_id, s.collector_key)
            except Exception:
                logger.exception("Unexpected error backfilling pricing source %s; continuing", s.source_id)
        time.sleep(BACKFILL_INTERVAL_SECONDS)
```

- [ ] **Step 12: Run tests to verify they pass**

Run: `cd pricing-collector && uv run pytest tests/test_backfill.py -v`
Expected: PASS (10 tests).

- [ ] **Step 13: Run full suite, then commit**

```bash
cd pricing-collector
uv run pytest -q
git add pricing_collector/backfill.py tests/test_backfill.py
git commit -m "Add 12-hourly gap-driven historical backfill"
```

---

## Task 13: main.py — wire the two schedules together

**Files:**
- Create: `pricing-collector/pricing_collector/main.py`
- Test: `pricing-collector/tests/test_main.py`

**Interfaces:**
- Consumes: everything from Tasks 6, 11, 12.
- Produces: `main() -> None`.

- [ ] **Step 1: Write the failing tests**

Create `pricing-collector/tests/test_main.py`:

```python
from __future__ import annotations

from pricing_collector import main as main_module
from pricing_collector.config import PricingSource, Settings

SETTINGS = Settings(core_api_url="http://localhost:8000", sources=[PricingSource(source_id=1, collector_key="k1")])


class FakeThread:
    instances: list["FakeThread"] = []

    def __init__(self, target, args, daemon):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False
        FakeThread.instances.append(self)

    def start(self):
        self.started = True


def test_main_starts_backfill_thread_and_runs_live_poll(monkeypatch):
    FakeThread.instances = []
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(main_module.threading, "Thread", FakeThread)
    live_poll_calls = []
    monkeypatch.setattr(main_module, "run_live_poll_forever", lambda base_url, sources: live_poll_calls.append((base_url, sources)))

    main_module.main()

    assert len(FakeThread.instances) == 1
    thread = FakeThread.instances[0]
    assert thread.daemon is True
    assert thread.started is True
    assert thread.target is main_module.run_backfill_forever
    assert thread.args == (SETTINGS.core_api_url, SETTINGS.sources)
    assert live_poll_calls == [(SETTINGS.core_api_url, SETTINGS.sources)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pricing-collector && uv run pytest tests/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pricing_collector.main'`.

- [ ] **Step 3: Implement `pricing_collector/main.py`**

```python
from __future__ import annotations

import logging
import threading

from pricing_collector.backfill import run_backfill_forever
from pricing_collector.config import get_settings
from pricing_collector.live_poll import run_live_poll_forever


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    backfill_thread = threading.Thread(
        target=run_backfill_forever, args=(settings.core_api_url, settings.sources), daemon=True
    )
    backfill_thread.start()

    run_live_poll_forever(settings.core_api_url, settings.sources)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd pricing-collector && uv run pytest tests/test_main.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Run full suite, then commit**

```bash
cd pricing-collector
uv run pytest -q
git add pricing_collector/main.py tests/test_main.py
git commit -m "Wire live poll and backfill schedules together in main()"
```

---

## Task 14: Deployment packaging (build-verified, not just written)

**Files:**
- Create: `pricing-collector/Dockerfile`

**Interfaces:** none (packaging only).

- [ ] **Step 1: Write the Dockerfile**

Two-stage `uv sync` and a direct venv-Python `CMD`, matching the Envoy/Weather collectors' Dockerfiles exactly:

```dockerfile
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY pricing_collector ./pricing_collector
RUN uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1

CMD ["/app/.venv/bin/python", "-m", "pricing_collector.main"]
```

- [ ] **Step 2: Build the image**

Run: `cd pricing-collector && docker build -t pricing-collector .`
Expected: builds successfully. (If `docker info` fails, start Colima first: `colima start`.)

- [ ] **Step 3: Verify no dev dependencies leaked into the image**

```bash
docker run --rm pricing-collector /app/.venv/bin/python -c "import pytest"
```

Expected: `ModuleNotFoundError: No module named 'pytest'` (exit code 1) — this is the correct/expected outcome, confirming the image is production-only.

- [ ] **Step 4: Smoke-run the actual entrypoint**

```bash
docker run --rm -e CORE_API_URL=http://localhost:8000 -e 'PRICING_SOURCES=[{"source_id": 1, "collector_key": "test"}]' pricing-collector /app/.venv/bin/python -c "from pricing_collector.config import get_settings; print(get_settings())"
```

Expected: prints the `Settings` object without error.

- [ ] **Step 5: Clean up the local test image**

```bash
docker rmi pricing-collector
```

- [ ] **Step 6: Commit**

```bash
git add Dockerfile
git commit -m "Add Dockerfile for containerized deployment"
```
