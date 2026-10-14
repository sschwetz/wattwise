# Local pricing: reprice when a window changes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a local rate window is created, replaced or deleted, core-api records where the change takes effect, and the collector reprices from there to the end of tomorrow, then clears the record.

**Architecture:** A new table `local_reprice_markers` (one row per source: `reprice_from`, `version`) is set by the window routes, delivered to the collector in the source's discovery config as `reprice`, and cleared by a compare-and-clear endpoint. The collector adds the marker as a trigger, adds a reprice range to the planner, and clears the marker only after every range has been pushed.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, Alembic, SQLite, httpx, pytest, respx.

**Spec:** `docs/superpowers/specs/2026-10-14-local-pricing-reprice-on-change-design.md` (amends `2026-10-14-local-pricing-daily-push-and-backfill-design.md`)

## Global Constraints

- Run all commands from the `wattwise/` directory (the git root is its parent). Core-api tests: `cd core-api && uv run pytest`. Collector tests: `cd local-pricing-collector && uv run pytest`. Docs tests: `cd webapp && npm test`.
- One marker row per source. `reprice_from` is the earliest instant whose price may have changed; `version` goes up by 1 on every change to the source's windows.
- Marker start by change: create = the window's `effective_from`; delete = the deleted window's `effective_from`; replace = if no price-relevant field changed except `effective_to`, the earlier of the old and new `effective_to` (a missing end counts as later than any date, and equal ends mean no change); if any price-relevant field changed, the earlier of the old and new `effective_from`; if nothing price-relevant changed (a label-only change), no marker. Price-relevant fields: channel, effective_from, months, days_of_week, start_time, end_time, rate_c_per_kwh, effective_to.
- A new marker keeps the earlier of its start and the existing `reprice_from`.
- Discovery config for a local source is exactly `timezone`, `rate_windows` and, only when a marker exists, `reprice: {"from": "<UTC ISO>", "version": <int>}`.
- The clear endpoint deletes the marker only when `version` matches and returns 204 either way. It uses the collector key and provider header checks.
- The collector reprices from the marker (clamped to the channel's history start and window dates) to the end of tomorrow in forward 7-day chunks, then clears with the version it read. A failed push or clear leaves the marker. A source with no windows, or a marker that clamps to nothing, is cleared without pushing.
- The reprice trigger needs the site data floor (to avoid repricing before the site's first data) but not coverage.
- Core-api response timestamps use `UtcDatetime`. No `any`. Functions stay under 30 lines. Prose follows `.claude/rules/avoid-ai-writing.md`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- This environment refuses compound shell commands that mention git. Run git commands as separate plain commands.

## Review Focus

1. End then Add from now (the common edit) marks only a short span: the End marks its end instant, the Add marks its start instant, so the reprice is from now. (Task 1)
2. An edit made while the collector reprices raises the version, so the collector's clear is ignored and the next tick handles the new edit. (Tasks 2, 4)
3. All windows deleted: the marker is cleared without a child per minute re-running forever. (Task 4)
4. A marker dated before the site's first data, or before the window's start, is clamped, so a window dated years ago does not reprice years of empty history. (Task 3)
5. A label-only change sets no marker. (Task 1)

## File Structure

Core-api (`core-api/`):
- Create `core_api/local_reprice.py` (`window_snapshot`, `reprice_start`, `mark_reprice`), `alembic/versions/a9c3e5f17b24_add_local_reprice_markers.py`.
- Modify `core_api/models/local_rates.py`, `core_api/models/__init__.py`, `core_api/routers/local_rates.py`, `core_api/routers/sources.py`, `core_api/routers/collector_config.py`, `core_api/routers/pricing_backfill.py`, `core_api/schemas.py`.
- Tests: `tests/test_local_reprice.py` (create), additions to `tests/test_local_rate_windows_api.py`, `tests/test_local_rate_windows_model.py`, `tests/test_sources.py`, `tests/test_pricing_backfill.py`.

Collector (`local-pricing-collector/local_pricing_collector/`):
- Modify `models.py` (`RepriceMarker`, `parse_reprice`), `core_api_client.py` (`clear_reprice_marker`), `triggers.py`, `plan.py`, `poll.py`.
- Tests: `tests/test_models.py` (create), additions to `tests/test_core_api_client.py`, `tests/test_triggers.py`, `tests/test_plan.py`, `tests/test_poll.py`.

Docs: `webapp/src/docs/04-sources.md`, `README.md`, the 2026-10-14 daily-push spec (replaced statements).

---

### Task 1: Marker table and setting the marker

**Files (under `core-api/`):**
- Create: `core_api/local_reprice.py`, `alembic/versions/a9c3e5f17b24_add_local_reprice_markers.py`, `tests/test_local_reprice.py`
- Modify: `core_api/models/local_rates.py`, `core_api/models/__init__.py`, `core_api/routers/local_rates.py`, `core_api/routers/sources.py`
- Test: `tests/test_local_rate_windows_model.py`, `tests/test_local_rate_windows_api.py`, `tests/test_sources.py`

**Interfaces:**
- Produces:
  - `models.local_rates.LocalRepriceMarker` with `source_id` (PK, FK `sources.id`), `reprice_from` (UTC datetime), `version` (int).
  - `local_reprice.window_snapshot(window) -> dict`, `local_reprice.reprice_start(old: dict, new: dict) -> datetime | None`, `local_reprice.mark_reprice(db: Session, source_id: int, start: datetime) -> None`.
  - The window routes set the marker in the same transaction as the change. Deleting a source deletes its marker.
  - Task 2 reads the marker.

- [ ] **Step 1: Write the failing pure tests**

Create `core-api/tests/test_local_reprice.py`:

```python
from __future__ import annotations

import datetime
import decimal

from core_api.local_reprice import reprice_start

UTC = datetime.timezone.utc


def dt(*args) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=UTC)


def snap(**overrides) -> dict:
    base = {
        "channel": "buy",
        "effective_from": dt(2026, 1, 1),
        "effective_to": None,
        "months": [1, 2, 3],
        "days_of_week": [0, 1],
        "start_time": datetime.time(0, 0),
        "end_time": datetime.time(7, 0),
        "rate_c_per_kwh": decimal.Decimal("30"),
    }
    return {**base, **overrides}


def test_a_rate_change_marks_from_the_window_start():
    assert reprice_start(snap(), snap(rate_c_per_kwh=decimal.Decimal("35"))) == dt(2026, 1, 1)


def test_a_rate_that_is_equal_but_written_differently_is_no_change():
    old = snap(rate_c_per_kwh=decimal.Decimal("30.0000"))
    assert reprice_start(old, snap(rate_c_per_kwh=decimal.Decimal("30"))) is None


def test_a_pattern_change_marks_from_the_window_start():
    assert reprice_start(snap(), snap(months=[1, 2])) == dt(2026, 1, 1)


def test_a_channel_change_marks_from_the_window_start():
    assert reprice_start(snap(), snap(channel="sell")) == dt(2026, 1, 1)


def test_moving_the_start_marks_the_earlier_start():
    assert reprice_start(snap(), snap(effective_from=dt(2026, 3, 1))) == dt(2026, 1, 1)
    assert reprice_start(snap(effective_from=dt(2026, 3, 1)), snap()) == dt(2026, 1, 1)


def test_ending_an_open_window_marks_the_new_end():
    assert reprice_start(snap(), snap(effective_to=dt(2026, 6, 15, 10, 0))) == dt(2026, 6, 15, 10, 0)


def test_reopening_an_ended_window_marks_the_old_end():
    assert reprice_start(snap(effective_to=dt(2026, 6, 15, 10, 0)), snap()) == dt(2026, 6, 15, 10, 0)


def test_moving_an_end_earlier_marks_the_new_end():
    old = snap(effective_to=dt(2026, 6, 20))
    assert reprice_start(old, snap(effective_to=dt(2026, 6, 10))) == dt(2026, 6, 10)


def test_moving_an_end_later_marks_the_old_end():
    old = snap(effective_to=dt(2026, 6, 10))
    assert reprice_start(old, snap(effective_to=dt(2026, 6, 20))) == dt(2026, 6, 10)


def test_no_change_marks_nothing():
    assert reprice_start(snap(), snap()) is None
    assert reprice_start(snap(effective_to=dt(2026, 6, 10)), snap(effective_to=dt(2026, 6, 10))) is None
```

Run: `cd core-api && uv run pytest tests/test_local_reprice.py -q`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 2: Add the model**

Append to `core-api/core_api/models/local_rates.py` (add `Integer` to the sqlalchemy import):

```python
class LocalRepriceMarker(Base):
    """At most one per local source: the earliest instant whose price may have
    changed because a rate window was created, replaced or deleted. The
    collector reprices from here and then clears it. `version` goes up on every
    change, so a clear that carries an old version leaves a newer edit in
    place."""

    __tablename__ = "local_reprice_markers"

    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    reprice_from: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
```

In `core-api/core_api/models/__init__.py`, change the import to `from core_api.models.local_rates import LocalRateWindow, LocalRepriceMarker` and add `"LocalRepriceMarker",` to `__all__`.

- [ ] **Step 3: Add the migration**

Create `core-api/alembic/versions/a9c3e5f17b24_add_local_reprice_markers.py`:

```python
"""add local_reprice_markers

Revision ID: a9c3e5f17b24
Revises: e5a1c3d7f902
Create Date: 2026-10-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9c3e5f17b24'
down_revision: Union[str, Sequence[str], None] = 'e5a1c3d7f902'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'local_reprice_markers',
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('reprice_from', sa.DateTime(timezone=True), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id']),
        sa.PrimaryKeyConstraint('source_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('local_reprice_markers')
```

- [ ] **Step 4: Add the helpers**

Create `core-api/core_api/local_reprice.py`:

```python
from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from core_api.models.local_rates import LocalRateWindow, LocalRepriceMarker
from core_api.time_utils import as_utc

_PRICE_FIELDS = ("channel", "effective_from", "months", "days_of_week", "start_time", "end_time", "rate_c_per_kwh")


def window_snapshot(window: LocalRateWindow) -> dict:
    """The fields of a window that decide its prices, with its dates as aware
    UTC. The label is left out: renaming a window prices nothing differently."""
    snapshot = {name: getattr(window, name) for name in _PRICE_FIELDS}
    snapshot["effective_from"] = as_utc(window.effective_from)
    snapshot["effective_to"] = as_utc(window.effective_to) if window.effective_to else None
    return snapshot


def reprice_start(old: dict, new: dict) -> datetime.datetime | None:
    """The earliest instant whose price a change to a window may alter, or
    None when nothing that decides a price changed. When only the end moved,
    that is the earlier end (a missing end counts as later than any date)."""
    if any(old[name] != new[name] for name in _PRICE_FIELDS):
        return min(old["effective_from"], new["effective_from"])
    if old["effective_to"] == new["effective_to"]:
        return None
    return min(end for end in (old["effective_to"], new["effective_to"]) if end is not None)


def mark_reprice(db: Session, source_id: int, start: datetime.datetime) -> None:
    """Records that prices from `start` may have changed. Keeps the earlier of
    `start` and any marker already there, and raises the version. Runs in the
    caller's transaction."""
    start = as_utc(start)
    marker = db.get(LocalRepriceMarker, source_id)
    if marker is None:
        db.add(LocalRepriceMarker(source_id=source_id, reprice_from=start, version=1))
        return
    marker.reprice_from = min(as_utc(marker.reprice_from), start)
    marker.version += 1
```

Run: `cd core-api && uv run pytest tests/test_local_reprice.py -q`
Expected: all pass.

- [ ] **Step 5: Write the failing API and model tests**

Append to `core-api/tests/test_local_rate_windows_model.py`:

```python
def test_migration_creates_the_reprice_marker_table(app_and_db_url):
    _, db_url = app_and_db_url
    inspector = sa.inspect(make_engine(db_url))

    assert "local_reprice_markers" in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns("local_reprice_markers")}
    assert columns == {"source_id", "reprice_from", "version"}
```

Append to `core-api/tests/test_local_rate_windows_api.py` (it already imports `pytest`, `SourceKind`, `PRICING_COLLECTOR_KEY`, `auth`, `make_owner_and_site`, `make_source` and defines `FLAT`, `_window`, `_url`, `local_source`; add the imports shown):

```python
import datetime

from core_api.db import make_engine, make_session_factory
from core_api.models.local_rates import LocalRepriceMarker
from core_api.time_utils import as_utc

UTC = datetime.timezone.utc


def _marker(ctx):
    session = make_session_factory(make_engine(ctx["db_url"]))()
    try:
        row = session.get(LocalRepriceMarker, ctx["source"])
        return (as_utc(row.reprice_from), row.version) if row else None
    finally:
        session.close()


def _drop_marker(ctx):
    session = make_session_factory(make_engine(ctx["db_url"]))()
    try:
        row = session.get(LocalRepriceMarker, ctx["source"])
        if row:
            session.delete(row)
            session.commit()
    finally:
        session.close()


def _create(client, ctx, **overrides) -> int:
    response = client.post(_url(ctx), json=_window(**overrides), headers=auth(ctx["owner"]))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _replace(client, ctx, window_id, **overrides):
    response = client.patch(_url(ctx, f"/{window_id}"), json=_window(**overrides), headers=auth(ctx["owner"]))
    assert response.status_code == 200, response.text


def test_a_new_source_has_no_marker(local_source):
    assert _marker(local_source) is None


def test_creating_a_window_marks_from_its_start(client, local_source):
    _create(client, local_source)
    assert _marker(local_source) == (datetime.datetime(2026, 1, 1, tzinfo=UTC), 1)


def test_a_later_change_keeps_the_earlier_start_and_raises_the_version(client, local_source):
    _create(client, local_source)
    _create(client, local_source, channel="sell", effective_from="2026-03-01T00:00:00Z")
    assert _marker(local_source) == (datetime.datetime(2026, 1, 1, tzinfo=UTC), 2)


def test_a_change_that_starts_earlier_lowers_the_start(client, local_source):
    _create(client, local_source, effective_from="2026-03-01T00:00:00Z")
    _create(client, local_source, channel="sell", effective_from="2026-01-01T00:00:00Z")
    assert _marker(local_source) == (datetime.datetime(2026, 1, 1, tzinfo=UTC), 2)


def test_changing_a_rate_marks_from_the_window_start(client, local_source):
    window_id = _create(client, local_source)
    _drop_marker(local_source)

    _replace(client, local_source, window_id, rate_c_per_kwh="35")

    assert _marker(local_source) == (datetime.datetime(2026, 1, 1, tzinfo=UTC), 1)


def test_ending_a_window_marks_the_moment_it_ends(client, local_source):
    window_id = _create(client, local_source)
    _drop_marker(local_source)

    _replace(client, local_source, window_id, effective_to="2026-06-15T10:00:00Z")

    assert _marker(local_source) == (datetime.datetime(2026, 6, 15, 10, 0, tzinfo=UTC), 1)


def test_end_then_add_from_now_marks_only_from_now(client, local_source):
    window_id = _create(client, local_source, effective_from="2025-01-01T00:00:00Z")
    _drop_marker(local_source)

    _replace(client, local_source, window_id, effective_from="2025-01-01T00:00:00Z", effective_to="2026-06-15T10:00:00Z")
    _create(client, local_source, effective_from="2026-06-15T10:00:01Z", rate_c_per_kwh="35")

    assert _marker(local_source) == (datetime.datetime(2026, 6, 15, 10, 0, tzinfo=UTC), 2)


def test_renaming_a_window_marks_nothing(client, local_source):
    window_id = _create(client, local_source)
    _drop_marker(local_source)

    _replace(client, local_source, window_id, label="Renamed")

    assert _marker(local_source) is None


def test_deleting_a_window_marks_from_its_start(client, local_source):
    window_id = _create(client, local_source, effective_from="2026-02-01T00:00:00Z")
    _drop_marker(local_source)

    response = client.delete(_url(local_source, f"/{window_id}"), headers=auth(local_source["owner"]))

    assert response.status_code == 204
    assert _marker(local_source) == (datetime.datetime(2026, 2, 1, tzinfo=UTC), 1)


def test_a_rejected_change_marks_nothing(client, local_source):
    _create(client, local_source)
    _drop_marker(local_source)

    overlap = client.post(
        _url(local_source), json=_window(label="Clash", start_time="07:00:00", end_time="09:00:00"), headers=auth(local_source["owner"])
    )

    assert overlap.status_code == 422
    assert _marker(local_source) is None
```

Append to `core-api/tests/test_sources.py` (it already imports what it needs for the window delete test; add `LocalRepriceMarker` to the model import):

```python
def test_deleting_a_source_deletes_its_reprice_marker(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "local", "name": "Fixed"},
        headers=auth(owner_id),
    ).json()
    client.post(
        f"/api/v1/sites/{site_id}/sources/{source['id']}/rate-windows",
        json={
            "channel": "buy", "label": "Flat", "effective_from": "2026-01-01T00:00:00Z",
            "months": list(range(1, 13)), "days_of_week": list(range(7)),
            "start_time": "00:00:00", "end_time": "00:00:00", "rate_c_per_kwh": "30",
        },
        headers=auth(owner_id),
    )

    response = client.delete(f"/api/v1/sites/{site_id}/sources/{source['id']}", headers=auth(owner_id))

    assert response.status_code == 204, response.text
    session = make_session_factory(make_engine(db_url))()
    try:
        assert session.query(LocalRepriceMarker).count() == 0
    finally:
        session.close()
```

Run: `cd core-api && uv run pytest tests/test_local_rate_windows_model.py tests/test_local_rate_windows_api.py tests/test_sources.py -k "reprice or marker or marks or mark or rejected_change or new_source_has_no_marker or ending_a_window or end_then_add or renaming_a_window or deleting_a_window or later_change or starts_earlier or changing_a_rate" -q`
Expected: the new tests FAIL (no marker is set yet; the source delete fails on the foreign key from a marker, once Step 6 adds markers).

- [ ] **Step 6: Set the marker in the window routes**

In `core-api/core_api/routers/local_rates.py`, add imports `from core_api.local_reprice import mark_reprice, reprice_start, window_snapshot`. In `create_rate_window`, after `db.add(window)` add `mark_reprice(db, source.id, payload.effective_from)`. Replace the body of `replace_rate_window` after `_reject_overlap` with:

```python
    old = window_snapshot(window)
    for field, value in payload.model_dump().items():
        setattr(window, field, value)
    start = reprice_start(old, window_snapshot(window))
    if start is not None:
        mark_reprice(db, source.id, start)
    db.commit()
    db.refresh(window)
    return window
```

and `delete_rate_window` becomes:

```python
    source = _get_local_source(db, site, source_id)
    window = _get_window(db, source, window_id)
    mark_reprice(db, source.id, window.effective_from)
    db.delete(window)
    db.commit()
```

- [ ] **Step 7: Delete the marker with its source**

In `core-api/core_api/routers/sources.py`, import `LocalRepriceMarker` next to `LocalRateWindow`, and in `delete_source` add, next to the window delete:

```python
    db.execute(delete(LocalRepriceMarker).where(LocalRepriceMarker.source_id == source.id))
```

(Both deletes sit in the same transaction as `db.delete(source)`, so a delete blocked by price readings rolls both back.)

- [ ] **Step 8: Run the tests**

Run: `cd core-api && uv run pytest tests/test_local_reprice.py tests/test_local_rate_windows_model.py tests/test_local_rate_windows_api.py tests/test_sources.py -q`
Expected: all pass. Then `cd core-api && uv run pytest -q`. Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add -A .
git commit -m "feat: record where a local rate window change takes effect

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Deliver the marker and clear it

**Files (under `core-api/`):**
- Modify: `core_api/routers/collector_config.py`, `core_api/routers/pricing_backfill.py`, `core_api/schemas.py`
- Test: `tests/test_local_rate_windows_api.py`, `tests/test_pricing_backfill.py`

**Interfaces:**
- Consumes: `LocalRepriceMarker` (Task 1).
- Produces:
  - A local source's config gains `reprice: {"from": "<UTC ISO, +00:00>", "version": <int>}` only when a marker exists.
  - `POST /api/v1/sources/{source_id}/prices/reprice-marker/clear` with `{"version": <int>}` returns 204 and deletes the marker only if its version matches. Task 3 calls it.

- [ ] **Step 1: Write the failing tests**

Append to `core-api/tests/test_local_rate_windows_api.py`:

```python
CONFIG_HEADERS = {"X-Collector-Key": PRICING_COLLECTOR_KEY, "X-Collector-Provider": "local"}


def _config(client, ctx) -> dict:
    return client.get(f"/api/v1/sources/{ctx['source']}/config", headers=CONFIG_HEADERS).json()["config"]


def _clear(client, ctx, version, headers=None):
    return client.post(
        f"/api/v1/sources/{ctx['source']}/prices/reprice-marker/clear",
        json={"version": version},
        headers=headers or CONFIG_HEADERS,
    )


def test_config_has_no_reprice_key_without_a_marker(client, local_source):
    assert "reprice" not in _config(client, local_source)


def test_config_carries_the_marker_as_utc_iso_and_a_version(client, local_source):
    _create(client, local_source, effective_from="2026-02-01T00:00:00Z")

    assert _config(client, local_source)["reprice"] == {"from": "2026-02-01T00:00:00+00:00", "version": 1}


def test_clearing_with_the_matching_version_removes_the_marker(client, local_source):
    _create(client, local_source)

    response = _clear(client, local_source, 1)

    assert response.status_code == 204
    assert "reprice" not in _config(client, local_source)


def test_clearing_with_a_stale_version_keeps_the_marker(client, local_source):
    _create(client, local_source)
    _create(client, local_source, channel="sell")  # version is now 2

    response = _clear(client, local_source, 1)

    assert response.status_code == 204
    assert _config(client, local_source)["reprice"]["version"] == 2


def test_clearing_when_there_is_no_marker_is_a_no_op(client, local_source):
    assert _clear(client, local_source, 1).status_code == 204


def test_clearing_needs_the_collector_key(client, local_source):
    assert _clear(client, local_source, 1, headers={"X-Collector-Key": "wrong"}).status_code == 401


def test_clearing_rejects_a_provider_header_for_another_provider(client, local_source):
    headers = {"X-Collector-Key": PRICING_COLLECTOR_KEY, "X-Collector-Provider": "amber"}
    assert _clear(client, local_source, 1, headers=headers).status_code == 401


def test_an_amber_config_never_has_a_reprice_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    amber_id, _ = make_source(db_url, SourceKind.PRICING, "amber")
    response = client.get(f"/api/v1/sources/{amber_id}/config", headers={"X-Collector-Key": PRICING_COLLECTOR_KEY})
    assert "reprice" not in response.json()["config"]
```

Run: `cd core-api && uv run pytest tests/test_local_rate_windows_api.py -k "reprice or clearing" -q`
Expected: FAIL (no `reprice` key, 404 on the clear route).

- [ ] **Step 2: Deliver the marker**

In `core-api/core_api/routers/collector_config.py`, import `LocalRepriceMarker` next to `LocalRateWindow`, and extend `_local_config` (it is under 30 lines):

```python
def _local_config(db: Session, source: Source, config: dict) -> None:
    site = db.get(Site, source.site_id)
    windows = db.execute(
        select(LocalRateWindow).where(LocalRateWindow.source_id == source.id).order_by(LocalRateWindow.id)
    ).scalars().all()
    config["timezone"] = site.timezone
    config["rate_windows"] = [_rate_window_config(w) for w in windows]
    marker = db.get(LocalRepriceMarker, source.id)
    if marker is not None:
        config["reprice"] = {"from": as_utc(marker.reprice_from).isoformat(), "version": marker.version}
```

- [ ] **Step 3: Add the schema and the clear endpoint**

In `core-api/core_api/schemas.py`, after `PriceCoverageOut`:

```python
class RepriceMarkerClear(BaseModel):
    version: int
```

In `core-api/core_api/routers/pricing_backfill.py`, import `LocalRepriceMarker` (from `core_api.models.local_rates`) and `RepriceMarkerClear`, and add after `get_price_coverage` (before `site_router`):

```python
@router.post("/reprice-marker/clear", status_code=status.HTTP_204_NO_CONTENT)
def clear_reprice_marker(
    payload: RepriceMarkerClear,
    source: Source = Depends(require_source_kind(SourceKind.PRICING)),
    db: Session = Depends(get_db_session),
) -> None:
    """Deletes the source's reprice marker only when its version matches the
    one the collector read. A newer change raised the version, so the marker
    stays and the next run handles it. Always 204: a missing marker or a stale
    version is not an error."""
    marker = db.get(LocalRepriceMarker, source.id)
    if marker is not None and marker.version == payload.version:
        db.delete(marker)
        db.commit()
```

- [ ] **Step 4: Run the tests**

Run: `cd core-api && uv run pytest tests/test_local_rate_windows_api.py tests/test_pricing_backfill.py -q`, then `cd core-api && uv run pytest -q`.
Expected: all pass. The existing exact-config tests (`test_config_for_a_local_source_with_no_windows_is_empty`, `test_local_config_ignores_extra_source_config_keys`) still pass because those sources have no marker.

- [ ] **Step 5: Commit**

```bash
git add -A .
git commit -m "feat: deliver the reprice marker to the collector and let it clear it

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Collector client, trigger and planner

**Files (under `local-pricing-collector/`):**
- Modify: `local_pricing_collector/models.py`, `local_pricing_collector/core_api_client.py`, `local_pricing_collector/triggers.py`, `local_pricing_collector/plan.py`
- Test: `tests/test_models.py` (create), `tests/test_core_api_client.py`, `tests/test_triggers.py`, `tests/test_plan.py`

**Interfaces:**
- Consumes: the `reprice` config key and clear endpoint (Task 2).
- Produces:
  - `models.RepriceMarker(start: datetime, version: int)` and `models.parse_reprice(config: dict) -> RepriceMarker | None` (raises `KeyError`, `TypeError` or `ValueError` on a malformed marker).
  - `core_api_client.clear_reprice_marker(base_url, source_id, collector_key, version: int) -> None`.
  - `Triggers(catch_up, scheduled_for=None, reprice=None)`; `Triggers.any` is also true with a marker; `due(state, now, tz, windows_key, reprice=None)`.
  - `plan_ranges` adds, for each channel with windows, the range `max(marker.start, history_start)` to the end of tomorrow when `triggers.reprice` is set.

- [ ] **Step 1: Write the failing tests**

Create `local-pricing-collector/tests/test_models.py`:

```python
from __future__ import annotations

import datetime

import pytest

from local_pricing_collector.models import RepriceMarker, parse_reprice

UTC = datetime.timezone.utc


def test_no_marker_in_the_config_is_none():
    assert parse_reprice({"timezone": "UTC", "rate_windows": []}) is None


def test_a_marker_is_parsed_to_aware_utc_and_an_int_version():
    config = {"reprice": {"from": "2026-02-01T00:00:00+00:00", "version": 3}}
    assert parse_reprice(config) == RepriceMarker(datetime.datetime(2026, 2, 1, tzinfo=UTC), 3)


def test_an_offset_less_start_is_read_as_utc():
    config = {"reprice": {"from": "2026-02-01T00:00:00", "version": 1}}
    assert parse_reprice(config).start == datetime.datetime(2026, 2, 1, tzinfo=UTC)


@pytest.mark.parametrize("bad", [{"from": "x", "version": 1}, {"from": "2026-02-01T00:00:00Z"}, "text", {"version": 1}])
def test_a_malformed_marker_raises(bad):
    with pytest.raises((KeyError, TypeError, ValueError)):
        parse_reprice({"reprice": bad})
```

Append to `local-pricing-collector/tests/test_core_api_client.py`:

```python
from local_pricing_collector.core_api_client import clear_reprice_marker


@respx.mock
def test_clear_reprice_marker_posts_the_version():
    route = respx.post(f"{BASE_URL}/api/v1/sources/4/prices/reprice-marker/clear").mock(
        return_value=httpx.Response(204)
    )

    clear_reprice_marker(BASE_URL, 4, "k", 3)

    request = route.calls[0].request
    assert json.loads(request.content) == {"version": 3}
    assert request.headers["X-Collector-Key"] == "k"
    assert request.headers["X-Collector-Provider"] == "local"


@respx.mock
def test_clear_reprice_marker_raises_on_failure():
    respx.post(f"{BASE_URL}/api/v1/sources/4/prices/reprice-marker/clear").mock(return_value=httpx.Response(500, text="no"))
    with pytest.raises(CoreApiError):
        clear_reprice_marker(BASE_URL, 4, "k", 3)


@respx.mock
def test_clear_reprice_marker_wraps_connection_errors():
    respx.post(f"{BASE_URL}/api/v1/sources/4/prices/reprice-marker/clear").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(CoreApiError):
        clear_reprice_marker(BASE_URL, 4, "k", 3)
```

Append to `local-pricing-collector/tests/test_triggers.py` (it already imports `datetime`, `SourceState`, `Triggers`, `due`, `SYDNEY`, `at`; add `RepriceMarker`):

```python
from local_pricing_collector.models import RepriceMarker

MARKER = RepriceMarker(datetime.datetime(2026, 6, 1, tzinfo=UTC), 3)


def test_a_marker_makes_a_caught_up_source_due():
    state = SourceState(caught_up_date=datetime.date(2026, 6, 15), windows_key="k")

    triggers = due(state, at(2026, 6, 15, 0, 0), SYDNEY, "k", MARKER)

    assert triggers.reprice == MARKER
    assert triggers.catch_up is False
    assert triggers.any


def test_without_a_marker_nothing_changes():
    state = SourceState(caught_up_date=datetime.date(2026, 6, 15), windows_key="k")
    triggers = due(state, at(2026, 6, 15, 0, 0), SYDNEY, "k")
    assert triggers.reprice is None
    assert not triggers.any


def test_triggers_default_to_no_marker():
    assert Triggers(catch_up=True).reprice is None
```

(If `test_triggers.py` names its UTC constant or helper differently, use the names that file already defines.)

Append to `local-pricing-collector/tests/test_plan.py` (module constants `NOW`, `TODAY_END`, `TOMORROW_END`, `plan`, `window`, `at`, `CATCH_UP`, `Triggers` already exist there; `plan(windows, coverage=None, floor=None, tz=SYDNEY, now=..., triggers=CATCH_UP)`; add `RepriceMarker`):

```python
from local_pricing_collector.models import RepriceMarker


def marker(*args, version=1) -> RepriceMarker:
    return RepriceMarker(at(*args), version)


def only_reprice(m):
    return Triggers(catch_up=False, reprice=m)


def test_a_marker_reprices_from_the_marker_to_the_end_of_tomorrow():
    ranges = plan([window()], floor=at(2026, 6, 1, 0, 0), triggers=only_reprice(marker(2026, 6, 10, 0, 0)))
    assert ranges == [PlannedRange("buy", at(2026, 6, 10, 0, 0), TOMORROW_END)]


def test_a_marker_before_the_site_data_floor_starts_at_the_floor():
    ranges = plan([window()], floor=at(2026, 6, 1, 0, 0), triggers=only_reprice(marker(2026, 5, 1, 0, 0)))
    assert ranges == [PlannedRange("buy", at(2026, 6, 1, 0, 0), TOMORROW_END)]


def test_a_marker_before_the_window_starts_at_the_window():
    early = window(effective_from="2026-06-10T00:00:00+00:00")
    ranges = plan([early], floor=at(2026, 6, 1, 0, 0), triggers=only_reprice(marker(2026, 6, 5, 0, 0)))
    assert ranges == [PlannedRange("buy", at(2026, 6, 10, 0, 0), TOMORROW_END)]


def test_a_marker_after_the_end_of_tomorrow_plans_nothing():
    assert plan([window()], floor=at(2026, 6, 1, 0, 0), triggers=only_reprice(marker(2026, 6, 20, 0, 0))) == []


def test_a_marker_is_cut_at_the_end_of_windows_that_have_all_ended():
    ended = window(effective_to="2026-06-14T00:00:00+00:00")
    ranges = plan([ended], floor=at(2026, 6, 1, 0, 0), triggers=only_reprice(marker(2026, 6, 10, 0, 0)))
    assert ranges == [PlannedRange("buy", at(2026, 6, 10, 0, 0), at(2026, 6, 14, 0, 0))]


def test_a_marker_reprices_every_direction_that_has_windows():
    windows = [window(), window(channel="sell")]
    ranges = plan(windows, floor=at(2026, 6, 1, 0, 0), triggers=only_reprice(marker(2026, 6, 10, 0, 0)))
    assert [(r.channel, r.start) for r in ranges] == [("buy", at(2026, 6, 10, 0, 0)), ("sell", at(2026, 6, 10, 0, 0))]


def test_a_marker_merges_with_a_catch_up_range():
    triggers = Triggers(catch_up=True, reprice=marker(2026, 6, 10, 0, 0))
    ranges = plan([window()], floor=at(2026, 6, 1, 0, 0), triggers=triggers)
    assert ranges == [PlannedRange("buy", at(2026, 6, 1, 0, 0), TOMORROW_END)]


def test_a_marker_range_is_not_backward():
    ranges = plan([window()], floor=at(2026, 6, 1, 0, 0), triggers=only_reprice(marker(2026, 6, 10, 0, 0)))
    assert ranges[0].backward is False


def test_a_marker_needs_no_coverage():
    ranges = plan([window()], coverage={}, floor=at(2026, 6, 1, 0, 0), triggers=only_reprice(marker(2026, 6, 10, 0, 0)))
    assert len(ranges) == 1
```

Run: `cd local-pricing-collector && uv run pytest tests/test_models.py tests/test_core_api_client.py tests/test_triggers.py tests/test_plan.py -q`
Expected: FAIL (ImportError for `RepriceMarker` and `clear_reprice_marker`).

- [ ] **Step 2: Implement the marker type and the client call**

Append to `local-pricing-collector/local_pricing_collector/models.py`:

```python
@dataclass(frozen=True)
class RepriceMarker:
    """core-api's note that prices from `start` may have changed. `version`
    identifies the note, so a clear for an old version leaves a newer one."""

    start: datetime.datetime
    version: int


def parse_reprice(config: dict) -> RepriceMarker | None:
    """The reprice marker in a source's config, or None. Raises KeyError,
    TypeError or ValueError on a malformed marker."""
    raw = config.get("reprice")
    if raw is None:
        return None
    start = datetime.datetime.fromisoformat(raw["from"])
    if start.tzinfo is None:
        start = start.replace(tzinfo=datetime.timezone.utc)
    return RepriceMarker(start=start.astimezone(datetime.timezone.utc), version=int(raw["version"]))
```

Append to `local-pricing-collector/local_pricing_collector/core_api_client.py`:

```python
def clear_reprice_marker(base_url: str, source_id: int, collector_key: str, version: int) -> None:
    """Tells core-api the repricing for this marker version is done. core-api
    keeps the marker if a newer change has raised the version."""
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/prices/reprice-marker/clear",
        headers=_headers(collector_key),
        json={"version": version},
        timeout=10,
    )
    if response.status_code != 204:
        raise CoreApiError(f"Failed to clear the reprice marker: {response.status_code} {response.text}")
```

- [ ] **Step 3: Implement the trigger**

In `local-pricing-collector/local_pricing_collector/triggers.py`: import `RepriceMarker` from `local_pricing_collector.models`; extend `Triggers`:

```python
    catch_up: bool
    scheduled_for: datetime.date | None = None
    reprice: RepriceMarker | None = None
```

with `any` returning `self.catch_up or self.scheduled or self.reprice is not None`, and the docstring gaining one sentence: "`reprice` is core-api's marker for a changed rate window." Change `due` to take `reprice: RepriceMarker | None = None` as a last parameter and return `Triggers(catch_up=..., scheduled_for=..., reprice=reprice)`. `after_success` is unchanged: the marker is cleared in core-api, not remembered here.

- [ ] **Step 4: Implement the planner range**

In `local-pricing-collector/local_pricing_collector/plan.py`, change `_wanted_ranges` to take the history start for the marker and add the range (the function stays under 30 lines; if it does not, move the three marker lines into a small helper):

```python
    if triggers.reprice is not None:
        ranges.append((max(triggers.reprice.start, history_start), tomorrow_end, False))
```

placed after the scheduled block, with the signature unchanged (it already receives `history_start`). Update its docstring to say the reprice range runs forward from the marker.

- [ ] **Step 5: Run the tests**

Run: `cd local-pricing-collector && uv run pytest tests/test_models.py tests/test_core_api_client.py tests/test_triggers.py tests/test_plan.py -q`
Expected: all pass. Then `cd local-pricing-collector && uv run pytest -q`. Expected: all pass (existing `due` calls with four arguments still work).

- [ ] **Step 6: Commit**

```bash
git add -A .
git commit -m "feat: let the local collector plan a reprice from core-api's marker

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Loop and sync

**Files (under `local-pricing-collector/`):**
- Modify: `local_pricing_collector/poll.py`
- Test: `tests/test_poll.py`

**Interfaces:**
- Consumes: Task 3.
- Produces: `_start_if_due` reads the marker from the discovery config and passes it to `due`; `_sync` fetches the site data floor for a reprice, pushes the planned ranges, then clears the marker with the version read, and also clears it when the source has no windows.

- [ ] **Step 1: Write the failing tests**

In `local-pricing-collector/tests/test_poll.py`:

1. Extend `FakeApi` (keep every existing method and attribute):

```python
    def __init__(self, coverage=None, floor=None, fail_push_after=None, fail_clear=False):
        ...                                  # keep the existing assignments
        self.fail_clear = fail_clear
        self.clears = []

    def clear_reprice_marker(self, url, source_id, key, version):
        if self.fail_clear:
            raise CoreApiError("down")
        self.clears.append((source_id, version))
```

and add `"clear_reprice_marker"` to the tuple of names in `install`.

2. Add a helper and the tests:

```python
from local_pricing_collector.models import RepriceMarker

MARKER_FROM = datetime.datetime(2026, 6, 10, 0, 0, tzinfo=UTC)
MARKER = RepriceMarker(MARKER_FROM, 3)
REPRICE = Triggers(catch_up=False, reprice=MARKER)
FLOOR = datetime.datetime(2026, 6, 1, 0, 0, tzinfo=UTC)


def test_a_reprice_pushes_from_the_marker_then_clears_it(monkeypatch):
    api = FakeApi(floor=FLOOR).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), REPRICE, NOW) is True

    assert api.coverage_calls == 0
    intervals = api.pushes[0][1]
    assert intervals[0]["interval_start"] == "2026-06-10T00:00:00+00:00"
    assert intervals[-1]["interval_end"] == "2026-06-16T14:00:00+00:00"
    assert api.clears == [(7, 3)]


def test_a_failed_push_leaves_the_marker(monkeypatch):
    api = FakeApi(floor=FLOOR, fail_push_after=0).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), REPRICE, NOW) is False
    assert api.clears == []


def test_a_failed_clear_reports_failure(monkeypatch):
    api = FakeApi(floor=FLOOR, fail_clear=True).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), REPRICE, NOW) is False
    assert len(api.pushes) >= 1


def test_a_source_with_no_windows_clears_its_marker_without_pushing(monkeypatch):
    api = FakeApi().install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(windows=[]), REPRICE, NOW) is True
    assert api.pushes == []
    assert api.clears == [(7, 3)]


def test_a_marker_that_clamps_to_nothing_is_cleared_without_pushing(monkeypatch):
    api = FakeApi(floor=FLOOR).install(monkeypatch)
    later = Triggers(catch_up=False, reprice=RepriceMarker(datetime.datetime(2026, 6, 30, tzinfo=UTC), 4))

    assert poll.sync_one_source(SETTINGS, source(), later, NOW) is True
    assert api.pushes == []
    assert api.clears == [(7, 4)]


def test_a_catch_up_and_a_reprice_in_one_pass_clear_once(monkeypatch):
    api = FakeApi(floor=FLOOR).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), Triggers(catch_up=True, reprice=MARKER), NOW) is True
    assert api.clears == [(7, 3)]
    assert api.coverage_calls == 1


def test_a_run_without_a_marker_never_clears(monkeypatch):
    api = FakeApi(floor=FLOOR).install(monkeypatch)

    poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW)

    assert api.clears == []


def marked_source(version=3, source_id=7) -> LocalSource:
    base = source(source_id=source_id)
    return LocalSource(source_id, {**base.config, "reprice": {"from": "2026-06-10T00:00:00+00:00", "version": version}})


def test_a_marker_starts_a_child_for_a_source_that_is_caught_up(monkeypatch):
    src = marked_source()
    fake_discovery(monkeypatch, [src])
    state = poll.TickState(
        states={7: SourceState(caught_up_date=datetime.date(2026, 6, 15), windows_key=poll.windows_key(src.config))}
    )

    tick(state)

    assert len(FakeProcess.instances) == 1
    assert FakeProcess.instances[0].args[2].reprice == MARKER


def test_a_malformed_marker_is_logged_and_the_source_still_gets_its_other_work(monkeypatch, caplog):
    bad = LocalSource(7, {**source().config, "reprice": {"from": "nope", "version": 1}})
    fake_discovery(monkeypatch, [bad])

    tick(poll.TickState())

    assert "reprice marker" in caplog.text
    assert FakeProcess.instances[0].args[2].reprice is None
    assert FakeProcess.instances[0].args[2].catch_up is True


def test_the_windows_fingerprint_ignores_the_marker():
    plain = source()
    assert poll.windows_key(plain.config) == poll.windows_key(marked_source().config)
```

Run: `cd local-pricing-collector && uv run pytest tests/test_poll.py -q`
Expected: the new tests FAIL.

- [ ] **Step 2: Implement the loop changes**

In `local-pricing-collector/local_pricing_collector/poll.py`:

1. Imports: add `clear_reprice_marker` to the `core_api_client` import and `parse_reprice` to the `models` import.

2. `_coverage_and_floor` fetches the floor for a reprice too:

```python
def _coverage_and_floor(
    settings: Settings, source_id: int, triggers: Triggers
) -> tuple[dict[str, Coverage], datetime.datetime | None]:
    """A catch-up needs what is already priced and the site's first data. A
    reprice needs the first data only, so it does not reprice years of empty
    history. The scheduled push needs neither."""
    url, key = settings.core_api_url, settings.collector_key
    coverage = fetch_price_coverage(url, source_id, key) if triggers.catch_up else {}
    wants_floor = triggers.catch_up or triggers.reprice is not None
    return coverage, (fetch_site_data_floor(url, source_id, key) if wants_floor else None)
```

3. A helper and the changes in `_sync`:

```python
def _clear_marker(settings: Settings, source_id: int, triggers: Triggers) -> None:
    if triggers.reprice is not None:
        clear_reprice_marker(settings.core_api_url, source_id, settings.collector_key, triggers.reprice.version)
```

In `_sync`, the no-windows branch becomes:

```python
    if not windows:
        return _clear_when_empty(settings, source.source_id, triggers)
```

with

```python
def _clear_when_empty(settings: Settings, source_id: int, triggers: Triggers) -> bool:
    """With no windows there is nothing to price, so a marker is spent."""
    try:
        _clear_marker(settings, source_id, triggers)
    except CoreApiError:
        logger.exception("Failed to clear the reprice marker for source %s; will retry", source_id)
        return False
    return True
```

and inside the existing `try` of `_sync`, after the loop that pushes the ranges, add `_clear_marker(settings, source.source_id, triggers)` (so a `CoreApiError` from a push or the clear goes to the same `except` and returns False).

4. `_start_if_due` reads the marker. Replace the lines from `key = windows_key(...)` to the `due` call with:

```python
    key = windows_key(source.config)
    triggers = due(tick.states.get(source.source_id), now, tz, key, _marker_or_none(source))
```

and add:

```python
def _marker_or_none(source: LocalSource) -> RepriceMarker | None:
    """A malformed marker is logged and ignored, so it cannot stop the rest of
    the source's pricing."""
    try:
        return parse_reprice(source.config)
    except CONFIG_ERRORS:
        logger.exception("Ignoring a malformed reprice marker for pricing source %s", source.source_id)
        return None
```

(import `RepriceMarker` from `local_pricing_collector.models` for the annotation).

- [ ] **Step 3: Run the tests**

Run: `cd local-pricing-collector && uv run pytest tests/test_poll.py -q`, then `cd local-pricing-collector && uv run pytest -q`.
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add -A .
git commit -m "feat: reprice from core-api's marker and clear it when done

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Docs and spec notes

**Files:**
- Modify: `webapp/src/docs/04-sources.md`, `README.md`, `docs/superpowers/specs/2026-10-14-local-pricing-daily-push-and-backfill-design.md`

**Interfaces:** none. `docs.test.ts` and `links.test.ts` check structure and links.

- [ ] **Step 1: Update the help chapter**

In `webapp/src/docs/04-sources.md`, section "Setting up a fixed tariff", rewrite these parts to match the built behaviour (read the current text first and keep its headings and anchors):

1. "Changing a rate" and "When changes take effect": a change to a window (adding, ending or deleting one, or changing one through the API) reprices from the moment the change takes effect to the end of tomorrow, within about a minute. Ending a window and adding the next one from now reprices from now. Correcting a rate on a window dated last month reprices from last month. To fix a mistyped rate, delete the window and add it again with the right rate, or end it and add a new one dated the same day; the stored prices follow within a minute.
2. State what repricing leaves alone: it overwrites prices for minutes that a window still prices. It does not remove prices for minutes that no window prices any more, so a window you delete with no replacement leaves the prices it wrote.
3. Remove the statements that the past keeps the old rate after a change and that an edit reaches only the next day's prices. Keep what is still true: the 23:59 push, the 02:00 retry, the fill for a new source or direction, and that prices written for minutes no window prices stay after End or Delete.
4. Renaming a window changes no prices and starts no repricing; say so in one sentence if the section lists what triggers a reprice.

Follow `.claude/rules/avoid-ai-writing.md`.

- [ ] **Step 2: Update the README and the spec**

`README.md`: add to the `local-pricing-collector/` row that a change to a rate window reprices from where it takes effect, within a minute.

In `docs/superpowers/specs/2026-10-14-local-pricing-daily-push-and-backfill-design.md`, under "Refinements made in planning", add a bullet: "A change to a rate window now reprices from where it takes effect, using a durable marker kept in core-api. See `2026-10-14-local-pricing-reprice-on-change-design.md`." Also change the sentence in that spec that says editing a window does not reprice the current day, and the "Not included" item about repricing the current day, so they point to the new spec instead of contradicting it.

- [ ] **Step 3: Run the checks**

Run: `cd webapp && npm test && npm run lint`
Expected: all pass (lint may show only the two older warnings in `AuthContext.tsx` and `useAvatarImage.ts`).

Scan every sentence you added against `.claude/rules/avoid-ai-writing.md`.

- [ ] **Step 4: Run every suite once**

```bash
(cd core-api && uv run pytest -q)
(cd amber-pricing-collector && uv run pytest -q)
(cd local-pricing-collector && uv run pytest -q)
(cd webapp && npm test)
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A .
git commit -m "docs: describe repricing when a local rate window changes

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage**

| Spec section | Task |
|---|---|
| `local_reprice_markers` table, migration, delete with source | 1 |
| Marker start rules for create, delete, replace (end-only, price change, label only) | 1 |
| Earlier-start-wins and version bump | 1 |
| Config delivery of `reprice` | 2 |
| Compare-and-clear endpoint with key and provider checks | 2 |
| Collector trigger, planner range, clamping, merging | 3 |
| Clear after all pushes; failure leaves the marker; empty and no-window cases cleared | 4 |
| Docs and README | 5 |
| End-to-end typo case | covered across Task 1 (marker), Task 3 (range) and Task 4 (push and clear) |

**Type consistency:** `RepriceMarker(start, version)` and `parse_reprice` (Task 3) are used with those names in Task 4. `Triggers.reprice` and `due(..., reprice)` (Task 3) match their use in Task 4. `LocalRepriceMarker(source_id, reprice_from, version)` (Task 1) is read by Task 2 and deleted with its source. The config key `reprice: {"from", "version"}` (Task 2) is what `parse_reprice` reads (Task 3).

**Known limits left alone:** prices for minutes that no window prices are never removed; the repriced span runs from the window's start, so a late correction rewrites a long span; a typo fixed after the marker was cleared reprices again from the new change, as designed.
