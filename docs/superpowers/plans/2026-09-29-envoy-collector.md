# Envoy Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Envoy collector (sub-project 2) — a standalone project that polls an Enphase Envoy's local API every minute and pushes energy/battery readings into the already-shipped Core API — plus the small set of additive Core API changes (config lives on the `Source` row, not the collector's environment) that this collector depends on.

**Architecture:** Two small, cleanly-separated deliverables. (1) Core API gains a config-merge on the existing `PATCH /sites/{id}/sources/{id}`, a computed `renewal_url` on source reads, and a new small collector-authenticated router for reading/reporting connection config. (2) A brand-new standalone project, `enphase-data/envoy-collector/`, with no dependency on `core-api`'s package, split into pure mapping logic (easy to test exhaustively against real fixture data), thin HTTP client modules (Envoy side, Core API side), and a small poll loop tying them together.

**Tech Stack:** Core API side: existing FastAPI/SQLAlchemy stack, no new dependencies. Collector side: `httpx` (sync client) for all HTTP, Python's built-in `xml.etree.ElementTree` for `/info.xml`, `respx` (dev-only) for HTTP-mocking in tests.

**Spec:** `docs/superpowers/specs/2026-09-29-envoy-collector-design.md` (see 'Envoy local API — confirmed field mapping', 'Core API changes', and 'Envoy collector design' sections).

## Global Constraints

