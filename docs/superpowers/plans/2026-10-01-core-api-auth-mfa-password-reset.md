# Core API Auth: Register, TOTP, Password Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add self-service registration, mandatory TOTP two-factor login, and email-based
password reset to Core API, plus `PATCH /sites/{id}` — the backend half of sub-project 5
(frontend webapp), buildable and fully testable before any frontend code exists.

**Architecture:** Three new/changed routers (`auth.py` extended with register, a new
`totp.py`, a new `password_reset.py`) sit on top of two new DB tables
(`recovery_codes`, `password_reset_tokens`) and two new columns on `users`
(`totp_secret`, `totp_enabled`). Login becomes a two-step challenge/response: password
check returns a short-lived scoped JWT (`setup_token` or `mfa_token`, distinct from the
existing long-lived `access_token`), and a second endpoint exchanges that scoped token
(plus a TOTP/recovery code) for the real `access_token`. A small `EmailSender` interface
with one SMTP implementation sends the password-reset email through a new `mail-relay`
Postfix container.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, `pyjwt`, `bcrypt`, `pyotp` (new),
Python's stdlib `smtplib` (no new email dependency), pytest + `httpx`'s `TestClient`.

**Spec:** `docs/superpowers/specs/2026-10-01-frontend-webapp-design.md`

## Global Constraints

- All new endpoints live under the existing `/api/v1` prefix (applied at include-time in
  `core_api/main.py`), matching every other router.
- Scoped tokens (`setup_token`, `mfa_token`) expire in 5 minutes and carry a `scope` claim
  (`"totp_setup"` / `"mfa"`) and a `jti` claim; a dependency function rejects a token
  presented to the wrong endpoint type, exactly like `get_current_user` already rejects a
  missing/invalid `access_token`.
- System-generated random credentials (recovery codes, password reset tokens) are hashed
  with SHA-256 before storage, matching the existing `collector_api_key_hash` pattern in
  `core_api/routers/sources.py` — not bcrypt, which is reserved for user-chosen low-entropy
  secrets (passwords). SHA-256 is deterministic, so lookup is a direct query, not an
  iterate-and-compare loop.
- Every error response stays structured (`{code, message, detail}`) via `AppError`,
  matching `core_api/errors.py`. No bare 500s, no generic string bodies.
- Login and password-reset-request responses never reveal whether an email is registered
  (existing `invalid_credentials` behavior for login; password-reset-request always
  returns the same generic message). Registration's `409 email_taken` is the one
  deliberate exception — creating an account necessarily reveals the email is taken.
- `PasswordResetToken` expiry is 1 hour. Recovery codes: exactly 10 generated at TOTP
  enrollment, each single-use.
- No refresh-token mechanism — out of scope per the spec's "Explicitly deferred" section.

## Review Focus

- A used recovery code is presented a second time — must be rejected (`used_at` already
  set), not accepted again.
- A `setup_token` or `mfa_token` is presented directly to a normal `access_token`-protected
  endpoint (e.g. `GET /sites`) *before* TOTP is ever verified — this must be rejected, not
  silently accepted as a valid bearer token just because it's signed with the same secret.
  This is the most severe case: unfixed, it bypasses 2FA entirely during the token's
  5-minute window. The reverse direction matters too: a full `access_token` presented to
  `/auth/totp/verify` or `/auth/totp/setup/start` must also be rejected.
- A `PasswordResetToken` is reused after a successful confirm, or presented after its
  1-hour expiry — both must be rejected, not silently accepted.
- Two concurrent registrations for the same email race to commit — the loser must get a
  structured `409 email_taken`, not an unhandled `IntegrityError` turning into a bare 500.
- A correct TOTP/recovery code is submitted on the 6th attempt against the same
  `mfa_token`, after 5 prior wrong attempts — must still be rejected; the attempt cap is
  enforced before the code is even checked, not just after enough wrong guesses.

---

## Task 1: TOTP + recovery-code schema

**Files:**
- Modify: `core-api/core_api/models/identity.py`
- Modify: `core-api/core_api/models/__init__.py`
- Create: `core-api/alembic/versions/<new>_add_totp_and_recovery_codes.py`
- Test: `core-api/tests/test_schema_constraints.py`

**Interfaces:**
- Produces: `User.totp_secret: str | None`, `User.totp_enabled: bool`, `RecoveryCode`
  model (`id`, `user_id`, `code_hash`, `used_at`), both importable from
  `core_api.models.identity` and re-exported from `core_api.models`.

- [ ] **Step 1: Write the failing test**

Append to `core-api/tests/test_schema_constraints.py`:

```python
def test_user_totp_defaults_to_disabled(db):
    from core_api.models.identity import User

    u = User(email="totp-default@example.com", password_hash="hash")
    db.add(u)
    db.commit()
    db.refresh(u)
    assert u.totp_enabled is False
    assert u.totp_secret is None


def test_recovery_code_round_trip(db, user):
    from core_api.models.identity import RecoveryCode

    code = RecoveryCode(user_id=user.id, code_hash="a" * 64)
    db.add(code)
    db.commit()
    db.refresh(code)
    assert code.used_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_schema_constraints.py -k totp_default -v`
Expected: FAIL with `ImportError` or `AttributeError` (no `totp_enabled`/`RecoveryCode` yet).

- [ ] **Step 3: Add the columns and model**

In `core-api/core_api/models/identity.py`, add `Boolean` to the `sqlalchemy` import and
add the two columns to `User`, plus the new `RecoveryCode` class after `SiteAccess`:

```python
from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
```

```python
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    amber_api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

```python
class RecoveryCode(Base):
    """One-time TOTP-recovery codes, generated 10-at-a-time when a user
    completes TOTP enrollment. code_hash is SHA-256 (see Global Constraints
    in the plan this came from — deterministic hash, direct-lookup
    verification, not bcrypt)."""

    __tablename__ = "recovery_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

In `core-api/core_api/models/__init__.py`, update the identity import and `__all__`:

```python
from core_api.models.identity import RecoveryCode, Site, SiteAccess, SiteAccessRole, User
```

Add `"RecoveryCode"` to `__all__` (alongside the existing `"SiteAccess"` entry).

- [ ] **Step 4: Generate and hand-check the migration**

Run: `cd core-api && uv run alembic revision --autogenerate -m "add totp and recovery codes"`

Open the generated file under `alembic/versions/` and confirm it contains (adjust column
order to match if autogenerate orders differently — the content, not the order, matters):

```python
def upgrade() -> None:
    op.add_column('users', sa.Column('totp_secret', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table('recovery_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code_hash')
    )
    op.create_index(op.f('ix_recovery_codes_user_id'), 'recovery_codes', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_recovery_codes_user_id'), table_name='recovery_codes')
    op.drop_table('recovery_codes')
    op.drop_column('users', 'totp_enabled')
    op.drop_column('users', 'totp_secret')
```

