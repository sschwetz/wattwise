# Avatar Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a logged-in user upload, replace, and remove a real photo avatar, stored on local disk and served only to its own owner, replacing the initials-only placeholder from the header/account-menu work.

**Architecture:** Core API stores the image on the existing `core_api_data` volume (no new infra, no image library) and serves it only via `/users/me/avatar`, which — like every other `/users/me/*` route — derives the user from the auth token with no user-id parameter anywhere, so there is no route through which one account could ever request another's avatar. The webapp fetches that image with its access token into a blob, exposes it as an object URL, and swaps the existing initials `Avatar` for the real photo when one is set.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic + `python-multipart` (core-api), React 19 + TanStack Query + Tailwind v4 + Vitest/RTL (webapp).

**Spec:** `docs/superpowers/specs/2026-10-12-avatar-upload-design.md`

## Global Constraints

- Storage is local disk under `avatar_storage_dir` (Docker: `/data/avatars`, sharing the
  existing `core_api_data` volume; dev default `./data/avatars`), file named by user id
  with no extension.
- No image-processing library, no server-side resizing/cropping/re-encoding.
- Upload accepts only `image/jpeg`, `image/png`, `image/webp`, max 2 MiB — read by
  capping the actual bytes read, never by trusting a client-supplied `Content-Length`.
- All three avatar endpoints (`POST`/`DELETE`/`GET /users/me/avatar`) require the
  existing `access_token`, exactly like every other `/users/me/*` route. None of the
  three ever takes a user id as input — the caller's own id, from the token, is the
  only id involved anywhere in this feature.
- `UserMeOut` gains `has_avatar: bool` only — never the stored content-type.
- A failed upload or remove must never fail silently: both actions, and the
  client-side type/size pre-check, surface a dismissible `role="alert"` banner using
  this page's existing `errorClass` pattern.
- New dependency: `python-multipart` (add via `uv add`, not a manual `pyproject.toml`
  edit, so the lockfile stays consistent).

## Review Focus

- A user must never be able to fetch anyone else's avatar. All three endpoints key off
  `current_user.id` from `get_current_user` with no user-id parameter in the route at
  all — pinned by an auth-required test on each of the three, plus confirming (by
  inspection, since there is nothing to execute) that no route in this feature takes an
  arbitrary user id.
- Upload must reject a disallowed content-type and an oversized file with a clear,
  structured error — not a 500, not silent truncation — and the webapp must show that
  rejection in a banner, not swallow it.
- Overwriting an existing avatar must fully replace it: a second, differently-sized or
  differently-typed upload must leave no trace of the first (stale bytes, stale
  content-type), and a concurrent `GET` must never observe a half-written file.
- Removing an avatar must be idempotent (no error on a second `DELETE`) and must
  actually make a subsequent `GET` 404 — not just flip the DB flag while the file
  stays on disk.
- `useAvatarImage` must revoke every object URL it creates, on avatar removal and on
  unmount — a leaked `blob:` URL is a quiet, cumulative memory leak that no test
  "looking right" on the surface would catch without explicitly asserting
  `revokeObjectURL` was called.

---

### Task 1: Core API — avatar storage, data model, migration, and endpoints

**Files:**
- Modify: `core-api/pyproject.toml` (new dependency, via `uv add`)
- Modify: `core-api/core_api/config.py` (`Settings.avatar_storage_dir`)
- Modify: `core-api/core_api/models/identity.py` (`User.avatar_content_type`)
- Create: `core-api/alembic/versions/c7e4a1f9b2d3_add_avatar_content_type_to_users.py`
- Modify: `core-api/core_api/schemas.py` (`UserMeOut.has_avatar`)
- Modify: `core-api/core_api/routers/account.py` (3 new endpoints)
- Modify: `core-api/tests/conftest.py` (`AVATAR_STORAGE_DIR` isolated per test)
- Test: `core-api/tests/test_account.py` (append)

**Interfaces:**
- Produces: `Settings.avatar_storage_dir: str`; `User.avatar_content_type: str | None`;
  `UserMeOut.has_avatar: bool`; `POST /api/v1/users/me/avatar` (multipart, field
  `file`) → `UserMeOut`; `DELETE /api/v1/users/me/avatar` → `UserMeOut`;
  `GET /api/v1/users/me/avatar` → raw image bytes with the stored content-type.

- [ ] **Step 1: Add the `python-multipart` dependency**

Run: `cd core-api && uv add "python-multipart>=0.0.9,<1.0"`
Expected: `pyproject.toml` and `uv.lock` both updated; command exits 0.

- [ ] **Step 2: Write the failing tests**

Append to `core-api/tests/test_account.py`:

