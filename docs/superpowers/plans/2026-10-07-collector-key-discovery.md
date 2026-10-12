# Collector Fleet Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-`Source` collector API key with one shared secret per collector kind (envoy/weather/pricing) plus a discovery endpoint, so adding a new site's source never requires a docker-compose/`.env` edit or restart beyond the next discovery refresh.

**Architecture:** Core API gains three env-configured per-kind secrets (`ENVOY_COLLECTOR_KEY`/`WEATHER_COLLECTOR_KEY`/`PRICING_COLLECTOR_KEY`, like `JWT_SECRET`), a changed (same-shape) `get_source_by_collector_key` dependency that checks the header against the source's kind's secret instead of a per-source hash, and a new `GET /api/v1/collector/sources?kind=` endpoint collectors poll to discover which sources to work on. Each of the three collectors replaces its static per-source/per-list config with a periodic discovery call feeding its existing per-source poll logic.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, pytest (core-api); Python 3.12, httpx, pytest (collectors); React 18 + TypeScript, Vitest (webapp); Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-07-collector-key-discovery-design.md`

## Global Constraints

- Per-kind secrets are plain required env vars read into `Settings` (no DB table, no hashing) — same treatment as `jwt_secret`, compared with `secrets.compare_digest`.
- `get_source_by_collector_key`'s signature and every endpoint that depends on it (`ingestion.py`, `collector_config.py`, `pricing_backfill.py`) stay unchanged — only its internal key check changes.
- The old per-source `collector_api_key_hash` mechanism is fully removed, not kept alongside the new one.
- `GET /collector/sources` is not scoped by owning user — it returns every matching source system-wide, matching every other collector-facing endpoint's existing posture.
- envoy/weather collectors refresh their source list on a separate, slower timer (`DISCOVERY_REFRESH_SECONDS`, default 300) layered on top of their existing `POLL_INTERVAL_SECONDS` poll cadence; they poll discovered sources sequentially (no forked/parallel polling).
- pricing-collector keeps its existing fork-per-site and 5-minute/12-hour tick structure; discovery is simply called once per existing tick instead of a separate timer.

## Review Focus

- A `X-Collector-Key` value valid for one kind (e.g. the weather key) presented against a source of a *different* kind (e.g. an energy source) must 401, not succeed — since the expected key is resolved from the source's own kind, independent of what the caller claims to be. Covered in Task 2.
- A source that becomes disabled or expired must still 403 at the ingestion/config endpoints even though the shared kind-key itself still validates (only `/collector/sources` filtering changed, not the per-source enabled/expiry check). Covered in Task 2.
- `GET /collector/sources` called with an unsupported `kind` value must be a clean 422 (FastAPI enum validation on the query param), never a 500 from an unhandled lookup. Covered in Task 3.
- A discovery-refresh HTTP failure (Core API temporarily unreachable) must not crash a collector process or wipe its last-known-good in-memory source list — it keeps polling the stale list until the next successful refresh. Covered in Tasks 5, 6, 7.
- A source id that disappears from one discovery refresh and reappears in a later one must not leave behind stale per-source in-memory state (e.g. a weather collector's `RainState`) from before the gap, since that could silently resume from stale totals. Covered in Tasks 5, 6.

---

## Task 1: Core API — per-kind collector secrets in Settings

**Files:**
- Modify: `core-api/core_api/config.py`
- Test: `core-api/tests/test_config.py` (create if it doesn't already cover `Settings`/`get_settings` — check first)

**Interfaces:**
- Produces: `Settings.envoy_collector_key: str`, `Settings.weather_collector_key: str`, `Settings.pricing_collector_key: str`, read from `ENVOY_COLLECTOR_KEY`/`WEATHER_COLLECTOR_KEY`/`PRICING_COLLECTOR_KEY`, each required.

- [ ] **Step 1: Check for an existing Settings test file**

Run: `ls core-api/tests/test_config.py 2>/dev/null && cat core-api/tests/test_config.py`

If it exists, you'll extend it in Step 2 below instead of creating a new file — adapt the step accordingly (same assertions, added to the existing file).

- [ ] **Step 2: Write the failing test** (new file `core-api/tests/test_config.py` if none exists)

```python
from __future__ import annotations

import pytest

from core_api.config import get_settings


REQUIRED_ENV = {
    "JWT_SECRET": "test-secret",
    "ENVOY_COLLECTOR_KEY": "envoy-key",
    "WEATHER_COLLECTOR_KEY": "weather-key",
    "PRICING_COLLECTOR_KEY": "pricing-key",
}


def _set_required_env(monkeypatch, **overrides):
    env = {**REQUIRED_ENV, **overrides}
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_get_settings_reads_collector_keys(monkeypatch):
    _set_required_env(monkeypatch)
    settings = get_settings()
    assert settings.envoy_collector_key == "envoy-key"
    assert settings.weather_collector_key == "weather-key"
    assert settings.pricing_collector_key == "pricing-key"


def test_get_settings_raises_if_envoy_collector_key_missing(monkeypatch):
    _set_required_env(monkeypatch, ENVOY_COLLECTOR_KEY=None)
    with pytest.raises(RuntimeError):
        get_settings()


def test_get_settings_raises_if_weather_collector_key_missing(monkeypatch):
    _set_required_env(monkeypatch, WEATHER_COLLECTOR_KEY=None)
    with pytest.raises(RuntimeError):
        get_settings()