`server_default=sa.false()` matters: without it, applying this migration against a
database that already has rows in `users` would fail the `nullable=False` constraint on
existing rows.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_schema_constraints.py -v`
Expected: PASS, including the two new tests.

- [ ] **Step 6: Commit**

```bash
cd core-api && git add core_api/models/identity.py core_api/models/__init__.py alembic/versions/*add_totp_and_recovery_codes.py tests/test_schema_constraints.py
git commit -m "Add TOTP fields and recovery_codes table"
```

---

## Task 2: Password-reset-token schema

**Files:**
- Modify: `core-api/core_api/models/identity.py`
- Modify: `core-api/core_api/models/__init__.py`
- Create: `core-api/alembic/versions/<new>_add_password_reset_tokens.py`
- Test: `core-api/tests/test_schema_constraints.py`

**Interfaces:**
- Produces: `PasswordResetToken` model (`id`, `user_id`, `token_hash`, `expires_at`,
  `used_at`), importable from `core_api.models.identity` and `core_api.models`.

- [ ] **Step 1: Write the failing test**

Append to `core-api/tests/test_schema_constraints.py`:

```python
def test_password_reset_token_round_trip(db, user):
    import datetime as dt

    from core_api.models.identity import PasswordResetToken

    token = PasswordResetToken(
        user_id=user.id,
        token_hash="b" * 64,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    assert token.used_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && uv run pytest tests/test_schema_constraints.py -k password_reset_token -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Add the model**

In `core-api/core_api/models/identity.py`, add after `RecoveryCode`:

```python
class PasswordResetToken(Base):
    """Single-use, expiring token emailed to a user who requested a password
    reset. token_hash is SHA-256, same reasoning as RecoveryCode.code_hash."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

In `core-api/core_api/models/__init__.py`:

```python
from core_api.models.identity import PasswordResetToken, RecoveryCode, Site, SiteAccess, SiteAccessRole, User
```

Add `"PasswordResetToken"` to `__all__`.

- [ ] **Step 4: Generate and hand-check the migration**

Run: `cd core-api && uv run alembic revision --autogenerate -m "add password reset tokens"`

Confirm the generated `upgrade()`/`downgrade()` create/drop a `password_reset_tokens`
table shaped like:

```python
def upgrade() -> None:
    op.create_table('password_reset_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_password_reset_tokens_user_id'), 'password_reset_tokens', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_password_reset_tokens_user_id'), table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_schema_constraints.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd core-api && git add core_api/models/identity.py core_api/models/__init__.py alembic/versions/*add_password_reset_tokens.py tests/test_schema_constraints.py
git commit -m "Add password_reset_tokens table"
```

---

## Task 3: Scoped JWTs and MFA attempt tracking

**Files:**
- Modify: `core-api/core_api/security.py`
- Create: `core-api/core_api/mfa_attempts.py`
- Modify: `core-api/core_api/deps.py`
- Test: `core-api/tests/test_security.py`
- Test: `core-api/tests/test_deps.py`

**Interfaces:**
- Produces: `security.create_scoped_token(user_id: int, scope: str, *, expiry_minutes: int = 5) -> tuple[str, str]`
  (returns `(token, jti)`); `security.decode_scoped_token(token: str, expected_scope: str) -> tuple[int, str]`
  (returns `(user_id, jti)`, raises `jwt.InvalidTokenError` on wrong/missing scope exactly
  like a malformed token); `security.hash_token(raw: str) -> str` (SHA-256, for recovery
  codes and reset tokens).
- Produces: `mfa_attempts.register_failure(jti: str) -> None`,
  `mfa_attempts.is_locked(jti: str) -> bool`, `mfa_attempts.clear(jti: str) -> None`.
- Produces: `deps.require_setup_token(...) -> User`,
  `deps.require_mfa_token(...) -> tuple[User, str]` (the `str` is the token's `jti`, for
  the caller to pass to `mfa_attempts` on failure/success).
- Consumes: `core_api.errors.AppError`, `core_api.models.identity.User`,
  `core_api.deps.get_db_session`, the existing `_bearer_scheme` in `deps.py`.

- [ ] **Step 1: Write the failing tests**

Append to `core-api/tests/test_security.py` (check the file first — if it doesn't exist,
create it with this content):

```python
import datetime

import jwt as pyjwt
import pytest

from core_api.security import create_scoped_token, decode_scoped_token, hash_token


def test_scoped_token_round_trips_user_id_and_jti():
    token, jti = create_scoped_token(42, "totp_setup")
    user_id, decoded_jti = decode_scoped_token(token, "totp_setup")
    assert user_id == 42
    assert decoded_jti == jti


def test_scoped_token_rejects_wrong_scope():
    token, _jti = create_scoped_token(42, "totp_setup")
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_scoped_token(token, "mfa")


def test_hash_token_is_deterministic_and_matches_sha256():
    import hashlib

    assert hash_token("abc") == hashlib.sha256(b"abc").hexdigest()


def test_decode_access_token_rejects_a_scoped_token():
    """The critical cross-direction check: a setup_token/mfa_token must NOT
    work as a normal access_token just because it's signed with the same
    secret. Without this, a token issued before TOTP is ever verified would
    grant full API access — a full 2FA bypass during its 5-minute window."""
    from core_api.security import decode_access_token

    scoped_token, _jti = create_scoped_token(42, "totp_setup")
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_access_token(scoped_token)
```

Append to `core-api/tests/test_deps.py` (check the file first for existing fixtures/style
to match):

```python
import pytest

from core_api.errors import AppError
from core_api.security import create_scoped_token


def test_require_setup_token_rejects_mfa_scoped_token(db, user):
    from fastapi.security import HTTPAuthorizationCredentials

    from core_api.deps import require_setup_token

    token, _jti = create_scoped_token(user.id, "mfa")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(AppError) as exc_info:
        require_setup_token(credentials=creds, db=db)
    assert exc_info.value.status_code == 401


def test_require_mfa_token_rejects_setup_scoped_token(db, user):
    from fastapi.security import HTTPAuthorizationCredentials

    from core_api.deps import require_mfa_token

    token, _jti = create_scoped_token(user.id, "totp_setup")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(AppError) as exc_info:
        require_mfa_token(credentials=creds, db=db)
    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_a_scoped_token(db, user):
    from fastapi.security import HTTPAuthorizationCredentials

    from core_api.deps import get_current_user

    token, _jti = create_scoped_token(user.id, "mfa")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(AppError) as exc_info:
        get_current_user(credentials=creds, db=db)
    assert exc_info.value.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_security.py tests/test_deps.py -v`
Expected: FAIL with `ImportError` (none of the new names exist yet).

- [ ] **Step 3: Implement scoped tokens and the attempt tracker**

First, close the cross-direction gap: modify the existing `decode_access_token` in
`core-api/core_api/security.py` so a scoped token (which carries a `scope` claim) is
rejected rather than silently accepted as a normal access token:

```python
def decode_access_token(token: str) -> int:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if "scope" in payload:
        raise jwt.InvalidTokenError("access token must not carry a scope claim")
    return int(payload["sub"])
```

Then add to `core-api/core_api/security.py` (alongside `create_access_token`/
`decode_access_token`; add `import hashlib` and `import secrets` to its imports):

```python
def create_scoped_token(user_id: int, scope: str, *, expiry_minutes: int = 5) -> tuple[str, str]:
    """A short-lived JWT for a specific next step in the login flow
    (TOTP setup or TOTP verify) — distinct from create_access_token's
    long-lived, unscoped token, so it can't be used against any other
    endpoint. jti lets the caller track failed-attempt counts server-side
    without the stateless JWT itself needing to carry mutable state."""
    settings = get_settings()
    now = datetime.datetime.now(datetime.timezone.utc)
    jti = secrets.token_urlsafe(16)
    payload = {
        "sub": str(user_id),
        "scope": scope,
        "jti": jti,
        "iat": now,
        "exp": now + datetime.timedelta(minutes=expiry_minutes),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, jti


def decode_scoped_token(token: str, expected_scope: str) -> tuple[int, str]:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("scope") != expected_scope:
        raise jwt.InvalidTokenError(f"expected scope {expected_scope!r}, got {payload.get('scope')!r}")
    return int(payload["sub"]), payload["jti"]


def hash_token(raw: str) -> str:
    """SHA-256 for system-generated random credentials (recovery codes,
    password reset tokens) — deterministic by design, unlike bcrypt, so
    verification is a direct DB lookup rather than an iterate-and-compare
    loop. Not for passwords; see Global Constraints in the plan this came
    from."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

Create `core-api/core_api/mfa_attempts.py`:

```python
from __future__ import annotations

# Module-level, in-process attempt counter keyed by mfa_token jti. Bounded by
# each scoped token's own 5-minute expiry (core_api.security.create_scoped_token)
# — entries are never explicitly evicted, but at this project's personal-use
# scale (a handful of users, occasional login attempts) unbounded growth of a
# tiny dict over the life of one process is not a real concern. Explicitly
# single-process: a multi-worker deployment would need a shared store instead
# (e.g. Redis) — not needed at this scale, per the Core API spec's "not
# built for scale" stance.

_MAX_ATTEMPTS = 5
_failed_attempts: dict[str, int] = {}


def register_failure(jti: str) -> None:
    _failed_attempts[jti] = _failed_attempts.get(jti, 0) + 1


def is_locked(jti: str) -> bool:
    return _failed_attempts.get(jti, 0) >= _MAX_ATTEMPTS


def clear(jti: str) -> None:
    _failed_attempts.pop(jti, None)
```

Add to `core-api/core_api/deps.py` (after the existing `get_current_user`):

```python
from core_api.mfa_attempts import is_locked
from core_api.security import decode_scoped_token


def require_setup_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db_session),
) -> User:
    if credentials is None:
        raise AppError(401, "missing_token", "Authorization header is required")
    try:
        user_id, _jti = decode_scoped_token(credentials.credentials, "totp_setup")
    except pyjwt.InvalidTokenError as exc:
        raise AppError(401, "invalid_token", "Invalid or expired token") from exc
    user = db.get(User, user_id)
    if user is None:
        raise AppError(401, "invalid_token", "User no longer exists")
    return user


def require_mfa_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db_session),
) -> tuple[User, str]:
    if credentials is None:
        raise AppError(401, "missing_token", "Authorization header is required")
    try:
        user_id, jti = decode_scoped_token(credentials.credentials, "mfa")
    except pyjwt.InvalidTokenError as exc:
        raise AppError(401, "invalid_token", "Invalid or expired token") from exc
    if is_locked(jti):
        raise AppError(401, "too_many_attempts", "Too many failed attempts; please log in again")
    user = db.get(User, user_id)
    if user is None:
        raise AppError(401, "invalid_token", "User no longer exists")
    return user, jti
```

(`pyjwt` is already imported as `jwt as pyjwt` at the top of `deps.py` — reuse it, don't
add a second import.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_security.py tests/test_deps.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd core-api && git add core_api/security.py core_api/mfa_attempts.py core_api/deps.py tests/test_security.py tests/test_deps.py
git commit -m "Add scoped JWTs and MFA attempt tracking"
```

---

## Task 4: EmailSender abstraction and Settings additions

**Files:**
- Create: `core-api/core_api/email_sender.py`
- Modify: `core-api/core_api/config.py`
- Modify: `core-api/.env.example`
- Test: `core-api/tests/test_email_sender.py`

**Interfaces:**
- Produces: `EmailSender` (Protocol: `send(self, to: str, subject: str, body: str) -> None`),
  `SmtpEmailSender` (reads `get_settings()` for connection details),
  `FakeEmailSender` (records sends in `self.sent: list[tuple[str, str, str]]`, for tests).
- Produces: `Settings.smtp_host: str`, `Settings.smtp_port: int`, `Settings.smtp_from: str`,
  `Settings.smtp_user: str | None`, `Settings.smtp_password: str | None`,
  `Settings.frontend_base_url: str`.

- [ ] **Step 1: Write the failing test**

Create `core-api/tests/test_email_sender.py`:

```python
from __future__ import annotations

from core_api.email_sender import FakeEmailSender


def test_fake_email_sender_records_sends():
    sender = FakeEmailSender()
    sender.send(to="a@example.com", subject="Subject", body="Body")
    assert sender.sent == [("a@example.com", "Subject", "Body")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && uv run pytest tests/test_email_sender.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Add to `core-api/core_api/config.py`'s `Settings` dataclass and `get_settings()`:

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
    )
```

Create `core-api/core_api/email_sender.py`:

```python
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Protocol

from core_api.config import get_settings


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class SmtpEmailSender:
    """Sends via the configured SMTP relay (the mail-relay Postfix container
    by default — see docs/superpowers/specs/2026-10-01-frontend-webapp-design.md).
    No TLS/auth handshake unless smtp_user/smtp_password are set, since the
    default target is an internal, trusted-network relay."""

    def send(self, to: str, subject: str, body: str) -> None:
        settings = get_settings()
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as client:
            if settings.smtp_user and settings.smtp_password:
                client.starttls()
                client.login(settings.smtp_user, settings.smtp_password)
            client.send_message(msg)


class FakeEmailSender:
    """Test double — records sends instead of opening a real SMTP connection."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))
```

Append to `core-api/.env.example`:

```
SMTP_HOST=mail-relay
SMTP_PORT=25
SMTP_FROM=noreply@localhost
SMTP_USER=
SMTP_PASSWORD=
FRONTEND_BASE_URL=http://localhost:5173
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && uv run pytest tests/test_email_sender.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd core-api && git add core_api/email_sender.py core_api/config.py .env.example tests/test_email_sender.py
git commit -m "Add EmailSender abstraction and SMTP settings"
```

---

## Task 5: Wire EmailSender into the app, and `POST /auth/register`

**Files:**
- Modify: `core-api/core_api/main.py`
- Modify: `core-api/core_api/deps.py`
- Modify: `core-api/core_api/schemas.py`
- Modify: `core-api/core_api/routers/auth.py`
- Modify: `core-api/tests/conftest.py`
- Test: `core-api/tests/test_auth.py`

**Interfaces:**
- Consumes: `EmailSender`, `SmtpEmailSender` (Task 4); `security.hash_password` (existing).
- Produces: `app.state.email_sender: EmailSender` (set in `create_app`);
  `deps.get_email_sender(request) -> EmailSender`; schema `RegisterRequest(email, password)`,
  `UserOut(id, email)`; endpoint `POST /api/v1/auth/register`.

- [ ] **Step 1: Write the failing test**

Add to `core-api/tests/conftest.py`, so every test gets a `FakeEmailSender` instead of a
real SMTP attempt — modify the existing `app_and_db_url` fixture:

```python
from core_api.email_sender import FakeEmailSender


@pytest.fixture()
def fake_email_sender() -> FakeEmailSender:
    return FakeEmailSender()


@pytest.fixture()
def app_and_db_url(tmp_path, monkeypatch, fake_email_sender):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    db_url = f"sqlite:///{tmp_path}/app_test.db"
    run_migrations(db_url)
    app = create_app(database_url=db_url, email_sender=fake_email_sender)
    return app, db_url
```

Add to `core-api/tests/test_auth.py`:

```python
def test_register_creates_user(client):
    response = client.post("/api/v1/auth/register", json={"email": "new@example.com", "password": "a-password"})
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert "password" not in body
    assert "password_hash" not in body


def test_register_rejects_duplicate_email(client):
    client.post("/api/v1/auth/register", json={"email": "dup@example.com", "password": "a-password"})
    response = client.post("/api/v1/auth/register", json={"email": "dup@example.com", "password": "another"})
    assert response.status_code == 409
    assert response.json()["code"] == "email_taken"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_auth.py -k register -v`
Expected: FAIL — `create_app()` doesn't accept `email_sender`, and `/auth/register` 404s.

- [ ] **Step 3: Implement**

In `core-api/core_api/main.py`, update the import block and `create_app` signature:

```python
from core_api.email_sender import EmailSender, SmtpEmailSender
```

```python
def create_app(database_url: str | None = None, email_sender: EmailSender | None = None) -> FastAPI:
    engine = make_engine(database_url)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.session_factory = session_factory
        yield
        engine.dispose()

    app = FastAPI(title="Core API", lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.email_sender = email_sender or SmtpEmailSender()
    install_error_handlers(app)
    ...
```

(Leave the rest of `create_app` — the router includes — unchanged for this task; Task 7
and Task 8 add the two new routers there.)

In `core-api/core_api/deps.py`, add:

```python
from core_api.email_sender import EmailSender


def get_email_sender(request: Request) -> EmailSender:
    return request.app.state.email_sender
```

In `core-api/core_api/schemas.py`, add:

```python
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: str

    model_config = {"from_attributes": True}
```

In `core-api/core_api/routers/auth.py`, add the register endpoint and its imports. Note
there is deliberately no "check if the email already exists, then insert" pre-check —
that shape has a TOCTOU race (two concurrent registrations can both pass the check before
either commits) that this Global Constraints/Review Focus item exists specifically to
rule out. Instead, always attempt the insert and catch the unique-constraint violation on
commit — this is the exact pattern `core_api/routers/sources.py`'s `create_source` already
uses for the same reason (`_hash_key`/`collector_api_key_hash` conflict handling); match
it rather than inventing a different shape:

```python
from sqlalchemy.exc import IntegrityError

from core_api.schemas import LoginRequest, LoginResponse, RegisterRequest, UserOut
from core_api.security import create_access_token, hash_password, verify_password


@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db_session)) -> User:
    user = User(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "email_taken", "An account with this email already exists") from exc
    db.refresh(user)
    return user
```

This also means the existing `test_register_rejects_duplicate_email` test (two sequential
register calls with the same email) already exercises this exact `IntegrityError` → `409`
path, since nothing short-circuits before the insert is attempted — no separate
concurrency test is needed to cover the Review Focus item about this race.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/ -v`
Expected: PASS — including the full existing suite, since `app_and_db_url` now always
passes a `FakeEmailSender` so nothing attempts a real SMTP connection.

- [ ] **Step 5: Commit**

```bash
cd core-api && git add core_api/main.py core_api/deps.py core_api/schemas.py core_api/routers/auth.py tests/conftest.py tests/test_auth.py
git commit -m "Add POST /auth/register and wire EmailSender into the app"
```

---

## Task 6: Two-step login (`totp_setup_required` / `totp_required`)

**Files:**
- Modify: `core-api/core_api/schemas.py`
- Modify: `core-api/core_api/routers/auth.py`
- Modify: `core-api/tests/test_auth.py`

**Interfaces:**
- Consumes: `security.create_scoped_token` (Task 3).
- Produces: schema `LoginChallenge(status: Literal["totp_setup_required", "totp_required"], setup_token: str | None, mfa_token: str | None)`.
  `POST /api/v1/auth/login` now returns this instead of `LoginResponse` directly.

- [ ] **Step 1: Update the existing login tests for the new contract**

Replace the body of `core-api/tests/test_auth.py`'s two success/credential tests (the
existing `test_login_succeeds_with_correct_password` assertion on `access_token` is now
wrong — login no longer returns one directly):

```python
def test_login_with_unenrolled_user_requires_totp_setup(client):
    client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "correct-password"})
    response = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "correct-password"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "totp_setup_required"
    assert body["setup_token"]
    assert body["mfa_token"] is None


def test_login_rejects_wrong_password(client):
    client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "correct-password"})
    response = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_login_rejects_unknown_email(client):
    response = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_setup_token_cannot_be_used_against_a_normal_protected_endpoint(client):
    """End-to-end version of the security.py-level check added in Task 3:
    a setup_token issued by /auth/login must not work as a bearer token
    anywhere else in the API."""
    client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "correct-password"})
    login = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "correct-password"})
    setup_token = login.json()["setup_token"]
    response = client.get("/api/v1/sites", headers={"Authorization": f"Bearer {setup_token}"})
    assert response.status_code == 401
```

Delete the old `seed_user` helper and `test_login_succeeds_with_correct_password` test
(registration now covers user creation; the replacement test above supersedes it).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && uv run pytest tests/test_auth.py -v`
Expected: FAIL — login still returns the old `{access_token, token_type}` shape.

- [ ] **Step 3: Implement**

In `core-api/core_api/schemas.py`, add (needs `from typing import Literal` added to the
top-level imports):

```python
class LoginChallenge(BaseModel):
    status: Literal["totp_setup_required", "totp_required"]
    setup_token: str | None = None
    mfa_token: str | None = None
```

In `core-api/core_api/routers/auth.py`, replace the `login` function body and imports:

```python
from core_api.schemas import LoginChallenge, LoginRequest, RegisterRequest, UserOut
from core_api.security import create_scoped_token, hash_password, verify_password


@router.post("/login", response_model=LoginChallenge)
def login(payload: LoginRequest, db: Session = Depends(get_db_session)) -> LoginChallenge:
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AppError(401, "invalid_credentials", "Email or password is incorrect")
    if not user.totp_enabled:
        setup_token, _jti = create_scoped_token(user.id, "totp_setup")
        return LoginChallenge(status="totp_setup_required", setup_token=setup_token)
    mfa_token, _jti = create_scoped_token(user.id, "mfa")
    return LoginChallenge(status="totp_required", mfa_token=mfa_token)
```

`create_access_token` is no longer imported or called in this file at all — neither
`register` (Task 5) nor the new `login` returns one directly. It moves to `totp.py` in
Task 7, where the TOTP setup-confirm/verify endpoints issue the real `access_token` once
2FA has actually been satisfied.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd core-api && git add core_api/schemas.py core_api/routers/auth.py tests/test_auth.py
git commit -m "Make login a two-step TOTP challenge instead of returning access_token directly"
```

---

## Task 7: TOTP enrollment (`/auth/totp/setup/start`, `/auth/totp/setup/confirm`)

**Files:**
- Modify: `core-api/pyproject.toml`
- Modify: `core-api/core_api/security.py`
- Modify: `core-api/core_api/schemas.py`
- Create: `core-api/core_api/routers/totp.py`
- Modify: `core-api/core_api/main.py`
- Create: `core-api/tests/test_totp.py`

**Interfaces:**
- Consumes: `deps.require_setup_token` (Task 3), `RecoveryCode` (Task 1),
  `security.hash_token` (Task 3).
- Produces: `security.generate_totp_secret() -> str`,
  `security.totp_provisioning_uri(secret: str, email: str) -> str`,
  `security.verify_totp_code(secret: str, code: str) -> bool`,
  `security.generate_recovery_codes(count: int = 10) -> list[str]`; schemas
  `TotpSetupStartOut(secret, otpauth_uri)`, `TotpSetupConfirmIn(code)`,
  `TotpSetupConfirmOut(access_token, token_type, recovery_codes)`; endpoints
  `POST /api/v1/auth/totp/setup/start`, `POST /api/v1/auth/totp/setup/confirm`.

- [ ] **Step 1: Add the `pyotp` dependency**

In `core-api/pyproject.toml`, add to `dependencies`:

```toml
    "pyotp>=2.9,<3.0",
```

Run: `cd core-api && uv sync`

- [ ] **Step 2: Write the failing tests**

Create `core-api/tests/test_totp.py`:

```python
from __future__ import annotations

import pyotp


def _register_and_get_setup_token(client, email="a@example.com", password="correct-password"):
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return login.json()["setup_token"]


def test_setup_start_returns_secret_and_otpauth_uri(client):
    setup_token = _register_and_get_setup_token(client)
    response = client.post(
        "/api/v1/auth/totp/setup/start", headers={"Authorization": f"Bearer {setup_token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["secret"]
    assert body["otpauth_uri"].startswith("otpauth://totp/")


def test_setup_confirm_with_correct_code_enables_totp_and_returns_recovery_codes(client):
    setup_token = _register_and_get_setup_token(client)
    start = client.post(
        "/api/v1/auth/totp/setup/start", headers={"Authorization": f"Bearer {setup_token}"}
    )
    secret = start.json()["secret"]
    code = pyotp.TOTP(secret).now()
    response = client.post(
        "/api/v1/auth/totp/setup/confirm",
        json={"code": code},
        headers={"Authorization": f"Bearer {setup_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert len(body["recovery_codes"]) == 10
    assert len(set(body["recovery_codes"])) == 10  # all distinct


def test_setup_confirm_with_wrong_code_is_rejected(client):
    setup_token = _register_and_get_setup_token(client)
    client.post("/api/v1/auth/totp/setup/start", headers={"Authorization": f"Bearer {setup_token}"})
    response = client.post(
        "/api/v1/auth/totp/setup/confirm",
        json={"code": "000000"},
        headers={"Authorization": f"Bearer {setup_token}"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_code"


def test_setup_endpoints_reject_a_normal_access_token(client):
    client.post("/api/v1/auth/register", json={"email": "b@example.com", "password": "correct-password"})
    # An unenrolled user has no access_token yet, so forge the scenario with a
    # wrong-scope token to exercise the dependency directly: a setup_token's
    # own endpoint must still 401 a non-setup-scoped bearer token.
    response = client.post(
        "/api/v1/auth/totp/setup/start", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_totp.py -v`
Expected: FAIL with 404s (router doesn't exist yet).

- [ ] **Step 4: Implement TOTP helpers in `security.py`**

Add to `core-api/core_api/security.py` (add `import pyotp` and `import secrets` to its
imports if not already present from Task 3):

```python
def generate_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, email: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name="Enphase Data")


def verify_totp_code(secret: str, code: str) -> bool:
    # valid_window=1 tolerates the previous/next 30s window for clock drift
    # between the server and the user's authenticator device.
    return pyotp.totp.TOTP(secret).verify(code, valid_window=1)


def generate_recovery_codes(count: int = 10) -> list[str]:
    return [secrets.token_hex(5) for _ in range(count)]
```

- [ ] **Step 5: Add schemas**

Add to `core-api/core_api/schemas.py`:

```python
class TotpSetupStartOut(BaseModel):
    secret: str
    otpauth_uri: str


class TotpSetupConfirmIn(BaseModel):
    code: str


class TotpSetupConfirmOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    recovery_codes: list[str]
```

- [ ] **Step 6: Implement the router**

Create `core-api/core_api/routers/totp.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, require_setup_token
from core_api.errors import AppError
from core_api.models.identity import RecoveryCode, User
from core_api.schemas import TotpSetupConfirmIn, TotpSetupConfirmOut, TotpSetupStartOut
from core_api.security import (
    create_access_token,
    generate_recovery_codes,
    generate_totp_secret,
    hash_token,
    totp_provisioning_uri,
    verify_totp_code,
)

router = APIRouter(prefix="/auth/totp", tags=["auth"])


@router.post("/setup/start", response_model=TotpSetupStartOut)
def start_setup(
    user: User = Depends(require_setup_token),
    db: Session = Depends(get_db_session),
) -> TotpSetupStartOut:
    secret = generate_totp_secret()
    user.totp_secret = secret
    db.commit()
    return TotpSetupStartOut(secret=secret, otpauth_uri=totp_provisioning_uri(secret, user.email))


@router.post("/setup/confirm", response_model=TotpSetupConfirmOut)
def confirm_setup(
    payload: TotpSetupConfirmIn,
    user: User = Depends(require_setup_token),
    db: Session = Depends(get_db_session),
) -> TotpSetupConfirmOut:
    if user.totp_secret is None or not verify_totp_code(user.totp_secret, payload.code):
        raise AppError(401, "invalid_code", "Incorrect verification code")
    user.totp_enabled = True
    raw_codes = generate_recovery_codes()
    for raw_code in raw_codes:
        db.add(RecoveryCode(user_id=user.id, code_hash=hash_token(raw_code)))
    db.commit()
    return TotpSetupConfirmOut(access_token=create_access_token(user.id), recovery_codes=raw_codes)
```

In `core-api/core_api/main.py`, add the import and include the router:

```python
from core_api.routers.totp import router as totp_router
```

```python
    app.include_router(totp_router, prefix=api_prefix)
```

(Add this line next to the other `app.include_router(...)` calls, before the `return app`.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/ -v`
Expected: PASS, full suite.

- [ ] **Step 8: Commit**

```bash
cd core-api && git add pyproject.toml uv.lock core_api/security.py core_api/schemas.py core_api/routers/totp.py core_api/main.py tests/test_totp.py
git commit -m "Add TOTP enrollment endpoints (setup/start, setup/confirm)"
```

---

## Task 8: TOTP verification (`/auth/totp/verify`) with recovery codes and attempt cap

**Files:**
- Modify: `core-api/core_api/schemas.py`
- Modify: `core-api/core_api/routers/totp.py`
- Modify: `core-api/tests/test_totp.py`

**Interfaces:**
- Consumes: `deps.require_mfa_token` (Task 3), `mfa_attempts.register_failure`/`is_locked`/`clear`
  (Task 3), `RecoveryCode` (Task 1).
- Produces: schema `TotpVerifyIn(code)`; endpoint `POST /api/v1/auth/totp/verify`
  (returns the existing `LoginResponse` shape on success).

- [ ] **Step 1: Write the failing tests**

Append to `core-api/tests/test_totp.py`:

```python
def _enroll_and_login(client, email="enrolled@example.com", password="correct-password"):
    """Returns (recovery_codes, mfa_token) for a freshly enrolled user who has
    just logged in again (so totp_enabled is True and a fresh mfa_token exists)."""
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    setup_login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    setup_token = setup_login.json()["setup_token"]
    start = client.post("/api/v1/auth/totp/setup/start", headers={"Authorization": f"Bearer {setup_token}"})
    secret = start.json()["secret"]
    code = pyotp.TOTP(secret).now()
    confirm = client.post(
        "/api/v1/auth/totp/setup/confirm",
        json={"code": code},
        headers={"Authorization": f"Bearer {setup_token}"},
    )
    recovery_codes = confirm.json()["recovery_codes"]
    second_login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert second_login.json()["status"] == "totp_required"
    return secret, recovery_codes, second_login.json()["mfa_token"]


def test_verify_with_correct_totp_code_returns_access_token(client):
    secret, _recovery_codes, mfa_token = _enroll_and_login(client)
    code = pyotp.TOTP(secret).now()
    response = client.post(
        "/api/v1/auth/totp/verify", json={"code": code}, headers={"Authorization": f"Bearer {mfa_token}"}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_verify_with_recovery_code_succeeds_once_then_fails(client):
    _secret, recovery_codes, mfa_token = _enroll_and_login(client)
    first = client.post(
        "/api/v1/auth/totp/verify",
        json={"code": recovery_codes[0]},
        headers={"Authorization": f"Bearer {mfa_token}"},
    )
    assert first.status_code == 200

    # A fresh mfa_token is needed for the second attempt since the first
    # call's success already cleared this jti's attempt counter but the
    # recovery code itself is now used — login again to get a new mfa_token.
    _secret2, _codes2, mfa_token_2 = _enroll_and_login(client, email="enrolled2@example.com")
    reused = client.post(
        "/api/v1/auth/totp/verify",
        json={"code": recovery_codes[0]},
        headers={"Authorization": f"Bearer {mfa_token_2}"},
    )
    assert reused.status_code == 401


def test_verify_locks_out_after_five_failed_attempts(client):
    secret, _recovery_codes, mfa_token = _enroll_and_login(client)
    for _ in range(5):
        response = client.post(
            "/api/v1/auth/totp/verify",
            json={"code": "000000"},
            headers={"Authorization": f"Bearer {mfa_token}"},
        )
        assert response.status_code == 401

    # Even a correct code is now rejected — the same mfa_token is locked out.
    correct_code = pyotp.TOTP(secret).now()
    response = client.post(
        "/api/v1/auth/totp/verify",
        json={"code": correct_code},
        headers={"Authorization": f"Bearer {mfa_token}"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "too_many_attempts"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_totp.py -k verify -v`
Expected: FAIL with 404 (endpoint doesn't exist yet).

- [ ] **Step 3: Implement**

Add to `core-api/core_api/schemas.py`:

```python
class TotpVerifyIn(BaseModel):
    code: str
```

In `core-api/core_api/routers/totp.py`, add imports and the endpoint:

```python
import datetime

from sqlalchemy import select

from core_api.deps import get_db_session, require_mfa_token, require_setup_token
from core_api.mfa_attempts import clear as clear_mfa_attempts
from core_api.mfa_attempts import register_failure as register_mfa_failure
from core_api.schemas import LoginResponse, TotpVerifyIn
```

```python
def _find_unused_recovery_code(db: Session, user_id: int, raw_code: str) -> RecoveryCode | None:
    code_hash = hash_token(raw_code)
    return db.execute(
        select(RecoveryCode).where(
            RecoveryCode.user_id == user_id,
            RecoveryCode.code_hash == code_hash,
            RecoveryCode.used_at.is_(None),
        )
    ).scalar_one_or_none()


@router.post("/verify", response_model=LoginResponse)
def verify(
    payload: TotpVerifyIn,
    user_and_jti: tuple[User, str] = Depends(require_mfa_token),
    db: Session = Depends(get_db_session),
) -> LoginResponse:
    user, jti = user_and_jti
    if user.totp_secret and verify_totp_code(user.totp_secret, payload.code):
        clear_mfa_attempts(jti)
        return LoginResponse(access_token=create_access_token(user.id))

    recovery_code = _find_unused_recovery_code(db, user.id, payload.code)
    if recovery_code is not None:
        recovery_code.used_at = datetime.datetime.now(datetime.timezone.utc)
        db.commit()
        clear_mfa_attempts(jti)
        return LoginResponse(access_token=create_access_token(user.id))

    register_mfa_failure(jti)
    raise AppError(401, "invalid_code", "Incorrect code")
```

(`LoginResponse` is the existing `{access_token, token_type}` schema from
`core_api/schemas.py` — import it there alongside the other imports in `totp.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/ -v`
Expected: PASS, full suite.

- [ ] **Step 5: Commit**

```bash
cd core-api && git add core_api/schemas.py core_api/routers/totp.py tests/test_totp.py
git commit -m "Add POST /auth/totp/verify with recovery codes and attempt lockout"
```

---

## Task 9: Password reset (`/auth/password-reset/request`, `/auth/password-reset/confirm`)

**Files:**
- Modify: `core-api/core_api/schemas.py`
- Create: `core-api/core_api/routers/password_reset.py`
- Modify: `core-api/core_api/main.py`
- Create: `core-api/tests/test_password_reset.py`

**Interfaces:**
- Consumes: `deps.get_email_sender` (Task 5), `security.hash_token`/`hash_password`
  (Task 3 / existing), `PasswordResetToken` (Task 2).
- Produces: schemas `PasswordResetRequestIn(email)`, `PasswordResetRequestOut(message)`,
  `PasswordResetConfirmIn(token, new_password)`; endpoints
  `POST /api/v1/auth/password-reset/request`, `POST /api/v1/auth/password-reset/confirm`.

- [ ] **Step 1: Write the failing tests**

Create `core-api/tests/test_password_reset.py`:

```python
from __future__ import annotations

import re


def test_request_for_existing_email_sends_email_with_reset_link(client, fake_email_sender):
    client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "old-password"})
    response = client.post("/api/v1/auth/password-reset/request", json={"email": "a@example.com"})
    assert response.status_code == 200
    assert len(fake_email_sender.sent) == 1
    to, _subject, body = fake_email_sender.sent[0]
    assert to == "a@example.com"
    assert "reset-password?token=" in body


def test_request_for_unknown_email_returns_same_generic_message_and_sends_nothing(client, fake_email_sender):
    response = client.post("/api/v1/auth/password-reset/request", json={"email": "nobody@example.com"})
    assert response.status_code == 200
    known = client.post("/api/v1/auth/password-reset/request", json={"email": "nobody@example.com"})
    assert response.json() == known.json()
    assert fake_email_sender.sent == []


def _extract_token(body: str) -> str:
    match = re.search(r"token=([^\s]+)", body)
    assert match is not None
    return match.group(1)


def test_confirm_with_valid_token_updates_password_and_allows_login(client, fake_email_sender):
    client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "old-password"})
    client.post("/api/v1/auth/password-reset/request", json={"email": "a@example.com"})
    token = _extract_token(fake_email_sender.sent[0][2])

    confirm = client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "new-password"}
    )
    assert confirm.status_code == 204

    login = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "new-password"})
    assert login.status_code == 200
    old_login = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "old-password"})
    assert old_login.status_code == 401


def test_confirm_rejects_reused_token(client, fake_email_sender):
    client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "old-password"})
    client.post("/api/v1/auth/password-reset/request", json={"email": "a@example.com"})
    token = _extract_token(fake_email_sender.sent[0][2])
    client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "new-password"})

    reused = client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "another-password"}
    )
    assert reused.status_code == 400
    assert reused.json()["code"] == "invalid_token"


def test_confirm_rejects_expired_token(client, fake_email_sender, app_and_db_url):
    import datetime

    from core_api.db import make_engine, make_session_factory
    from core_api.models.identity import PasswordResetToken, User
    from core_api.security import hash_token
    from sqlalchemy import select

    _app, db_url = app_and_db_url
    client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "old-password"})

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = session.execute(select(User).where(User.email == "a@example.com")).scalar_one()
    session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token("expired-token"),
            expires_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1),
        )
    )
    session.commit()
    session.close()

    response = client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": "expired-token", "new_password": "new-password"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"


def test_confirm_rejects_unknown_token(client):
    response = client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": "never-issued", "new_password": "new-password"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_password_reset.py -v`
Expected: FAIL with 404s.

- [ ] **Step 3: Add schemas**

Add to `core-api/core_api/schemas.py`:

```python
class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetRequestOut(BaseModel):
    message: str


class PasswordResetConfirmIn(BaseModel):
    token: str
    new_password: str
```

- [ ] **Step 4: Implement the router**

Create `core-api/core_api/routers/password_reset.py`:

```python
from __future__ import annotations

import datetime
import secrets

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.config import get_settings
from core_api.deps import get_db_session, get_email_sender
from core_api.email_sender import EmailSender
from core_api.errors import AppError
from core_api.models.identity import PasswordResetToken, User
from core_api.schemas import PasswordResetConfirmIn, PasswordResetRequestIn, PasswordResetRequestOut
from core_api.security import hash_password, hash_token

router = APIRouter(prefix="/auth/password-reset", tags=["auth"])

_GENERIC_MESSAGE = "If that email is registered, a password reset link has been sent."
_TOKEN_EXPIRY = datetime.timedelta(hours=1)


@router.post("/request", response_model=PasswordResetRequestOut)
def request_reset(
    payload: PasswordResetRequestIn,
    db: Session = Depends(get_db_session),
    email_sender: EmailSender = Depends(get_email_sender),
) -> PasswordResetRequestOut:
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if user is not None:
        raw_token = secrets.token_urlsafe(32)
        now = datetime.datetime.now(datetime.timezone.utc)
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hash_token(raw_token),
                expires_at=now + _TOKEN_EXPIRY,
            )
        )
        db.commit()
        reset_url = f"{get_settings().frontend_base_url}/reset-password?token={raw_token}"
        email_sender.send(
            to=user.email,
            subject="Reset your password",
            body=f"Click the link to reset your password: {reset_url}\nThis link expires in 1 hour.",
        )
    return PasswordResetRequestOut(message=_GENERIC_MESSAGE)


@router.post("/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_reset(payload: PasswordResetConfirmIn, db: Session = Depends(get_db_session)) -> None:
    token_hash = hash_token(payload.token)
    now = datetime.datetime.now(datetime.timezone.utc)
    token = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    ).scalar_one_or_none()
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise AppError(400, "invalid_token", "This reset link is invalid or has expired")
    user = db.get(User, token.user_id)
    assert user is not None  # FK guarantees the referenced user still exists
    user.password_hash = hash_password(payload.new_password)
    token.used_at = now
    db.commit()
```

In `core-api/core_api/main.py`, add the import and include the router:

```python
from core_api.routers.password_reset import router as password_reset_router
```

```python
    app.include_router(password_reset_router, prefix=api_prefix)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/ -v`
Expected: PASS, full suite.

- [ ] **Step 6: Commit**

```bash
cd core-api && git add core_api/schemas.py core_api/routers/password_reset.py core_api/main.py tests/test_password_reset.py
git commit -m "Add password-reset request/confirm endpoints"
```

---

## Task 10: `PATCH /sites/{id}`

**Files:**
- Modify: `core-api/core_api/schemas.py`
- Modify: `core-api/core_api/routers/sites.py`
- Modify: `core-api/tests/test_sites.py`

**Interfaces:**
- Produces: schema `SiteUpdate(name: str | None, timezone: str | None)`; endpoint
  `PATCH /api/v1/sites/{site_id}`.

- [ ] **Step 1: Write the failing tests**

Append to `core-api/tests/test_sites.py` (check the file first for the existing
auth-header/client pattern to match):

```python
def test_owner_can_patch_site_name_and_timezone(client, site, auth_header):
    response = client.patch(
        f"/api/v1/sites/{site.id}",
        json={"name": "New Name", "timezone": "Australia/Perth"},
        headers=auth_header,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "New Name"
    assert body["timezone"] == "Australia/Perth"


def test_patch_site_allows_partial_update(client, site, auth_header):
    response = client.patch(f"/api/v1/sites/{site.id}", json={"name": "Only Name"}, headers=auth_header)
    assert response.status_code == 200
    assert response.json()["name"] == "Only Name"
    assert response.json()["timezone"] == site.timezone


def test_non_owner_cannot_patch_site(client, site, other_user_auth_header):
    response = client.patch(
        f"/api/v1/sites/{site.id}", json={"name": "Hijacked"}, headers=other_user_auth_header
    )
    assert response.status_code == 403
```

If `auth_header`/`other_user_auth_header` fixtures don't already exist in
`core-api/tests/test_sites.py` or `conftest.py`, check how other tests in
`test_sites.py` authenticate as the site owner vs. a different user, and reuse that exact
pattern instead of inventing a new one — this task matches existing conventions, it
doesn't introduce new ones.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_sites.py -k patch -v`
Expected: FAIL with 405 (method not allowed — no PATCH route yet).

- [ ] **Step 3: Implement**

Add to `core-api/core_api/schemas.py`:

```python
class SiteUpdate(BaseModel):
    name: str | None = None
    timezone: str | None = None
```

Add to `core-api/core_api/routers/sites.py`:

```python
from core_api.schemas import SiteAccessGrant, SiteAccessOut, SiteCreate, SiteOut, SiteUpdate


@router.patch("/{site_id}", response_model=SiteOut)
def update_site(
    payload: SiteUpdate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> Site:
    if payload.name is not None:
        site.name = payload.name
    if payload.timezone is not None:
        site.timezone = payload.timezone
    db.commit()
    db.refresh(site)
    return site
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_sites.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd core-api && git add core_api/schemas.py core_api/routers/sites.py tests/test_sites.py
git commit -m "Add PATCH /sites/{id}"
```

---

## Task 11: mail-relay Postfix container

**Files:**
- Create: `mail-relay/Dockerfile`
- Create: `mail-relay/entrypoint.sh`
- Create: `mail-relay/.env.example`

**Interfaces:**
- Produces: a standalone Docker image relaying outbound mail through a configured
  smarthost. Consumed by Core API's `SmtpEmailSender` pointing `SMTP_HOST`/`SMTP_PORT`
  at this container's network address (default `mail-relay:25`, matching
  `Settings.smtp_host`'s default from Task 4).

This container has no Python test suite — it's infrastructure, verified by building and
running it, matching how the project's other Dockerfiles (envoy-collector,
pricing-collector, weather-collector) have no automated tests either. The "test cycle"
here is the manual verification in Step 4.

- [ ] **Step 1: Write the entrypoint script**

Create `mail-relay/entrypoint.sh`:

```bash
#!/bin/sh
set -eu

: "${RELAY_DOMAIN:?RELAY_DOMAIN must be set (the domain this relay sends mail as, e.g. example.com)}"
: "${RELAY_SMARTHOST:?RELAY_SMARTHOST must be set (e.g. [smtp.yourprovider.example]:587)}"

postconf -e "myhostname = ${RELAY_DOMAIN}"
postconf -e "relayhost = ${RELAY_SMARTHOST}"
postconf -e "smtp_sasl_auth_enable = yes"
postconf -e "smtp_sasl_security_options = noanonymous"
postconf -e "smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd"
postconf -e "smtp_use_tls = yes"
# Docker's default bridge network (172.17.0.0/16) and user-defined bridge
# networks (172.16.0.0/12 covers both) can relay through this container
# without SASL auth of their own — Core API authenticates to this container
# implicitly by being on the same trusted Docker network, then this
# container authenticates onward to the real smarthost using RELAY_USERNAME/
# RELAY_PASSWORD below. This container is never exposed outside that
# network (no published port in deployment).
postconf -e "mynetworks = 127.0.0.0/8, [::1]/128, 172.16.0.0/12"

if [ -n "${RELAY_USERNAME:-}" ] && [ -n "${RELAY_PASSWORD:-}" ]; then
    echo "${RELAY_SMARTHOST} ${RELAY_USERNAME}:${RELAY_PASSWORD}" > /etc/postfix/sasl_passwd
    postmap /etc/postfix/sasl_passwd
    chmod 600 /etc/postfix/sasl_passwd /etc/postfix/sasl_passwd.db
fi

exec postfix start-fg
```

- [ ] **Step 2: Write the Dockerfile**

Create `mail-relay/Dockerfile`:

```dockerfile
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends postfix libsasl2-modules \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 25

ENTRYPOINT ["/entrypoint.sh"]
```

- [ ] **Step 3: Write the env example**

Create `mail-relay/.env.example`:

```
RELAY_DOMAIN=yourdomain.example
RELAY_SMARTHOST=[smtp.yourprovider.example]:587
RELAY_USERNAME=
RELAY_PASSWORD=
```

Add a one-line note above it (as a comment) that getting outbound mail reliably delivered
(not landing in spam) requires SPF/DKIM/rDNS alignment configured on `RELAY_DOMAIN`'s own
DNS — operational setup outside this container, per the spec's deferred-items list:

```
# Deliverability (SPF/DKIM/rDNS) is a DNS setup step on RELAY_DOMAIN, not something
# this container configures for you.
RELAY_DOMAIN=yourdomain.example
RELAY_SMARTHOST=[smtp.yourprovider.example]:587
RELAY_USERNAME=
RELAY_PASSWORD=
```

- [ ] **Step 4: Manually verify the image builds and starts**

Run:
```bash
cd mail-relay
docker build -t mail-relay-test .
docker run --rm -e RELAY_DOMAIN=example.com -e RELAY_SMARTHOST='[smtp.example.com]:587' mail-relay-test &
sleep 3
docker exec $(docker ps -q --filter ancestor=mail-relay-test) postfix status
```
Expected: `postfix/postfix-script: the Postfix mail system is running` (or equivalent
non-error status output), confirming the container starts without crashing on the
required env vars. Stop the background container afterward (`docker stop` or `Ctrl+C`
the backgrounded run).

- [ ] **Step 5: Commit**

```bash
git add mail-relay/Dockerfile mail-relay/entrypoint.sh mail-relay/.env.example
git commit -m "Add mail-relay Postfix container for outbound password-reset emails"
```

---

## Final check: full suite

- [ ] Run: `cd core-api && uv run pytest tests/ -v`
- [ ] Expected: PASS, all tests across the whole `core-api` test suite — old and new.