```python
def test_get_me_reports_has_avatar_false_when_unset(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.get("/api/v1/users/me", headers=auth(user_id))
    assert response.status_code == 200, response.text
    assert response.json()["has_avatar"] is False


def test_upload_avatar_sets_content_type_and_persists_bytes(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.png", b"fake-png-bytes", "image/png")},
        headers=auth(user_id),
    )
    assert response.status_code == 200, response.text
    assert response.json()["has_avatar"] is True

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = session.get(User, user_id)
    session.close()
    assert user.avatar_content_type == "image/png"


def test_upload_avatar_rejects_disallowed_content_type(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers=auth(user_id),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_file_type"


def test_upload_avatar_rejects_oversized_file(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    oversized = b"x" * (2 * 1024 * 1024 + 1)
    response = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.png", oversized, "image/png")},
        headers=auth(user_id),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "file_too_large"


def test_upload_avatar_overwrites_existing_avatar(client, app_and_db_url):
    from core_api.routers.account import _avatar_path

    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.jpg", b"old-jpeg-bytes", "image/jpeg")},
        headers=auth(user_id),
    )
    response = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.png", b"new-png-bytes", "image/png")},
        headers=auth(user_id),
    )
    assert response.status_code == 200, response.text

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = session.get(User, user_id)
    session.close()
    assert user.avatar_content_type == "image/png"
    assert _avatar_path(user_id).read_bytes() == b"new-png-bytes"


def test_upload_avatar_requires_auth(client, app_and_db_url):
    response = client.post(
        "/api/v1/users/me/avatar", files={"file": ("avatar.png", b"bytes", "image/png")}
    )
    assert response.status_code in (401, 422)


def test_remove_avatar_clears_content_type_and_deletes_file(client, app_and_db_url):
    from core_api.routers.account import _avatar_path

    _, db_url = app_and_db_url
    user_id = make_user(db_url)
    client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.png", b"bytes", "image/png")},
        headers=auth(user_id),
    )

    response = client.delete("/api/v1/users/me/avatar", headers=auth(user_id))
    assert response.status_code == 200, response.text
    assert response.json()["has_avatar"] is False

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = session.get(User, user_id)
    session.close()
    assert user.avatar_content_type is None
    assert not _avatar_path(user_id).exists()


def test_remove_avatar_is_idempotent_when_none_exists(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.delete("/api/v1/users/me/avatar", headers=auth(user_id))
    assert response.status_code == 200, response.text
    assert response.json()["has_avatar"] is False


def test_remove_avatar_requires_auth(client, app_and_db_url):
    response = client.delete("/api/v1/users/me/avatar")
    assert response.status_code in (401, 422)


def test_get_avatar_streams_bytes_and_content_type(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)
    client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.png", b"fake-png-bytes", "image/png")},
        headers=auth(user_id),
    )

    response = client.get("/api/v1/users/me/avatar", headers=auth(user_id))
    assert response.status_code == 200
    assert response.content == b"fake-png-bytes"
    assert response.headers["content-type"] == "image/png"


def test_get_avatar_404s_when_unset(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.get("/api/v1/users/me/avatar", headers=auth(user_id))
    assert response.status_code == 404
    assert response.json()["code"] == "no_avatar"


def test_get_avatar_requires_auth(client, app_and_db_url):
    response = client.get("/api/v1/users/me/avatar")
    assert response.status_code in (401, 422)


def test_two_different_users_get_independent_avatars(client, app_and_db_url):
    """Pins the Review Focus item: there is no path by which uploading or
    fetching an avatar can touch another account's file or row."""
    _, db_url = app_and_db_url
    user_a = make_user(db_url)
    user_b = User(email="second-user@example.com", password_hash="x")
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    session.add(user_b)
    session.commit()
    user_b_id = user_b.id
    session.close()

    client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("a.png", b"user-a-bytes", "image/png")},
        headers=auth(user_a),
    )
    client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("b.jpg", b"user-b-bytes", "image/jpeg")},
        headers=auth(user_b_id),
    )

    response_a = client.get("/api/v1/users/me/avatar", headers=auth(user_a))
    response_b = client.get("/api/v1/users/me/avatar", headers=auth(user_b_id))
    assert response_a.content == b"user-a-bytes"
    assert response_b.content == b"user-b-bytes"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_account.py -k "avatar" -v`
Expected: FAIL — `has_avatar` missing from the `UserMeOut` response, and the three
`/avatar` routes 404.

- [ ] **Step 4: Isolate avatar storage per test**

In `core-api/tests/conftest.py`, update `app_and_db_url`:

```python
@pytest.fixture()
def app_and_db_url(tmp_path, monkeypatch, fake_email_sender):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("AVATAR_STORAGE_DIR", str(tmp_path / "avatars"))
    db_url = f"sqlite:///{tmp_path}/app_test.db"
    run_migrations(db_url)
    app = create_app(database_url=db_url, email_sender=fake_email_sender)
    return app, db_url
```

- [ ] **Step 5: Add the setting**

In `core-api/core_api/config.py`, add to `Settings`:

```python
    avatar_storage_dir: str
```

and to `get_settings()`:

```python
        avatar_storage_dir=os.environ.get("AVATAR_STORAGE_DIR", "./data/avatars"),
```