def test_get_settings_raises_if_pricing_collector_key_missing(monkeypatch):
    _set_required_env(monkeypatch, PRICING_COLLECTOR_KEY=None)
    with pytest.raises(RuntimeError):
        get_settings()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd core-api && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL — `Settings` has no `envoy_collector_key` attribute (or `get_settings` doesn't raise for the missing var yet).

- [ ] **Step 4: Implement**

In `core-api/core_api/config.py`, add the three fields to the `Settings` dataclass and read them in `get_settings()`, raising the same way `jwt_secret` does for a missing value:

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
    admin_email: str
    envoy_collector_key: str
    weather_collector_key: str
    pricing_collector_key: str


def get_settings() -> Settings:
    jwt_secret = os.environ.get("JWT_SECRET")
    if not jwt_secret:
        raise RuntimeError(
            "JWT_SECRET environment variable must be set (no insecure default is provided)."
        )
    envoy_collector_key = os.environ.get("ENVOY_COLLECTOR_KEY")
    if not envoy_collector_key:
        raise RuntimeError("ENVOY_COLLECTOR_KEY environment variable must be set.")
    weather_collector_key = os.environ.get("WEATHER_COLLECTOR_KEY")
    if not weather_collector_key:
        raise RuntimeError("WEATHER_COLLECTOR_KEY environment variable must be set.")
    pricing_collector_key = os.environ.get("PRICING_COLLECTOR_KEY")
    if not pricing_collector_key:
        raise RuntimeError("PRICING_COLLECTOR_KEY environment variable must be set.")
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
        admin_email=os.environ.get("ADMIN_EMAIL", "admin@localhost"),
        envoy_collector_key=envoy_collector_key,
        weather_collector_key=weather_collector_key,
        pricing_collector_key=pricing_collector_key,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd core-api && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 6: Add the three env vars to every other test in the suite via conftest, so existing tests that call `get_settings()`/boot the app don't start failing**

In `core-api/tests/conftest.py`, add a session-scoped autouse fixture alongside `_set_test_jwt_secret`, and export the three key constants other test files will import in later tasks:

```python
ENVOY_COLLECTOR_KEY = "test-envoy-key"
WEATHER_COLLECTOR_KEY = "test-weather-key"
PRICING_COLLECTOR_KEY = "test-pricing-key"


@pytest.fixture(scope="session", autouse=True)
def _set_test_collector_keys():
    os.environ.setdefault("ENVOY_COLLECTOR_KEY", ENVOY_COLLECTOR_KEY)
    os.environ.setdefault("WEATHER_COLLECTOR_KEY", WEATHER_COLLECTOR_KEY)
    os.environ.setdefault("PRICING_COLLECTOR_KEY", PRICING_COLLECTOR_KEY)
```

Place it directly after the existing `_set_test_jwt_secret` fixture (same file, same pattern).

- [ ] **Step 7: Run the full core-api test suite to confirm nothing else broke**

Run: `cd core-api && .venv/bin/python -m pytest -q`
Expected: PASS, same count as before this task (no new failures — the new env vars are additive and every other `Settings`-dependent test already goes through fixtures that now set them).

- [ ] **Step 8: Commit**

```bash
cd core-api
git add core_api/config.py tests/test_config.py tests/conftest.py
git commit -m "feat: add per-kind collector secrets to core-api Settings"
```

---

## Task 2: Core API — shared-kind-key auth + drop `collector_api_key_hash`

**Files:**
- Modify: `core-api/core_api/deps.py`
- Modify: `core-api/core_api/models/sources.py`
- Create: `core-api/alembic/versions/<new>_drop_collector_api_key_hash.py`
- Modify (remove the now-invalid `collector_api_key_hash=...` kwarg): `core-api/tests/test_cost.py`, `core-api/tests/test_query.py`, `core-api/tests/test_readings_query.py`, `core-api/tests/test_schema_constraints.py`
- Modify (behavioral rewrite to shared kind-key): `core-api/tests/test_deps.py`, `core-api/tests/test_ingestion.py`, `core-api/tests/test_collector_config.py`, `core-api/tests/test_pricing_backfill.py`

**Interfaces:**
- Consumes: `Settings.envoy_collector_key`/`weather_collector_key`/`pricing_collector_key` (Task 1); `tests.conftest.ENVOY_COLLECTOR_KEY`/`WEATHER_COLLECTOR_KEY`/`PRICING_COLLECTOR_KEY` (Task 1).
- Produces: `core_api.deps._KIND_KEY_ATTR: dict[SourceKind, str]` (maps a kind to the `Settings` attribute name backing its key) — imported by Task 3's new discovery endpoint. `get_source_by_collector_key`'s signature is unchanged.

- [ ] **Step 1: Write the failing test proving kind-key auth (add to `test_collector_config.py`)**

Replace the existing `make_energy_source` helper (which generates a per-source raw key) with one that no longer does, and add a test proving cross-kind keys are rejected:

```python
from tests.conftest import ENVOY_COLLECTOR_KEY, PRICING_COLLECTOR_KEY, WEATHER_COLLECTOR_KEY


def make_energy_source(db_url: str, config: dict | None = None) -> int:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email=f"cc-owner-{secrets.token_hex(4)}@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config=config or {"host": "envoy.local", "token": "abc"},
        effective_from=datetime.now(timezone.utc),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id


def test_get_source_config_rejects_a_different_kinds_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    response = client.get(
        f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": WEATHER_COLLECTOR_KEY}
    )
    assert response.status_code == 401
```

Update every other test in `test_collector_config.py` and `make_pricing_source` the same way: drop `collector_api_key_hash`/`raw_key` generation from the helpers (they now return just `source_id`, not a tuple), and replace every `headers={"X-Collector-Key": raw_key}` with the matching constant (`ENVOY_COLLECTOR_KEY` for energy/envoy sources, `PRICING_COLLECTOR_KEY` for amber/pricing sources). `test_get_source_config_wrong_key_rejected` keeps using the literal `"wrong"` string — that still must 401.

- [ ] **Step 2: Run to verify the new test fails**

Run: `cd core-api && .venv/bin/python -m pytest tests/test_collector_config.py -v`
Expected: FAIL — `Source(...)` now raises because `collector_api_key_hash` is still a required column with no value supplied, and `get_source_by_collector_key` still checks the old hash.

- [ ] **Step 3: Implement the deps.py auth change**

In `core-api/core_api/deps.py`, remove the `hashlib` import if nothing else in the file uses it (check first — `_resolve_user_from_token` and friends don't), add `secrets`, and replace `get_source_by_collector_key`:

```python
import secrets
```

```python
_KIND_KEY_ATTR: dict[SourceKind, str] = {
    SourceKind.ENERGY: "envoy_collector_key",
    SourceKind.WEATHER: "weather_collector_key",
    SourceKind.PRICING: "pricing_collector_key",
}


def get_source_by_collector_key(
    source_id: int,
    x_collector_key: str = Header(...),
    db: Session = Depends(get_db_session),
) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this source")
    expected = getattr(get_settings(), _KIND_KEY_ATTR[source.kind])
    if not secrets.compare_digest(x_collector_key, expected):
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this source")
    if not source.enabled:
        raise AppError(403, "source_disabled", "This source is disabled")
    if source.effective_to is not None and source.effective_to <= datetime.datetime.now(datetime.timezone.utc):
        raise AppError(403, "source_expired", "This source's effective period has ended")
    return source
```

Add `from core_api.config import get_settings` to the imports (check it isn't already imported under a different alias).

- [ ] **Step 4: Drop the model column**

In `core-api/core_api/models/sources.py`, delete the `collector_api_key_hash` mapped column and its comment, and the now-unused `# The raw collector API key...` comment above it.

- [ ] **Step 5: Generate and write the Alembic migration**

Run: `cd core-api && .venv/bin/alembic revision -m "drop collector_api_key_hash from sources"`

This prints the generated file path (`alembic/versions/<revision>_drop_collector_api_key_hash_from_sources.py`) and a fresh revision id. Edit that file to set `down_revision = 'a1c9f2e4b7d3'` (the current head) and implement both directions using batch mode (SQLite needs it to drop a column with a unique constraint):

```python
def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('sources') as batch_op:
        batch_op.drop_column('collector_api_key_hash')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('sources') as batch_op:
        batch_op.add_column(sa.Column('collector_api_key_hash', sa.String(64), nullable=False, server_default=''))
        batch_op.create_unique_constraint('uq_sources_collector_api_key_hash', ['collector_api_key_hash'])
```

- [ ] **Step 6: Run the target test to verify it now passes**

Run: `cd core-api && .venv/bin/python -m pytest tests/test_collector_config.py -v`
Expected: PASS

- [ ] **Step 7: Run the full suite and fix every remaining break**

Run: `cd core-api && .venv/bin/python -m pytest -q`
Expected at this point: failures in `test_cost.py`, `test_query.py`, `test_readings_query.py`, `test_schema_constraints.py` (unexpected keyword argument `collector_api_key_hash`), and `test_deps.py`/`test_ingestion.py`/`test_pricing_backfill.py` (same, plus key-comparison assertions that no longer hold under a shared key).

Fix each:

**`test_cost.py`**: in `_make_energy_source` and `_make_pricing_source`, delete the `collector_api_key_hash="cost-energy"` / `collector_api_key_hash="cost-pricing"` lines — nothing else in the file references them.

**`test_query.py`**: in `seed_energy_reading`, delete `collector_api_key_hash="k",`.

**`test_readings_query.py`**: in each of the three `Source(...)` constructions (`collector_api_key_hash="k1"`/`"k2"`/`"k3"`), delete that line.

**`test_schema_constraints.py`**: change `make_source`'s signature from `def make_source(db, site, kind, provider="envoy", key_hash="deadbeef"):` to `def make_source(db, site, kind, provider="envoy"):`, delete the `collector_api_key_hash=key_hash,` line from the `Source(...)` call, and update its three call sites (`test_only_one_weather_source_per_site`, `test_multiple_energy_sources_per_site_allowed`) to drop the now-nonexistent `key_hash="key1"`/`"key2"` arguments.

**`test_deps.py`**: in `test_collector_key_auth`, replace the per-source raw-key generation with the shared constant:

```python
from tests.conftest import ENVOY_COLLECTOR_KEY


def test_collector_key_auth(wired_app):
    app, db_url = wired_app

    @app.get("/sources/{source_id}/ping")
    def ping(source: Source = Depends(get_source_by_collector_key)):
        return {"source_id": source.id}

    ids = seed(db_url)
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    import datetime

    source = Source(
        site_id=ids["site"],
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        effective_from=datetime.datetime.now(datetime.timezone.utc),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()

    client = TestClient(app)
    ok = client.get(f"/sources/{source_id}/ping", headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    assert ok.status_code == 200

    bad = client.get(f"/sources/{source_id}/ping", headers={"X-Collector-Key": "wrong-key"})
    assert bad.status_code == 401

    missing = client.get(f"/sources/{source_id}/ping")
    assert missing.status_code in (401, 422)
```

(Drop the inline `import hashlib`/`import secrets`/`__import__("datetime")` cruft the old version used — a plain top-of-function `import datetime` is enough now that there's no key to generate.)

**`test_ingestion.py`**: at the top, add `from tests.conftest import ENVOY_COLLECTOR_KEY, PRICING_COLLECTOR_KEY, WEATHER_COLLECTOR_KEY` (check which are actually used — energy and pricing helpers exist in this file; add weather's constant only if a weather test needs it). Change `make_energy_source_full`/`make_energy_source` to drop raw-key generation and return just the ids (no more `raw_key` in the returned tuple):

```python
def make_energy_source(db_url: str) -> int:
    _owner_id, _site_id, source_id = make_energy_source_full(db_url)
    return source_id


def make_energy_source_full(db_url: str) -> tuple[int, int, int]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email=f"ing-owner-{secrets.token_hex(4)}@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        effective_from=datetime.now(timezone.utc),
    )
    session.add(source)
    session.commit()
    owner_id, site_id, source_id = owner.id, site.id, source.id
    session.close()
    return owner_id, site_id, source_id
```

Do the same for `make_pricing_source` in this file (drop the raw-key generation, return just `source_id`). Update every call site that unpacked a `(source_id, raw_key)` tuple to just take `source_id`, and replace every `headers={"X-Collector-Key": raw_key}` with `headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY}` (energy) or `PRICING_COLLECTOR_KEY` (pricing) as appropriate.

Rewrite `test_ingest_energy_readings_wrong_key_for_source_rejected` — under the new model two energy sources share the *same* key, so "source A's id with source B's key" is no longer a meaningful negative case (it now succeeds, correctly). Replace it with a test of what's actually still invalid — a different *kind's* key against this endpoint:

```python
def test_ingest_energy_readings_wrong_kind_key_rejected(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    batch = {"readings": [{"ts": "2026-01-01T00:00:00Z", "metric": "production_w", "value": 1.0}]}
    response = client.post(
        f"/api/v1/sources/{source_id}/readings/energy", json=batch, headers={"X-Collector-Key": WEATHER_COLLECTOR_KEY}
    )
    assert response.status_code == 401
```

`test_ingestion_rejected_after_source_disabled` and `test_energy_source_key_rejected_on_weather_endpoint` keep their current assertions (403) — just update them to use `make_energy_source_full`'s new 3-tuple return and `ENVOY_COLLECTOR_KEY` instead of `raw_key`.

**`test_pricing_backfill.py`**: same treatment — `make_pricing_source` and `make_energy_source_for_site` drop raw-key generation and return just `source_id`; every `headers={"X-Collector-Key": raw_key}` becomes `PRICING_COLLECTOR_KEY` (pricing helper) or `ENVOY_COLLECTOR_KEY` (energy helper). `test_create_gap_marker_wrong_key_rejected` keeps using the literal `"wrong"` string.

- [ ] **Step 8: Run the full suite again**

Run: `cd core-api && .venv/bin/python -m pytest -q`
Expected: PASS, full suite green.

- [ ] **Step 9: Commit**

```bash
cd core-api
git add core_api/deps.py core_api/models/sources.py alembic/versions/*_drop_collector_api_key_hash_from_sources.py tests/test_cost.py tests/test_query.py tests/test_readings_query.py tests/test_schema_constraints.py tests/test_deps.py tests/test_ingestion.py tests/test_collector_config.py tests/test_pricing_backfill.py
git commit -m "feat: authenticate collector requests by shared per-kind key, not per-source"
```

---

## Task 3: Core API — discovery endpoint

**Files:**
- Modify: `core-api/core_api/routers/collector_config.py`
- Create: `core-api/core_api/routers/collector.py`
- Modify: `core-api/core_api/schemas.py`
- Modify: `core-api/core_api/main.py`
- Create: `core-api/tests/test_collector_discovery.py`

**Interfaces:**
- Consumes: `core_api.deps._KIND_KEY_ATTR` (Task 2); `tests.conftest.ENVOY_COLLECTOR_KEY`/`WEATHER_COLLECTOR_KEY`/`PRICING_COLLECTOR_KEY` (Task 1).
- Produces: `GET /api/v1/collector/sources?kind=<energy|weather|pricing>` → `list[CollectorSourceOut]` where `CollectorSourceOut = {source_id: int, site_id: int, config: dict}`. `core_api.routers.collector_config.resolve_source_config(db: Session, source: Source) -> dict`, reused by Task 3's own endpoint and by the existing `/sources/{id}/config`.

- [ ] **Step 1: Write the failing test**

Create `core-api/tests/test_collector_discovery.py`:

```python
from __future__ import annotations

import datetime

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, User
from core_api.models.sources import Source, SourceKind
from tests.conftest import ENVOY_COLLECTOR_KEY, PRICING_COLLECTOR_KEY, WEATHER_COLLECTOR_KEY


def make_source(
    db_url: str,
    kind: SourceKind,
    provider: str,
    *,
    enabled: bool = True,
    effective_to: datetime.datetime | None = None,
    config: dict | None = None,
    amber_api_key: str | None = None,
) -> tuple[int, int]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email=f"disc-owner-{id(object())}@example.com", password_hash="x", amber_api_key=amber_api_key)
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=kind,
        provider=provider,
        config=config or {},
        enabled=enabled,
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
        effective_to=effective_to,
    )
    session.add(source)
    session.commit()
    source_id, site_id = source.id, site.id
    session.close()
    return source_id, site_id


def test_discovery_returns_only_matching_kind(client, app_and_db_url):
    _, db_url = app_and_db_url
    energy_id, energy_site = make_source(db_url, SourceKind.ENERGY, "envoy", config={"host": "envoy.local"})
    make_source(db_url, SourceKind.WEATHER, "weatherlink_live")

    response = client.get(
        "/api/v1/collector/sources", params={"kind": "energy"}, headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == [{"source_id": energy_id, "site_id": energy_site, "config": {"host": "envoy.local"}}]


def test_discovery_excludes_disabled_sources(client, app_and_db_url):
    _, db_url = app_and_db_url
    make_source(db_url, SourceKind.WEATHER, "weatherlink_live", enabled=False)

    response = client.get(
        "/api/v1/collector/sources", params={"kind": "weather"}, headers={"X-Collector-Key": WEATHER_COLLECTOR_KEY}
    )
    assert response.status_code == 200
    assert response.json() == []


def test_discovery_excludes_expired_sources(client, app_and_db_url):
    _, db_url = app_and_db_url
    past = datetime.datetime(2020, 6, 1, tzinfo=datetime.timezone.utc)
    make_source(db_url, SourceKind.WEATHER, "weatherlink_live", effective_to=past)

    response = client.get(
        "/api/v1/collector/sources", params={"kind": "weather"}, headers={"X-Collector-Key": WEATHER_COLLECTOR_KEY}
    )
    assert response.status_code == 200
    assert response.json() == []


def test_discovery_rejects_wrong_kind_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    make_source(db_url, SourceKind.ENERGY, "envoy")

    response = client.get(
        "/api/v1/collector/sources", params={"kind": "energy"}, headers={"X-Collector-Key": WEATHER_COLLECTOR_KEY}
    )
    assert response.status_code == 401


def test_discovery_rejects_invalid_kind(client, app_and_db_url):
    response = client.get(
        "/api/v1/collector/sources", params={"kind": "not-a-real-kind"}, headers={"X-Collector-Key": "anything"}
    )
    assert response.status_code == 422


def test_discovery_merges_owner_amber_key_for_amber_provider(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, site_id = make_source(
        db_url, SourceKind.PRICING, "amber", config={"amber_site_id": "abc"}, amber_api_key="psk_token"
    )

    response = client.get(
        "/api/v1/collector/sources", params={"kind": "pricing"}, headers={"X-Collector-Key": PRICING_COLLECTOR_KEY}
    )
    assert response.status_code == 200
    assert response.json() == [
        {"source_id": source_id, "site_id": site_id, "config": {"amber_site_id": "abc", "amber_api_key": "psk_token"}}
    ]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd core-api && .venv/bin/python -m pytest tests/test_collector_discovery.py -v`
Expected: FAIL — 404s (`/api/v1/collector/sources` doesn't exist yet).

- [ ] **Step 3: Extract the shared config-resolution helper**

In `core-api/core_api/routers/collector_config.py`, pull the amber-merge logic out of `get_source_config` into a standalone function both it and the new router will call:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, get_source_by_collector_key
from core_api.models.identity import Site, User
from core_api.models.sources import Source
from core_api.schemas import DiscoveredSourceConfig, SourceConfigOut

router = APIRouter(prefix="/sources/{source_id}", tags=["collector-config"])


def resolve_source_config(db: Session, source: Source) -> dict:
    config = dict(source.config or {})
    if source.provider == "amber":
        site = db.get(Site, source.site_id)
        owner = db.get(User, site.owner_user_id)
        config["amber_api_key"] = owner.amber_api_key
    return config


@router.get("/config", response_model=SourceConfigOut)
def get_source_config(
    source: Source = Depends(get_source_by_collector_key), db: Session = Depends(get_db_session)
) -> SourceConfigOut:
    return SourceConfigOut(config=resolve_source_config(db, source))


@router.patch("/discovered-config", status_code=status.HTTP_204_NO_CONTENT)
def report_discovered_config(
    payload: DiscoveredSourceConfig,
    source: Source = Depends(get_source_by_collector_key),
    db: Session = Depends(get_db_session),
) -> None:
    source.config = {**(source.config or {}), "serial_num": payload.serial_num}
    db.commit()
```

- [ ] **Step 4: Add the `CollectorSourceOut` schema**

In `core-api/core_api/schemas.py`, add near `SourceConfigOut`:

```python
class CollectorSourceOut(BaseModel):
    source_id: int
    site_id: int
    config: dict
```

- [ ] **Step 5: Create the discovery router**

Create `core-api/core_api/routers/collector.py`:

```python
from __future__ import annotations

import datetime
import secrets

from fastapi import APIRouter, Depends, Header
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from core_api.config import get_settings
from core_api.deps import _KIND_KEY_ATTR, get_db_session
from core_api.errors import AppError
from core_api.models.sources import Source, SourceKind
from core_api.routers.collector_config import resolve_source_config
from core_api.schemas import CollectorSourceOut

router = APIRouter(prefix="/collector", tags=["collector-config"])


@router.get("/sources", response_model=list[CollectorSourceOut])
def list_collector_sources(
    kind: SourceKind,
    x_collector_key: str = Header(...),
    db: Session = Depends(get_db_session),
) -> list[CollectorSourceOut]:
    expected = getattr(get_settings(), _KIND_KEY_ATTR[kind])
    if not secrets.compare_digest(x_collector_key, expected):
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this kind")
    now = datetime.datetime.now(datetime.timezone.utc)
    sources = db.execute(
        select(Source).where(
            Source.kind == kind,
            Source.enabled.is_(True),
            or_(Source.effective_to.is_(None), Source.effective_to > now),
        )
    ).scalars().all()
    return [
        CollectorSourceOut(source_id=s.id, site_id=s.site_id, config=resolve_source_config(db, s))
        for s in sources
    ]
```

(`kind: SourceKind` as a plain function parameter, not under `Depends`, makes FastAPI parse it as a query param and validate it against the enum automatically — an invalid value is a 422 before the function body runs, satisfying Review Focus item 3.)

- [ ] **Step 6: Register the router**

In `core-api/core_api/main.py`, add the import and registration alongside the others:

```python
from core_api.routers.collector import router as collector_router
```

```python
app.include_router(collector_router, prefix=api_prefix)
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `cd core-api && .venv/bin/python -m pytest tests/test_collector_discovery.py -v`
Expected: PASS

- [ ] **Step 8: Run the full suite**

Run: `cd core-api && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
cd core-api
git add core_api/routers/collector_config.py core_api/routers/collector.py core_api/schemas.py core_api/main.py tests/test_collector_discovery.py
git commit -m "feat: add GET /collector/sources fleet-discovery endpoint"
```

---

## Task 4: Core API — stop generating a per-source key on creation

**Files:**
- Modify: `core-api/core_api/routers/sources.py`
- Modify: `core-api/core_api/schemas.py`
- Modify: `core-api/tests/test_sources.py`

**Interfaces:**
- Produces: `POST /sites/{id}/sources` now responds with `SourceOut` (no `collector_api_key` field) instead of the retired `SourceCreateOut`.

- [ ] **Step 1: Write the failing test**

In `core-api/tests/test_sources.py`, replace `test_create_source_returns_raw_key_once`:

```python
def test_create_source_does_not_return_a_collector_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    response = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {"host": "envoy.local"}},
        headers=auth(owner_id),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "collector_api_key" not in body
    assert body["kind"] == "energy"
    assert body["provider"] == "envoy"
    assert body["enabled"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd core-api && .venv/bin/python -m pytest tests/test_sources.py -v`
Expected: FAIL — the current response still includes `collector_api_key`.

- [ ] **Step 3: Implement**

In `core-api/core_api/routers/sources.py`: remove the `_hash_key` function, the `import hashlib`, and `import secrets` if nothing else in the file uses `secrets` (check — `create_source` is the only user). Change `create_source`:

```python
@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def create_source(
    payload: SourceCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> Source:
    now = datetime.datetime.now(datetime.timezone.utc)
    if payload.kind == SourceKind.PRICING and _pricing_source_overlaps(db, site.id, None, now, None):
        raise AppError(409, "conflict", "This site already has an active pricing source; close it before adding a new one")
    source = Source(
        site_id=site.id,
        kind=payload.kind,
        provider=payload.provider,
        config=payload.config,
        effective_from=now,
    )
    db.add(source)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "This site already has an active source of this kind") from exc
    db.refresh(source)
    return source
```

Update the import line to drop `SourceCreateOut` from `from core_api.schemas import ...`.

In `core-api/core_api/schemas.py`, delete the `SourceCreateOut` class entirely.

- [ ] **Step 4: Run to verify it passes**

Run: `cd core-api && .venv/bin/python -m pytest tests/test_sources.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `cd core-api && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd core-api
git add core_api/routers/sources.py core_api/schemas.py tests/test_sources.py
git commit -m "feat: stop generating a per-source collector key on creation"
```

---

## Task 5: envoy-collector — discovery-driven multi-source polling

**Files:**
- Modify: `envoy-collector/envoy_collector/config.py`
- Modify: `envoy-collector/envoy_collector/core_api_client.py`
- Modify: `envoy-collector/envoy_collector/main.py`
- Modify: `envoy-collector/tests/test_config.py`
- Modify: `envoy-collector/tests/test_main.py`

**Interfaces:**
- Consumes: `GET /api/v1/collector/sources?kind=energy` (Task 3).
- Produces: `envoy_collector.core_api_client.fetch_collector_sources(base_url: str, collector_key: str) -> list[dict]` where each dict is `{"source_id": int, "site_id": int, "config": dict}`.

- [ ] **Step 1: Write the failing config test**

Replace `envoy-collector/tests/test_config.py` with:

```python
from __future__ import annotations

import pytest

from envoy_collector.config import get_settings


def test_get_settings_reads_env_vars(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000/")
    monkeypatch.setenv("COLLECTOR_KEY", "secret-key")
    monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("DISCOVERY_REFRESH_SECONDS", raising=False)

    settings = get_settings()

    assert settings.core_api_url == "http://localhost:8000"  # trailing slash stripped
    assert settings.collector_key == "secret-key"
    assert settings.poll_interval_seconds == 60
    assert settings.discovery_refresh_seconds == 300


def test_get_settings_respects_custom_intervals(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("DISCOVERY_REFRESH_SECONDS", "120")

    settings = get_settings()
    assert settings.poll_interval_seconds == 30
    assert settings.discovery_refresh_seconds == 120


def test_get_settings_raises_if_required_var_missing(monkeypatch):
    monkeypatch.delenv("CORE_API_URL", raising=False)
    monkeypatch.delenv("COLLECTOR_KEY", raising=False)

    with pytest.raises(RuntimeError):
        get_settings()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd envoy-collector && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL — `Settings` still has `source_id`/`collector_key` in the old shape, no `discovery_refresh_seconds`.

- [ ] **Step 3: Implement the new config shape**

Replace `envoy-collector/envoy_collector/config.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    collector_key: str
    poll_interval_seconds: int
    discovery_refresh_seconds: int


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    collector_key = os.environ.get("COLLECTOR_KEY")
    if not core_api_url or not collector_key:
        raise RuntimeError("CORE_API_URL and COLLECTOR_KEY must both be set")
    return Settings(
        core_api_url=core_api_url.rstrip("/"),
        collector_key=collector_key,
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "60")),
        discovery_refresh_seconds=int(os.environ.get("DISCOVERY_REFRESH_SECONDS", "300")),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd envoy-collector && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Add the discovery client function**

In `envoy-collector/envoy_collector/core_api_client.py`, add:

```python
def fetch_collector_sources(base_url: str, collector_key: str) -> list[dict]:
    response = _request(
        "GET",
        f"{base_url}/api/v1/collector/sources",
        params={"kind": "energy"},
        headers={"X-Collector-Key": collector_key},
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch collector sources: {response.status_code} {response.text}")
    return response.json()
```

Add to `envoy-collector/tests/test_core_api_client.py` (same `respx`-mocked style as the file's other tests): add `fetch_collector_sources` to the existing `from envoy_collector.core_api_client import (...)` block, then:

```python
@respx.mock
def test_fetch_collector_sources_sends_kind_and_collector_key():
    route = respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(
        return_value=httpx.Response(
            200, json=[{"source_id": 1, "site_id": 10, "config": {"host": "envoy.local"}}]
        )
    )
    result = fetch_collector_sources(BASE_URL, "my-key")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    assert route.calls[0].request.url.params["kind"] == "energy"
    assert result == [{"source_id": 1, "site_id": 10, "config": {"host": "envoy.local"}}]


@respx.mock
def test_fetch_collector_sources_raises_on_non_200():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(return_value=httpx.Response(401))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "wrong-key")


@respx.mock
def test_fetch_collector_sources_raises_core_api_error_on_connection_failure():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "my-key")
```

- [ ] **Step 6: Run that test**

Run: `cd envoy-collector && .venv/bin/python -m pytest tests/test_core_api_client.py -v`
Expected: PASS

- [ ] **Step 7: Write the failing main-loop test**

In `envoy-collector/tests/test_main.py`, replace the module-level `SETTINGS` constant and add tests for the new multi-source loop. Every existing `poll_once`/`discover_and_sync_serial` test needs updating for two signature changes: the `SETTINGS` shape itself, and `poll_once`/`discover_and_sync_serial` each gaining a `source_id` parameter (Step 9 below) — update every call site in this file accordingly (e.g. `poll_once(SETTINGS, {...})` becomes `poll_once(SETTINGS, 1, {...})`).

```python
SETTINGS = Settings(
    core_api_url="http://localhost:8000", collector_key="key", poll_interval_seconds=60, discovery_refresh_seconds=300
)
# discovery_refresh_seconds=0 makes main()'s "now - last_refresh >= discovery_refresh_seconds"
# check true on every cycle, without needing to mock time.monotonic -- used by tests below
# that need a refresh to happen every single loop iteration.
SETTINGS_ALWAYS_REFRESH = Settings(
    core_api_url="http://localhost:8000", collector_key="key", poll_interval_seconds=60, discovery_refresh_seconds=0
)
```

Add:

```python
def test_main_polls_every_discovered_source_each_cycle(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(
        main_module,
        "fetch_collector_sources",
        lambda base_url, key: [
            {"source_id": 1, "site_id": 10, "config": {"host": "envoy-a.local", "token": "a"}},
            {"source_id": 2, "site_id": 11, "config": {"host": "envoy-b.local", "token": "b"}},
        ],
    )
    monkeypatch.setattr(main_module, "discover_and_sync_serial", lambda settings, source_id, config: None)
    polled = []
    monkeypatch.setattr(
        main_module, "poll_once", lambda settings, config: (polled.append(config), config)[1]
    )

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        if calls["sleep"] >= 1:
            raise SystemExit  # stop after the first full cycle

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert len(polled) == 2
    assert {c["host"] for c in polled} == {"envoy-a.local", "envoy-b.local"}


def test_main_keeps_last_known_sources_when_discovery_refresh_fails(monkeypatch):
    """Review Focus: a discovery-refresh failure must not crash the process
    or wipe the in-memory source list -- the next poll cycle keeps using the
    last-known-good list. Uses SETTINGS_ALWAYS_REFRESH so a refresh is
    attempted every cycle, making the 2nd cycle's failure actually exercise
    the fallback path (rather than relying on timing)."""
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS_ALWAYS_REFRESH)

    call_count = {"n": 0}

    def fake_fetch(base_url, key):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [{"source_id": 1, "site_id": 10, "config": {"host": "envoy-a.local", "token": "a"}}]
        raise CoreApiError("core-api unreachable")

    monkeypatch.setattr(main_module, "fetch_collector_sources", fake_fetch)
    monkeypatch.setattr(main_module, "discover_and_sync_serial", lambda settings, source_id, config: None)
    polled = []
    monkeypatch.setattr(
        main_module, "poll_once", lambda settings, source_id, config: (polled.append(config), config)[1]
    )

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert len(polled) == 2  # both cycles still polled the one known source, despite the 2nd refresh failing
```

- [ ] **Step 8: Run to verify these fail**

Run: `cd envoy-collector && .venv/bin/python -m pytest tests/test_main.py -v`
Expected: FAIL — `main_module` has no `fetch_collector_sources` name yet, `SETTINGS_ALWAYS_REFRESH` doesn't exist, and `main()` doesn't loop over multiple sources.

- [ ] **Step 9: Implement the multi-source main loop**

Replace `envoy-collector/envoy_collector/main.py`'s imports, `discover_and_sync_serial`, and `main()`:

```python
from __future__ import annotations

import datetime
import logging
import time

from envoy_collector.config import Settings, get_settings
from envoy_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_source_config,
    push_energy_readings,
    report_discovered_config,
)
from envoy_collector.envoy_client import EnvoyAuthError, fetch_battery_power, fetch_production, fetch_serial_number
from envoy_collector.mapping import map_battery_readings, map_production_readings

logger = logging.getLogger("envoy_collector")


def discover_and_sync_serial(settings: Settings, source_id: int, config: dict) -> None:
    try:
        serial = fetch_serial_number(config["host"])
    except Exception:
        logger.exception("Failed to fetch Envoy serial number from /info.xml for source %s", source_id)
        return
    if config.get("serial_num") != serial:
        try:
            report_discovered_config(settings.core_api_url, source_id, settings.collector_key, serial)
            logger.info("Reported discovered Envoy serial number %s for source %s", serial, source_id)
        except CoreApiError:
            logger.exception("Failed to report discovered serial number to Core API for source %s", source_id)
```

(`_renewal_message`, `_fetch_battery_readings_or_empty`, `_fetch_and_map` are unchanged — leave them exactly as they are.)

`poll_once` changes only in how it refetches config on a 401 — it now needs the `source_id` to know which source to refetch:

```python
def poll_once(settings: Settings, source_id: int, config: dict) -> dict:
    """Runs one poll cycle for one source. Returns the config to use next
    cycle for this source (unchanged, unless an Envoy 401 triggered a
    successful refetch)."""
    ts = datetime.datetime.now(datetime.timezone.utc).replace(second=0, microsecond=0)
    try:
        readings = _fetch_and_map(config, ts)
    except EnvoyAuthError:
        logger.warning("Envoy rejected our token for source %s; refetching config from Core API", source_id)
        try:
            config = fetch_source_config(settings.core_api_url, source_id, settings.collector_key)
        except CoreApiError:
            logger.exception("Failed to refetch source config after Envoy auth failure for source %s", source_id)
            return config
        try:
            readings = _fetch_and_map(config, ts)
        except EnvoyAuthError:
            logger.error(_renewal_message(config))
            return config
        except Exception:
            logger.exception("Failed to poll Envoy this cycle (after config refetch) for source %s", source_id)
            return config
    except Exception:
        logger.exception("Failed to poll Envoy this cycle for source %s", source_id)
        return config

    try:
        push_energy_readings(settings.core_api_url, source_id, settings.collector_key, readings)
    except CoreApiError:
        logger.exception("Failed to push readings to Core API this cycle for source %s", source_id)
    return config
```

Add the discovery-refresh helper and rewrite `main()`:

```python
def _refresh_sources(settings: Settings, known: dict[int, dict]) -> dict[int, dict]:
    """Fetches the current fleet list and returns an updated {source_id:
    config} dict, carrying over each source's last-known config unchanged
    (poll_once/discover_and_sync_serial are the only things that mutate a
    source's config in memory, via the 401-refetch path -- discovery itself
    only tells us *which* sources exist, not their live token state)."""
    try:
        discovered = fetch_collector_sources(settings.core_api_url, settings.collector_key)
    except CoreApiError:
        logger.exception("Failed to refresh source list from Core API; keeping last-known sources")
        return known
    refreshed: dict[int, dict] = {}
    for entry in discovered:
        source_id = entry["source_id"]
        refreshed[source_id] = known.get(source_id, entry["config"])
        if source_id not in known:
            discover_and_sync_serial(settings, source_id, refreshed[source_id])
    return refreshed


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    sources: dict[int, dict] = {}
    while not sources:
        sources = _refresh_sources(settings, sources)
        if not sources:
            logger.warning("No energy sources discovered yet; retrying in %s seconds", settings.poll_interval_seconds)
            time.sleep(settings.poll_interval_seconds)
    last_refresh = time.monotonic()

    while True:
        now = time.monotonic()
        if now - last_refresh >= settings.discovery_refresh_seconds:
            sources = _refresh_sources(settings, sources)
            last_refresh = now

        for source_id, config in list(sources.items()):
            try:
                sources[source_id] = poll_once(settings, source_id, config)
            except Exception:
                logger.exception("Unexpected error in poll cycle for source %s; continuing", source_id)
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
```

Remove `_fetch_initial_config` entirely — its retry-forever-at-startup responsibility is now covered by `main()`'s `while not sources` loop above, which retries discovery itself until at least one source shows up. Delete `test_fetch_initial_config_retries_until_success` from `test_main.py` (the behavior it covered is superseded) and add in its place:

```python
def test_main_retries_discovery_until_a_source_appears(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    attempts = {"n": 0}

    def fake_fetch(base_url, key):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return []
        return [{"source_id": 1, "site_id": 10, "config": {"host": "envoy.local", "token": "a"}}]

    monkeypatch.setattr(main_module, "fetch_collector_sources", fake_fetch)
    monkeypatch.setattr(main_module, "discover_and_sync_serial", lambda settings, source_id, config: None)
    monkeypatch.setattr(main_module, "poll_once", lambda settings, source_id, config: config)
    sleeps = []
    monkeypatch.setattr(main_module.time, "sleep", lambda s: (sleeps.append(s), (_ for _ in ()).throw(SystemExit) if len(sleeps) > 1 else None)[-1])

    with pytest.raises(SystemExit):
        main_module.main()

    assert attempts["n"] >= 3
```

Also update `test_discover_and_sync_serial_reports_when_changed`, `test_discover_and_sync_serial_skips_report_when_unchanged`, and `test_discover_and_sync_serial_does_not_raise_when_envoy_unreachable` to call `main_module.discover_and_sync_serial(SETTINGS, 1, {...})` (now takes a `source_id` positional argument between `SETTINGS` and the config dict) — the `report_discovered_config` lambda signatures in the first of these (`lambda base_url, source_id, key, serial_num: ...`) already match and need no change.

Update every existing `main_module.poll_once(SETTINGS, {...})` call by inserting a literal `1` as the second positional argument — `main_module.poll_once(SETTINGS, 1, {...})` — leaving the config dict exactly as it is. This applies to every call in: `test_poll_once_pushes_mapped_readings_on_success`, `test_poll_once_floors_timestamp_to_whole_minute`, `test_poll_once_refetches_config_once_on_envoy_401_then_succeeds`, `test_poll_once_logs_and_returns_unchanged_config_when_still_401_after_refetch`, `test_poll_once_skips_cycle_when_envoy_unreachable`, `test_poll_once_skips_cycle_when_production_response_is_malformed`, `test_poll_once_still_pushes_production_when_battery_fetch_raises_non_auth_error`, and `test_poll_once_skips_push_but_does_not_raise_when_core_api_unreachable`. None of these tests' assertions change — only the call arity. The `fetch_source_config` lambda signatures used inside `test_poll_once_refetches_config_once_on_envoy_401_then_succeeds` and `test_poll_once_logs_and_returns_unchanged_config_when_still_401_after_refetch` (`lambda base_url, source_id, key: {...}`) and every `push_energy_readings` lambda (`lambda base_url, source_id, key, readings: ...`) already match the unchanged shape — no change needed there.

Update `test_main_loop_survives_unexpected_exception_from_poll_once`: it currently monkeypatches `_fetch_initial_config`, which no longer exists. Replace with:

```python
def test_main_loop_survives_unexpected_exception_from_poll_once(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(
        main_module, "fetch_collector_sources", lambda base_url, key: [{"source_id": 1, "site_id": 10, "config": {"host": "envoy.local", "token": "abc"}}]
    )
    monkeypatch.setattr(main_module, "discover_and_sync_serial", lambda settings, source_id, config: None)
    monkeypatch.setattr(main_module, "poll_once", lambda settings, source_id, config: (_ for _ in ()).throw(RuntimeError("boom")))

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert calls["sleep"] == 2
```

- [ ] **Step 10: Run to verify everything passes**

Run: `cd envoy-collector && .venv/bin/python -m pytest -q`
Expected: PASS, full suite green.

- [ ] **Step 11: Commit**

```bash
cd envoy-collector
git add envoy_collector/config.py envoy_collector/core_api_client.py envoy_collector/main.py tests/test_config.py tests/test_main.py tests/test_core_api_client.py
git commit -m "feat: poll all discovered energy sources via shared collector key"
```

---

## Task 6: weather-collector — discovery-driven multi-source polling

**Files:**
- Modify: `weather-collector/weather_collector/config.py`
- Modify: `weather-collector/weather_collector/core_api_client.py`
- Modify: `weather-collector/weather_collector/main.py`
- Modify: `weather-collector/tests/test_config.py`
- Modify: `weather-collector/tests/test_main.py`

**Interfaces:**
- Consumes: `GET /api/v1/collector/sources?kind=weather` (Task 3).
- Produces: `weather_collector.core_api_client.fetch_collector_sources(base_url: str, collector_key: str) -> list[dict]`.

This follows the exact same shape as Task 5, with two differences: weather has no token-refresh-on-401 path (so no `discover_and_sync_serial` equivalent), and each source carries its own `RainState` (per Review Focus item 5 — a source that drops out and comes back must not resume from a stale `RainState`).

- [ ] **Step 1: Write the failing config test**

Replace `weather-collector/tests/test_config.py` with the same shape as envoy-collector's Task 5 Step 1, but without `source_id`:

```python
from __future__ import annotations

import pytest

from weather_collector.config import get_settings


def test_get_settings_reads_env_vars(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000/")
    monkeypatch.setenv("COLLECTOR_KEY", "secret-key")
    monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("DISCOVERY_REFRESH_SECONDS", raising=False)

    settings = get_settings()

    assert settings.core_api_url == "http://localhost:8000"
    assert settings.collector_key == "secret-key"
    assert settings.poll_interval_seconds == 60
    assert settings.discovery_refresh_seconds == 300


def test_get_settings_respects_custom_intervals(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("DISCOVERY_REFRESH_SECONDS", "120")

    settings = get_settings()
    assert settings.poll_interval_seconds == 30
    assert settings.discovery_refresh_seconds == 120


def test_get_settings_raises_if_required_var_missing(monkeypatch):
    monkeypatch.delenv("CORE_API_URL", raising=False)
    monkeypatch.delenv("COLLECTOR_KEY", raising=False)

    with pytest.raises(RuntimeError):
        get_settings()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd weather-collector && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Replace `weather-collector/weather_collector/config.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    collector_key: str
    poll_interval_seconds: int
    discovery_refresh_seconds: int


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    collector_key = os.environ.get("COLLECTOR_KEY")
    if not core_api_url or not collector_key:
        raise RuntimeError("CORE_API_URL and COLLECTOR_KEY must both be set")
    return Settings(
        core_api_url=core_api_url.rstrip("/"),
        collector_key=collector_key,
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "60")),
        discovery_refresh_seconds=int(os.environ.get("DISCOVERY_REFRESH_SECONDS", "300")),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd weather-collector && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Add the discovery client function and its test**

In `weather-collector/weather_collector/core_api_client.py`, add:

```python
def fetch_collector_sources(base_url: str, collector_key: str) -> list[dict]:
    response = _request(
        "GET",
        f"{base_url}/api/v1/collector/sources",
        params={"kind": "weather"},
        headers={"X-Collector-Key": collector_key},
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch collector sources: {response.status_code} {response.text}")
    return response.json()
```

Add to `weather-collector/tests/test_core_api_client.py` (same `respx`-mocked style as the file's other tests): add `fetch_collector_sources` to the existing `from weather_collector.core_api_client import ...` line, then:

```python
@respx.mock
def test_fetch_collector_sources_sends_kind_and_collector_key():
    route = respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(
        return_value=httpx.Response(
            200, json=[{"source_id": 1, "site_id": 10, "config": {"host": "weatherlink.local"}}]
        )
    )
    result = fetch_collector_sources(BASE_URL, "my-key")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    assert route.calls[0].request.url.params["kind"] == "weather"
    assert result == [{"source_id": 1, "site_id": 10, "config": {"host": "weatherlink.local"}}]


@respx.mock
def test_fetch_collector_sources_raises_on_non_200():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(return_value=httpx.Response(401))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "wrong-key")


@respx.mock
def test_fetch_collector_sources_raises_core_api_error_on_connection_failure():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "my-key")
```

- [ ] **Step 6: Run that test**

Run: `cd weather-collector && .venv/bin/python -m pytest tests/test_core_api_client.py -v`
Expected: PASS

- [ ] **Step 7: Write the failing main-loop test**

In `weather-collector/tests/test_main.py`, check its current `SETTINGS`/fixture shape first (`cat weather-collector/tests/test_main.py`), then replace it with the new `Settings(core_api_url=..., collector_key=..., poll_interval_seconds=..., discovery_refresh_seconds=...)` shape (dropping `source_id`), plus an always-refresh variant for tests that need a refresh every cycle without mocking `time.monotonic`:

```python
SETTINGS = Settings(
    core_api_url="http://localhost:8000", collector_key="key", poll_interval_seconds=60, discovery_refresh_seconds=300
)
SETTINGS_ALWAYS_REFRESH = Settings(
    core_api_url="http://localhost:8000", collector_key="key", poll_interval_seconds=60, discovery_refresh_seconds=0
)
```

Add two tests mirroring envoy-collector's Task 5 Step 7, adapted to weather's `poll_once` signature (`settings, source_id, config, rain_state` — see Step 9):

```python
def test_main_polls_every_discovered_source_each_cycle(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(
        main_module,
        "fetch_collector_sources",
        lambda base_url, key: [
            {"source_id": 1, "site_id": 10, "config": {"host": "wl-a.local"}},
            {"source_id": 2, "site_id": 11, "config": {"host": "wl-b.local"}},
        ],
    )
    polled = []
    monkeypatch.setattr(
        main_module, "poll_once", lambda settings, source_id, config, rain_state: polled.append(config)
    )

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        if calls["sleep"] >= 1:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert len(polled) == 2
    assert {c["host"] for c in polled} == {"wl-a.local", "wl-b.local"}


def test_main_keeps_last_known_sources_when_discovery_refresh_fails(monkeypatch):
    """Review Focus: a discovery-refresh failure must not crash the process
    or wipe the in-memory source list. SETTINGS_ALWAYS_REFRESH makes a
    refresh attempted every cycle, so the 2nd cycle's failure actually
    exercises the fallback path."""
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS_ALWAYS_REFRESH)

    call_count = {"n": 0}

    def fake_fetch(base_url, key):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [{"source_id": 1, "site_id": 10, "config": {"host": "wl.local"}}]
        raise CoreApiError("core-api unreachable")

    monkeypatch.setattr(main_module, "fetch_collector_sources", fake_fetch)
    polled = []
    monkeypatch.setattr(
        main_module, "poll_once", lambda settings, source_id, config, rain_state: polled.append(config)
    )

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert len(polled) == 2  # both cycles still polled the one known source, despite the 2nd refresh failing
```

Also add a test for each discovered source's `RainState` lifecycle: created once when the source first appears and reused across cycles while it keeps appearing, but a source that disappears and later reappears starts a **fresh** `RainState` (its old rain baseline is gone, per Review Focus item 5 — resuming from a stale baseline would silently misreport rain totals):

```python
def test_main_gives_a_reappearing_source_a_fresh_rain_state(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS_ALWAYS_REFRESH)
    discovery_results = [
        [{"source_id": 1, "site_id": 10, "config": {"host": "wl.local"}}],
        [],  # source disappears
        [{"source_id": 1, "site_id": 10, "config": {"host": "wl.local"}}],  # reappears
    ]
    monkeypatch.setattr(main_module, "fetch_collector_sources", lambda base_url, key: discovery_results.pop(0))
    seen_states = []
    monkeypatch.setattr(
        main_module,
        "poll_once",
        lambda settings, source_id, config, rain_state: seen_states.append(rain_state),
    )

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        if calls["sleep"] >= 3:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert len(seen_states) == 2  # cycle 2 had no sources to poll
    assert seen_states[0] is not seen_states[1]  # the reappearing source got a brand-new RainState
```

- [ ] **Step 8: Run to verify these fail**

Run: `cd weather-collector && .venv/bin/python -m pytest tests/test_main.py -v`
Expected: FAIL

- [ ] **Step 9: Implement the multi-source main loop**

Replace `weather-collector/weather_collector/main.py`'s imports and `main()`, and change `poll_once`'s signature to take `source_id` for logging context (mirroring envoy-collector):

```python
from __future__ import annotations

import datetime
import logging
import time

from weather_collector.config import Settings, get_settings
from weather_collector.core_api_client import CoreApiError, fetch_collector_sources, push_weather_readings
from weather_collector.mapping import RainState, map_barometer_readings, map_iss_readings
from weather_collector.weatherlink_client import fetch_current_conditions

logger = logging.getLogger("weather_collector")


def _map_iss_or_empty(conditions: list[dict], ts: datetime.datetime, rain_state: RainState) -> list[dict]:
    try:
        iss = next(c for c in conditions if c["data_structure_type"] == 1)
        return map_iss_readings(iss, ts, rain_state)
    except Exception:
        logger.exception("Failed to find/parse ISS (outdoor) data this cycle; continuing without it")
        return []


def _map_barometer_or_empty(conditions: list[dict], ts: datetime.datetime) -> list[dict]:
    try:
        barometer = next(c for c in conditions if c["data_structure_type"] == 3)
        return map_barometer_readings(barometer, ts)
    except Exception:
        logger.exception("Failed to find/parse barometer data this cycle; continuing without it")
        return []


def poll_once(settings: Settings, source_id: int, config: dict, rain_state: RainState) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).replace(second=0, microsecond=0)
    try:
        data = fetch_current_conditions(config["host"])
        conditions = data["data"]["conditions"]
    except Exception:
        logger.exception("Failed to poll WeatherLink this cycle for source %s", source_id)
        return

    prev_rain_baseline = rain_state.last_daily_mm
    readings = _map_iss_or_empty(conditions, ts, rain_state) + _map_barometer_or_empty(conditions, ts)
    if not readings:
        return

    try:
        push_weather_readings(settings.core_api_url, source_id, settings.collector_key, readings)
    except Exception:
        rain_state.last_daily_mm = prev_rain_baseline
        logger.exception("Failed to push readings to Core API this cycle for source %s", source_id)


def _refresh_sources(settings: Settings, known: dict[int, tuple[dict, RainState]]) -> dict[int, tuple[dict, RainState]]:
    try:
        discovered = fetch_collector_sources(settings.core_api_url, settings.collector_key)
    except CoreApiError:
        logger.exception("Failed to refresh source list from Core API; keeping last-known sources")
        return known
    refreshed: dict[int, tuple[dict, RainState]] = {}
    for entry in discovered:
        source_id = entry["source_id"]
        if source_id in known:
            refreshed[source_id] = (entry["config"], known[source_id][1])
        else:
            refreshed[source_id] = (entry["config"], RainState())
    return refreshed


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    sources: dict[int, tuple[dict, RainState]] = {}
    while not sources:
        sources = _refresh_sources(settings, sources)
        if not sources:
            logger.warning("No weather sources discovered yet; retrying in %s seconds", settings.poll_interval_seconds)
            time.sleep(settings.poll_interval_seconds)
    last_refresh = time.monotonic()

    while True:
        now = time.monotonic()
        if now - last_refresh >= settings.discovery_refresh_seconds:
            sources = _refresh_sources(settings, sources)
            last_refresh = now

        for source_id, (config, rain_state) in list(sources.items()):
            try:
                poll_once(settings, source_id, config, rain_state)
            except Exception:
                logger.exception("Unexpected error in poll cycle for source %s; continuing", source_id)
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
```

Check `weather_collector/mapping.py`'s `RainState` — confirm it's a plain class with a no-arg constructor (`RainState()`) and a `last_daily_mm` attribute, matching what's used above (read the file if unsure before finalizing this step).

Update every existing `main_module.poll_once(SETTINGS, {...}, <rain_state_expr>)` call already in `test_main.py` by inserting a literal `1` as the second positional argument — `main_module.poll_once(SETTINGS, 1, {...}, <rain_state_expr>)` — leaving the config dict and rain-state expression exactly as they are. This mechanical change applies to every call in: `test_poll_once_pushes_iss_and_barometer_readings_on_success`, `test_poll_once_floors_timestamp_to_whole_minute`, `test_poll_once_isolates_iss_failure_from_barometer`, `test_poll_once_isolates_entirely_missing_iss_entry`, `test_poll_once_isolates_missing_barometer_entry`, `test_poll_once_skips_cycle_when_weatherlink_unreachable`, `test_poll_once_logs_but_does_not_raise_when_core_api_unreachable`, `test_rain_state_persists_across_poll_once_calls` (both calls), `test_failed_push_does_not_advance_rain_baseline`, and `test_rain_delta_recovers_after_failed_push` (all three calls). None of these tests' assertions change — only the call arity.

Delete `_fetch_initial_config` from `main.py` entirely (superseded by `main()`'s own `while not sources: sources = _refresh_sources(...)` retry loop) and delete `test_fetch_initial_config_retries_until_success` from `test_main.py`, replacing it with:

```python
def test_main_retries_discovery_until_a_source_appears(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    attempts = {"n": 0}

    def fake_fetch(base_url, key):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return []
        return [{"source_id": 1, "site_id": 10, "config": {"host": "weatherlink.local"}}]

    monkeypatch.setattr(main_module, "fetch_collector_sources", fake_fetch)
    monkeypatch.setattr(main_module, "poll_once", lambda settings, source_id, config, rain_state: None)
    sleeps = []

    def _fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 1:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert attempts["n"] >= 3
```

Replace `test_main_loop_survives_unexpected_exception_from_poll_once` (it currently monkeypatches the now-deleted `_fetch_initial_config`):

```python
def test_main_loop_survives_unexpected_exception_from_poll_once(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(
        main_module,
        "fetch_collector_sources",
        lambda base_url, key: [{"source_id": 1, "site_id": 10, "config": {"host": "weatherlink.local"}}],
    )
    monkeypatch.setattr(
        main_module, "poll_once", lambda settings, source_id, config, rain_state: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert calls["sleep"] == 2
```

Every `push_weather_readings` lambda already matches the unchanged `lambda base_url, source_id, key, readings: ...` shape — no change needed there.

- [ ] **Step 10: Run to verify everything passes**

Run: `cd weather-collector && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
cd weather-collector
git add weather_collector/config.py weather_collector/core_api_client.py weather_collector/main.py tests/test_config.py tests/test_main.py tests/test_core_api_client.py
git commit -m "feat: poll all discovered weather sources via shared collector key"
```

---

## Task 7: pricing-collector — discovery-driven live-poll and backfill

**Files:**
- Modify: `pricing-collector/pricing_collector/config.py`
- Modify: `pricing-collector/pricing_collector/core_api_client.py`
- Modify: `pricing-collector/pricing_collector/live_poll.py`
- Modify: `pricing-collector/pricing_collector/backfill.py`
- Modify: `pricing-collector/pricing_collector/main.py`
- Modify: `pricing-collector/tests/test_config.py`, `tests/test_live_poll.py`, `tests/test_backfill.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `GET /api/v1/collector/sources?kind=pricing` (Task 3).
- Produces: `pricing_collector.core_api_client.fetch_collector_sources(base_url: str, collector_key: str) -> list[dict]`. `PricingSource` drops its `collector_key` field (now just `source_id: int`); every call site that used `s.collector_key` now uses a separately-passed shared `collector_key` argument.

Unlike envoy/weather, pricing already loops over a list of sources on a fixed tick (`run_live_poll_forever`, every aligned 5-minute tick) and a fixed interval (`run_backfill_forever`, every 12 hours) — discovery is simply called once at the top of each existing loop body, reusing that cadence instead of adding a new timer.

- [ ] **Step 1: Write the failing config test**

Replace `pricing-collector/tests/test_config.py`:

```python
from __future__ import annotations

import pytest

from pricing_collector.config import get_settings


def test_get_settings_reads_env_vars(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000/")
    monkeypatch.setenv("COLLECTOR_KEY", "secret-key")

    settings = get_settings()

    assert settings.core_api_url == "http://localhost:8000"
    assert settings.collector_key == "secret-key"


def test_get_settings_raises_when_core_api_url_missing(monkeypatch):
    monkeypatch.delenv("CORE_API_URL", raising=False)
    monkeypatch.setenv("COLLECTOR_KEY", "k")
    with pytest.raises(RuntimeError):
        get_settings()


def test_get_settings_raises_when_collector_key_missing(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.delenv("COLLECTOR_KEY", raising=False)
    with pytest.raises(RuntimeError):
        get_settings()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pricing-collector && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Replace `pricing-collector/pricing_collector/config.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PricingSource:
    source_id: int


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    collector_key: str


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    collector_key = os.environ.get("COLLECTOR_KEY")
    if not core_api_url or not collector_key:
        raise RuntimeError("CORE_API_URL and COLLECTOR_KEY must both be set")
    return Settings(core_api_url=core_api_url.rstrip("/"), collector_key=collector_key)
```

`PricingSource` is kept (now just a thin wrapper around `source_id`) so `live_poll.py`/`backfill.py`'s existing `list[PricingSource]` typing doesn't need to change shape at every call site — only what's inside each entry does.

- [ ] **Step 4: Run to verify it passes**

Run: `cd pricing-collector && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Add the discovery client function and its test**

In `pricing-collector/pricing_collector/core_api_client.py`, add:

```python
def fetch_collector_sources(base_url: str, collector_key: str) -> list[PricingSource]:
    response = _request(
        "GET",
        f"{base_url}/api/v1/collector/sources",
        params={"kind": "pricing"},
        headers={"X-Collector-Key": collector_key},
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch collector sources: {response.status_code} {response.text}")
    return [PricingSource(source_id=entry["source_id"]) for entry in response.json()]
```

Add `from pricing_collector.config import PricingSource` to this file's imports. Add to `pricing-collector/tests/test_core_api_client.py` (same `respx`-mocked style as the file's other tests): add `fetch_collector_sources` and `PricingSource` to the existing imports, then:

```python
@respx.mock
def test_fetch_collector_sources_sends_kind_and_collector_key():
    route = respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(
        return_value=httpx.Response(
            200, json=[{"source_id": 1, "site_id": 10, "config": {"amber_site_id": "abc"}}]
        )
    )
    result = fetch_collector_sources(BASE_URL, "my-key")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    assert route.calls[0].request.url.params["kind"] == "pricing"
    assert result == [PricingSource(source_id=1)]


@respx.mock
def test_fetch_collector_sources_raises_on_non_200():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(return_value=httpx.Response(401))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "wrong-key")


@respx.mock
def test_fetch_collector_sources_raises_core_api_error_on_connection_failure():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "my-key")
```

- [ ] **Step 6: Run that test**

Run: `cd pricing-collector && .venv/bin/python -m pytest tests/test_core_api_client.py -v`
Expected: PASS

- [ ] **Step 7: Write the failing live_poll test**

In `pricing-collector/tests/test_live_poll.py`: `poll_one_site`'s own signature and tests (`test_poll_one_site_*`) are unchanged — it already takes `(core_api_url, source_id, collector_key)` positionally, and that stays true (the `collector_key` passed in is now the shared one, but the function doesn't care where it came from). Only `run_live_poll_forever`'s tests change, since its signature changes from `(core_api_url, sources: list[PricingSource])` (a static list, each entry carrying its own key) to `(core_api_url, collector_key)` (no `sources` parameter at all — it now calls `fetch_collector_sources` itself every tick; see Step 9). Update:

```python
def test_run_live_poll_forever_forks_one_child_per_source(monkeypatch):
    FakeProcess.instances = []
    monkeypatch.setattr(live_poll.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now: now)
    monkeypatch.setattr(
        live_poll,
        "fetch_collector_sources",
        lambda base_url, key: [PricingSource(source_id=1), PricingSource(source_id=2)],
    )

    calls = {"count": 0}

    def _fake_sleep_then_stop(seconds):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep_then_stop)

    with pytest.raises(SystemExit):
        live_poll.run_live_poll_forever(BASE_URL, "shared-key")

    assert len(FakeProcess.instances) == 2
    assert {p.args[1] for p in FakeProcess.instances} == {1, 2}
    assert all(p.args[2] == "shared-key" for p in FakeProcess.instances)
    assert all(p.started for p in FakeProcess.instances)
    assert all(p.joined_with_timeout == live_poll.CHILD_JOIN_TIMEOUT_SECONDS for p in FakeProcess.instances)
```

Update `FakeProcess.__init__` in this file to accept the same `(target, args)` shape it already does — no change needed there, since `args` just grows to include the shared key (`(core_api_url, source_id, collector_key)`), still a single `args` tuple.

Replace `test_run_live_poll_forever_terminates_child_that_does_not_join_in_time` and `test_run_live_poll_forever_survives_unexpected_exception`:

```python
def test_run_live_poll_forever_terminates_child_that_does_not_join_in_time(monkeypatch):
    class StuckProcess(FakeProcess):
        def is_alive(self):
            return not self.terminated

        def terminate(self):
            self.terminated = True

    FakeProcess.instances = []
    monkeypatch.setattr(live_poll.multiprocessing, "Process", StuckProcess)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now: now)
    monkeypatch.setattr(live_poll, "fetch_collector_sources", lambda base_url, key: [PricingSource(source_id=1)])

    calls = {"count": 0}

    def _fake_sleep_then_stop(seconds):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep_then_stop)

    with pytest.raises(SystemExit):
        live_poll.run_live_poll_forever(BASE_URL, "shared-key")

    assert FakeProcess.instances[0].terminated is True


def test_run_live_poll_forever_survives_unexpected_exception(monkeypatch):
    def _raise_process(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(live_poll.multiprocessing, "Process", _raise_process)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now: now)
    monkeypatch.setattr(live_poll, "fetch_collector_sources", lambda base_url, key: [PricingSource(source_id=1)])

    calls = {"count": 0}

    def _fake_sleep_then_stop(seconds):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep_then_stop)

    with pytest.raises(SystemExit):
        live_poll.run_live_poll_forever(BASE_URL, "shared-key")

    assert calls["count"] == 2
```

Add one new test for the discovery-failure path (no equivalent existed before, since the source list used to be static — this is a live-poll-specific instance of Review Focus item 4):

```python
def test_run_live_poll_forever_skips_tick_when_discovery_fails(monkeypatch):
    FakeProcess.instances = []
    monkeypatch.setattr(live_poll.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now: now)

    def _raise(base_url, key):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(live_poll, "fetch_collector_sources", _raise)

    calls = {"count": 0}

    def _fake_sleep_then_stop(seconds):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep_then_stop)

    with pytest.raises(SystemExit):
        live_poll.run_live_poll_forever(BASE_URL, "shared-key")

    assert FakeProcess.instances == []  # no children forked this tick, but the loop kept going
```

Update `test_run_live_poll_forever_real_fork_isolates_sibling_crash`: the existing module-level `_real_child_target(core_api_url, source_id, collector_key)` helper (defined earlier in this file, above `test_run_live_poll_forever_real_fork_isolates_sibling_crash`) already dispatches purely on `source_id` and writes to whatever path `collector_key` names — it needs no changes. Since `collector_key` is now one shared value for every forked child in a run (not per-source), passing the marker path itself as that shared value still produces the same emergent behavior: source 1 crashes before ever touching `collector_key`, source 2 writes to it.

```python
def test_run_live_poll_forever_real_fork_isolates_sibling_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(live_poll, "poll_one_site", _real_child_target)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now: now)
    monkeypatch.setattr(
        live_poll,
        "fetch_collector_sources",
        lambda base_url, key: [PricingSource(source_id=1), PricingSource(source_id=2)],
    )

    marker_path = tmp_path / "sibling_done.txt"

    calls = {"n": 0}

    def _fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        live_poll.run_live_poll_forever(BASE_URL, str(marker_path))

    assert marker_path.read_text() == "done"
```

- [ ] **Step 8: Run to verify these fail**

Run: `cd pricing-collector && .venv/bin/python -m pytest tests/test_live_poll.py -v`
Expected: FAIL

- [ ] **Step 9: Implement**

In `pricing-collector/pricing_collector/live_poll.py`, add the import and change `run_live_poll_forever`:

```python
from pricing_collector.core_api_client import CoreApiError, fetch_collector_sources, fetch_source_config, push_price_intervals
```

```python
def run_live_poll_forever(core_api_url: str, collector_key: str) -> None:
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            tick = next_aligned_tick(now)
            time.sleep(max((tick - now).total_seconds(), 0))

            try:
                sources = fetch_collector_sources(core_api_url, collector_key)
            except CoreApiError:
                logger.exception("Failed to refresh pricing source list this tick; skipping")
                continue

            children = [
                multiprocessing.Process(target=poll_one_site, args=(core_api_url, s.source_id, collector_key))
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

(Note: a failed discovery refresh here `continue`s to the next tick rather than falling back to a "last known" list, unlike envoy/weather — pricing's tick is only 5 minutes, so skipping one tick on a Core API blip is cheap and simpler than carrying state across ticks for a fork-per-child model with no persistent main-loop state to update in place.)

- [ ] **Step 10: Run to verify it passes**

Run: `cd pricing-collector && .venv/bin/python -m pytest tests/test_live_poll.py -v`
Expected: PASS

- [ ] **Step 11: Write the failing backfill test**

In `pricing-collector/tests/test_backfill.py`, `backfill_one_site`'s own tests are unchanged (still takes `(core_api_url, source_id, collector_key)`). Replace the two `run_backfill_forever` tests (`test_run_backfill_forever_visits_every_site_each_cycle` and `test_run_backfill_forever_isolates_one_site_failure_from_the_next`) — it now calls `fetch_collector_sources` once per 12-hour pass instead of iterating a static `sources` argument:

```python
def test_run_backfill_forever_visits_every_site_each_cycle(monkeypatch):
    monkeypatch.setattr(
        backfill, "fetch_collector_sources", lambda base_url, key: [PricingSource(source_id=1), PricingSource(source_id=2)]
    )
    visited = []
    monkeypatch.setattr(backfill, "backfill_one_site", lambda base_url, sid, key: visited.append(sid))

    calls = {"n": 0}

    def _fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] >= 1:
            raise SystemExit

    monkeypatch.setattr(backfill.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        backfill.run_backfill_forever(BASE_URL, "shared-key")

    assert visited == [1, 2]


def test_run_backfill_forever_isolates_one_site_failure_from_the_next(monkeypatch):
    monkeypatch.setattr(
        backfill, "fetch_collector_sources", lambda base_url, key: [PricingSource(source_id=1), PricingSource(source_id=2)]
    )
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

    with pytest.raises(SystemExit):
        backfill.run_backfill_forever(BASE_URL, "shared-key")

    assert visited == [2]  # site 1's crash didn't stop site 2 from running
```

Add a third test for the discovery-failure path (no equivalent existed before, since the source list used to be static):

```python
def test_run_backfill_forever_skips_pass_when_discovery_fails(monkeypatch):
    def _raise(base_url, key):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(backfill, "fetch_collector_sources", _raise)
    visited = []
    monkeypatch.setattr(backfill, "backfill_one_site", lambda base_url, sid, key: visited.append(sid))

    calls = {"n": 0}

    def _fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] >= 1:
            raise SystemExit

    monkeypatch.setattr(backfill.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        backfill.run_backfill_forever(BASE_URL, "shared-key")

    assert visited == []  # no sites visited this pass, but the process did not crash
```

- [ ] **Step 12: Run to verify it fails**

Run: `cd pricing-collector && .venv/bin/python -m pytest tests/test_backfill.py -v`
Expected: FAIL

- [ ] **Step 13: Implement**

In `pricing-collector/pricing_collector/backfill.py`, add the import and change `run_backfill_forever`:

```python
from pricing_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_historical_prices,  # keep existing imports from amber_client as-is; this line only adds fetch_collector_sources to the core_api_client import
)
```

(Check the real existing import block first — `fetch_historical_prices` actually comes from `pricing_collector.amber_client`, not `core_api_client`; only add `fetch_collector_sources` to the existing `from pricing_collector.core_api_client import (...)` block, don't restructure the amber_client import.)

```python
def run_backfill_forever(core_api_url: str, collector_key: str) -> None:
    while True:
        try:
            sources = fetch_collector_sources(core_api_url, collector_key)
        except CoreApiError:
            logger.exception("Failed to refresh pricing source list for backfill pass; skipping this pass")
            sources = []
        for s in sources:
            try:
                backfill_one_site(core_api_url, s.source_id, collector_key)
            except Exception:
                logger.exception("Unexpected error backfilling pricing source %s; continuing", s.source_id)
        time.sleep(BACKFILL_INTERVAL_SECONDS)
```

- [ ] **Step 14: Run to verify it passes**

Run: `cd pricing-collector && .venv/bin/python -m pytest tests/test_backfill.py -v`
Expected: PASS

- [ ] **Step 15: Update main.py and its test**

In `pricing-collector/tests/test_main.py`, update `SETTINGS` and the assertions:

```python
SETTINGS = Settings(core_api_url="http://localhost:8000", collector_key="shared-key")
```

```python
def test_main_starts_backfill_process_and_runs_live_poll(monkeypatch):
    FakeProcess.instances = []
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(main_module.multiprocessing, "Process", FakeProcess)
    live_poll_calls = []
    monkeypatch.setattr(main_module, "run_live_poll_forever", lambda base_url, key: live_poll_calls.append((base_url, key)))

    main_module.main()

    assert len(FakeProcess.instances) == 1
    process = FakeProcess.instances[0]
    assert process.daemon is True
    assert process.started is True
    assert process.target is main_module.run_backfill_forever
    assert process.args == (SETTINGS.core_api_url, SETTINGS.collector_key)
    assert live_poll_calls == [(SETTINGS.core_api_url, SETTINGS.collector_key)]
```

`test_main_does_not_use_threading` is unchanged.

In `pricing-collector/pricing_collector/main.py`, `main()`'s body barely changes — only the arguments passed through:

```python
def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    backfill_process = multiprocessing.Process(
        target=run_backfill_forever, args=(settings.core_api_url, settings.collector_key), daemon=True
    )
    backfill_process.start()

    run_live_poll_forever(settings.core_api_url, settings.collector_key)
```

- [ ] **Step 16: Run the full suite**

Run: `cd pricing-collector && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 17: Commit**

```bash
cd pricing-collector
git add pricing_collector/config.py pricing_collector/core_api_client.py pricing_collector/live_poll.py pricing_collector/backfill.py pricing_collector/main.py tests/test_config.py tests/test_live_poll.py tests/test_backfill.py tests/test_main.py tests/test_core_api_client.py
git commit -m "feat: discover pricing sources via shared collector key each tick"
```

---

## Task 8: docker-compose.yml — static per-kind collector config

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: Tasks 1–7 (core-api reads `ENVOY_COLLECTOR_KEY`/`WEATHER_COLLECTOR_KEY`/`PRICING_COLLECTOR_KEY`; each collector reads `COLLECTOR_KEY`).

- [ ] **Step 1: Edit the compose file**

In `docker-compose.yml`, add the three new env vars to `core-api`'s `environment` block:

```yaml
  core-api:
    build: ./core-api
    environment:
      JWT_SECRET: ${JWT_SECRET:?set a long random secret}
      ENVOY_COLLECTOR_KEY: ${ENVOY_COLLECTOR_KEY:?set a long random secret, shared with the envoy-collector service below}
      WEATHER_COLLECTOR_KEY: ${WEATHER_COLLECTOR_KEY:?set a long random secret, shared with the weather-collector service below}
      PRICING_COLLECTOR_KEY: ${PRICING_COLLECTOR_KEY:?set a long random secret, shared with the pricing-collector service below}
      DATABASE_URL: sqlite:////data/core_api.db
      SMTP_HOST: mail-relay
      SMTP_PORT: "25"
      SMTP_FROM: ${SMTP_FROM:-noreply@localhost}
      FRONTEND_BASE_URL: ${FRONTEND_BASE_URL:-http://localhost}
    volumes:
      - core_api_data:/data
    ports:
      - "8000:8000"
    depends_on:
      - mail-relay
```

Replace the comment above the collectors and all three collector service blocks:

```yaml
  # The three collectors below each discover their own sites' sources from
  # Core API using one shared key per collector kind -- create sites and
  # sources for them in the webapp any time; no compose/.env edit or
  # restart is needed when adding a new site of an existing kind.

  envoy-collector:
    build: ./envoy-collector
    environment:
      CORE_API_URL: http://core-api:8000
      COLLECTOR_KEY: ${ENVOY_COLLECTOR_KEY:?set a long random secret, shared with core-api's own ENVOY_COLLECTOR_KEY}
    depends_on:
      - core-api

  weather-collector:
    build: ./weather-collector
    environment:
      CORE_API_URL: http://core-api:8000
      COLLECTOR_KEY: ${WEATHER_COLLECTOR_KEY:?set a long random secret, shared with core-api's own WEATHER_COLLECTOR_KEY}
    depends_on:
      - core-api

  pricing-collector:
    build: ./pricing-collector
    environment:
      CORE_API_URL: http://core-api:8000
      COLLECTOR_KEY: ${PRICING_COLLECTOR_KEY:?set a long random secret, shared with core-api's own PRICING_COLLECTOR_KEY}
    depends_on:
      - core-api
```

- [ ] **Step 2: Validate the compose file parses**

Run: `docker compose -f docker-compose.yml config --quiet`
Expected: no output, exit code 0 (compose only validates YAML/interpolation syntax here — it will still complain about the required `${...:?}` vars being unset unless you export dummy values first; that's expected and fine, e.g. `JWT_SECRET=x ENVOY_COLLECTOR_KEY=x WEATHER_COLLECTOR_KEY=x PRICING_COLLECTOR_KEY=x RELAY_DOMAIN=x RELAY_SMARTHOST=x docker compose -f docker-compose.yml config --quiet`).

- [ ] **Step 3: Update `.env.example`**

Read `.env.example` first, then add the three new required vars next to the existing `JWT_SECRET` entry, following its existing comment style:

```
# Shared secrets, one per collector kind -- the SAME value goes into both
# core-api's environment and that collector's environment (docker-compose
# substitutes each from this single .env entry into both places). Generate
# each with, e.g.: openssl rand -hex 32
ENVOY_COLLECTOR_KEY=
WEATHER_COLLECTOR_KEY=
PRICING_COLLECTOR_KEY=
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat: wire per-kind collector secrets into docker-compose"
```

---

## Task 9: webapp — remove the per-source key reveal

**Files:**
- Modify: `webapp/src/sources/useSources.ts`
- Modify: `webapp/src/pages/SourcesPage.tsx`
- Modify: `webapp/src/pages/SourcesPage.test.tsx`

**Interfaces:**
- Produces: `useCreateSource` resolves to a plain `Source` (no `collector_api_key` field) — `SourceCreateResult` is retired.

- [ ] **Step 1: Write the failing test**

In `webapp/src/pages/SourcesPage.test.tsx`, replace the "creates a source and shows the generated collector key once" test:

```tsx
it("creates a source and confirms it without a collector key", async () => {
  vi.mocked(apiFetch)
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce({
      id: 2,
      site_id: 7,
      kind: "weather",
      provider: "weatherlink_live",
      enabled: true,
      config: {},
    });
  renderPage();
  await screen.findByText("No sources yet.");
  await userEvent.selectOptions(screen.getByLabelText("Kind"), "weather");
  await userEvent.type(screen.getByPlaceholderText("Provider"), "weatherlink_live");
  await userEvent.click(screen.getByText("Create source"));
  expect(await screen.findByText(/will start collecting/i)).toBeInTheDocument();
});
```

Update the "shows an error on failed source creation" test's final assertion from `expect(screen.queryByText(/Collector key/)).not.toBeInTheDocument();` to `expect(screen.queryByText(/will start collecting/i)).not.toBeInTheDocument();`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd webapp && npm test -- SourcesPage`
Expected: FAIL — the current component still renders the old "Collector key (shown once...)" text with `createSource.data.collector_api_key`, which is now `undefined`.

- [ ] **Step 3: Implement**

In `webapp/src/sources/useSources.ts`, delete the `SourceCreateResult` interface and change `useCreateSource`'s generic back to `Source`:

```typescript
export function useCreateSource(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateSourceInput) =>
      apiFetch<Source>(`/api/v1/sites/${siteId}/sources`, {
        method: "POST",
        body: JSON.stringify({ ...input, config: {} }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources", siteId] });
    },
  });
}
```

In `webapp/src/pages/SourcesPage.tsx`, replace the key-reveal paragraph in `CreateSourceForm`:

```tsx
      {createSource.isSuccess && (
        <p className="border bg-green-50 p-4">
          Source created — the matching collector will start collecting for it automatically within a few minutes.
        </p>
      )}
```

(Replacing the previous `{createSource.data && (...)}` block, which read `createSource.data.collector_api_key`.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd webapp && npm test -- SourcesPage`
Expected: PASS

- [ ] **Step 5: Run the full webapp test suite**

Run: `cd webapp && npm test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd webapp
git add src/sources/useSources.ts src/pages/SourcesPage.tsx src/pages/SourcesPage.test.tsx
git commit -m "feat: remove per-source collector key reveal from Sources page"
```
