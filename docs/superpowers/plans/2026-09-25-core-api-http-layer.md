# Core API HTTP Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FastAPI HTTP layer for Core API — JWT user auth, collector-key ingestion auth, CRUD for Site/SiteAccess/Source/SiteTariff/DemandWindow/BillingCycle, ingestion endpoints for energy/weather/price data, query endpoints including the on-read cost calculation, and the demand-charges read endpoint — completing sub-project 1's "API contract" per the design spec, on top of the schema/bootstrapping already built.

**Architecture:** A single FastAPI app (`core_api/main.py`) with thin routers (route handler + one SQLAlchemy query each, per the spec's boundaries) grouped by resource. The DB session is a per-request dependency resolved from `request.app.state.session_factory`, set at app-creation time via `create_app(database_url=...)` — this makes the app trivially testable (each test builds its own app against its own temp SQLite file) without global mutable state or `dependency_overrides` plumbing. Auth is two schemes: JWT bearer for users (`Authorization: Bearer <jwt>`), and a raw pre-shared key for collectors (`X-Collector-Key: <raw_key>`, checked against a stored SHA-256 hash). All errors go through one exception type (`AppError`) and one exception handler, so every error response has the spec's `{code, message, detail}` shape.

**Tech Stack:** FastAPI, Pydantic v2 (ships with FastAPI), PyJWT (JWT), bcrypt (password hashing), SQLAlchemy 2.0 (existing), httpx (TestClient dependency, dev-only).

**Spec:** `docs/superpowers/specs/2026-09-25-core-api-design.md` (see 'API contract' and 'Failure handling' sections in particular).

## Global Constraints

- Every mutating request from a user must pass through `require_site_owner` (owner-only writes) or `require_site_access` (owner-or-viewer reads) — never trust a `site_id` path/body parameter without one of these.
- Every error response body is `{"code": str, "message": str, "detail": ... | null}`, raised via `AppError`, never a bare `HTTPException` with a plain string.
- Ingestion batch endpoints are all-or-nothing: a malformed batch returns 422 with per-row detail; nothing is partially written (spec: 'API contract').
- `collector_api_key` is never stored or returned in plaintext after creation; only shown once in the create-Source response body.
- JWT secret comes from the `JWT_SECRET` environment variable with no insecure default — `get_settings()` raises if it's unset, so a misconfigured deployment fails loudly at startup instead of running with a guessable key.
- CRUD scope for this plan: create/list/get for every entity, plus the specific updates the spec's domain model actually needs (`Source.enabled` toggle, closing an effective-dated row via `effective_to`). No delete endpoints for effective-dated entities (Source, SiteTariff, BillingCycle, DemandWindow) — closing `effective_to` is the delete-equivalent that preserves historical billing/readings correctness, matching the pattern the schema already uses. `SiteAccess` (not effective-dated) supports revoke (hard delete of the grant row).

## Review Focus

- A viewer-role user hitting a write endpoint must get 403, not silently succeed. `require_site_owner` itself is tested directly in Task 3 (denies a viewer); at least one router beyond that (Task 5's `SiteAccess` grant endpoint) re-proves it end-to-end through a real router, so a future router that forgets to apply the dependency at all — not just one where the dependency itself is broken — would still be caught by that pattern being visibly required.
- A user with no relationship to a site at all (not owner, no `SiteAccess` row) must get 403/404 on every read endpoint for that site, not just the ones tested first.
- An ingestion request with a valid `collector_api_key_hash` for a *different* source than the URL's `{source_id}` must be rejected — the key must be checked against that specific source, not just "some valid key."
- A `/cost` query over a range where price data is missing for part of the range must surface the gap (e.g. a `minutes_missing_price` count) rather than silently treating missing price as free energy.
- Re-POSTing an identical ingestion batch (collector retry after a timeout) must be a no-op on the data (idempotent upsert), not a duplicate-row or duplicate-charge outcome — this is the spec's core failure-handling guarantee and needs a test through the actual HTTP endpoint, not just at the ORM layer (already covered at the ORM layer in `tests/test_schema_constraints.py`).

---

## File Structure

- `core_api/config.py` — **modify**: add `jwt_secret` (required, raises if unset) and `jwt_expiry_hours` (default 24) to `Settings`.
- `core_api/security.py` — **create**: password hashing (bcrypt) + JWT create/decode.
- `core_api/errors.py` — **create**: `AppError` exception + `{code, message, detail}` response shape + FastAPI exception handler registration.
- `core_api/deps.py` — **create**: `get_db_session`, `get_current_user`, `require_site_access`, `require_site_owner`, `get_source_by_collector_key`.
- `core_api/main.py` — **create**: `create_app(database_url=None)` factory; mounts all routers + error handler.
- `core_api/schemas.py` — **create**: every Pydantic request/response model (one file — they're small and reviewed together far more often than in isolation).
- `core_api/routers/__init__.py` — **create**: empty.
- `core_api/routers/auth.py` — **create**: `POST /auth/login`.
- `core_api/routers/sites.py` — **create**: Site CRUD + SiteAccess grant/list/revoke.
- `core_api/routers/sources.py` — **create**: Source CRUD.
- `core_api/routers/billing.py` — **create**: SiteTariff, DemandWindow, BillingCycle CRUD.
- `core_api/routers/ingestion.py` — **create**: energy/weather/price ingestion.
- `core_api/routers/query.py` — **create**: readings/prices/cost/demand-charges reads.
- `core_api/readings_query.py` — **create**: the shared raw-vs-rollup range-routing helper the spec calls for.
- `core_api/cost.py` — **create**: the on-read cost-calculation module.
- `tests/test_app.py` — **create**: shared `client(tmp_path)` fixture building a fresh app+migrated DB per test.
- `tests/test_auth.py`, `tests/test_sites.py`, `tests/test_sources.py`, `tests/test_billing.py`, `tests/test_ingestion.py`, `tests/test_query.py`, `tests/test_cost.py` — **create**: one test file per router/module.
- `pyproject.toml` — **modify**: add `fastapi`, `pyjwt`, `bcrypt` to `dependencies`; add `httpx` to the `dev` group.

## Interfaces reference (carried through every task)

```python
# core_api/errors.py
class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, detail: object | None = None): ...

# core_api/deps.py
def get_db_session(request: Request) -> Iterator[Session]: ...
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(...), db: Session = Depends(get_db_session)) -> User: ...
def require_site_access(site_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db_session)) -> Site: ...
def require_site_owner(site_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db_session)) -> Site: ...
def get_source_by_collector_key(source_id: int, x_collector_key: str = Header(...), db: Session = Depends(get_db_session)) -> Source: ...

# core_api/security.py
def hash_password(password: str) -> str: ...
def verify_password(password: str, password_hash: str) -> bool: ...
def create_access_token(user_id: int) -> str: ...
def decode_access_token(token: str) -> int: ...  # returns user_id, raises InvalidTokenError

# core_api/main.py
def create_app(database_url: str | None = None) -> FastAPI: ...

# core_api/readings_query.py
def query_readings(db: Session, model_raw: type, model_rollup: type, *, key_column, key_value, ts_start, ts_end, metric_column=None, metric_value=None) -> list: ...

# core_api/cost.py
@dataclasses.dataclass
class CostResult:
    total_cost_c: Decimal
    total_import_kwh: Decimal
    total_export_kwh: Decimal
    minutes_missing_price: int

def compute_cost(db: Session, site_id: int, start: datetime, end: datetime, method: Literal["net", "gross"]) -> CostResult: ...
```

---

## Task 1: Dependencies, settings, error handling, DB session dependency

**Files:**
- Modify: `pyproject.toml`
- Create: `core_api/errors.py`
- Modify: `core_api/config.py`
- Create: `core_api/deps.py` (only `get_db_session` in this task; auth deps come in Task 4)
- Test: `tests/test_errors.py`

**Interfaces:**
- Produces: `AppError`, `install_error_handlers(app: FastAPI) -> None`, `Settings.jwt_secret`, `Settings.jwt_expiry_hours`, `get_db_session(request: Request) -> Iterator[Session]`.

- [ ] **Step 1: Add dependencies**

Edit `pyproject.toml`'s `dependencies` list to add `"fastapi>=0.110,<1.0"`, `"pyjwt>=2.8,<3.0"`, `"bcrypt>=4.1,<5.0"`, and its `[dependency-groups].dev` list to add `"httpx>=0.27,<1.0"`. Then run:

```bash
uv sync
```

- [ ] **Step 2: Write the failing test for AppError's response shape**

```python
# tests/test_errors.py
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core_api.errors import AppError, install_error_handlers


def make_test_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise AppError(404, "not_found", "Widget not found", detail={"widget_id": 42})

    return app


def test_app_error_renders_structured_body():
    client = TestClient(make_test_app())
    response = client.get("/boom")
    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "Widget not found",
        "detail": {"widget_id": 42},
    }
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core_api.errors'`

- [ ] **Step 4: Implement `core_api/errors.py`**

```python
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, detail: object | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "detail": exc.detail},
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_errors.py -v`
Expected: PASS

- [ ] **Step 6: Add JWT settings to config.py**

```python
# core_api/config.py
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str
    jwt_expiry_hours: int


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
    )
```

- [ ] **Step 7: Update existing tests/conftest.py and any script that calls get_settings() indirectly**

`core_api/bootstrap.py` and `alembic/env.py` both call `get_settings().database_url` — neither touches `jwt_secret`, so they still work, but `get_settings()` will now raise if `JWT_SECRET` isn't set, which breaks `alembic revision --autogenerate` and `python -m core_api.bootstrap` run from a bare shell. Add a `JWT_SECRET` default export instruction to a new `.env.example` file for humans, and export it inline for the two commands below when running them locally:

```bash
JWT_SECRET=dev-local-only uv run alembic revision --autogenerate -m "..."
JWT_SECRET=dev-local-only uv run python -m core_api.bootstrap
```

Create `core-api/.env.example`:

```
DATABASE_URL=sqlite:///./core_api.db
JWT_SECRET=change-me-to-a-long-random-value
JWT_EXPIRY_HOURS=24
```

- [ ] **Step 8: Write `core_api/deps.py` with `get_db_session` only**

```python
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from core_api.db import get_db


def get_db_session(request: Request) -> Iterator[Session]:
    yield from get_db(request.app.state.session_factory)
```

- [ ] **Step 9: Run the full suite to confirm nothing existing broke**

Run: `JWT_SECRET=dev-local-only uv run pytest -q`
Expected: all prior tests (13) plus the new one (14) PASS.

- [ ] **Step 10: Commit**

```bash
cd core-api
git add pyproject.toml uv.lock core_api/errors.py core_api/config.py core_api/deps.py tests/test_errors.py .env.example
git commit -m "Add FastAPI deps, structured AppError, JWT settings, DB session dependency"
```

---

## Task 2: Password hashing + JWT utilities

**Files:**
- Create: `core_api/security.py`
- Test: `tests/test_security.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 1.
- Produces: `hash_password(password: str) -> str`, `verify_password(password: str, password_hash: str) -> bool`, `create_access_token(user_id: int) -> str`, `decode_access_token(token: str) -> int`, `InvalidTokenError` (re-exported from `jwt`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_security.py
from __future__ import annotations

import time

import jwt
import pytest

from core_api.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_password_round_trips(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_create_and_decode_access_token(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    token = create_access_token(user_id=42)
    assert decode_access_token(token) == 42


def test_decode_access_token_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    token = create_access_token(user_id=42)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(tampered)


def test_decode_access_token_rejects_expired_token(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("JWT_EXPIRY_HOURS", "0")
    token = create_access_token(user_id=42)
    time.sleep(1.1)
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_security.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core_api.security'`

- [ ] **Step 3: Implement `core_api/security.py`**

```python
from __future__ import annotations

import datetime

import bcrypt
import jwt

from core_api.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + datetime.timedelta(hours=settings.jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> int:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    return int(payload["sub"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_security.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add core_api/security.py tests/test_security.py
git commit -m "Add password hashing and JWT create/decode utilities"
```

---

## Task 3: App factory + auth dependencies (`get_current_user`, `require_site_access`, `require_site_owner`)

**Files:**
- Create: `core_api/main.py`
- Modify: `core_api/deps.py` (add the three dependencies below)
- Test: `tests/test_app.py` (shared fixture other test files import), `tests/test_deps.py`

**Interfaces:**
- Consumes: `create_access_token`/`decode_access_token` (Task 2), `AppError`/`install_error_handlers` (Task 1), `get_db_session` (Task 1), `run_migrations` (existing `core_api/bootstrap.py`).
- Produces: `create_app(database_url: str | None = None) -> FastAPI`; `get_current_user`, `require_site_access`, `require_site_owner` in `core_api/deps.py`.

- [ ] **Step 1: Write the shared test app fixture**

```python
# tests/test_app.py
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core_api.bootstrap import run_migrations
from core_api.main import create_app


@pytest.fixture()
def app_and_db_url(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    db_url = f"sqlite:///{tmp_path}/app_test.db"
    run_migrations(db_url)
    app = create_app(database_url=db_url)
    return app, db_url


@pytest.fixture()
def client(app_and_db_url):
    app, _ = app_and_db_url
    return TestClient(app)


def test_create_app_boots(client):
    # No routes yet at this point in the plan beyond error handling; this
    # just proves the app + migrated DB + session dependency all wire up.
    response = client.get("/openapi.json")
    assert response.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core_api.main'`

- [ ] **Step 3: Implement `core_api/main.py`**

```python
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from core_api.db import make_engine, make_session_factory
from core_api.errors import install_error_handlers


def create_app(database_url: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(database_url)
        app.state.session_factory = make_session_factory(engine)
        yield

    app = FastAPI(title="Core API", lifespan=lifespan)
    install_error_handlers(app)
    return app
```

`TestClient(app)` runs the `lifespan` context manager on `__enter__`/first request in modern Starlette, so `app.state.session_factory` is set before any route executes — no manual startup call needed in tests.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for the three auth dependencies**

```python
# tests/test_deps.py
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from core_api.bootstrap import run_migrations
from core_api.db import get_db, make_engine, make_session_factory
from core_api.deps import get_current_user, require_site_access, require_site_owner
from core_api.main import create_app
from core_api.models.identity import Site, SiteAccess, SiteAccessRole, User
from core_api.security import create_access_token


@pytest.fixture()
def wired_app(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    db_url = f"sqlite:///{tmp_path}/deps_test.db"
    run_migrations(db_url)
    app = create_app(database_url=db_url)

    @app.get("/whoami")
    def whoami(user: User = Depends(get_current_user)):
        return {"user_id": user.id}

    @app.get("/sites/{site_id}/peek")
    def peek(site: Site = Depends(require_site_access)):
        return {"site_id": site.id}

    @app.get("/sites/{site_id}/owner-only")
    def owner_only(site: Site = Depends(require_site_owner)):
        return {"site_id": site.id}

    return app, db_url


def seed(db_url: str) -> dict:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="owner@example.com", password_hash="x")
    viewer = User(email="viewer@example.com", password_hash="x")
    stranger = User(email="stranger@example.com", password_hash="x")
    session.add_all([owner, viewer, stranger])
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    session.add(SiteAccess(site_id=site.id, user_id=viewer.id, role=SiteAccessRole.VIEWER))
    session.commit()
    ids = {"owner": owner.id, "viewer": viewer.id, "stranger": stranger.id, "site": site.id}
    session.close()
    return ids


def auth(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_get_current_user_requires_valid_token(wired_app):
    app, _ = wired_app
    client = TestClient(app)
    assert client.get("/whoami").status_code in (401, 403)  # no header at all


def test_get_current_user_accepts_valid_token(wired_app):
    app, db_url = wired_app
    ids = seed(db_url)
    client = TestClient(app)
    response = client.get("/whoami", headers=auth(ids["owner"]))
    assert response.status_code == 200
    assert response.json() == {"user_id": ids["owner"]}


def test_require_site_access_allows_owner_and_viewer_denies_stranger(wired_app):
    app, db_url = wired_app
    ids = seed(db_url)
    client = TestClient(app)
    assert client.get(f"/sites/{ids['site']}/peek", headers=auth(ids["owner"])).status_code == 200
    assert client.get(f"/sites/{ids['site']}/peek", headers=auth(ids["viewer"])).status_code == 200
    assert client.get(f"/sites/{ids['site']}/peek", headers=auth(ids["stranger"])).status_code == 403


def test_require_site_owner_denies_viewer(wired_app):
    app, db_url = wired_app
    ids = seed(db_url)
    client = TestClient(app)
    assert client.get(f"/sites/{ids['site']}/owner-only", headers=auth(ids["owner"])).status_code == 200
    assert client.get(f"/sites/{ids['site']}/owner-only", headers=auth(ids["viewer"])).status_code == 403
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_deps.py -v`
Expected: FAIL — `get_current_user`, `require_site_access`, `require_site_owner` don't exist yet.

- [ ] **Step 7: Add the three dependencies to `core_api/deps.py`**

```python
# Append to core_api/deps.py
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt as pyjwt

from core_api.errors import AppError
from core_api.models.identity import Site, SiteAccess, User
from core_api.security import decode_access_token

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db_session),
) -> User:
    if credentials is None:
        raise AppError(401, "missing_token", "Authorization header is required")
    try:
        user_id = decode_access_token(credentials.credentials)
    except pyjwt.InvalidTokenError as exc:
        raise AppError(401, "invalid_token", "Invalid or expired token") from exc
    user = db.get(User, user_id)
    if user is None:
        raise AppError(401, "invalid_token", "User no longer exists")
    return user


def require_site_access(
    site_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise AppError(404, "not_found", f"Site {site_id} not found")
    if site.owner_user_id != current_user.id:
        access = db.get(SiteAccess, (site_id, current_user.id))
        if access is None:
            raise AppError(403, "forbidden", "No access to this site")
    return site


def require_site_owner(
    site_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise AppError(404, "not_found", f"Site {site_id} not found")
    if site.owner_user_id != current_user.id:
        raise AppError(403, "forbidden", "Only the site owner can perform this action")
    return site
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_deps.py -v`
Expected: PASS (4 tests)

- [ ] **Step 9: Run full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add core_api/main.py core_api/deps.py tests/test_app.py tests/test_deps.py
git commit -m "Add app factory and JWT/site-access auth dependencies"
```

---

## Task 4: `POST /auth/login`

**Files:**
- Create: `core_api/routers/__init__.py`, `core_api/routers/auth.py`
- Modify: `core_api/main.py` (include the router), `core_api/schemas.py` (create if it doesn't exist yet)
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `hash_password`/`verify_password`/`create_access_token` (Task 2), `require_site_access`-style deps pattern, `AppError` (Task 1).
- Produces: `router` (APIRouter) in `core_api/routers/auth.py`, mounted at `/auth`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth.py
from __future__ import annotations

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import User
from core_api.security import hash_password


def seed_user(db_url: str, email: str, password: str) -> None:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    session.add(User(email=email, password_hash=hash_password(password)))
    session.commit()
    session.close()


def test_login_succeeds_with_correct_password(client, app_and_db_url):
    _, db_url = app_and_db_url
    seed_user(db_url, "a@example.com", "correct-password")
    response = client.post("/auth/login", json={"email": "a@example.com", "password": "correct-password"})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_rejects_wrong_password(client, app_and_db_url):
    _, db_url = app_and_db_url
    seed_user(db_url, "a@example.com", "correct-password")
    response = client.post("/auth/login", json={"email": "a@example.com", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_login_rejects_unknown_email(client, app_and_db_url):
    response = client.post("/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"
```

(`client`/`app_and_db_url` fixtures come from `tests/test_app.py` — move them into `tests/conftest.py` in this step so every router test file can use them without re-declaring. Delete the now-duplicate fixture definitions from `tests/test_app.py` and import nothing extra — pytest fixtures in `conftest.py` are auto-discovered.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth.py -v`
Expected: FAIL with 404 (no `/auth/login` route yet).

- [ ] **Step 3: Create `core_api/schemas.py` with the login schemas**

```python
from __future__ import annotations

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
```

Add `"pydantic[email]"` is not needed separately — `EmailStr` requires the `email-validator` package. Add `"email-validator>=2.0,<3.0"` to `pyproject.toml` dependencies and `uv sync`.

- [ ] **Step 4: Implement `core_api/routers/auth.py`**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.deps import get_db_session
from core_api.errors import AppError
from core_api.models.identity import User
from core_api.schemas import LoginRequest, LoginResponse
from core_api.security import create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db_session)) -> LoginResponse:
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AppError(401, "invalid_credentials", "Email or password is incorrect")
    return LoginResponse(access_token=create_access_token(user.id))
```

- [ ] **Step 5: Mount the router in `core_api/main.py`**

```python
# core_api/main.py — add import and one line inside create_app, before `return app`
from core_api.routers.auth import router as auth_router
...
    app.include_router(auth_router)
    return app
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_auth.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Run full suite, then commit**

Run: `uv run pytest -q`

```bash
git add core_api/routers core_api/schemas.py core_api/main.py tests/test_auth.py tests/conftest.py tests/test_app.py pyproject.toml uv.lock
git commit -m "Add POST /auth/login"
```

---

## Task 5: Site CRUD + SiteAccess grant/list/revoke

**Files:**
- Create: `core_api/routers/sites.py`
- Modify: `core_api/schemas.py`, `core_api/main.py`
- Test: `tests/test_sites.py`

**Interfaces:**
- Consumes: `require_site_access`, `require_site_owner`, `get_current_user`, `get_db_session` (Task 3), `AppError` (Task 1).
- Produces: `router` mounted at `/sites` exposing `POST /sites`, `GET /sites`, `GET /sites/{site_id}`, `POST /sites/{site_id}/access`, `GET /sites/{site_id}/access`, `DELETE /sites/{site_id}/access/{user_id}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sites.py
from __future__ import annotations

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import User
from core_api.security import create_access_token


def make_user(db_url: str, email: str) -> int:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = User(email=email, password_hash="x")
    session.add(user)
    session.commit()
    user_id = user.id
    session.close()
    return user_id


def auth(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_create_and_get_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    create = client.post(
        "/sites", json={"name": "Home", "timezone": "Australia/Sydney"}, headers=auth(owner_id)
    )
    assert create.status_code == 201, create.text
    site_id = create.json()["id"]

    get = client.get(f"/sites/{site_id}", headers=auth(owner_id))
    assert get.status_code == 200
    assert get.json()["name"] == "Home"


def test_list_sites_returns_only_accessible_sites(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner2@example.com")
    stranger_id = make_user(db_url, "stranger2@example.com")
    client.post("/sites", json={"name": "Home", "timezone": "UTC"}, headers=auth(owner_id))

    mine = client.get("/sites", headers=auth(owner_id)).json()
    theirs = client.get("/sites", headers=auth(stranger_id)).json()
    assert len(mine) == 1
    assert theirs == []


def test_grant_list_and_revoke_site_access(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner3@example.com")
    viewer_id = make_user(db_url, "viewer3@example.com")
    site_id = client.post(
        "/sites", json={"name": "Home", "timezone": "UTC"}, headers=auth(owner_id)
    ).json()["id"]

    grant = client.post(
        f"/sites/{site_id}/access", json={"user_id": viewer_id}, headers=auth(owner_id)
    )
    assert grant.status_code == 201

    listing = client.get(f"/sites/{site_id}/access", headers=auth(owner_id)).json()
    assert any(row["user_id"] == viewer_id for row in listing)

    # Viewer can now read the site.
    assert client.get(f"/sites/{site_id}", headers=auth(viewer_id)).status_code == 200
    # But not grant access to others — owner-only.
    assert client.post(
        f"/sites/{site_id}/access", json={"user_id": owner_id}, headers=auth(viewer_id)
    ).status_code == 403

    revoke = client.delete(f"/sites/{site_id}/access/{viewer_id}", headers=auth(owner_id))
    assert revoke.status_code == 204
    assert client.get(f"/sites/{site_id}", headers=auth(viewer_id)).status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sites.py -v`
Expected: FAIL (404s — no `/sites` routes yet).

- [ ] **Step 3: Add Site/SiteAccess schemas to `core_api/schemas.py`**

```python
# Append to core_api/schemas.py
import datetime

from core_api.models.identity import SiteAccessRole


class SiteCreate(BaseModel):
    name: str
    timezone: str


class SiteOut(BaseModel):
    id: int
    owner_user_id: int
    name: str
    timezone: str

    model_config = {"from_attributes": True}


class SiteAccessGrant(BaseModel):
    user_id: int


class SiteAccessOut(BaseModel):
    site_id: int
    user_id: int
    role: SiteAccessRole

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Implement `core_api/routers/sites.py`**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.deps import get_current_user, get_db_session, require_site_access, require_site_owner
from core_api.errors import AppError
from core_api.models.identity import Site, SiteAccess, SiteAccessRole, User
from core_api.schemas import SiteAccessGrant, SiteAccessOut, SiteCreate, SiteOut

router = APIRouter(prefix="/sites", tags=["sites"])


@router.post("", response_model=SiteOut, status_code=status.HTTP_201_CREATED)
def create_site(
    payload: SiteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> Site:
    site = Site(owner_user_id=current_user.id, name=payload.name, timezone=payload.timezone)
    db.add(site)
    db.commit()
    db.refresh(site)
    return site


@router.get("", response_model=list[SiteOut])
def list_sites(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db_session)
) -> list[Site]:
    owned = select(Site).where(Site.owner_user_id == current_user.id)
    shared = (
        select(Site)
        .join(SiteAccess, SiteAccess.site_id == Site.id)
        .where(SiteAccess.user_id == current_user.id)
    )
    owned_sites = db.execute(owned).scalars().all()
    shared_sites = db.execute(shared).scalars().all()
    by_id = {s.id: s for s in (*owned_sites, *shared_sites)}
    return list(by_id.values())


@router.get("/{site_id}", response_model=SiteOut)
def get_site(site: Site = Depends(require_site_access)) -> Site:
    return site


@router.post("/{site_id}/access", response_model=SiteAccessOut, status_code=status.HTTP_201_CREATED)
def grant_access(
    payload: SiteAccessGrant,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> SiteAccess:
    existing = db.get(SiteAccess, (site.id, payload.user_id))
    if existing is not None:
        raise AppError(409, "already_granted", "This user already has access to the site")
    access = SiteAccess(site_id=site.id, user_id=payload.user_id, role=SiteAccessRole.VIEWER)
    db.add(access)
    db.commit()
    return access


@router.get("/{site_id}/access", response_model=list[SiteAccessOut])
def list_access(
    site: Site = Depends(require_site_owner), db: Session = Depends(get_db_session)
) -> list[SiteAccess]:
    return db.execute(select(SiteAccess).where(SiteAccess.site_id == site.id)).scalars().all()


@router.delete("/{site_id}/access/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_access(
    user_id: int,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> None:
    access = db.get(SiteAccess, (site.id, user_id))
    if access is None:
        raise AppError(404, "not_found", "No such access grant")
    db.delete(access)
    db.commit()
```

- [ ] **Step 5: Mount the router in `core_api/main.py`**

```python
from core_api.routers.sites import router as sites_router
...
    app.include_router(sites_router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_sites.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Run full suite, then commit**

Run: `uv run pytest -q`

```bash
git add core_api/routers/sites.py core_api/schemas.py core_api/main.py tests/test_sites.py
git commit -m "Add Site CRUD and SiteAccess grant/list/revoke"
```

---

## Task 6: Source CRUD (with one-time collector-key generation)

**Files:**
- Create: `core_api/routers/sources.py`
- Modify: `core_api/schemas.py`, `core_api/main.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Consumes: `require_site_owner`, `require_site_access` (Task 3).
- Produces: `router` mounted at `/sites/{site_id}/sources`: `POST`, `GET` (list), `GET /{source_id}`, `PATCH /{source_id}` (enable/disable, close `effective_to`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sources.py
from __future__ import annotations

import datetime

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, User
from core_api.security import create_access_token


def make_owner_and_site(db_url: str) -> tuple[int, int]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="src-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    ids = (owner.id, site.id)
    session.close()
    return ids


def auth(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_create_source_returns_raw_key_once(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    response = client.post(
        f"/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {"host": "envoy.local"}},
        headers=auth(owner_id),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "collector_api_key" in body and len(body["collector_api_key"]) > 20
    assert "collector_api_key_hash" not in body


def test_second_weather_source_for_same_site_rejected(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    first = client.post(
        f"/sites/{site_id}/sources",
        json={"kind": "weather", "provider": "weatherlink_live", "config": {}},
        headers=auth(owner_id),
    )
    assert first.status_code == 201
    second = client.post(
        f"/sites/{site_id}/sources",
        json={"kind": "weather", "provider": "weatherlink_live", "config": {}},
        headers=auth(owner_id),
    )
    assert second.status_code == 409


def test_disable_source(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {}},
        headers=auth(owner_id),
    ).json()["id"]

    patch = client.patch(
        f"/sites/{site_id}/sources/{source_id}", json={"enabled": False}, headers=auth(owner_id)
    )
    assert patch.status_code == 200
    assert patch.json()["enabled"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sources.py -v`
Expected: FAIL (404s).

- [ ] **Step 3: Add Source schemas to `core_api/schemas.py`**

```python
# Append to core_api/schemas.py
from core_api.models.sources import SourceKind


class SourceCreate(BaseModel):
    kind: SourceKind
    provider: str
    config: dict = {}


class SourceCreateOut(BaseModel):
    id: int
    site_id: int
    kind: SourceKind
    provider: str
    enabled: bool
    collector_api_key: str  # raw key, shown once

    model_config = {"from_attributes": True}


class SourceOut(BaseModel):
    id: int
    site_id: int
    kind: SourceKind
    provider: str
    enabled: bool
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None

    model_config = {"from_attributes": True}


class SourceUpdate(BaseModel):
    enabled: bool | None = None
    effective_to: datetime.datetime | None = None
```

- [ ] **Step 4: Implement `core_api/routers/sources.py`**

```python
from __future__ import annotations

import datetime
import hashlib
import secrets

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, require_site_access, require_site_owner
from core_api.errors import AppError
from core_api.models.identity import Site
from core_api.models.sources import Source
from core_api.schemas import SourceCreate, SourceCreateOut, SourceOut, SourceUpdate

router = APIRouter(prefix="/sites/{site_id}/sources", tags=["sources"])


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


@router.post("", response_model=SourceCreateOut, status_code=status.HTTP_201_CREATED)
def create_source(
    payload: SourceCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> SourceCreateOut:
    raw_key = secrets.token_urlsafe(32)
    source = Source(
        site_id=site.id,
        kind=payload.kind,
        provider=payload.provider,
        config=payload.config,
        collector_api_key_hash=_hash_key(raw_key),
        effective_from=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(source)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "This site already has an active source of this kind") from exc
    db.refresh(source)
    return SourceCreateOut(
        id=source.id,
        site_id=source.site_id,
        kind=source.kind,
        provider=source.provider,
        enabled=source.enabled,
        collector_api_key=raw_key,
    )


@router.get("", response_model=list[SourceOut])
def list_sources(
    site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
) -> list[Source]:
    return db.execute(select(Source).where(Source.site_id == site.id)).scalars().all()


@router.get("/{source_id}", response_model=SourceOut)
def get_source(
    source_id: int, site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
) -> Source:
    source = db.get(Source, source_id)
    if source is None or source.site_id != site.id:
        raise AppError(404, "not_found", f"Source {source_id} not found")
    return source


@router.patch("/{source_id}", response_model=SourceOut)
def update_source(
    source_id: int,
    payload: SourceUpdate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> Source:
    source = db.get(Source, source_id)
    if source is None or source.site_id != site.id:
        raise AppError(404, "not_found", f"Source {source_id} not found")
    if payload.enabled is not None:
        source.enabled = payload.enabled
    if payload.effective_to is not None:
        source.effective_to = payload.effective_to
    db.commit()
    db.refresh(source)
    return source
```

- [ ] **Step 5: Mount the router**

```python
from core_api.routers.sources import router as sources_router
...
    app.include_router(sources_router)
```

- [ ] **Step 6: Run tests, then run full suite, then commit**

```bash
uv run pytest tests/test_sources.py -v
uv run pytest -q
git add core_api/routers/sources.py core_api/schemas.py core_api/main.py tests/test_sources.py
git commit -m "Add Source CRUD with one-time collector-key generation"
```

---

## Task 7: Collector-key ingestion auth dependency

**Files:**
- Modify: `core_api/deps.py`
- Test: `tests/test_deps.py` (add to it)

**Interfaces:**
- Consumes: `Source` model, `get_db_session`.
- Produces: `get_source_by_collector_key(source_id: int, x_collector_key: str = Header(...), db: Session = Depends(get_db_session)) -> Source`.

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_deps.py
from fastapi import Header

from core_api.deps import get_source_by_collector_key
from core_api.models.sources import Source, SourceKind


def test_collector_key_auth(wired_app):
    app, db_url = wired_app

    @app.get("/sources/{source_id}/ping")
    def ping(source: Source = Depends(get_source_by_collector_key)):
        return {"source_id": source.id}

    ids = seed(db_url)
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    import hashlib
    import secrets

    raw_key = secrets.token_urlsafe(16)
    source = Source(
        site_id=ids["site"],
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        collector_api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        effective_from=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()

    client = TestClient(app)
    ok = client.get(f"/sources/{source_id}/ping", headers={"X-Collector-Key": raw_key})
    assert ok.status_code == 200

    bad = client.get(f"/sources/{source_id}/ping", headers={"X-Collector-Key": "wrong-key"})
    assert bad.status_code == 401

    missing = client.get(f"/sources/{source_id}/ping")
    assert missing.status_code in (401, 422)
```

(This test imports `make_engine`/`make_session_factory` and `TestClient`, which `tests/test_deps.py` doesn't yet import at module scope — add those imports at the top of the file alongside the existing ones.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_deps.py -v`
Expected: FAIL — `get_source_by_collector_key` doesn't exist.

- [ ] **Step 3: Add the dependency to `core_api/deps.py`**

```python
# Append to core_api/deps.py
import hashlib

from fastapi import Header

from core_api.models.sources import Source


def get_source_by_collector_key(
    source_id: int,
    x_collector_key: str = Header(...),
    db: Session = Depends(get_db_session),
) -> Source:
    source = db.get(Source, source_id)
    key_hash = hashlib.sha256(x_collector_key.encode("utf-8")).hexdigest()
    if source is None or source.collector_api_key_hash != key_hash:
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this source")
    return source
```

Note the check compares the hash for *this specific* `source_id` only — a valid key for a different source will not match `source.collector_api_key_hash` and correctly returns 401 (this is the exact scenario called out in this plan's Review Focus).

- [ ] **Step 4: Run tests, then full suite, then commit**

```bash
uv run pytest tests/test_deps.py -v
uv run pytest -q
git add core_api/deps.py tests/test_deps.py
git commit -m "Add collector-key ingestion auth dependency"
```

---

## Task 8: Ingestion — energy and weather readings

**Files:**
- Create: `core_api/routers/ingestion.py`
- Modify: `core_api/schemas.py`, `core_api/main.py`
- Test: `tests/test_ingestion.py`

**Interfaces:**
- Consumes: `get_source_by_collector_key` (Task 7).
- Produces: `router` mounted with `POST /sources/{source_id}/readings/energy`, `POST /sources/{source_id}/readings/weather`. (Prices come in Task 9, appended to the same router.)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ingestion.py
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, User
from core_api.models.sources import Source, SourceKind


def make_energy_source(db_url: str) -> tuple[int, str]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="ing-owner@example.com", password_hash="x")
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
        config={},
        collector_api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        effective_from=datetime.now(timezone.utc),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id, raw_key


def test_ingest_energy_readings_batch(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_energy_source(db_url)
    batch = {
        "readings": [
            {"ts": "2026-01-01T00:00:00Z", "metric": "production_w", "value": 1500.0},
            {"ts": "2026-01-01T00:01:00Z", "metric": "production_w", "value": 1600.0},
        ]
    }
    response = client.post(
        f"/sources/{source_id}/readings/energy", json=batch, headers={"X-Collector-Key": raw_key}
    )
    assert response.status_code == 202, response.text


def test_ingest_energy_readings_retry_is_idempotent(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_energy_source(db_url)
    batch = {"readings": [{"ts": "2026-01-01T00:00:00Z", "metric": "production_w", "value": 1500.0}]}
    headers = {"X-Collector-Key": raw_key}
    first = client.post(f"/sources/{source_id}/readings/energy", json=batch, headers=headers)
    second = client.post(f"/sources/{source_id}/readings/energy", json=batch, headers=headers)
    assert first.status_code == 202
    assert second.status_code == 202

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.readings import EnergyReading

    rows = session.query(EnergyReading).all()
    session.close()
    assert len(rows) == 1


def test_ingest_energy_readings_rejects_malformed_batch(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_energy_source(db_url)
    batch = {"readings": [{"ts": "2026-01-01T00:00:00Z", "metric": "not_a_real_metric", "value": 1.0}]}
    response = client.post(
        f"/sources/{source_id}/readings/energy", json=batch, headers={"X-Collector-Key": raw_key}
    )
    assert response.status_code == 422


def test_ingest_energy_readings_wrong_key_for_source_rejected(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, _raw_key = make_energy_source(db_url)
    other_source_id, other_key = make_energy_source(db_url)
    batch = {"readings": [{"ts": "2026-01-01T00:00:00Z", "metric": "production_w", "value": 1.0}]}
    response = client.post(
        f"/sources/{source_id}/readings/energy", json=batch, headers={"X-Collector-Key": other_key}
    )
    assert response.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ingestion.py -v`
Expected: FAIL (404s).

- [ ] **Step 3: Add ingestion schemas to `core_api/schemas.py`**

```python
# Append to core_api/schemas.py
from core_api.models.readings import EnergyMetric, WeatherMetric


class EnergyReadingIn(BaseModel):
    ts: datetime.datetime
    metric: EnergyMetric
    value: float


class EnergyReadingBatch(BaseModel):
    readings: list[EnergyReadingIn]


class WeatherReadingIn(BaseModel):
    ts: datetime.datetime
    metric: WeatherMetric
    value: float


class WeatherReadingBatch(BaseModel):
    readings: list[WeatherReadingIn]
```

- [ ] **Step 4: Implement `core_api/routers/ingestion.py`**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, get_source_by_collector_key
from core_api.models.readings import EnergyReading, WeatherReading
from core_api.models.sources import Source
from core_api.schemas import EnergyReadingBatch, WeatherReadingBatch

router = APIRouter(prefix="/sources/{source_id}", tags=["ingestion"])


@router.post("/readings/energy", status_code=status.HTTP_202_ACCEPTED)
def ingest_energy_readings(
    batch: EnergyReadingBatch,
    source: Source = Depends(get_source_by_collector_key),
    db: Session = Depends(get_db_session),
) -> dict:
    for reading in batch.readings:
        stmt = sqlite_insert(EnergyReading).values(
            source_id=source.id, ts=reading.ts, metric=reading.metric, value=reading.value
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id", "ts", "metric"], set_={"value": stmt.excluded.value}
        )
        db.execute(stmt)
    db.commit()
    return {"ingested": len(batch.readings)}


@router.post("/readings/weather", status_code=status.HTTP_202_ACCEPTED)
def ingest_weather_readings(
    batch: WeatherReadingBatch,
    source: Source = Depends(get_source_by_collector_key),
    db: Session = Depends(get_db_session),
) -> dict:
    for reading in batch.readings:
        stmt = sqlite_insert(WeatherReading).values(
            source_id=source.id, ts=reading.ts, metric=reading.metric, value=reading.value
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id", "ts", "metric"], set_={"value": stmt.excluded.value}
        )
        db.execute(stmt)
    db.commit()
    return {"ingested": len(batch.readings)}
```

Pydantic validates `metric` against the `EnergyMetric`/`WeatherMetric` enum before the handler runs at all, so a malformed metric name is rejected with FastAPI's standard 422 automatically — this is what makes the "all-or-nothing" batch guarantee free here: nothing is written until every row in the batch has parsed successfully.

- [ ] **Step 5: Mount the router**

```python
from core_api.routers.ingestion import router as ingestion_router
...
    app.include_router(ingestion_router)
```

- [ ] **Step 6: Run tests, then full suite, then commit**

```bash
uv run pytest tests/test_ingestion.py -v
uv run pytest -q
git add core_api/routers/ingestion.py core_api/schemas.py core_api/main.py tests/test_ingestion.py
git commit -m "Add energy and weather readings ingestion endpoints"
```

---

## Task 9: Ingestion — prices (with synchronous PriceReading fan-out)

**Files:**
- Modify: `core_api/routers/ingestion.py`, `core_api/schemas.py`
- Test: `tests/test_ingestion.py` (append)

**Interfaces:**
- Consumes: `get_source_by_collector_key` (Task 7).
- Produces: `POST /sources/{source_id}/prices` appended to the ingestion router.

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_ingestion.py
def make_pricing_source(db_url: str) -> tuple[int, str]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="price-owner@example.com", password_hash="x")
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
        config={},
        collector_api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        effective_from=datetime.now(timezone.utc),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id, raw_key


def test_ingest_price_interval_fans_out_to_per_minute_readings(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_pricing_source(db_url)
    batch = {
        "intervals": [
            {
                "channel": "buy",
                "interval_start": "2026-01-01T00:00:00Z",
                "interval_end": "2026-01-01T00:05:00Z",
                "price": "25.5000",
                "is_demand_window": False,
            }
        ]
    }
    response = client.post(
        f"/sources/{source_id}/prices", json=batch, headers={"X-Collector-Key": raw_key}
    )
    assert response.status_code == 202, response.text

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.pricing import PriceInterval, PriceReading

    intervals = session.query(PriceInterval).all()
    readings = session.query(PriceReading).all()
    session.close()
    assert len(intervals) == 1
    assert len(readings) == 5  # one per covered minute: :00 :01 :02 :03 :04
    assert all(r.value == readings[0].value for r in readings)


def test_ingest_price_interval_retry_is_idempotent(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, raw_key = make_pricing_source(db_url)
    batch = {
        "intervals": [
            {
                "channel": "buy",
                "interval_start": "2026-01-01T00:00:00Z",
                "interval_end": "2026-01-01T00:01:00Z",
                "price": "25.5000",
                "is_demand_window": False,
            }
        ]
    }
    headers = {"X-Collector-Key": raw_key}
    client.post(f"/sources/{source_id}/prices", json=batch, headers=headers)
    client.post(f"/sources/{source_id}/prices", json=batch, headers=headers)

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.pricing import PriceInterval, PriceReading

    assert session.query(PriceInterval).count() == 1
    assert session.query(PriceReading).count() == 1
    session.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ingestion.py -v -k price`
Expected: FAIL (404s).

- [ ] **Step 3: Add price ingestion schemas to `core_api/schemas.py`**

```python
# Append to core_api/schemas.py
import decimal

from core_api.models.pricing import PriceChannel


class PriceIntervalIn(BaseModel):
    channel: PriceChannel
    interval_start: datetime.datetime
    interval_end: datetime.datetime
    price: decimal.Decimal
    is_demand_window: bool = False


class PriceIntervalBatch(BaseModel):
    intervals: list[PriceIntervalIn]
```

- [ ] **Step 4: Append the prices endpoint to `core_api/routers/ingestion.py`**

```python
# Append to core_api/routers/ingestion.py
import datetime

from core_api.models.pricing import PriceInterval, PriceReading
from core_api.schemas import PriceIntervalBatch


@router.post("/prices", status_code=status.HTTP_202_ACCEPTED)
def ingest_prices(
    batch: PriceIntervalBatch,
    source: Source = Depends(get_source_by_collector_key),
    db: Session = Depends(get_db_session),
) -> dict:
    fanned_out = 0
    for interval in batch.intervals:
        stmt = sqlite_insert(PriceInterval).values(
            pricing_source_id=source.id,
            channel=interval.channel,
            interval_start=interval.interval_start,
            interval_end=interval.interval_end,
            price=interval.price,
            is_demand_window=interval.is_demand_window,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["pricing_source_id", "channel", "interval_start"],
            set_={
                "interval_end": stmt.excluded.interval_end,
                "price": stmt.excluded.price,
                "is_demand_window": stmt.excluded.is_demand_window,
            },
        )
        db.execute(stmt)

        minute = interval.interval_start
        while minute < interval.interval_end:
            reading_stmt = sqlite_insert(PriceReading).values(
                pricing_source_id=source.id, ts=minute, channel=interval.channel, value=interval.price
            )
            reading_stmt = reading_stmt.on_conflict_do_update(
                index_elements=["pricing_source_id", "ts", "channel"],
                set_={"value": reading_stmt.excluded.value},
            )
            db.execute(reading_stmt)
            fanned_out += 1
            minute += datetime.timedelta(minutes=1)
    db.commit()
    return {"ingested": len(batch.intervals), "readings_fanned_out": fanned_out}
```

- [ ] **Step 5: Run tests, then full suite, then commit**

```bash
uv run pytest tests/test_ingestion.py -v
uv run pytest -q
git add core_api/routers/ingestion.py core_api/schemas.py tests/test_ingestion.py
git commit -m "Add price ingestion with synchronous per-minute PriceReading fan-out"
```

---

## Task 10: Raw/rollup query helper + `GET /sites/{id}/readings/{energy|weather}`

**Files:**
- Create: `core_api/readings_query.py`
- Create: `core_api/routers/query.py`
- Modify: `core_api/schemas.py`, `core_api/main.py`
- Test: `tests/test_readings_query.py`, `tests/test_query.py`

**Interfaces:**
- Consumes: `require_site_access` (Task 3).
- Produces: `query_readings(db, model_raw, model_rollup, *, key_column, key_value, ts_start, ts_end, metric_column=None, metric_value=None) -> list` in `core_api/readings_query.py`; `GET /sites/{site_id}/readings/energy`, `GET /sites/{site_id}/readings/weather`.

- [ ] **Step 1: Write the failing test for the helper**

```python
# tests/test_readings_query.py
from __future__ import annotations

import datetime

from core_api.models.identity import Site, User
from core_api.models.readings import EnergyMetric, EnergyReading, EnergyReadingRollup5m
from core_api.models.sources import Source, SourceKind
from core_api.readings_query import RAW_RETENTION, query_readings

UTC = datetime.timezone.utc


def test_query_readings_uses_raw_table_within_retention_window(db, site):
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        collector_api_key_hash="k1",
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()

    recent_ts = datetime.datetime.now(UTC) - datetime.timedelta(days=1)
    db.add(EnergyReading(source_id=source.id, ts=recent_ts, metric=EnergyMetric.PRODUCTION_W, value=100.0))
    db.commit()

    rows = query_readings(
        db,
        EnergyReading,
        EnergyReadingRollup5m,
        key_column=EnergyReading.source_id,
        key_value=source.id,
        ts_start=recent_ts - datetime.timedelta(minutes=1),
        ts_end=recent_ts + datetime.timedelta(minutes=1),
    )
    assert len(rows) == 1
    assert rows[0].value == 100.0


def test_query_readings_uses_rollup_table_beyond_retention_window(db, site):
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        collector_api_key_hash="k2",
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()

    old_ts = datetime.datetime.now(UTC) - RAW_RETENTION - datetime.timedelta(days=1)
    db.add(
        EnergyReadingRollup5m(source_id=source.id, ts=old_ts, metric=EnergyMetric.PRODUCTION_W, value=50.0)
    )
    db.commit()

    rows = query_readings(
        db,
        EnergyReading,
        EnergyReadingRollup5m,
        key_column=EnergyReading.source_id,
        key_value=source.id,
        ts_start=old_ts - datetime.timedelta(minutes=1),
        ts_end=old_ts + datetime.timedelta(minutes=1),
    )
    assert len(rows) == 1
    assert rows[0].value == 50.0
```

(`db`/`site` fixtures already exist in `tests/conftest.py` from the schema-constraints work.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_readings_query.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `core_api/readings_query.py`**

```python
from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

# Matches the design spec's 1-year raw retention window (see 'Retention & rollup').
RAW_RETENTION = datetime.timedelta(days=365)


def query_readings(
    db: Session,
    model_raw: type,
    model_rollup: type,
    *,
    key_column: InstrumentedAttribute,
    key_value: int,
    ts_start: datetime.datetime,
    ts_end: datetime.datetime,
    metric_column: InstrumentedAttribute | None = None,
    metric_value=None,
) -> list:
    """Which table(s) does this range span? A range entirely inside the raw
    retention window reads raw; a range entirely older than it reads the
    rollup. One shared helper so every query endpoint routes the same way,
    rather than duplicating raw/rollup selection logic per endpoint."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - RAW_RETENTION
    model = model_raw if ts_start >= cutoff else model_rollup

    stmt = select(model).where(key_column == key_value, model.ts >= ts_start, model.ts <= ts_end)
    if metric_column is not None:
        stmt = stmt.where(metric_column == metric_value)
    return list(db.execute(stmt).scalars().all())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_readings_query.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Write failing tests for the HTTP endpoint**

```python
# tests/test_query.py
from __future__ import annotations

import datetime

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, User
from core_api.models.readings import EnergyMetric, EnergyReading
from core_api.models.sources import Source, SourceKind
from core_api.security import create_access_token

UTC = datetime.timezone.utc


def seed_energy_reading(db_url: str) -> tuple[int, int]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="q-owner@example.com", password_hash="x")
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
        collector_api_key_hash="k",
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    session.add(source)
    session.commit()
    ts = datetime.datetime.now(UTC) - datetime.timedelta(hours=1)
    session.add(EnergyReading(source_id=source.id, ts=ts, metric=EnergyMetric.PRODUCTION_W, value=1234.0))
    session.commit()
    ids = (owner.id, site.id)
    session.close()
    return ids


def test_get_energy_readings_for_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_energy_reading(db_url)
    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/sites/{site_id}/readings/energy",
        params={
            "metric": "production_w",
            "start": (now - datetime.timedelta(hours=2)).isoformat(),
            "end": now.isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["value"] == 1234.0


def test_get_energy_readings_denies_non_member(client, app_and_db_url):
    _, db_url = app_and_db_url
    _owner_id, site_id = seed_energy_reading(db_url)
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    stranger = User(email="q-stranger@example.com", password_hash="x")
    session.add(stranger)
    session.commit()
    stranger_id = stranger.id
    session.close()

    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/sites/{site_id}/readings/energy",
        params={
            "metric": "production_w",
            "start": (now - datetime.timedelta(hours=2)).isoformat(),
            "end": now.isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(stranger_id)}"},
    )
    assert response.status_code == 403
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_query.py -v`
Expected: FAIL (404s).

- [ ] **Step 7: Add readings query schema and implement `core_api/routers/query.py`**

```python
# Append to core_api/schemas.py
class ReadingOut(BaseModel):
    ts: datetime.datetime
    metric: str
    value: float

    model_config = {"from_attributes": True}
```

```python
# core_api/routers/query.py
from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, require_site_access
from core_api.models.identity import Site
from core_api.models.readings import EnergyMetric, EnergyReading, EnergyReadingRollup5m, WeatherMetric, WeatherReading, WeatherReadingRollup5m
from core_api.models.sources import Source, SourceKind
from core_api.readings_query import query_readings
from core_api.schemas import ReadingOut

router = APIRouter(prefix="/sites/{site_id}", tags=["query"])


def _energy_source_ids(db: Session, site_id: int) -> list[int]:
    stmt = select(Source.id).where(Source.site_id == site_id, Source.kind == SourceKind.ENERGY)
    return list(db.execute(stmt).scalars().all())


@router.get("/readings/energy", response_model=list[ReadingOut])
def get_energy_readings(
    metric: EnergyMetric,
    start: datetime.datetime,
    end: datetime.datetime,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[EnergyReading]:
    source_ids = _energy_source_ids(db, site.id)
    if not source_ids:
        return []
    rows: list = []
    for source_id in source_ids:
        rows.extend(
            query_readings(
                db,
                EnergyReading,
                EnergyReadingRollup5m,
                key_column=EnergyReading.source_id,
                key_value=source_id,
                ts_start=start,
                ts_end=end,
                metric_column=EnergyReading.metric,
                metric_value=metric,
            )
        )
    return sorted(rows, key=lambda r: r.ts)


@router.get("/readings/weather", response_model=list[ReadingOut])
def get_weather_readings(
    metric: WeatherMetric,
    start: datetime.datetime,
    end: datetime.datetime,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[WeatherReading]:
    stmt = select(Source.id).where(Source.site_id == site.id, Source.kind == SourceKind.WEATHER)
    source_id = db.execute(stmt).scalar_one_or_none()
    if source_id is None:
        return []
    return query_readings(
        db,
        WeatherReading,
        WeatherReadingRollup5m,
        key_column=WeatherReading.source_id,
        key_value=source_id,
        ts_start=start,
        ts_end=end,
        metric_column=WeatherReading.metric,
        metric_value=metric,
    )
```

- [ ] **Step 8: Mount the router**

```python
from core_api.routers.query import router as query_router
...
    app.include_router(query_router)
```

- [ ] **Step 9: Run tests, then full suite, then commit**

```bash
uv run pytest tests/test_readings_query.py tests/test_query.py -v
uv run pytest -q
git add core_api/readings_query.py core_api/routers/query.py core_api/schemas.py core_api/main.py tests/test_readings_query.py tests/test_query.py
git commit -m "Add raw/rollup query helper and energy/weather readings query endpoints"
```

---

## Task 11: `GET /sites/{id}/prices`

**Files:**
- Modify: `core_api/routers/query.py`, `core_api/schemas.py`
- Test: `tests/test_query.py` (append)

**Interfaces:**
- Consumes: `query_readings`, `require_site_access`.
- Produces: `GET /sites/{site_id}/prices` appended to the query router.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_query.py
from core_api.models.pricing import PriceChannel, PriceReading


def seed_price_reading(db_url: str) -> tuple[int, int]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="pq-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.PRICING,
        provider="amber",
        config={},
        collector_api_key_hash="k",
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    session.add(source)
    session.commit()
    ts = datetime.datetime.now(UTC) - datetime.timedelta(hours=1)
    session.add(PriceReading(pricing_source_id=source.id, ts=ts, channel=PriceChannel.BUY, value=25.5))
    session.commit()
    ids = (owner.id, site.id)
    session.close()
    return ids


def test_get_prices_for_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_price_reading(db_url)
    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/sites/{site_id}/prices",
        params={
            "channel": "buy",
            "start": (now - datetime.timedelta(hours=2)).isoformat(),
            "end": now.isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_query.py -v -k price`
Expected: FAIL (404).

- [ ] **Step 3: Append the endpoint to `core_api/routers/query.py`**

```python
# Append to core_api/routers/query.py
from core_api.models.pricing import PriceChannel, PriceReading, PriceReadingRollup5m


@router.get("/prices", response_model=list[ReadingOut])
def get_prices(
    channel: PriceChannel,
    start: datetime.datetime,
    end: datetime.datetime,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[PriceReading]:
    stmt = select(Source.id).where(Source.site_id == site.id, Source.kind == SourceKind.PRICING)
    source_ids = list(db.execute(stmt).scalars().all())
    rows: list = []
    for source_id in source_ids:
        rows.extend(
            query_readings(
                db,
                PriceReading,
                PriceReadingRollup5m,
                key_column=PriceReading.pricing_source_id,
                key_value=source_id,
                ts_start=start,
                ts_end=end,
                metric_column=PriceReading.channel,
                metric_value=channel,
            )
        )
    return sorted(rows, key=lambda r: r.ts)
```

`PriceReading` has a `channel` column, not `metric` — `ReadingOut` expects `metric`, so build it explicitly rather than relying on `from_attributes` ORM conversion. Replace the function's `return` statement with:

```python
    return [ReadingOut(ts=r.ts, metric=r.channel.value, value=float(r.value)) for r in sorted(rows, key=lambda r: r.ts)]
```

`response_model` stays `list[ReadingOut]` — only the return statement builds `ReadingOut` instances directly instead of letting FastAPI convert ORM rows itself.

- [ ] **Step 4: Run tests, then full suite, then commit**

```bash
uv run pytest tests/test_query.py -v
uv run pytest -q
git add core_api/routers/query.py tests/test_query.py
git commit -m "Add GET /sites/{id}/prices"
```

---

## Task 12: SiteTariff, DemandWindow, BillingCycle CRUD

**Files:**
- Create: `core_api/routers/billing.py`
- Modify: `core_api/schemas.py`, `core_api/main.py`
- Test: `tests/test_billing.py`

**Interfaces:**
- Consumes: `require_site_owner`, `require_site_access`.
- Produces: `router` mounted at `/sites/{site_id}`: `POST/GET /tariffs`, `POST/GET /tariffs/{tariff_id}/demand-windows`, `POST/GET /billing-cycle`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_billing.py
from __future__ import annotations

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, User
from core_api.security import create_access_token


def make_owner_and_site(db_url: str) -> tuple[int, int]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="bill-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="Australia/Sydney")
    session.add(site)
    session.commit()
    ids = (owner.id, site.id)
    session.close()
    return ids


def auth(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_create_tariff_and_demand_window(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff = client.post(
        f"/sites/{site_id}/tariffs",
        json={"effective_from": "2026-01-01T00:00:00Z", "service_availability_c_per_day": "80.5"},
        headers=auth(owner_id),
    )
    assert tariff.status_code == 201, tariff.text
    tariff_id = tariff.json()["id"]

    window = client.post(
        f"/sites/{site_id}/tariffs/{tariff_id}/demand-windows",
        json={
            "label": "Summer peak",
            "months": [12, 1, 2],
            "days_of_week": [0, 1, 2, 3, 4],
            "start_time": "15:00:00",
            "end_time": "21:00:00",
            "rate_c_per_kwh": "45.0",
            "measurement_interval_minutes": 30,
        },
        headers=auth(owner_id),
    )
    assert window.status_code == 201, window.text

    listed = client.get(f"/sites/{site_id}/tariffs/{tariff_id}/demand-windows", headers=auth(owner_id))
    assert len(listed.json()) == 1


def test_create_and_get_billing_cycle(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    create = client.post(
        f"/sites/{site_id}/billing-cycle",
        json={"effective_from": "2026-01-01T00:00:00Z", "cycle_months": 3, "anchor_day": 18},
        headers=auth(owner_id),
    )
    assert create.status_code == 201, create.text

    invalid = client.post(
        f"/sites/{site_id}/billing-cycle",
        json={"effective_from": "2026-01-01T00:00:00Z", "cycle_months": 0, "anchor_day": 18},
        headers=auth(owner_id),
    )
    assert invalid.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_billing.py -v`
Expected: FAIL (404s).

- [ ] **Step 3: Add billing schemas to `core_api/schemas.py`**

```python
# Append to core_api/schemas.py
from pydantic import Field


class SiteTariffCreate(BaseModel):
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None = None
    service_availability_c_per_day: decimal.Decimal


class SiteTariffOut(SiteTariffCreate):
    id: int
    site_id: int

    model_config = {"from_attributes": True}


class DemandWindowCreate(BaseModel):
    label: str
    months: list[int]
    days_of_week: list[int]
    start_time: datetime.time
    end_time: datetime.time
    rate_c_per_kwh: decimal.Decimal
    measurement_interval_minutes: int = 30


class DemandWindowOut(DemandWindowCreate):
    id: int
    site_tariff_id: int

    model_config = {"from_attributes": True}


class BillingCycleCreate(BaseModel):
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None = None
    cycle_months: int = Field(gt=0)
    anchor_day: int = Field(ge=1, le=31)


class BillingCycleOut(BillingCycleCreate):
    id: int
    site_id: int

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Implement `core_api/routers/billing.py`**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, require_site_access, require_site_owner
from core_api.errors import AppError
from core_api.models.billing import BillingCycle, DemandWindow, SiteTariff
from core_api.models.identity import Site
from core_api.schemas import (
    BillingCycleCreate,
    BillingCycleOut,
    DemandWindowCreate,
    DemandWindowOut,
    SiteTariffCreate,
    SiteTariffOut,
)

router = APIRouter(prefix="/sites/{site_id}", tags=["billing"])


@router.post("/tariffs", response_model=SiteTariffOut, status_code=status.HTTP_201_CREATED)
def create_tariff(
    payload: SiteTariffCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> SiteTariff:
    tariff = SiteTariff(site_id=site.id, **payload.model_dump())
    db.add(tariff)
    db.commit()
    db.refresh(tariff)
    return tariff


@router.get("/tariffs", response_model=list[SiteTariffOut])
def list_tariffs(
    site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
) -> list[SiteTariff]:
    return db.execute(select(SiteTariff).where(SiteTariff.site_id == site.id)).scalars().all()


@router.post(
    "/tariffs/{tariff_id}/demand-windows",
    response_model=DemandWindowOut,
    status_code=status.HTTP_201_CREATED,
)
def create_demand_window(
    tariff_id: int,
    payload: DemandWindowCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> DemandWindow:
    tariff = db.get(SiteTariff, tariff_id)
    if tariff is None or tariff.site_id != site.id:
        raise AppError(404, "not_found", f"Tariff {tariff_id} not found")
    window = DemandWindow(site_tariff_id=tariff.id, **payload.model_dump())
    db.add(window)
    db.commit()
    db.refresh(window)
    return window


@router.get("/tariffs/{tariff_id}/demand-windows", response_model=list[DemandWindowOut])
def list_demand_windows(
    tariff_id: int,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[DemandWindow]:
    tariff = db.get(SiteTariff, tariff_id)
    if tariff is None or tariff.site_id != site.id:
        raise AppError(404, "not_found", f"Tariff {tariff_id} not found")
    return db.execute(select(DemandWindow).where(DemandWindow.site_tariff_id == tariff.id)).scalars().all()


@router.post("/billing-cycle", response_model=BillingCycleOut, status_code=status.HTTP_201_CREATED)
def create_billing_cycle(
    payload: BillingCycleCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> BillingCycle:
    cycle = BillingCycle(site_id=site.id, **payload.model_dump())
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


@router.get("/billing-cycle", response_model=list[BillingCycleOut])
def list_billing_cycles(
    site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
) -> list[BillingCycle]:
    return db.execute(select(BillingCycle).where(BillingCycle.site_id == site.id)).scalars().all()
```

Pydantic's `Field(gt=0)`/`Field(ge=1, le=31)` on `BillingCycleCreate` reject an invalid `cycle_months`/`anchor_day` with a 422 before the handler runs, so the DB-level `CheckConstraint` from the schema work is a second line of defense, not the only one — consistent with validating at the boundary the caller talks to (see backend-design's API-contract guidance) while keeping the DB the ultimate authority.

- [ ] **Step 5: Mount the router**

```python
from core_api.routers.billing import router as billing_router
...
    app.include_router(billing_router)
```

- [ ] **Step 6: Run tests, then full suite, then commit**

```bash
uv run pytest tests/test_billing.py -v
uv run pytest -q
git add core_api/routers/billing.py core_api/schemas.py core_api/main.py tests/test_billing.py
git commit -m "Add SiteTariff, DemandWindow, and BillingCycle CRUD"
```

---

## Task 13: Cost calculation module + `GET /sites/{id}/cost`

**Files:**
- Create: `core_api/cost.py`
- Modify: `core_api/routers/query.py`, `core_api/schemas.py`
- Test: `tests/test_cost.py`

**Interfaces:**
- Consumes: `EnergyReading`, `PriceReading`, `Source` models.
- Produces:
  ```python
  @dataclasses.dataclass
  class CostResult:
      total_cost_c: decimal.Decimal
      total_import_kwh: decimal.Decimal
      total_export_kwh: decimal.Decimal
      minutes_missing_price: int

  def compute_cost(db: Session, site_id: int, start: datetime, end: datetime, method: Literal["net", "gross"]) -> CostResult: ...
  ```
  `GET /sites/{site_id}/cost?start=&end=&method=net|gross` appended to the query router.

**Design note carried from the design spec's "Per-minute cost" section:** cost is computed by joining energy readings against price readings at matching timestamps, never persisted. "Net" uses `grid_import_w`/`grid_export_w` (only energy crossing the meter boundary); "gross" uses `consumption_w`/`production_w` (all consumption billed, all production credited, regardless of self-consumption) — this is the standard AU net-vs-gross feed-in-tariff distinction the spec's Purpose section names but doesn't spell out mechanically.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cost.py
from __future__ import annotations

import datetime
import decimal

from core_api.models.identity import Site, User
from core_api.models.pricing import PriceChannel, PriceReading
from core_api.models.readings import EnergyMetric, EnergyReading
from core_api.models.sources import Source, SourceKind
from core_api.cost import compute_cost

UTC = datetime.timezone.utc


def _make_energy_source(db, site, kind_metrics_ok=True) -> Source:
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        collector_api_key_hash="cost-energy",
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def _make_pricing_source(db, site) -> Source:
    source = Source(
        site_id=site.id,
        kind=SourceKind.PRICING,
        provider="amber",
        config={},
        collector_api_key_hash="cost-pricing",
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def test_compute_cost_net_method(db, site):
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    # 1 minute at 600W import (0.01 kWh) and 300W export (0.005 kWh).
    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=300.0))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.BUY, value=decimal.Decimal("30.0")))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("10.0")))
    db.commit()

    result = compute_cost(db, site.id, ts, ts + datetime.timedelta(minutes=1), method="net")

    # import cost = 0.01 kWh * 30 c/kWh = 0.3c; export revenue = 0.005 kWh * 10 c/kWh = 0.05c
    assert result.total_cost_c == decimal.Decimal("0.25")
    assert result.minutes_missing_price == 0


def test_compute_cost_flags_minutes_missing_price(db, site):
    energy_source = _make_energy_source(db, site)
    _make_pricing_source(db, site)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
    db.commit()
    # No PriceReading at all for this minute.

    result = compute_cost(db, site.id, ts, ts + datetime.timedelta(minutes=1), method="net")
    assert result.minutes_missing_price == 1
    assert result.total_cost_c == decimal.Decimal("0")


def test_compute_cost_gross_method_uses_production_and_consumption(db, site):
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.CONSUMPTION_W, value=1200.0))
    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.PRODUCTION_W, value=900.0))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.BUY, value=decimal.Decimal("20.0")))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("8.0")))
    db.commit()

    result = compute_cost(db, site.id, ts, ts + datetime.timedelta(minutes=1), method="gross")

    # consumption cost = (1200/60000)*20 = 0.4c; production revenue = (900/60000)*8 = 0.12c
    assert result.total_cost_c == decimal.Decimal("0.28")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cost.py -v`
Expected: FAIL — `core_api.cost` doesn't exist.

- [ ] **Step 3: Implement `core_api/cost.py`**

```python
from __future__ import annotations

import dataclasses
import datetime
import decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.models.pricing import PriceChannel, PriceReading
from core_api.models.readings import EnergyMetric, EnergyReading
from core_api.models.sources import Source, SourceKind

MINUTES_TO_KWH = decimal.Decimal(1) / decimal.Decimal(60000)  # 1 minute at N watts -> N/60000 kWh


@dataclasses.dataclass
class CostResult:
    total_cost_c: decimal.Decimal
    total_import_kwh: decimal.Decimal
    total_export_kwh: decimal.Decimal
    minutes_missing_price: int


def _energy_by_ts(
    db: Session, source_ids: list[int], metric: EnergyMetric, start: datetime.datetime, end: datetime.datetime
) -> dict[datetime.datetime, decimal.Decimal]:
    if not source_ids:
        return {}
    stmt = select(EnergyReading.ts, EnergyReading.value).where(
        EnergyReading.source_id.in_(source_ids),
        EnergyReading.metric == metric,
        EnergyReading.ts >= start,
        EnergyReading.ts < end,
    )
    return {ts: decimal.Decimal(str(value)) for ts, value in db.execute(stmt)}


def _price_by_ts(
    db: Session, pricing_source_ids: list[int], channel: PriceChannel, start: datetime.datetime, end: datetime.datetime
) -> dict[datetime.datetime, decimal.Decimal]:
    if not pricing_source_ids:
        return {}
    stmt = select(PriceReading.ts, PriceReading.value).where(
        PriceReading.pricing_source_id.in_(pricing_source_ids),
        PriceReading.channel == channel,
        PriceReading.ts >= start,
        PriceReading.ts < end,
    )
    return {ts: value for ts, value in db.execute(stmt)}


def compute_cost(
    db: Session, site_id: int, start: datetime.datetime, end: datetime.datetime, method: Literal["net", "gross"]
) -> CostResult:
    energy_source_ids = list(
        db.execute(
            select(Source.id).where(Source.site_id == site_id, Source.kind == SourceKind.ENERGY)
        ).scalars()
    )
    pricing_source_ids = list(
        db.execute(
            select(Source.id).where(Source.site_id == site_id, Source.kind == SourceKind.PRICING)
        ).scalars()
    )

    import_metric = EnergyMetric.GRID_IMPORT_W if method == "net" else EnergyMetric.CONSUMPTION_W
    export_metric = EnergyMetric.GRID_EXPORT_W if method == "net" else EnergyMetric.PRODUCTION_W

    import_by_ts = _energy_by_ts(db, energy_source_ids, import_metric, start, end)
    export_by_ts = _energy_by_ts(db, energy_source_ids, export_metric, start, end)
    buy_price_by_ts = _price_by_ts(db, pricing_source_ids, PriceChannel.BUY, start, end)
    sell_price_by_ts = _price_by_ts(db, pricing_source_ids, PriceChannel.SELL, start, end)

    total_cost_c = decimal.Decimal("0")
    total_import_kwh = decimal.Decimal("0")
    total_export_kwh = decimal.Decimal("0")
    minutes_missing_price = 0

    all_minutes = set(import_by_ts) | set(export_by_ts)
    for minute in all_minutes:
        import_w = import_by_ts.get(minute, decimal.Decimal("0"))
        export_w = export_by_ts.get(minute, decimal.Decimal("0"))
        buy_price = buy_price_by_ts.get(minute)
        sell_price = sell_price_by_ts.get(minute)

        if (import_w and buy_price is None) or (export_w and sell_price is None):
            minutes_missing_price += 1
            continue

        import_kwh = import_w * MINUTES_TO_KWH
        export_kwh = export_w * MINUTES_TO_KWH
        total_import_kwh += import_kwh
        total_export_kwh += export_kwh
        if import_w:
            total_cost_c += import_kwh * buy_price
        if export_w:
            total_cost_c -= export_kwh * sell_price

    return CostResult(
        total_cost_c=total_cost_c,
        total_import_kwh=total_import_kwh,
        total_export_kwh=total_export_kwh,
        minutes_missing_price=minutes_missing_price,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cost.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add the HTTP endpoint**

```python
# Append to core_api/schemas.py
class CostOut(BaseModel):
    total_cost_c: decimal.Decimal
    total_import_kwh: decimal.Decimal
    total_export_kwh: decimal.Decimal
    minutes_missing_price: int
```

```python
# Append to core_api/routers/query.py
from typing import Literal

from core_api.cost import compute_cost
from core_api.schemas import CostOut


@router.get("/cost", response_model=CostOut)
def get_cost(
    start: datetime.datetime,
    end: datetime.datetime,
    method: Literal["net", "gross"],
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> CostOut:
    result = compute_cost(db, site.id, start, end, method)
    return CostOut(**dataclasses.asdict(result))
```

Add `import dataclasses` at the top of `core_api/routers/query.py` alongside the existing `import datetime`.

- [ ] **Step 6: Write one HTTP-level test for the endpoint**

```python
# Append to tests/test_query.py
def test_get_cost_for_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="cost-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/sites/{site_id}/cost",
        params={
            "start": (now - datetime.timedelta(hours=1)).isoformat(),
            "end": now.isoformat(),
            "method": "net",
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["minutes_missing_price"] == 0  # no data at all in range -> nothing to flag
```

- [ ] **Step 7: Run tests, then full suite, then commit**

```bash
uv run pytest tests/test_cost.py tests/test_query.py -v
uv run pytest -q
git add core_api/cost.py core_api/routers/query.py core_api/schemas.py tests/test_cost.py tests/test_query.py
git commit -m "Add cost calculation module and GET /sites/{id}/cost"
```

---

## Task 14: `GET /sites/{id}/demand-charges`

**Files:**
- Modify: `core_api/routers/query.py`, `core_api/schemas.py`
- Test: `tests/test_query.py` (append)

**Interfaces:**
- Consumes: `require_site_access`.
- Produces: `GET /sites/{site_id}/demand-charges` appended to the query router.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_query.py
from core_api.models.billing import DemandCharge, DemandInterval


def test_get_demand_charges_for_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="dc-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    peak_start = datetime.datetime(2026, 1, 1, 18, 0, tzinfo=UTC)
    session.add(
        DemandInterval(
            site_id=site.id,
            interval_start=peak_start,
            interval_end=peak_start + datetime.timedelta(minutes=30),
            avg_import_w=5000.0,
            is_demand_window=True,
        )
    )
    session.commit()
    session.add(
        DemandCharge(
            site_id=site.id,
            billing_period_start=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            billing_period_end=datetime.datetime(2026, 2, 1, tzinfo=UTC),
            peak_interval_start=peak_start,
            peak_avg_import_w=5000.0,
            rate_applied=decimal.Decimal("45.0"),
            demand_window_hours=decimal.Decimal("2"),
            charge_amount=decimal.Decimal("450.0"),
        )
    )
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    response = client.get(
        f"/sites/{site_id}/demand-charges",
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.json()[0]["charge_amount"] == "450.0000"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_query.py -v -k demand_charges`
Expected: FAIL (404).

- [ ] **Step 3: Add the schema and endpoint**

```python
# Append to core_api/schemas.py
class DemandChargeOut(BaseModel):
    id: int
    site_id: int
    billing_period_start: datetime.datetime
    billing_period_end: datetime.datetime
    peak_interval_start: datetime.datetime
    peak_avg_import_w: float
    rate_applied: decimal.Decimal
    demand_window_hours: decimal.Decimal
    charge_amount: decimal.Decimal
    is_estimated: bool

    model_config = {"from_attributes": True}
```

```python
# Append to core_api/routers/query.py
from core_api.models.billing import DemandCharge
from core_api.schemas import DemandChargeOut


@router.get("/demand-charges", response_model=list[DemandChargeOut])
def get_demand_charges(
    site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
) -> list[DemandCharge]:
    stmt = select(DemandCharge).where(DemandCharge.site_id == site.id).order_by(DemandCharge.billing_period_start)
    return db.execute(stmt).scalars().all()
```

- [ ] **Step 4: Run tests, then full suite, then commit**

```bash
uv run pytest tests/test_query.py -v
uv run pytest -q
git add core_api/routers/query.py core_api/schemas.py tests/test_query.py
git commit -m "Add GET /sites/{id}/demand-charges"
```

---

## Task 15: Final wiring check + OpenAPI smoke test

**Files:**
- Test: `tests/test_app.py` (extend the existing smoke test)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write a test asserting every expected route is registered**

```python
# Append to tests/test_app.py
def test_all_expected_routes_are_registered(client):
    paths = {route.path for route in client.app.routes}
    expected = {
        "/auth/login",
        "/sites",
        "/sites/{site_id}",
        "/sites/{site_id}/access",
        "/sites/{site_id}/access/{user_id}",
        "/sites/{site_id}/sources",
        "/sites/{site_id}/sources/{source_id}",
        "/sources/{source_id}/readings/energy",
        "/sources/{source_id}/readings/weather",
        "/sources/{source_id}/prices",
        "/sites/{site_id}/readings/energy",
        "/sites/{site_id}/readings/weather",
        "/sites/{site_id}/prices",
        "/sites/{site_id}/cost",
        "/sites/{site_id}/demand-charges",
        "/sites/{site_id}/tariffs",
        "/sites/{site_id}/tariffs/{tariff_id}/demand-windows",
        "/sites/{site_id}/billing-cycle",
    }
    missing = expected - paths
    assert not missing, f"Missing routes: {missing}"
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS. If it fails, a route was mounted under a different path than planned somewhere above — fix the mismatch (either the router's `prefix`/path or this test, whichever is actually wrong per the spec's API contract) rather than deleting the assertion.

- [ ] **Step 3: Run the entire test suite one final time**

Run: `JWT_SECRET=dev-local-only uv run pytest -q`
Expected: every test across every file passes.

- [ ] **Step 4: Commit**

```bash
git add tests/test_app.py
git commit -m "Add route-registration smoke test for the full Core API HTTP layer"
```