(Insert both right after the `database_url` field/assignment, grouping storage
settings together.)

- [ ] **Step 6: Add the `docker-compose.yml` setting**

In `wattwise/docker-compose.yml`, in the `core-api` service's `environment:` block,
add right after `DATABASE_URL`:

```yaml
      AVATAR_STORAGE_DIR: /data/avatars
```

(No new volume — this shares the existing `core_api_data:/data` mount.)

- [ ] **Step 7: Add the `avatar_content_type` column**

In `core-api/core_api/models/identity.py`, in the `User` class, right after
`display_name`:

```python
    avatar_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

- [ ] **Step 8: Create the migration**

Create `core-api/alembic/versions/c7e4a1f9b2d3_add_avatar_content_type_to_users.py`:

```python
"""add avatar_content_type to users

Revision ID: c7e4a1f9b2d3
Revises: b4f29d7a1c56
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e4a1f9b2d3'
down_revision: Union[str, Sequence[str], None] = 'b4f29d7a1c56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('avatar_content_type', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'avatar_content_type')
```

- [ ] **Step 9: Add `has_avatar` to the schema**

In `core-api/core_api/schemas.py`, update `UserMeOut`:

```python
class UserMeOut(BaseModel):
    id: int
    email: str
    display_name: str | None
    # Never the raw key -- just whether one is on file, so the UI can show
    # "configured" without ever having the secret pass back through a response.
    has_amber_api_key: bool
    # Never the stored content-type -- the UI only ever needs true/false to decide
    # whether to fetch the image at all.
    has_avatar: bool
```

- [ ] **Step 10: Add the endpoints**

In `core-api/core_api/routers/account.py`, add to the imports:

```python
import os
from pathlib import Path

from fastapi import File, UploadFile
from fastapi.responses import Response
```

Update `_to_user_me_out`:

```python
def _to_user_me_out(user: User) -> UserMeOut:
    return UserMeOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        has_amber_api_key=user.amber_api_key is not None,
        has_avatar=user.avatar_content_type is not None,
    )
```

Add right after `_to_user_me_out`:

```python
_ALLOWED_AVATAR_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
_MAX_AVATAR_BYTES = 2 * 1024 * 1024


def _avatar_path(user_id: int) -> Path:
    """The caller's own avatar file, keyed only by the id already proven by
    get_current_user -- no route in this feature accepts a user id as input,
    so there is no way to name another account's file through this API."""
    return Path(get_settings().avatar_storage_dir) / str(user_id)