- The collector's own environment variables are exactly `CORE_API_URL`, `CORE_API_SOURCE_ID`, `CORE_API_COLLECTOR_KEY`, `POLL_INTERVAL_SECONDS` (default `60`) — never an Envoy host or token env var; those live in `Source.config` in Core API's database.
- Every HTTPS request to the Envoy uses `verify=False` (self-signed cert on a LAN-local device) — an accepted, documented tradeoff, not something to "fix" with cert pinning.
- `production_w` comes from the `eim`-type entry in `production.json`'s `production` array, never the `inverters`-type entry — they report different values and the `eim` (CT-metered) one is authoritative.
- `/ivp/ensemble/power`'s top-level key is literally `"devices:"` (with a trailing colon) — must be matched exactly.
- Battery sign convention: `real_power_mw` negative = charging, positive = discharging (confirmed live against the user's own device). Values are milliwatts; divide by 1000 for watts.
- `PATCH /sources/{id}/discovered-config` accepts only `{"serial_num": str}` — any other field must be rejected with 422, never silently ignored or merged.
- Core API's `config` merge (on both `PATCH /sites/{id}/sources/{id}` and the new discovered-config endpoint) is a shallow dict merge (`{**existing, **patch}`), never a full replace — a token-only patch must not wipe `host`/`serial_num`.
- `renewal_url` on `SourceOut` is populated only when `provider == "envoy"` and `config.serial_num` is known; `null` otherwise. The raw `config` dict (which holds the token) is never included in any `SourceOut`/list response.
- Collector failure handling: an unreachable Envoy, an unreachable Core API, or a malformed response logs and skips that poll cycle — no retries within a cycle, no crash. An Envoy 401 triggers exactly one config refetch-and-retry per cycle; if it still fails, log an actionable message naming the renewal URL, not a generic error.

## Review Focus

- A `production.json` response where the `inverters` and `eim` production entries disagree (as in the real sample data) must yield the `eim` value for `production_w`, not whichever entry happens to be first in the array.
- A `/ivp/ensemble/power` response with zero battery devices (a site with no battery) must not crash (e.g. a `ZeroDivisionError` averaging `soc` over an empty list) — it should simply produce no battery readings.
- `PATCH /sources/{id}/discovered-config` with an extra field (e.g. `{"serial_num": "x", "host": "evil"}`) must return 422 and must not merge `host` into the source's config.
- `PATCH /sites/{id}/sources/{id}` with `{"config": {"token": "new"}}` on a source whose `config` already has `host`/`serial_num` must preserve both after the patch — a full-replace bug here would silently break the collector's next config fetch.
- An Envoy 401 followed by a still-401 retry must log the `renewal_url` (not a bare "unauthorized" message) so the failure is actionable without needing to read source code to know what to do next.

---

## File Structure

**Core API** (`enphase-data/core-api/`):
- `core_api/schemas.py` — **modify**: `SourceUpdate` gains `config: dict | None = None`; `SourceOut` gains `renewal_url: str | None = None`; new `SourceConfigOut`, `DiscoveredSourceConfig` schemas.
- `core_api/routers/sources.py` — **modify**: add `_to_source_out` helper computing `renewal_url`; `list_sources`/`get_source`/`update_source` return via it; `update_source` shallow-merges `payload.config`.
- `core_api/routers/collector_config.py` — **create**: `GET /sources/{source_id}/config`, `PATCH /sources/{source_id}/discovered-config`, both collector-key authenticated.
- `core_api/main.py` — **modify**: mount the new router.
- `tests/test_sources.py` — **modify**: add config-merge and `renewal_url` tests.
- `tests/test_collector_config.py` — **create**.

**Envoy collector** (new project, `enphase-data/envoy-collector/`):
- `pyproject.toml` — **create**.
- `envoy_collector/__init__.py` — **create**: empty.
- `envoy_collector/config.py` — **create**: env-var settings.
- `envoy_collector/mapping.py` — **create**: pure Envoy-JSON → reading-dict functions.
- `envoy_collector/envoy_client.py` — **create**: HTTP calls to the Envoy.
- `envoy_collector/core_api_client.py` — **create**: HTTP calls to Core API.
- `envoy_collector/main.py` — **create**: startup + poll loop.
- `tests/fixtures/production.json`, `tests/fixtures/ensemble_power.json`, `tests/fixtures/info.xml` — **create**: the user's real sample data, verbatim.
- `tests/test_mapping.py`, `tests/test_envoy_client.py`, `tests/test_core_api_client.py`, `tests/test_main.py` — **create**.
- `Dockerfile`, `.env.example` — **create**.

## Interfaces reference (carried through every task)

```python
# core_api/schemas.py additions
class SourceConfigOut(BaseModel):
    config: dict

class DiscoveredSourceConfig(BaseModel):
    model_config = {"extra": "forbid"}
    serial_num: str

# core_api/routers/sources.py
def _to_source_out(source: Source) -> SourceOut: ...

# envoy_collector/config.py
@dataclass(frozen=True)
class Settings:
    core_api_url: str
    source_id: int
    collector_key: str
    poll_interval_seconds: int

def get_settings() -> Settings: ...

# envoy_collector/mapping.py
def map_production_readings(production_json: dict, ts: datetime.datetime) -> list[dict]: ...
def map_battery_readings(ensemble_json: dict, ts: datetime.datetime) -> list[dict]: ...
# each returned dict is {"ts": datetime, "metric": str, "value": float}

# envoy_collector/envoy_client.py
class EnvoyAuthError(Exception): ...
def fetch_production(host: str, token: str) -> dict: ...
def fetch_battery_power(host: str, token: str) -> dict: ...
def fetch_serial_number(host: str) -> str: ...

# envoy_collector/core_api_client.py
class CoreApiError(Exception): ...
def fetch_source_config(base_url: str, source_id: int, collector_key: str) -> dict: ...
def report_discovered_config(base_url: str, source_id: int, collector_key: str, serial_num: str) -> None: ...
def push_energy_readings(base_url: str, source_id: int, collector_key: str, readings: list[dict]) -> None: ...

# envoy_collector/main.py
def discover_and_sync_serial(settings: Settings, config: dict) -> None: ...
def poll_once(settings: Settings, config: dict) -> dict: ...  # returns possibly-refreshed config
def main() -> None: ...
```

---

## Task 1: Core API — `SourceUpdate.config` merge + computed `renewal_url`

**Files:**
- Modify: `core_api/schemas.py`, `core_api/routers/sources.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Produces: `_to_source_out(source: Source) -> SourceOut` (used by Task 2's tests indirectly through the API, not called directly by other tasks).

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_sources.py
def test_patch_config_merges_without_wiping_other_keys(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {"host": "envoy.local", "token": "old-token"}},
        headers=auth(owner_id),
    ).json()["id"]

    patch = client.patch(
        f"/api/v1/sites/{site_id}/sources/{source_id}",
        json={"config": {"token": "new-token"}},
        headers=auth(owner_id),
    )
    assert patch.status_code == 200, patch.text

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.sources import Source

    source = session.get(Source, source_id)
    session.close()
    assert source.config == {"host": "envoy.local", "token": "new-token"}


def test_renewal_url_present_for_envoy_with_serial_num(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {"serial_num": "123456789012"}},
        headers=auth(owner_id),
    ).json()["id"]

    response = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert response.status_code == 200
    assert response.json()["renewal_url"] == (
        "https://enlighten.enphaseenergy.com/entrez-auth-token?serial_num=123456789012"
    )


def test_renewal_url_absent_without_serial_num(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {}},
        headers=auth(owner_id),
    ).json()["id"]

    response = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert response.json()["renewal_url"] is None


def test_renewal_url_absent_for_non_envoy_provider(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "weather", "provider": "weatherlink_live", "config": {"serial_num": "123"}},
        headers=auth(owner_id),
    ).json()["id"]

    response = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert response.json()["renewal_url"] is None
```

(`make_owner_and_site`/`auth` already exist in `tests/test_sources.py` from earlier work; `make_engine`/`make_session_factory` are already imported there too.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sources.py -v -k "merge or renewal"`
Expected: FAIL — `config` isn't accepted by `SourceUpdate`, `renewal_url` doesn't exist on `SourceOut`.

- [ ] **Step 3: Add the schema fields**

```python
# In core_api/schemas.py, replace the existing SourceOut and SourceUpdate classes with:
class SourceOut(BaseModel):
    id: int
    site_id: int
    kind: SourceKind
    provider: str
    enabled: bool
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None
    renewal_url: str | None = None

    model_config = {"from_attributes": True}


class SourceUpdate(BaseModel):
    enabled: bool | None = None
    effective_to: UtcDatetime | None = None
    config: dict | None = None
```

- [ ] **Step 4: Add the `_to_source_out` helper and use it in `sources.py`**

```python
# core_api/routers/sources.py — add this helper after _hash_key, and change
# list_sources/get_source/update_source's bodies and return-type annotations
# as shown.

def _to_source_out(source: Source) -> SourceOut:
    renewal_url = None
    if source.provider == "envoy":
        serial_num = (source.config or {}).get("serial_num")
        if serial_num:
            renewal_url = f"https://enlighten.enphaseenergy.com/entrez-auth-token?serial_num={serial_num}"
    return SourceOut(
        id=source.id,
        site_id=source.site_id,
        kind=source.kind,
        provider=source.provider,
        enabled=source.enabled,
        effective_from=source.effective_from,
        effective_to=source.effective_to,
        renewal_url=renewal_url,
    )


@router.get("", response_model=list[SourceOut])
def list_sources(
    site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
) -> list[SourceOut]:
    sources = db.execute(select(Source).where(Source.site_id == site.id)).scalars().all()
    return [_to_source_out(s) for s in sources]


@router.get("/{source_id}", response_model=SourceOut)
def get_source(
    source_id: int, site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
) -> SourceOut:
    source = db.get(Source, source_id)
    if source is None or source.site_id != site.id:
        raise AppError(404, "not_found", f"Source {source_id} not found")
    return _to_source_out(source)


@router.patch("/{source_id}", response_model=SourceOut)
def update_source(
    source_id: int,
    payload: SourceUpdate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> SourceOut:
    source = db.get(Source, source_id)
    if source is None or source.site_id != site.id:
        raise AppError(404, "not_found", f"Source {source_id} not found")
    if payload.enabled is not None:
        source.enabled = payload.enabled
    if payload.effective_to is not None:
        source.effective_to = payload.effective_to
    if payload.config is not None:
        source.config = {**(source.config or {}), **payload.config}
    db.commit()
    db.refresh(source)
    return _to_source_out(source)
```

Note `create_source` is unchanged — it still returns `SourceCreateOut` (which has no `renewal_url`, since a serial number is never known at creation time).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_sources.py -v`
Expected: PASS (all tests in the file, including the 4 new ones)

- [ ] **Step 6: Run full suite, then commit**

```bash
uv run pytest -q
git add core_api/schemas.py core_api/routers/sources.py tests/test_sources.py
git commit -m "Add Source.config merge on PATCH and a computed renewal_url"
```

---

## Task 2: Core API — collector-facing config endpoints

**Files:**
- Create: `core_api/routers/collector_config.py`
- Modify: `core_api/schemas.py`, `core_api/main.py`
- Test: `tests/test_collector_config.py`

**Interfaces:**
- Consumes: `get_source_by_collector_key` (existing, from `core_api/deps.py`).
- Produces: `router` mounted with `GET /sources/{source_id}/config`, `PATCH /sources/{source_id}/discovered-config`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_collector_config.py
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, User
from core_api.models.sources import Source, SourceKind


def make_energy_source(db_url: str, config: dict | None = None) -> tuple[int, str]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email=f"cc-owner-{secrets.token_hex(4)}@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    raw_key = secrets.token_urlsafe(16)
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config=config or {"host": "envoy.local", "token": "abc"},
        collector_api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        effective_from=datetime.now(timezone.utc),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id, raw_key


def test_get_source_config(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_energy_source(db_url, {"host": "envoy.local", "token": "abc"})
    response = client.get(
        f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": raw_key}
    )
    assert response.status_code == 200, response.text
    assert response.json()["config"] == {"host": "envoy.local", "token": "abc"}


def test_get_source_config_wrong_key_rejected(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, _raw_key = make_energy_source(db_url)
    response = client.get(
        f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": "wrong"}
    )
    assert response.status_code == 401


def test_report_discovered_config_merges_serial_num(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_energy_source(db_url, {"host": "envoy.local", "token": "abc"})
    patch = client.patch(
        f"/api/v1/sources/{source_id}/discovered-config",
        json={"serial_num": "123456789012"},
        headers={"X-Collector-Key": raw_key},
    )
    assert patch.status_code == 204, patch.text

    get = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": raw_key})
    assert get.json()["config"] == {"host": "envoy.local", "token": "abc", "serial_num": "123456789012"}


def test_report_discovered_config_rejects_extra_fields(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_energy_source(db_url, {"host": "envoy.local", "token": "abc"})
    patch = client.patch(
        f"/api/v1/sources/{source_id}/discovered-config",
        json={"serial_num": "123456789012", "host": "evil.example.com"},
        headers={"X-Collector-Key": raw_key},
    )
    assert patch.status_code == 422

    get = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": raw_key})
    assert get.json()["config"] == {"host": "envoy.local", "token": "abc"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_collector_config.py -v`
Expected: FAIL (404s — no `/sources/{id}/config` route yet).

- [ ] **Step 3: Add the two schemas**

```python
# Append to core_api/schemas.py
class SourceConfigOut(BaseModel):
    config: dict


class DiscoveredSourceConfig(BaseModel):
    model_config = {"extra": "forbid"}

    serial_num: str
```

- [ ] **Step 4: Implement `core_api/routers/collector_config.py`**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, get_source_by_collector_key
from core_api.models.sources import Source
from core_api.schemas import DiscoveredSourceConfig, SourceConfigOut

router = APIRouter(prefix="/sources/{source_id}", tags=["collector-config"])


@router.get("/config", response_model=SourceConfigOut)
def get_source_config(source: Source = Depends(get_source_by_collector_key)) -> SourceConfigOut:
    return SourceConfigOut(config=source.config or {})


@router.patch("/discovered-config", status_code=status.HTTP_204_NO_CONTENT)
def report_discovered_config(
    payload: DiscoveredSourceConfig,
    source: Source = Depends(get_source_by_collector_key),
    db: Session = Depends(get_db_session),
) -> None:
    source.config = {**(source.config or {}), "serial_num": payload.serial_num}
    db.commit()
```

- [ ] **Step 5: Mount the router**

```python
# core_api/main.py — add this import at the top with the other router imports:
from core_api.routers.collector_config import router as collector_config_router

# and this line inside create_app, alongside the other app.include_router(...,
# prefix=api_prefix) calls (after query_router, before `return app`):
    app.include_router(collector_config_router, prefix=api_prefix)
```

- [ ] **Step 6: Run tests, then full suite, then commit**

```bash
uv run pytest tests/test_collector_config.py -v
uv run pytest -q
git add core_api/routers/collector_config.py core_api/schemas.py core_api/main.py tests/test_collector_config.py
git commit -m "Add collector-facing source config read/report endpoints"
```

---

## Task 3: Envoy collector — project scaffold + settings

**Files:**
- Create: `envoy-collector/pyproject.toml`, `envoy-collector/envoy_collector/__init__.py`, `envoy-collector/envoy_collector/config.py`, `envoy-collector/.env.example`
- Test: `envoy-collector/tests/test_config.py`

**Interfaces:**
- Produces: `Settings` dataclass, `get_settings() -> Settings` in `envoy_collector/config.py`.

- [ ] **Step 1: Create the project directory and files**

From `enphase-data/`, create `envoy-collector/`:

```bash
mkdir -p envoy-collector/envoy_collector envoy-collector/tests
touch envoy-collector/envoy_collector/__init__.py envoy-collector/tests/__init__.py
```

`envoy-collector/pyproject.toml`:

```toml
[project]
name = "envoy-collector"
version = "0.1.0"
description = "Polls an Enphase Envoy's local API and pushes readings into Core API (sub-project 2)."
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
packages = ["envoy_collector"]
```

`envoy-collector/.env.example`:

```
CORE_API_URL=http://localhost:8000
CORE_API_SOURCE_ID=1
CORE_API_COLLECTOR_KEY=change-me
POLL_INTERVAL_SECONDS=60
```

- [ ] **Step 2: Set up the environment**

```bash
cd envoy-collector
uv sync
```

- [ ] **Step 3: Write the failing test**

```python
# envoy-collector/tests/test_config.py
from __future__ import annotations

import pytest

from envoy_collector.config import get_settings


def test_get_settings_reads_env_vars(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000/")
    monkeypatch.setenv("CORE_API_SOURCE_ID", "42")
    monkeypatch.setenv("CORE_API_COLLECTOR_KEY", "secret-key")
    monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)

    settings = get_settings()

    assert settings.core_api_url == "http://localhost:8000"  # trailing slash stripped
    assert settings.source_id == 42
    assert settings.collector_key == "secret-key"
    assert settings.poll_interval_seconds == 60


def test_get_settings_respects_custom_poll_interval(monkeypatch):
    monkeypatch.setenv("CORE_API_URL", "http://localhost:8000")
    monkeypatch.setenv("CORE_API_SOURCE_ID", "1")
    monkeypatch.setenv("CORE_API_COLLECTOR_KEY", "k")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "30")

    assert get_settings().poll_interval_seconds == 30


def test_get_settings_raises_if_required_var_missing(monkeypatch):
    monkeypatch.delenv("CORE_API_URL", raising=False)
    monkeypatch.delenv("CORE_API_SOURCE_ID", raising=False)
    monkeypatch.delenv("CORE_API_COLLECTOR_KEY", raising=False)

    with pytest.raises(RuntimeError):
        get_settings()
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'envoy_collector.config'`

- [ ] **Step 5: Implement `envoy_collector/config.py`**

```python
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    core_api_url: str
    source_id: int
    collector_key: str
    poll_interval_seconds: int


def get_settings() -> Settings:
    core_api_url = os.environ.get("CORE_API_URL")
    source_id = os.environ.get("CORE_API_SOURCE_ID")
    collector_key = os.environ.get("CORE_API_COLLECTOR_KEY")
    if not core_api_url or not source_id or not collector_key:
        raise RuntimeError(
            "CORE_API_URL, CORE_API_SOURCE_ID, and CORE_API_COLLECTOR_KEY must all be set"
        )
    return Settings(
        core_api_url=core_api_url.rstrip("/"),
        source_id=int(source_id),
        collector_key=collector_key,
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "60")),
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
cd envoy-collector
git add pyproject.toml uv.lock envoy_collector/__init__.py envoy_collector/config.py tests/__init__.py tests/test_config.py .env.example
git commit -m "Scaffold envoy-collector project with env-var settings"
```

---

## Task 4: Envoy collector — pure mapping logic

**Files:**
- Create: `envoy_collector/mapping.py`, `tests/fixtures/production.json`, `tests/fixtures/ensemble_power.json`
- Test: `tests/test_mapping.py`

**Interfaces:**
- Produces: `map_production_readings(production_json: dict, ts: datetime.datetime) -> list[dict]`, `map_battery_readings(ensemble_json: dict, ts: datetime.datetime) -> list[dict]`.

- [ ] **Step 1: Save the real fixture data**

`envoy-collector/tests/fixtures/production.json` (the user's real sample — use exactly this):

```json
{
  "production": [
    {"type": "inverters", "activeCount": 25, "readingTime": 1790645894, "wNow": 4920, "whLifetime": 1480836},
    {"type": "eim", "activeCount": 1, "measurementType": "production", "readingTime": 1790645913, "wNow": 7474.653, "whLifetime": 1477806.116}
  ],
  "consumption": [
    {"type": "eim", "activeCount": 1, "measurementType": "total-consumption", "readingTime": 1790645913, "wNow": 7621.994, "whLifetime": 1304948.246},
    {"type": "eim", "activeCount": 1, "measurementType": "net-consumption", "readingTime": 1790645913, "wNow": 147.341, "whLifetime": -170980.963}
  ],
  "storage": [
    {"type": "acb", "activeCount": 0, "readingTime": 0, "wNow": 0, "whNow": 0, "state": "idle"}
  ]
}
```

`envoy-collector/tests/fixtures/ensemble_power.json` (the user's real sample):

```json
{
  "devices:": [
    {"serial_num": "492544001917", "real_power_mw": -1826000, "apparent_power_mva": -1826000, "soc": 95},
    {"serial_num": "492544001920", "real_power_mw": -1834000, "apparent_power_mva": -1834000, "soc": 94},
    {"serial_num": "492544001922", "real_power_mw": -1339000, "apparent_power_mva": -1339000, "soc": 93},
    {"serial_num": "492544001923", "real_power_mw": -1327000, "apparent_power_mva": -1327000, "soc": 91}
  ]
}
```

- [ ] **Step 2: Write the failing tests**

```python
# envoy-collector/tests/test_mapping.py
from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from envoy_collector.mapping import map_battery_readings, map_production_readings

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TS = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=datetime.timezone.utc)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_production_uses_eim_not_inverters():
    readings = map_production_readings(load_fixture("production.json"), TS)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["production_w"] == 7474.653  # the eim value, not 4920 (inverters)


def test_consumption_and_grid_import_derived_correctly():
    readings = map_production_readings(load_fixture("production.json"), TS)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["consumption_w"] == 7621.994
    assert by_metric["grid_import_w"] == 147.341
    assert "grid_export_w" not in by_metric


def test_negative_net_consumption_yields_grid_export():
    production = load_fixture("production.json")
    production["consumption"][1]["wNow"] = -500.0
    readings = map_production_readings(production, TS)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["grid_export_w"] == 500.0
    assert "grid_import_w" not in by_metric


def test_all_production_readings_share_the_given_timestamp():
    readings = map_production_readings(load_fixture("production.json"), TS)
    assert all(r["ts"] == TS for r in readings)


def test_battery_negative_power_yields_charge_and_averaged_soc():
    readings = map_battery_readings(load_fixture("ensemble_power.json"), TS)
    by_metric = {r["metric"]: r["value"] for r in readings}
    # sum(-1826,-1834,-1339,-1327) mW = -6326000 mW = -6326.0 W -> charging 6326.0W
    assert by_metric["battery_charge_w"] == pytest.approx(6326.0)
    assert "battery_discharge_w" not in by_metric
    assert by_metric["battery_soc_pct"] == pytest.approx((95 + 94 + 93 + 91) / 4)


def test_battery_positive_power_yields_discharge():
    ensemble = {"devices:": [{"serial_num": "x", "real_power_mw": 2000000, "soc": 50}]}
    readings = map_battery_readings(ensemble, TS)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["battery_discharge_w"] == pytest.approx(2000.0)
    assert "battery_charge_w" not in by_metric


def test_no_battery_devices_yields_no_battery_readings():
    readings = map_battery_readings({"devices:": []}, TS)
    assert readings == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_mapping.py -v`
Expected: FAIL — `envoy_collector.mapping` doesn't exist.

- [ ] **Step 4: Implement `envoy_collector/mapping.py`**

```python
from __future__ import annotations

import datetime


def map_production_readings(production_json: dict, ts: datetime.datetime) -> list[dict]:
    """Map a /production.json response to a list of {ts, metric, value} dicts.

    production_w always comes from the "eim" (CT-metered) entry, never the
    "inverters" entry — the two disagree in practice, and the CT reading is
    the billing-grade one. grid_import_w/grid_export_w are derived from the
    "net-consumption" eim entry's sign (positive = importing, negative =
    exporting), confirmed against real sample data where
    total_consumption - production == net_consumption.
    """
    production_eim = next(p for p in production_json["production"] if p["type"] == "eim")
    consumption_total = next(
        c for c in production_json["consumption"] if c["measurementType"] == "total-consumption"
    )
    consumption_net = next(
        c for c in production_json["consumption"] if c["measurementType"] == "net-consumption"
    )

    readings = [
        {"ts": ts, "metric": "production_w", "value": production_eim["wNow"]},
        {"ts": ts, "metric": "consumption_w", "value": consumption_total["wNow"]},
    ]
    net = consumption_net["wNow"]
    if net >= 0:
        readings.append({"ts": ts, "metric": "grid_import_w", "value": net})
    else:
        readings.append({"ts": ts, "metric": "grid_export_w", "value": -net})
    return readings


def map_battery_readings(ensemble_json: dict, ts: datetime.datetime) -> list[dict]:
    """Map an /ivp/ensemble/power response to {ts, metric, value} dicts.

    Note the top-level key is "devices:" with a trailing colon — a real
    quirk in Enphase's API. real_power_mw is milliwatts; negative means
    charging, positive means discharging (confirmed live against real
    hardware: SoC rose across two samples while all values were negative).
    soc is averaged across units (no per-unit capacity data is exposed to
    weight by). A site with no battery (empty devices list) yields no
    readings at all, not a crash.
    """
    devices = ensemble_json.get("devices:", [])
    if not devices:
        return []

    total_w = sum(d["real_power_mw"] for d in devices) / 1000
    avg_soc = sum(d["soc"] for d in devices) / len(devices)

    readings = [{"ts": ts, "metric": "battery_soc_pct", "value": avg_soc}]
    if total_w <= 0:
        readings.append({"ts": ts, "metric": "battery_charge_w", "value": -total_w})
    else:
        readings.append({"ts": ts, "metric": "battery_discharge_w", "value": total_w})
    return readings
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_mapping.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add envoy_collector/mapping.py tests/fixtures/production.json tests/fixtures/ensemble_power.json tests/test_mapping.py
git commit -m "Add Envoy production/battery JSON to EnergyReading mapping"
```

---

## Task 5: Envoy collector — Envoy HTTP client

**Files:**
- Create: `envoy_collector/envoy_client.py`, `tests/fixtures/info.xml`
- Test: `tests/test_envoy_client.py`

**Interfaces:**
- Produces: `EnvoyAuthError`, `fetch_production(host, token) -> dict`, `fetch_battery_power(host, token) -> dict`, `fetch_serial_number(host) -> str`.

- [ ] **Step 1: Save the real fixture**

`envoy-collector/tests/fixtures/info.xml` (the user's real sample, trimmed to the relevant `<device>` block — full doc still valid XML):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<envoy_info>
    <time>1790646700</time>
    <device>
        <sn>123456789012</sn>
        <pn>800-00649-r01</pn>
        <software>D8.3.5226</software>
    </device>
    <web-tokens>true</web-tokens>
</envoy_info>
```

- [ ] **Step 2: Write the failing tests**

```python
# envoy-collector/tests/test_envoy_client.py
from __future__ import annotations

import pathlib

import httpx
import pytest
import respx

from envoy_collector.envoy_client import (
    EnvoyAuthError,
    fetch_battery_power,
    fetch_production,
    fetch_serial_number,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@respx.mock
def test_fetch_production_sends_bearer_token_and_parses_json():
    route = respx.get("https://envoy.local/production.json").mock(
        return_value=httpx.Response(200, json={"production": [], "consumption": [], "storage": []})
    )
    result = fetch_production("envoy.local", "my-token")
    assert route.called
    sent_request = route.calls[0].request
    assert sent_request.headers["Authorization"] == "Bearer my-token"
    assert result == {"production": [], "consumption": [], "storage": []}


@respx.mock
def test_fetch_production_raises_envoy_auth_error_on_401():
    respx.get("https://envoy.local/production.json").mock(return_value=httpx.Response(401))
    with pytest.raises(EnvoyAuthError):
        fetch_production("envoy.local", "bad-token")


@respx.mock
def test_fetch_battery_power_sends_bearer_token():
    route = respx.get("https://envoy.local/ivp/ensemble/power").mock(
        return_value=httpx.Response(200, json={"devices:": []})
    )
    result = fetch_battery_power("envoy.local", "my-token")
    assert route.called
    assert result == {"devices:": []}


@respx.mock
def test_fetch_serial_number_parses_xml_with_no_auth_header():
    xml_text = (FIXTURES / "info.xml").read_text()
    route = respx.get("https://envoy.local/info.xml").mock(
        return_value=httpx.Response(200, text=xml_text)
    )
    serial = fetch_serial_number("envoy.local")
    assert route.called
    assert "Authorization" not in route.calls[0].request.headers
    assert serial == "123456789012"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_envoy_client.py -v`
Expected: FAIL — `envoy_collector.envoy_client` doesn't exist.

- [ ] **Step 4: Implement `envoy_collector/envoy_client.py`**

```python
from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx


class EnvoyAuthError(Exception):
    """Raised when the Envoy rejects our current token (HTTP 401)."""


def _envoy_get(host: str, token: str, path: str) -> dict:
    response = httpx.get(
        f"https://{host}{path}",
        headers={"Authorization": f"Bearer {token}"},
        verify=False,
        timeout=10,
    )
    if response.status_code == 401:
        raise EnvoyAuthError(f"Envoy rejected the token for {path}")
    response.raise_for_status()
    return response.json()


def fetch_production(host: str, token: str) -> dict:
    return _envoy_get(host, token, "/production.json")


def fetch_battery_power(host: str, token: str) -> dict:
    return _envoy_get(host, token, "/ivp/ensemble/power")


def fetch_serial_number(host: str) -> str:
    response = httpx.get(f"https://{host}/info.xml", verify=False, timeout=10)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    serial = root.findtext("device/sn")
    if not serial:
        raise ValueError("/info.xml response did not contain a device serial number")
    return serial
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_envoy_client.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run full suite, then commit**

```bash
uv run pytest -q
git add envoy_collector/envoy_client.py tests/fixtures/info.xml tests/test_envoy_client.py
git commit -m "Add Envoy local-API HTTP client"
```

---

## Task 6: Envoy collector — Core API HTTP client

**Files:**
- Create: `envoy_collector/core_api_client.py`
- Test: `tests/test_core_api_client.py`

**Interfaces:**
- Produces: `CoreApiError`, `fetch_source_config(base_url, source_id, collector_key) -> dict`, `report_discovered_config(base_url, source_id, collector_key, serial_num) -> None`, `push_energy_readings(base_url, source_id, collector_key, readings: list[dict]) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# envoy-collector/tests/test_core_api_client.py
from __future__ import annotations

import datetime

import httpx
import pytest
import respx

from envoy_collector.core_api_client import (
    CoreApiError,
    fetch_source_config,
    push_energy_readings,
    report_discovered_config,
)

BASE_URL = "http://localhost:8000"


@respx.mock
def test_fetch_source_config_sends_collector_key_header():
    route = respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(
        return_value=httpx.Response(200, json={"config": {"host": "envoy.local", "token": "abc"}})
    )
    result = fetch_source_config(BASE_URL, 1, "my-key")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    assert result == {"host": "envoy.local", "token": "abc"}


@respx.mock
def test_fetch_source_config_raises_on_non_200():
    respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(return_value=httpx.Response(401))
    with pytest.raises(CoreApiError):
        fetch_source_config(BASE_URL, 1, "wrong-key")


@respx.mock
def test_report_discovered_config_sends_serial_num():
    route = respx.patch(f"{BASE_URL}/api/v1/sources/1/discovered-config").mock(
        return_value=httpx.Response(204)
    )
    report_discovered_config(BASE_URL, 1, "my-key", "123456789012")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    import json as _json

    assert _json.loads(route.calls[0].request.content) == {"serial_num": "123456789012"}


@respx.mock
def test_report_discovered_config_raises_on_non_204():
    respx.patch(f"{BASE_URL}/api/v1/sources/1/discovered-config").mock(
        return_value=httpx.Response(422)
    )
    with pytest.raises(CoreApiError):
        report_discovered_config(BASE_URL, 1, "my-key", "x")


@respx.mock
def test_push_energy_readings_sends_batch_as_iso_timestamps():
    route = respx.post(f"{BASE_URL}/api/v1/sources/1/readings/energy").mock(
        return_value=httpx.Response(202, json={"ingested": 1})
    )
    ts = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=datetime.timezone.utc)
    push_energy_readings(BASE_URL, 1, "my-key", [{"ts": ts, "metric": "production_w", "value": 100.0}])

    import json as _json

    body = _json.loads(route.calls[0].request.content)
    assert body == {"readings": [{"ts": "2026-09-29T12:00:00+00:00", "metric": "production_w", "value": 100.0}]}


@respx.mock
def test_push_energy_readings_raises_on_non_202():
    respx.post(f"{BASE_URL}/api/v1/sources/1/readings/energy").mock(return_value=httpx.Response(422))
    with pytest.raises(CoreApiError):
        push_energy_readings(BASE_URL, 1, "my-key", [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_core_api_client.py -v`
Expected: FAIL — `envoy_collector.core_api_client` doesn't exist.

- [ ] **Step 3: Implement `envoy_collector/core_api_client.py`**

```python
from __future__ import annotations

import httpx


class CoreApiError(Exception):
    """Raised on any non-success response from Core API."""


def fetch_source_config(base_url: str, source_id: int, collector_key: str) -> dict:
    response = httpx.get(
        f"{base_url}/api/v1/sources/{source_id}/config",
        headers={"X-Collector-Key": collector_key},
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch source config: {response.status_code} {response.text}")
    return response.json()["config"]


def report_discovered_config(base_url: str, source_id: int, collector_key: str, serial_num: str) -> None:
    response = httpx.patch(
        f"{base_url}/api/v1/sources/{source_id}/discovered-config",
        headers={"X-Collector-Key": collector_key},
        json={"serial_num": serial_num},
        timeout=10,
    )
    if response.status_code != 204:
        raise CoreApiError(
            f"Failed to report discovered config: {response.status_code} {response.text}"
        )


def push_energy_readings(base_url: str, source_id: int, collector_key: str, readings: list[dict]) -> None:
    payload = {
        "readings": [
            {"ts": r["ts"].isoformat(), "metric": r["metric"], "value": r["value"]} for r in readings
        ]
    }
    response = httpx.post(
        f"{base_url}/api/v1/sources/{source_id}/readings/energy",
        headers={"X-Collector-Key": collector_key},
        json=payload,
        timeout=10,
    )
    if response.status_code != 202:
        raise CoreApiError(f"Failed to push readings: {response.status_code} {response.text}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_core_api_client.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run full suite, then commit**

```bash
uv run pytest -q
git add envoy_collector/core_api_client.py tests/test_core_api_client.py
git commit -m "Add Core API HTTP client"
```

---

## Task 7: Envoy collector — poll loop and startup

**Files:**
- Create: `envoy_collector/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: everything from Tasks 3-6.
- Produces: `discover_and_sync_serial(settings, config) -> None`, `poll_once(settings, config) -> dict`, `main() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# envoy-collector/tests/test_main.py
from __future__ import annotations

from unittest.mock import call

import pytest

from envoy_collector import main as main_module
from envoy_collector.config import Settings
from envoy_collector.envoy_client import EnvoyAuthError
from envoy_collector.core_api_client import CoreApiError

SETTINGS = Settings(
    core_api_url="http://localhost:8000", source_id=1, collector_key="key", poll_interval_seconds=60
)


def test_discover_and_sync_serial_reports_when_changed(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_serial_number", lambda host: "new-serial")
    reported = {}
    monkeypatch.setattr(
        main_module,
        "report_discovered_config",
        lambda base_url, source_id, key, serial_num: reported.update(serial_num=serial_num),
    )

    main_module.discover_and_sync_serial(SETTINGS, {"host": "envoy.local", "serial_num": "old"})

    assert reported == {"serial_num": "new-serial"}


def test_discover_and_sync_serial_skips_report_when_unchanged(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_serial_number", lambda host: "same")
    called = []
    monkeypatch.setattr(
        main_module, "report_discovered_config", lambda *a, **k: called.append((a, k))
    )

    main_module.discover_and_sync_serial(SETTINGS, {"host": "envoy.local", "serial_num": "same"})

    assert called == []


def test_discover_and_sync_serial_does_not_raise_when_envoy_unreachable(monkeypatch):
    def _raise(host):
        raise OSError("network unreachable")

    monkeypatch.setattr(main_module, "fetch_serial_number", _raise)
    main_module.discover_and_sync_serial(SETTINGS, {"host": "envoy.local"})  # must not raise


def test_poll_once_pushes_mapped_readings_on_success(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_production", lambda host, token: {"production": []})
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token: {"devices:": []})
    monkeypatch.setattr(main_module, "map_production_readings", lambda data, ts: [{"ts": ts, "metric": "production_w", "value": 1.0}])
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])
    pushed = []
    monkeypatch.setattr(
        main_module, "push_energy_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )

    config = {"host": "envoy.local", "token": "abc"}
    result_config = main_module.poll_once(SETTINGS, config)

    assert result_config == config
    assert len(pushed) == 1
    assert pushed[0][0]["metric"] == "production_w"


def test_poll_once_refetches_config_once_on_envoy_401_then_succeeds(monkeypatch):
    calls = {"production": 0}

    def fake_fetch_production(host, token):
        calls["production"] += 1
        if token == "old-token":
            raise EnvoyAuthError("nope")
        return {"production": []}

    monkeypatch.setattr(main_module, "fetch_production", fake_fetch_production)
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token: {"devices:": []})
    monkeypatch.setattr(main_module, "map_production_readings", lambda data, ts: [])
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])
    monkeypatch.setattr(
        main_module,
        "fetch_source_config",
        lambda base_url, source_id, key: {"host": "envoy.local", "token": "new-token"},
    )
    monkeypatch.setattr(main_module, "push_energy_readings", lambda *a, **k: None)

    result_config = main_module.poll_once(SETTINGS, {"host": "envoy.local", "token": "old-token"})

    assert result_config["token"] == "new-token"
    assert calls["production"] == 2  # one failed attempt, one retry after refetch


def test_poll_once_logs_and_returns_unchanged_config_when_still_401_after_refetch(monkeypatch, caplog):
    monkeypatch.setattr(main_module, "fetch_production", lambda host, token: (_ for _ in ()).throw(EnvoyAuthError("nope")))
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token: {"devices:": []})
    monkeypatch.setattr(
        main_module,
        "fetch_source_config",
        lambda base_url, source_id, key: {"host": "envoy.local", "token": "old-token", "serial_num": "s"},
    )

    with caplog.at_level("ERROR"):
        result_config = main_module.poll_once(SETTINGS, {"host": "envoy.local", "token": "old-token"})

    assert result_config == {"host": "envoy.local", "token": "old-token", "serial_num": "s"}
    assert any("entrez-auth-token" in message for message in caplog.messages)


def test_poll_once_skips_cycle_when_envoy_unreachable(monkeypatch, caplog):
    def _raise(host, token):
        raise OSError("network unreachable")

    monkeypatch.setattr(main_module, "fetch_production", _raise)
    config = {"host": "envoy.local", "token": "abc"}

    with caplog.at_level("ERROR"):
        result_config = main_module.poll_once(SETTINGS, config)

    assert result_config == config  # unchanged, no crash


def test_poll_once_skips_push_but_does_not_raise_when_core_api_unreachable(monkeypatch, caplog):
    monkeypatch.setattr(main_module, "fetch_production", lambda host, token: {"production": []})
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token: {"devices:": []})
    monkeypatch.setattr(main_module, "map_production_readings", lambda data, ts: [{"ts": ts, "metric": "production_w", "value": 1.0}])
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])

    def _raise_core_api(*a, **k):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(main_module, "push_energy_readings", _raise_core_api)

    config = {"host": "envoy.local", "token": "abc"}
    with caplog.at_level("ERROR"):
        result_config = main_module.poll_once(SETTINGS, config)

    assert result_config == config  # no crash, cycle just skipped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_main.py -v`
Expected: FAIL — `envoy_collector.main` doesn't exist.

- [ ] **Step 3: Implement `envoy_collector/main.py`**

```python
from __future__ import annotations

import datetime
import logging
import time

from envoy_collector.config import Settings, get_settings
from envoy_collector.core_api_client import (
    CoreApiError,
    fetch_source_config,
    push_energy_readings,
    report_discovered_config,
)
from envoy_collector.envoy_client import EnvoyAuthError, fetch_battery_power, fetch_production, fetch_serial_number
from envoy_collector.mapping import map_battery_readings, map_production_readings

logger = logging.getLogger("envoy_collector")


def discover_and_sync_serial(settings: Settings, config: dict) -> None:
    try:
        serial = fetch_serial_number(config["host"])
    except Exception:
        logger.exception("Failed to fetch Envoy serial number from /info.xml")
        return
    if config.get("serial_num") != serial:
        try:
            report_discovered_config(settings.core_api_url, settings.source_id, settings.collector_key, serial)
            logger.info("Reported discovered Envoy serial number %s", serial)
        except CoreApiError:
            logger.exception("Failed to report discovered serial number to Core API")


def _renewal_message(config: dict) -> str:
    serial_num = config.get("serial_num", "<unknown - check Core API source config>")
    return (
        "Envoy token still invalid after refetching config. Renew it at: "
        f"https://enlighten.enphaseenergy.com/entrez-auth-token?serial_num={serial_num}"
    )


def poll_once(settings: Settings, config: dict) -> dict:
    """Runs one poll cycle. Returns the config to use next cycle (unchanged,
    unless an Envoy 401 triggered a successful refetch)."""
    ts = datetime.datetime.now(datetime.timezone.utc)
    try:
        production = fetch_production(config["host"], config["token"])
        battery = fetch_battery_power(config["host"], config["token"])
    except EnvoyAuthError:
        logger.warning("Envoy rejected our token; refetching config from Core API")
        try:
            config = fetch_source_config(settings.core_api_url, settings.source_id, settings.collector_key)
        except CoreApiError:
            logger.exception("Failed to refetch source config after Envoy auth failure")
            return config
        try:
            production = fetch_production(config["host"], config["token"])
            battery = fetch_battery_power(config["host"], config["token"])
        except EnvoyAuthError:
            logger.error(_renewal_message(config))
            return config
        except Exception:
            logger.exception("Failed to poll Envoy this cycle (after config refetch)")
            return config
    except Exception:
        logger.exception("Failed to poll Envoy this cycle")
        return config

    readings = map_production_readings(production, ts) + map_battery_readings(battery, ts)
    try:
        push_energy_readings(settings.core_api_url, settings.source_id, settings.collector_key, readings)
    except CoreApiError:
        logger.exception("Failed to push readings to Core API this cycle")
    return config


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    config = fetch_source_config(settings.core_api_url, settings.source_id, settings.collector_key)
    discover_and_sync_serial(settings, config)

    while True:
        config = poll_once(settings, config)
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_main.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run full suite, then commit**

```bash
uv run pytest -q
git add envoy_collector/main.py tests/test_main.py
git commit -m "Add poll loop with startup serial-discovery and 401-refetch handling"
```

---

## Task 8: Deployment packaging

**Files:**
- Create: `Dockerfile`

**Interfaces:** none (packaging only).

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY envoy_collector ./envoy_collector

CMD ["uv", "run", "python", "-m", "envoy_collector.main"]
```

- [ ] **Step 2: Attempt to build the image**

Run: `docker build -t envoy-collector .`
Expected: image builds successfully. **If Docker isn't available in this environment**, report this step as BLOCKED with that specific reason (not a code problem) rather than skipping it silently — the Dockerfile still needs a human or CI with Docker available to verify it before this is truly done.

- [ ] **Step 3: If the build succeeded, do a smoke run**

```bash
docker run --rm -e CORE_API_URL=http://localhost:8000 -e CORE_API_SOURCE_ID=1 -e CORE_API_COLLECTOR_KEY=test envoy-collector python -c "from envoy_collector.config import get_settings; print(get_settings())"
```

Expected: prints the `Settings` object without error (proves the image has the package installed and importable — it will still fail on `main()` in a real run since there's no reachable Core API here, which is expected).

- [ ] **Step 4: Commit**

```bash
git add Dockerfile
git commit -m "Add Dockerfile for containerized deployment"
```