```

Add the three endpoints right after `update_display_name`:

```python
@router.post("/avatar", response_model=UserMeOut)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> UserMeOut:
    if file.content_type not in _ALLOWED_AVATAR_CONTENT_TYPES:
        raise AppError(400, "invalid_file_type", "Avatar must be a JPEG, PNG, or WebP image")

    # Cap the bytes actually read rather than trusting a client-supplied
    # Content-Length -- the one-past-the-limit read is what makes "too large"
    # detectable without buffering an arbitrarily huge upload first.
    content = await file.read(_MAX_AVATAR_BYTES + 1)
    if len(content) > _MAX_AVATAR_BYTES:
        raise AppError(400, "file_too_large", "Avatar must be 2MB or smaller")

    path = _avatar_path(current_user.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-replace so a concurrent GET never observes a half-written file,
    # and so overwriting an existing avatar leaves no stale bytes behind.
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_bytes(content)
    os.replace(tmp_path, path)

    current_user.avatar_content_type = file.content_type
    db.commit()
    db.refresh(current_user)
    return _to_user_me_out(current_user)


@router.delete("/avatar", response_model=UserMeOut)
def remove_avatar(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> UserMeOut:
    _avatar_path(current_user.id).unlink(missing_ok=True)
    current_user.avatar_content_type = None
    db.commit()
    db.refresh(current_user)
    return _to_user_me_out(current_user)


@router.get("/avatar")
def get_avatar(current_user: User = Depends(get_current_user)) -> Response:
    if current_user.avatar_content_type is None:
        raise AppError(404, "no_avatar", "No avatar is set")
    content = _avatar_path(current_user.id).read_bytes()
    return Response(content=content, media_type=current_user.avatar_content_type)
```

- [ ] **Step 11: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_account.py -v`
Expected: PASS (all tests in the file, including every pre-existing one — confirms
the `UserMeOut` shape change didn't break the Amber-key/display-name tests).

- [ ] **Step 12: Commit**

```bash
git add core-api/pyproject.toml core-api/uv.lock core-api/core_api/config.py core-api/core_api/models/identity.py core-api/alembic/versions/c7e4a1f9b2d3_add_avatar_content_type_to_users.py core-api/core_api/schemas.py core-api/core_api/routers/account.py core-api/tests/conftest.py core-api/tests/test_account.py docker-compose.yml
git commit -m "feat(core-api): add avatar upload/remove/get endpoints"
```

---

### Task 2: Webapp — `apiFetch` FormData fix and `apiFetchBlob`

**Files:**
- Modify: `webapp/src/api/client.ts`
- Test: `webapp/src/api/client.test.ts`

**Interfaces:**
- Produces: `apiFetch` no longer forces `Content-Type: application/json` when
  `options.body instanceof FormData`; new `apiFetchBlob(path, options?): Promise<Blob>`,
  used by `useAvatarImage` (Task 3).

- [ ] **Step 1: Write the failing tests**

In `webapp/src/api/client.test.ts`, change `mockFetchOnce` to also stub `.blob()`:

```ts
function mockFetchOnce(status: number, body: unknown, blobContent: BlobPart = "") {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
      blob: async () => new Blob([blobContent]),
    }),
  );
}
```

Add inside the existing `describe("apiFetch", ...)` block, after the
"does not overwrite an explicitly-passed Authorization header" test:

```ts
  it("does not force a JSON content-type when the body is FormData", async () => {
    mockFetchOnce(200, {});
    const formData = new FormData();
    formData.append("file", new Blob(["x"]), "avatar.png");
    await apiFetch("/api/v1/users/me/avatar", { method: "POST", body: formData });
    const [, requestInit] = vi.mocked(fetch).mock.calls[0];
    const headers = requestInit!.headers as Headers;
    expect(headers.has("Content-Type")).toBe(false);
  });
```

Add a new top-level `describe` block after the `apiFetch` block, before
`describe("errorMessage", ...)`:

```ts
describe("apiFetchBlob", () => {
  beforeEach(() => {
    configureApiClient(() => null, () => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the blob body on success", async () => {
    mockFetchOnce(200, null, "fake-image-bytes");
    const result = await apiFetchBlob("/api/v1/users/me/avatar");
    expect(result).toBeInstanceOf(Blob);
    expect(await result.text()).toBe("fake-image-bytes");
  });

  it("throws ApiError with the structured error body on failure", async () => {
    mockFetchOnce(404, { code: "no_avatar", message: "No avatar is set", detail: null });
    await expect(apiFetchBlob("/api/v1/users/me/avatar")).rejects.toMatchObject(
      new ApiError(404, "no_avatar", "No avatar is set", null),
    );
  });

  it("attaches the Authorization header when a token is configured", async () => {
    configureApiClient(() => "the-token", () => {});
    mockFetchOnce(200, null);
    await apiFetchBlob("/api/v1/users/me/avatar");
    const [, requestInit] = vi.mocked(fetch).mock.calls[0];
    const headers = requestInit!.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer the-token");
  });

  it("calls onUnauthorized on a 401 with a dead-session code", async () => {
    const onUnauthorized = vi.fn();
    configureApiClient(() => "stale-token", onUnauthorized);
    mockFetchOnce(401, { code: "invalid_token", message: "Invalid or expired token", detail: null });
    await expect(apiFetchBlob("/api/v1/users/me/avatar")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });
});
```

Update the top-level import to include `apiFetchBlob`:

```ts
import { ApiError, apiFetch, apiFetchBlob, configureApiClient, errorMessage } from "./client";
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webapp && npx vitest run src/api/client.test.ts`
Expected: FAIL — `apiFetchBlob` is not exported yet, and the FormData test sees
`Content-Type: application/json` still set.

- [ ] **Step 3: Implement the fix and the new helper**

In `webapp/src/api/client.ts`, replace the body of `apiFetch` with a shared
`_authorizedFetch` helper:

```ts
async function _authorizedFetch(path: string, options: RequestInit): Promise<Response> {
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${getApiBaseUrl()}${path}`, { ...options, headers });

  if (!response.ok) {
    // One body-parse, used both to decide whether the session is dead and to
    // build the ApiError — the body can only be consumed once.
    const body: ErrorBody = await response.json().catch(() => ({}));
    const code = body.code ?? "unknown_error";
    if (response.status === 401 && DEAD_SESSION_CODES.has(code)) {
      onUnauthorized();
    }
    throw new ApiError(response.status, code, body.message ?? response.statusText, body.detail ?? null);
  }

  return response;
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  // FormData (the avatar upload) sets its own multipart boundary; overriding
  // it with application/json breaks the request.
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await _authorizedFetch(path, { ...options, headers });

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** Like apiFetch, but for a binary response body (the avatar image) rather than
 * JSON. Shares the same auth-header and 401-handling behavior via
 * _authorizedFetch. */
export async function apiFetchBlob(path: string, options: RequestInit = {}): Promise<Blob> {
  const response = await _authorizedFetch(path, options);
  return await response.blob();
}
```

(The existing `DEAD_SESSION_CODES` constant and its comment, and the `ApiError`,
`ErrorBody`, `configureApiClient`, `errorMessage` declarations above this point, are
unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && npx vitest run src/api/client.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webapp/src/api/client.ts webapp/src/api/client.test.ts
git commit -m "feat(webapp): add apiFetchBlob and stop forcing JSON content-type on FormData bodies"
```

---

### Task 3: Webapp — avatar mutations and `useAvatarImage`

**Files:**
- Modify: `webapp/src/account/useAccount.ts`
- Create: `webapp/src/account/useAvatarImage.ts`
- Test: `webapp/src/account/useAvatarImage.test.ts`

**Interfaces:**
- Consumes: `apiFetch`, `apiFetchBlob` (Task 2).
- Produces: `Me.has_avatar: boolean`; `useUploadAvatar(): UseMutationResult<Me, Error,
  File>`; `useRemoveAvatar(): UseMutationResult<Me, Error, void>`;
  `useAvatarImage(hasAvatar: boolean): string | null`, used by `Header` and
  `AccountPage` (Task 4, Task 5).

- [ ] **Step 1: Update `Me` and add the mutations**

In `webapp/src/account/useAccount.ts`, update the `Me` interface:

```ts
export interface Me {
  id: number;
  email: string;
  display_name: string | null;
  has_amber_api_key: boolean;
  has_avatar: boolean;
}
```

Add right after `useUpdateDisplayName`:

```ts
export function useUploadAvatar() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      return apiFetch<Me>("/api/v1/users/me/avatar", { method: "POST", body: formData });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["me"] });
      queryClient.invalidateQueries({ queryKey: ["me", "avatar"] });
    },
  });
}

export function useRemoveAvatar() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<Me>("/api/v1/users/me/avatar", { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["me"] });
      queryClient.invalidateQueries({ queryKey: ["me", "avatar"] });
    },
  });
}
```

(These two are exercised through `AccountPage.test.tsx` in Task 5, following this
file's existing convention — none of its other hooks have a standalone test file
either.)

- [ ] **Step 2: Write the failing test**

Create `webapp/src/account/useAvatarImage.test.ts`:

```ts
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetchBlob } from "../api/client";
import { useAvatarImage } from "./useAvatarImage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetchBlob: vi.fn() };
});

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("useAvatarImage", () => {
  beforeEach(() => {
    vi.mocked(apiFetchBlob).mockReset();
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
  });

  it("returns null and does not fetch when there is no avatar", () => {
    const { result } = renderHook(() => useAvatarImage(false), { wrapper });
    expect(result.current).toBeNull();
    expect(apiFetchBlob).not.toHaveBeenCalled();
  });

  it("fetches and returns an object URL when an avatar exists", async () => {
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    const { result } = renderHook(() => useAvatarImage(true), { wrapper });
    await waitFor(() => expect(result.current).toBe("blob:mock-url"));
    expect(apiFetchBlob).toHaveBeenCalledWith("/api/v1/users/me/avatar");
  });

  it("revokes the object URL when the avatar is removed", async () => {
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    const { result, rerender } = renderHook(({ hasAvatar }) => useAvatarImage(hasAvatar), {
      wrapper,
      initialProps: { hasAvatar: true },
    });
    await waitFor(() => expect(result.current).toBe("blob:mock-url"));

    rerender({ hasAvatar: false });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    expect(result.current).toBeNull();
  });

  it("revokes the object URL on unmount", async () => {
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    const { result, unmount } = renderHook(() => useAvatarImage(true), { wrapper });
    await waitFor(() => expect(result.current).toBe("blob:mock-url"));

    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd webapp && npx vitest run src/account/useAvatarImage.test.ts`
Expected: FAIL with "Failed to resolve import ./useAvatarImage" (file doesn't exist
yet).

- [ ] **Step 4: Implement `useAvatarImage`**

Create `webapp/src/account/useAvatarImage.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetchBlob } from "../api/client";

/** The caller's own avatar image as a blob: URL, or null while there isn't one
 * (no avatar set, still loading, or load failed). Revokes every object URL it
 * creates -- on a new fetch, on hasAvatar flipping false, and on unmount -- so
 * this never leaks blob: URLs across uploads/removals or repeated mounts. */
export function useAvatarImage(hasAvatar: boolean): string | null {
  const query = useQuery({
    queryKey: ["me", "avatar"],
    queryFn: () => apiFetchBlob("/api/v1/users/me/avatar"),
    enabled: hasAvatar,
  });
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!hasAvatar || !query.data) {
      setUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(query.data);
    setUrl(objectUrl);
    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [hasAvatar, query.data]);

  return url;
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd webapp && npx vitest run src/account/useAvatarImage.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add webapp/src/account/useAccount.ts webapp/src/account/useAvatarImage.ts webapp/src/account/useAvatarImage.test.ts
git commit -m "feat(webapp): add avatar mutations and the useAvatarImage hook"
```

---

### Task 4: Webapp — `Avatar` image rendering and `Header` wiring

**Files:**
- Modify: `webapp/src/layout/Avatar.tsx`
- Modify: `webapp/src/layout/Avatar.test.tsx`
- Modify: `webapp/src/layout/Header.tsx`
- Modify: `webapp/src/layout/Header.test.tsx`
- Modify: `webapp/src/App.test.tsx`
- Modify: `webapp/src/layout/ProtectedLayout.test.tsx`

**Interfaces:**
- Consumes: `useAvatarImage` (Task 3).
- Produces: `Avatar`'s new optional `imageUrl?: string | null` prop.

- [ ] **Step 1: Hygiene sweep -- add `has_avatar: false` to every existing mock**

`Me` now requires `has_avatar`. Run, from `webapp/`:

```bash
sed -i '' 's/has_amber_api_key:/has_avatar: false, has_amber_api_key:/g' src/App.test.tsx
sed -i '' 's/has_amber_api_key:/has_avatar: false, has_amber_api_key:/g' src/layout/ProtectedLayout.test.tsx
sed -i '' 's/has_amber_api_key:/has_avatar: false, has_amber_api_key:/g' src/layout/Header.test.tsx
```

(No behavior changes here -- `useAvatarImage` is disabled for `has_avatar: false`, so
every existing test keeps passing exactly as before. This only keeps the mocked shape
honest.)

- [ ] **Step 2: Write the failing tests**

Append to `webapp/src/layout/Avatar.test.tsx`, inside the `describe("Avatar", ...)`
block:

```tsx
  it("renders the uploaded photo instead of initials when an image URL is given", () => {
    render(<Avatar name="Ada Lovelace" email="ada@example.com" imageUrl="blob:mock-url" />);
    expect(screen.queryByText("AL")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { hidden: true })).toHaveAttribute("src", "blob:mock-url");
  });

  it("falls back to initials when imageUrl is null", () => {
    render(<Avatar name="Ada Lovelace" email="ada@example.com" imageUrl={null} />);
    expect(screen.getByText("AL")).toBeInTheDocument();
    expect(screen.queryByRole("img", { hidden: true })).not.toBeInTheDocument();
  });
```

In `webapp/src/layout/Header.test.tsx`, update the mock to also cover `apiFetchBlob`:

```tsx
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn(), apiFetchBlob: vi.fn() };
});
```

and update the import line above it:

```tsx
import { apiFetch, apiFetchBlob } from "../api/client";
```

Update the existing `beforeEach` to also reset it:

```tsx
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetchBlob).mockReset();
    localStorage.clear();
    localStorage.setItem("access_token", "fake-token");
  });
```

Append, inside `describe("Header", ...)`:

```tsx
  it("shows the uploaded avatar image when one is set", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_avatar: true,
      has_amber_api_key: false,
    });
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();

    renderHeader();
    await screen.findByText("Ada Lovelace");
    expect(await screen.findByRole("img", { hidden: true })).toHaveAttribute("src", "blob:mock-url");
  });
```

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `cd webapp && npx vitest run src/layout/Avatar.test.tsx src/layout/Header.test.tsx`
Expected: FAIL — `Avatar` has no `imageUrl` prop/branch yet, and `Header` never renders
an `img`.

- [ ] **Step 4: Add the `imageUrl` branch to `Avatar`**

In `webapp/src/layout/Avatar.tsx`, update `AvatarProps` and the component:

```tsx
export interface AvatarProps {
  name: string | null;
  email: string;
  imageUrl?: string | null;
}

/** Initials-only fallback when no image has been uploaded yet; see
 * useAvatarImage for how imageUrl is produced. */
export function Avatar({ name, email, imageUrl }: AvatarProps) {
  if (imageUrl) {
    return (
      <img
        src={imageUrl}
        alt=""
        aria-hidden="true"
        className="h-9 w-9 shrink-0 rounded-full object-cover"
      />
    );
  }

  const trimmedName = name?.trim();
  const hasName = !!trimmedName;
  const initials = hasName ? initialsFromName(trimmedName) : initialsFromEmail(email);
  const colorKey = hasName ? trimmedName : email;
  const palette = PALETTE[hashString(colorKey) % PALETTE.length]!;

  return (
    <span
      aria-hidden="true"
      className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full font-display text-sm font-semibold ${palette.bg} ${palette.text}`}
    >
      {initials}
    </span>
  );
}
```

- [ ] **Step 5: Wire `useAvatarImage` into `Header`**

In `webapp/src/layout/Header.tsx`, add the import:

```tsx
import { useAvatarImage } from "../account/useAvatarImage";
```

Inside `Header`, right after `const { data: me } = useMe();`:

```tsx
  const avatarUrl = useAvatarImage(me?.has_avatar ?? false);
```

Update the `Avatar` usage:

```tsx
          <Avatar name={displayName} email={email} imageUrl={avatarUrl} />
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd webapp && npx vitest run src/layout/Avatar.test.tsx src/layout/Header.test.tsx`
Expected: PASS

- [ ] **Step 7: Run the full webapp test suite**

Run: `cd webapp && npx vitest run`
Expected: PASS — confirms the Step 1 sweep didn't disturb `App.test.tsx` or
`ProtectedLayout.test.tsx`.

- [ ] **Step 8: Commit**

```bash
git add webapp/src/layout/Avatar.tsx webapp/src/layout/Avatar.test.tsx webapp/src/layout/Header.tsx webapp/src/layout/Header.test.tsx webapp/src/App.test.tsx webapp/src/layout/ProtectedLayout.test.tsx
git commit -m "feat(webapp): render the uploaded avatar image in the header"
```

---

### Task 5: Webapp — upload/remove UI on the Account page

**Files:**
- Modify: `webapp/src/pages/AccountPage.tsx`
- Modify: `webapp/src/pages/AccountPage.test.tsx`

**Interfaces:**
- Consumes: `useUploadAvatar`, `useRemoveAvatar`, `useAvatarImage` (Task 3); `Avatar`'s
  `imageUrl` prop (Task 4).

- [ ] **Step 1: Hygiene sweep -- add `has_avatar: false` to every existing mock**

Run, from `webapp/`:

```bash
sed -i '' 's/has_amber_api_key:/has_avatar: false, has_amber_api_key:/g' src/pages/AccountPage.test.tsx
```

- [ ] **Step 2: Write the failing tests**

In `webapp/src/pages/AccountPage.test.tsx`, update the top of the file to also mock
`apiFetchBlob` (the Remove-button tests below need `useAvatarImage`'s fetch mocked
too, since it calls `apiFetchBlob`, not `apiFetch`):

```tsx
import { apiFetch, apiFetchBlob } from "../api/client";
```

```tsx
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn(), apiFetchBlob: vi.fn() };
});
```

Update the existing `beforeEach` to also reset it:

```tsx
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetchBlob).mockReset();
  });
```

Append to `webapp/src/pages/AccountPage.test.tsx`, inside `describe("AccountPage",
...)`:

```tsx
  it("uploads a new avatar", async () => {
    // The upload response flips has_avatar to true, which re-enables
    // useAvatarImage and triggers an apiFetchBlob call -- mock that too, or
    // the query's fetch rejects with "apiFetchBlob is not a function".
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: true, has_amber_api_key: false });
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    renderPage();
    await screen.findByText(/current name: not set/i);

    const file = new File(["fake-bytes"], "avatar.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("Upload avatar"), file);

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/avatar",
      expect.objectContaining({ method: "POST" }),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows an alert when the server rejects the uploaded avatar", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(400, "invalid_file_type", "Avatar must be a JPEG, PNG, or WebP image", null));
    renderPage();
    await screen.findByText(/current name: not set/i);

    const file = new File(["fake-bytes"], "avatar.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("Upload avatar"), file);

    expect(await screen.findByRole("alert")).toHaveTextContent("Avatar must be a JPEG, PNG, or WebP image");
  });

  it("rejects an oversized file before calling the API", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "me@example.com",
      display_name: null,
      has_avatar: false,
      has_amber_api_key: false,
    });
    renderPage();
    await screen.findByText(/current name: not set/i);

    const oversized = new File([new Uint8Array(2 * 1024 * 1024 + 1)], "avatar.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("Upload avatar"), oversized);

    expect(await screen.findByRole("alert")).toHaveTextContent(/2mb or smaller/i);
    expect(apiFetch).toHaveBeenCalledTimes(1); // only the initial useMe call
  });

  it("rejects a disallowed file type before calling the API", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "me@example.com",
      display_name: null,
      has_avatar: false,
      has_amber_api_key: false,
    });
    renderPage();
    await screen.findByText(/current name: not set/i);

    const wrongType = new File(["hello"], "notes.txt", { type: "text/plain" });
    await userEvent.upload(screen.getByLabelText("Upload avatar"), wrongType);

    expect(await screen.findByRole("alert")).toHaveTextContent(/jpeg, png, or webp/i);
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it("shows a Remove button only when an avatar is set, and removes it", async () => {
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: true, has_amber_api_key: false })
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false });
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    renderPage();
    await screen.findByText(/current name: not set/i);

    await userEvent.click(screen.getByRole("button", { name: "Remove" }));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/avatar",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("does not show a Remove button when no avatar is set", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "me@example.com",
      display_name: null,
      has_avatar: false,
      has_amber_api_key: false,
    });
    renderPage();
    await screen.findByText(/current name: not set/i);

    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });

  it("shows an alert when removing the avatar fails", async () => {
    const { ApiError } = await import("../api/client");
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: true, has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(500, "internal_error", "An internal error occurred", null));
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    renderPage();
    await screen.findByText(/current name: not set/i);

    await userEvent.click(screen.getByRole("button", { name: "Remove" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("An internal error occurred");
  });
```

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `cd webapp && npx vitest run src/pages/AccountPage.test.tsx`
Expected: FAIL — no "Upload avatar" input or "Remove" button exists yet.

- [ ] **Step 4: Add the upload/remove UI to `ProfileSection`**

In `webapp/src/pages/AccountPage.tsx`, update the import line:

```tsx
import { type ChangeEvent, type FormEvent, useState } from "react";
import QRCode from "qrcode";
import { Avatar } from "../layout/Avatar";
import {
  useChangePassword,
  useConfirmTotpReenroll,
  useMe,
  useRemoveAvatar,
  useRequestEmailChange,
  useStartTotpReenroll,
  useUpdateAmberApiKey,
  useUpdateDisplayName,
  useUploadAvatar,
} from "../account/useAccount";
import { useAvatarImage } from "../account/useAvatarImage";
import { errorMessage } from "../api/client";
```

Add right after the existing constants (`cardClass`, `errorClass`, etc.):

```tsx
const ALLOWED_AVATAR_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_AVATAR_BYTES = 2 * 1024 * 1024;
```

Update `ProfileSection`:

```tsx
function ProfileSection() {
  const { data: me } = useMe();
  const [displayName, setDisplayName] = useState("");
  const updateDisplayName = useUpdateDisplayName();
  const avatarUrl = useAvatarImage(me?.has_avatar ?? false);
  const uploadAvatar = useUploadAvatar();
  const removeAvatar = useRemoveAvatar();
  const [avatarClientError, setAvatarClientError] = useState<string | null>(null);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    updateDisplayName.mutate(displayName, {
      onSuccess: () => setDisplayName(""),
    });
  }

  function handleAvatarChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;

    setAvatarClientError(null);
    if (!ALLOWED_AVATAR_TYPES.has(file.type)) {
      setAvatarClientError("Please choose a JPEG, PNG, or WebP image.");
      return;
    }
    if (file.size > MAX_AVATAR_BYTES) {
      setAvatarClientError("Image must be 2MB or smaller.");
      return;
    }
    uploadAvatar.mutate(file);
  }

  const avatarError = avatarClientError ?? (
    (uploadAvatar.isError || removeAvatar.isError)
      ? errorMessage(uploadAvatar.error ?? removeAvatar.error, "Failed to save your avatar. Please try again.")
      : null
  );

  return (
    <section className={cardClass}>
      <h2 className="font-display text-lg font-semibold text-ink">Profile</h2>
      <div className="flex items-center gap-4">
        <Avatar name={me?.display_name ?? null} email={me?.email ?? ""} imageUrl={avatarUrl} />
        <div className="flex items-center gap-2">
          <label className="cursor-pointer rounded-md border border-line px-3 py-2 text-sm text-ink hover:bg-surface-2">
            Upload photo
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              aria-label="Upload avatar"
              onChange={handleAvatarChange}
              className="hidden"
            />
          </label>
          {me?.has_avatar && (
            <button
              type="button"
              onClick={() => removeAvatar.mutate()}
              disabled={removeAvatar.isPending}
              className="rounded-md border border-line px-3 py-2 text-sm text-ink hover:bg-surface-2 disabled:opacity-50"
            >
              Remove
            </button>
          )}
        </div>
      </div>
      {avatarError && (
        <p role="alert" className={errorClass}>
          {avatarError}
        </p>
      )}
      {me && <p className="text-sm text-ink-muted">Current name: {me.display_name ?? "Not set"}</p>}
      {updateDisplayName.isError && (
        <p role="alert" className={errorClass}>
          {updateDisplayName.error instanceof Error
            ? updateDisplayName.error.message
            : "Failed to save your name. Please try again."}
        </p>
      )}
      {updateDisplayName.isSuccess && <p className={successClass}>Saved.</p>}
      <form onSubmit={handleSubmit} className="flex items-end gap-2">
        <label className="flex flex-col text-sm text-ink-muted">
          Display name
          <input
            aria-label="Display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Your name"
            autoComplete="name"
            spellCheck={false}
            className="rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
            required
          />
        </label>
        <button type="submit" disabled={updateDisplayName.isPending} className={primaryButtonClass}>
          Save
        </button>
      </form>
    </section>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd webapp && npx vitest run src/pages/AccountPage.test.tsx`
Expected: PASS

- [ ] **Step 6: Run the full webapp test suite**

Run: `cd webapp && npx vitest run`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add webapp/src/pages/AccountPage.tsx webapp/src/pages/AccountPage.test.tsx
git commit -m "feat(webapp): add avatar upload/remove to the Account page"
```

---

## Final verification

- [ ] Run `cd core-api && uv run pytest` — full backend suite passes.
- [ ] Run `cd webapp && npx vitest run` — full frontend suite passes.
- [ ] Run `cd webapp && npm run build` — type-checks and builds cleanly.
- [ ] Manually start the stack, log in, upload a JPEG/PNG avatar, confirm it shows in
  both the header and the Account page; reject a `.txt` file and an oversized file and
  confirm a visible error each time (not a silent no-op); remove the avatar and confirm
  it reverts to initials everywhere.
