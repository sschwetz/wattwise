# Account Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a logged-in user change their password, change their email (with
confirmation-link verification), and replace their TOTP authenticator — all from the
Account page, all gated behind re-entering their current password and a live TOTP code.

**Architecture:** Three new endpoint pairs/singles added to the existing
`core-api/core_api/routers/account.py` (`/users/me` prefix), backed by one new DB column
(`User.pending_totp_secret`) and one new table (`EmailChangeToken`, same shape as the
existing `PasswordResetToken`). The frontend adds three new sections to the existing
`AccountPage` plus one new unauthenticated page (`ConfirmEmailChangePage`) for the emailed
confirmation link, following the exact component/hook/test shapes already established by
`AmberApiKeySection`, `TotpSetupPage`, and `ResetPasswordPage`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, `pyjwt`, `bcrypt`, `pyotp`, pytest +
`httpx`'s `TestClient` (backend); React + Vite + TypeScript, TanStack Query, Vitest +
React Testing Library (frontend).

**Spec:** `docs/superpowers/specs/2026-10-08-account-settings-design.md`

## Global Constraints

- Every mutation requires BOTH `current_password` and a live `totp_code`, even though
  the caller already holds a valid `access_token` — a bare access token must never be
  sufficient on its own to change a credential.
- TOTP can never be disabled through this feature — "manage TOTP" means re-enrollment
  only (replacing the secret and recovery codes), matching the system's existing
  mandatory-2FA design.
- `pending_totp_secret` is a staging column: `totp/reenroll/start` writes only to it,
  never to the live `totp_secret` — an abandoned re-enroll must never break the user's
  existing authenticator.
- Random tokens (`EmailChangeToken.token_hash`) are SHA-256 via the existing
  `security.hash_token`, matching `PasswordResetToken`/`RecoveryCode` — not bcrypt.
- All errors are structured (`{code, message, detail}`) via the existing `AppError`.
- No session/access-token revocation on any of these changes (no refresh-token
  mechanism exists anywhere in this system yet — already an accepted limitation).

## Review Focus

- A `totp/reenroll/confirm` call with no prior `start` (`pending_totp_secret is None`)
  must be rejected with `400 invalid_state`, not crash calling `verify_totp_code(None, ...)`.
- Calling `totp/reenroll/start` must not touch the live `totp_secret` — logging in with
  the OLD TOTP code must still work after `start` but before `confirm`.
- A correct `current_password` with a wrong/stale `totp_code` (or vice versa) must be
  rejected on `password`, `email/change/request`, and `totp/reenroll/start` alike — both
  checks are required, neither alone is sufficient.
- Two different accounts both requesting a change to the same still-available
  `new_email` must not both succeed — the second `/email/change/confirm` must fail with
  `409 email_taken` via commit-time `IntegrityError` handling (matching `register`'s
  pattern), not a precheck-then-insert race.
- An `email/change/confirm` token must be rejected a second time (used) and after its
  1-hour expiry, and `totp/reenroll/confirm` must invalidate the user's previously-unused
  recovery codes when it issues new ones (a lost-phone scenario must not leave the old
  10 codes live alongside the new 10).

---

## Task 1: Data model — `pending_totp_secret` + `EmailChangeToken`

**Files:**
- Modify: `core-api/core_api/models/identity.py`
- Modify: `core-api/core_api/models/__init__.py`
- Create: `core-api/alembic/versions/<new>_add_pending_totp_secret_and_email_change_tokens.py`
- Test: `core-api/tests/test_schema_constraints.py`

**Interfaces:**
- Produces: `User.pending_totp_secret: str | None`, `EmailChangeToken` model (`id`,
  `user_id`, `new_email`, `token_hash`, `expires_at`, `used_at`), both importable from
  `core_api.models.identity` and re-exported from `core_api.models`.

- [ ] **Step 1: Write the failing tests**

Append to `core-api/tests/test_schema_constraints.py` (the file already has `import
datetime` at the top — no new import needed):

```python
def test_user_pending_totp_secret_defaults_to_none(db):
    from core_api.models.identity import User

    u = User(email="pending-totp@example.com", password_hash="hash")
    db.add(u)
    db.commit()
    db.refresh(u)
    assert u.pending_totp_secret is None


def test_email_change_token_round_trip(db, user):
    from core_api.models.identity import EmailChangeToken

    token = EmailChangeToken(
        user_id=user.id,
        new_email="new@example.com",
        token_hash="c" * 64,
        expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    assert token.used_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_schema_constraints.py -k "pending_totp_secret or email_change_token" -v`
Expected: FAIL with `AttributeError`/`ImportError` (neither exists yet).

- [ ] **Step 3: Add the column and model**

In `core-api/core_api/models/identity.py`, add `pending_totp_secret` to `User` right
after `totp_secret`:

```python
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pending_totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

Add `EmailChangeToken` after `PasswordResetToken`:

```python
class EmailChangeToken(Base):
    """Single-use, expiring token emailed to the NEW address when a user
    requests an email change. token_hash is SHA-256, same reasoning as
    PasswordResetToken.token_hash. new_email is staged here, not written to
    User.email, until the link is actually clicked."""

    __tablename__ = "email_change_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    new_email: Mapped[str] = mapped_column(String(320), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

In `core-api/core_api/models/__init__.py`, update the identity import and `__all__`:

```python
from core_api.models.identity import EmailChangeToken, PasswordResetToken, RecoveryCode, Site, SiteAccess, SiteAccessRole, User
```

Add `"EmailChangeToken"` to `__all__` (alongside `"PasswordResetToken"`).

- [ ] **Step 4: Generate and hand-check the migration**

Run: `cd core-api && uv run alembic revision --autogenerate -m "add pending totp secret and email change tokens"`

Open the generated file under `alembic/versions/` and confirm it contains (adjust column
order to match if autogenerate orders differently — the content, not the order, matters):

```python
def upgrade() -> None:
    op.add_column('users', sa.Column('pending_totp_secret', sa.String(length=64), nullable=True))
    op.create_table('email_change_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('new_email', sa.String(length=320), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_email_change_tokens_user_id'), 'email_change_tokens', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_email_change_tokens_user_id'), table_name='email_change_tokens')
    op.drop_table('email_change_tokens')
    op.drop_column('users', 'pending_totp_secret')
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_schema_constraints.py -v`
Expected: PASS, including the two new tests.

- [ ] **Step 6: Commit**

```bash
cd core-api && git add core_api/models/identity.py core_api/models/__init__.py alembic/versions/*add_pending_totp_secret_and_email_change_tokens.py tests/test_schema_constraints.py
git commit -m "Add pending_totp_secret column and email_change_tokens table"
```

---

## Task 2: `POST /users/me/password`

**Files:**
- Modify: `core-api/core_api/schemas.py`
- Modify: `core-api/core_api/routers/account.py`
- Test: `core-api/tests/test_account.py`

**Interfaces:**
- Consumes: `security.verify_password`, `security.hash_password`, `security.verify_totp_code`
  (all existing), `deps.get_current_user` (existing).
- Produces: schema `ChangePasswordIn(current_password, new_password, totp_code)`;
  function `account._verify_step_up(user, current_password, totp_code) -> None` (raises
  `AppError` — reused by Tasks 3 and 4, so its name and signature must not change);
  endpoint `POST /users/me/password`.

- [ ] **Step 1: Write the failing tests**

`core-api/tests/test_account.py` currently starts with:

```python
from __future__ import annotations

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import User
from core_api.security import create_access_token
```

Add to that top import block (do not append these as a second import block at the
bottom of the file):

```python
import pyotp

from core_api.security import generate_totp_secret, hash_password, verify_totp_code
```

Then append the following to the bottom of the file — a TOTP-enrollment helper (every
new endpoint in this plan requires an enrolled user with a known secret) plus the tests:

```python
def make_enrolled_user(db_url: str, *, password: str = "correct-password") -> tuple[int, str]:
    """Returns (user_id, totp_secret) for a user who is already fully TOTP-enrolled,
    matching the state every endpoint in this plan expects its caller to be in."""
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    secret = generate_totp_secret()
    u = User(email="enrolled@example.com", password_hash=hash_password(password), totp_secret=secret, totp_enabled=True)
    session.add(u)
    session.commit()
    user_id = u.id
    session.close()
    return user_id, secret


def test_change_password_with_correct_current_password_and_totp_succeeds(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/password",
        json={
            "current_password": "correct-password",
            "new_password": "new-password-123",
            "totp_code": pyotp.TOTP(secret).now(),
        },
        headers=auth(user_id),
    )
    assert response.status_code == 204

    login = client.post(
        "/api/v1/auth/login", json={"email": "enrolled@example.com", "password": "new-password-123"}
    )
    assert login.status_code == 200
    assert login.json()["status"] == "totp_required"


def test_change_password_rejects_wrong_current_password(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/password",
        json={"current_password": "wrong-password", "new_password": "new-password-123", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_change_password_rejects_wrong_totp_code_even_with_correct_password(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, _secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/password",
        json={"current_password": "correct-password", "new_password": "new-password-123", "totp_code": "000000"},
        headers=auth(user_id),
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_code"


def test_change_password_requires_auth(client, app_and_db_url):
    response = client.post(
        "/api/v1/users/me/password",
        json={"current_password": "x", "new_password": "new-password-123", "totp_code": "000000"},
    )
    assert response.status_code in (401, 422)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_account.py -k change_password -v`
Expected: FAIL with 404 (endpoint doesn't exist yet).

- [ ] **Step 3: Add the schema**

In `core-api/core_api/schemas.py`, add after `UpdateAmberApiKeyIn`:

```python
class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: Password
    totp_code: str
```

- [ ] **Step 4: Implement the endpoint**

Replace the full contents of `core-api/core_api/routers/account.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from core_api.deps import get_current_user, get_db_session
from core_api.errors import AppError
from core_api.models.identity import User
from core_api.schemas import ChangePasswordIn, UpdateAmberApiKeyIn, UserMeOut
from core_api.security import hash_password, verify_password, verify_totp_code

router = APIRouter(prefix="/users/me", tags=["account"])


def _to_user_me_out(user: User) -> UserMeOut:
    return UserMeOut(id=user.id, email=user.email, has_amber_api_key=user.amber_api_key is not None)


def _verify_step_up(user: User, current_password: str, totp_code: str) -> None:
    """Re-auth gate shared by every security-sensitive account mutation: proves
    the caller still holds BOTH the password and the TOTP device, even though
    they already carry a valid access_token. A bare access_token must never be
    sufficient on its own to change a credential (see
    docs/superpowers/specs/2026-10-08-account-settings-design.md)."""
    if not verify_password(current_password, user.password_hash):
        raise AppError(401, "invalid_credentials", "Current password is incorrect")
    if not user.totp_secret or not verify_totp_code(user.totp_secret, totp_code):
        raise AppError(401, "invalid_code", "Incorrect verification code")


@router.get("", response_model=UserMeOut)
def get_me(current_user: User = Depends(get_current_user)) -> UserMeOut:
    return _to_user_me_out(current_user)


@router.patch("", response_model=UserMeOut)
def update_amber_api_key(
    payload: UpdateAmberApiKeyIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> UserMeOut:
    current_user.amber_api_key = payload.amber_api_key
    db.commit()
    db.refresh(current_user)
    return _to_user_me_out(current_user)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> None:
    _verify_step_up(current_user, payload.current_password, payload.totp_code)
    current_user.password_hash = hash_password(payload.new_password)
    db.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/ -v`
Expected: PASS, full suite.

- [ ] **Step 6: Commit**

```bash
cd core-api && git add core_api/schemas.py core_api/routers/account.py tests/test_account.py
git commit -m "Add POST /users/me/password with password+TOTP step-up auth"
```

---

## Task 3: Email change (`/users/me/email/change/request`, `/confirm`)

**Files:**
- Modify: `core-api/core_api/schemas.py`
- Modify: `core-api/core_api/routers/account.py`
- Test: `core-api/tests/test_account.py`

**Interfaces:**
- Consumes: `account._verify_step_up` (Task 2), `deps.get_email_sender`,
  `security.hash_token`, `EmailChangeToken` (Task 1).
- Produces: schemas `RequestEmailChangeIn(new_email, current_password, totp_code)`,
  `RequestEmailChangeOut(message)`, `ConfirmEmailChangeIn(token)`; endpoints
  `POST /users/me/email/change/request`, `POST /users/me/email/change/confirm`.

- [ ] **Step 1: Write the failing tests**

Add to `core-api/tests/test_account.py`'s top import block (alongside the imports Task 2
added there):

```python
import datetime
import re

from sqlalchemy import select

from core_api.security import hash_token
```

Also change the existing `from core_api.models.identity import User` line in that same
block to:

```python
from core_api.models.identity import EmailChangeToken, User
```

Then append to the bottom of the file:

```python
def _extract_confirm_token(body: str) -> str:
    match = re.search(r"token=([^\s]+)", body)
    assert match is not None
    return match.group(1)


def test_request_email_change_sends_link_to_the_new_address(client, app_and_db_url, fake_email_sender):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "new@example.com", "current_password": "correct-password", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    assert response.status_code == 200, response.text
    assert len(fake_email_sender.sent) == 1
    to, _subject, body = fake_email_sender.sent[0]
    assert to == "new@example.com"
    assert "confirm-email-change?token=" in body


def test_request_email_change_rejects_wrong_current_password(client, app_and_db_url, fake_email_sender):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "new@example.com", "current_password": "wrong", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    assert response.status_code == 401
    assert fake_email_sender.sent == []


def test_request_email_change_rejects_an_email_already_in_use(client, app_and_db_url, fake_email_sender):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)
    client.post("/api/v1/auth/register", json={"email": "taken@example.com", "password": "another-password"})

    response = client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "taken@example.com", "current_password": "correct-password", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "email_taken"


def test_confirm_email_change_updates_email_and_allows_login_with_it(client, app_and_db_url, fake_email_sender):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)
    client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "new@example.com", "current_password": "correct-password", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    token = _extract_confirm_token(fake_email_sender.sent[0][2])

    confirm = client.post("/api/v1/users/me/email/change/confirm", json={"token": token})
    assert confirm.status_code == 204

    login = client.post("/api/v1/auth/login", json={"email": "new@example.com", "password": "correct-password"})
    assert login.status_code == 200


def test_confirm_email_change_rejects_reused_token(client, app_and_db_url, fake_email_sender):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)
    client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "new@example.com", "current_password": "correct-password", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    token = _extract_confirm_token(fake_email_sender.sent[0][2])
    client.post("/api/v1/users/me/email/change/confirm", json={"token": token})

    reused = client.post("/api/v1/users/me/email/change/confirm", json={"token": token})
    assert reused.status_code == 400
    assert reused.json()["code"] == "invalid_token"


def test_confirm_email_change_rejects_unknown_token(client):
    response = client.post("/api/v1/users/me/email/change/confirm", json={"token": "never-issued"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"


def test_confirm_email_change_rejects_expired_token(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, _secret = make_enrolled_user(db_url)

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    session.add(
        EmailChangeToken(
            user_id=user_id,
            new_email="new@example.com",
            token_hash=hash_token("expired-token"),
            expires_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1),
        )
    )
    session.commit()
    session.close()

    response = client.post("/api/v1/users/me/email/change/confirm", json={"token": "expired-token"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"


def test_two_concurrent_requests_for_the_same_new_email_only_one_confirm_succeeds(client, app_and_db_url, fake_email_sender):
    """The Review Focus race: two different accounts both request a change to
    the same still-available address. Both /request calls succeed (the
    address isn't taken yet), but only the first /confirm may actually claim
    it -- the second must fail at commit time, not silently overwrite."""
    _, db_url = app_and_db_url
    user_a_id, secret_a = make_enrolled_user(db_url)
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user_b = User(email="b@example.com", password_hash=hash_password("correct-password"), totp_secret=generate_totp_secret(), totp_enabled=True)
    session.add(user_b)
    session.commit()
    user_b_id, secret_b = user_b.id, user_b.totp_secret
    session.close()

    client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "shared@example.com", "current_password": "correct-password", "totp_code": pyotp.TOTP(secret_a).now()},
        headers=auth(user_a_id),
    )
    token_a = _extract_confirm_token(fake_email_sender.sent[0][2])
    client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "shared@example.com", "current_password": "correct-password", "totp_code": pyotp.TOTP(secret_b).now()},
        headers=auth(user_b_id),
    )
    token_b = _extract_confirm_token(fake_email_sender.sent[1][2])

    first = client.post("/api/v1/users/me/email/change/confirm", json={"token": token_a})
    assert first.status_code == 204

    second = client.post("/api/v1/users/me/email/change/confirm", json={"token": token_b})
    assert second.status_code == 409
    assert second.json()["code"] == "email_taken"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_account.py -k email_change -v`
Expected: FAIL with 404s.

- [ ] **Step 3: Add the schemas**

In `core-api/core_api/schemas.py`, add after `ChangePasswordIn`:

```python
class RequestEmailChangeIn(BaseModel):
    new_email: EmailStr
    current_password: str
    totp_code: str


class RequestEmailChangeOut(BaseModel):
    message: str


class ConfirmEmailChangeIn(BaseModel):
    token: str
```

- [ ] **Step 4: Implement the endpoints**

In `core-api/core_api/routers/account.py`, update the imports and add the two endpoints
plus their helpers:

```python
from __future__ import annotations

import datetime
import logging
import secrets

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_api.config import get_settings
from core_api.deps import get_current_user, get_db_session, get_email_sender
from core_api.email_sender import EmailSender
from core_api.errors import AppError
from core_api.models.identity import EmailChangeToken, User
from core_api.schemas import (
    ChangePasswordIn,
    ConfirmEmailChangeIn,
    RequestEmailChangeIn,
    RequestEmailChangeOut,
    UpdateAmberApiKeyIn,
    UserMeOut,
)
from core_api.security import hash_password, hash_token, verify_password, verify_totp_code

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users/me", tags=["account"])

_EMAIL_CHANGE_TOKEN_EXPIRY = datetime.timedelta(hours=1)
```

(`_to_user_me_out`, `_verify_step_up`, `get_me`, `update_amber_api_key`, and
`change_password` from Task 2 stay unchanged below this point.)

Add after `change_password`:

```python
def _invalidate_outstanding_email_change_tokens(db: Session, user_id: int, now: datetime.datetime) -> None:
    """Burn every unused email-change token for this user, not just a newly
    issued one -- same reasoning as password_reset._invalidate_outstanding_tokens:
    an older unconfirmed link must not keep working after a newer request."""
    outstanding = (
        db.execute(
            select(EmailChangeToken).where(
                EmailChangeToken.user_id == user_id,
                EmailChangeToken.used_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for token in outstanding:
        token.used_at = now


def _try_send_email_change_email(email_sender: EmailSender, to: str, raw_token: str) -> None:
    """Send the confirmation link, swallowing any delivery failure -- an
    operational relay problem must surface in the log, not as a 500 to the
    caller, who is already an authenticated account owner, not an anonymous
    caller being protected from an enumeration oracle (that concern is what
    password_reset._try_send_reset_email guards; it doesn't apply here)."""
    confirm_url = f"{get_settings().frontend_base_url}/confirm-email-change?token={raw_token}"
    try:
        email_sender.send(
            to=to,
            subject="Confirm your new email address",
            body=f"Click the link to confirm this email change: {confirm_url}\nThis link expires in 1 hour.",
        )
    except Exception:
        logger.exception("Failed to send email-change confirmation email")


@router.post("/email/change/request", response_model=RequestEmailChangeOut)
def request_email_change(
    payload: RequestEmailChangeIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
    email_sender: EmailSender = Depends(get_email_sender),
) -> RequestEmailChangeOut:
    _verify_step_up(current_user, payload.current_password, payload.totp_code)

    # Best-effort early feedback only -- NOT the race-safe guard. Two accounts
    # can both pass this check for the same new_email before either confirms;
    # the authoritative guard is the IntegrityError catch in confirm_email_change.
    existing = db.execute(select(User).where(User.email == payload.new_email)).scalar_one_or_none()
    if existing is not None:
        raise AppError(409, "email_taken", "An account with this email already exists")

    now = datetime.datetime.now(datetime.timezone.utc)
    _invalidate_outstanding_email_change_tokens(db, current_user.id, now)
    raw_token = secrets.token_urlsafe(32)
    db.add(
        EmailChangeToken(
            user_id=current_user.id,
            new_email=payload.new_email,
            token_hash=hash_token(raw_token),
            expires_at=now + _EMAIL_CHANGE_TOKEN_EXPIRY,
        )
    )
    db.commit()
    _try_send_email_change_email(email_sender, payload.new_email, raw_token)
    return RequestEmailChangeOut(message="Check your new email address for a confirmation link.")


def _load_valid_email_change_token(db: Session, raw_token: str, now: datetime.datetime) -> EmailChangeToken:
    """The presented token, or a uniform 400 if it is unknown, already used, or
    expired -- matching password_reset._load_valid_token's reasoning: distinguishing
    the three failures would tell a caller which tokens have ever existed."""
    token = db.execute(
        select(EmailChangeToken).where(EmailChangeToken.token_hash == hash_token(raw_token))
    ).scalar_one_or_none()
    if token is not None:
        expires_at = token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
        if token.used_at is None and expires_at > now:
            return token
    raise AppError(400, "invalid_token", "This confirmation link is invalid or has expired")


@router.post("/email/change/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_email_change(payload: ConfirmEmailChangeIn, db: Session = Depends(get_db_session)) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    token = _load_valid_email_change_token(db, payload.token, now)
    target_user = db.get(User, token.user_id)
    assert target_user is not None  # FK guarantees the referenced user still exists

    target_user.email = token.new_email
    token.used_at = now
    _invalidate_outstanding_email_change_tokens(db, token.user_id, now)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "email_taken", "An account with this email already exists") from exc
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/ -v`
Expected: PASS, full suite.

- [ ] **Step 6: Commit**

```bash
cd core-api && git add core_api/schemas.py core_api/routers/account.py tests/test_account.py
git commit -m "Add email-change request/confirm endpoints"
```

---

## Task 4: TOTP re-enrollment (`/users/me/totp/reenroll/start`, `/confirm`)

**Files:**
- Modify: `core-api/core_api/schemas.py`
- Modify: `core-api/core_api/routers/account.py`
- Test: `core-api/tests/test_account.py`

**Interfaces:**
- Consumes: `account._verify_step_up` (Task 2), `security.generate_totp_secret`,
  `security.totp_provisioning_uri`, `security.generate_recovery_codes` (all existing),
  `RecoveryCode` (existing model).
- Produces: schemas `StartTotpReenrollIn(current_password, totp_code)`,
  `TotpReenrollStartOut(secret, otpauth_uri)`, `ConfirmTotpReenrollIn(code)`,
  `TotpReenrollConfirmOut(recovery_codes)`; endpoints
  `POST /users/me/totp/reenroll/start`, `POST /users/me/totp/reenroll/confirm`.

- [ ] **Step 1: Write the failing tests**

Change the `from core_api.models.identity import EmailChangeToken, User` line (added in
Task 3) to:

```python
from core_api.models.identity import EmailChangeToken, RecoveryCode, User
```

Then append to the bottom of the file (reuses the `hash_token` already imported in Task 3):

```python
def test_reenroll_start_returns_a_new_secret_without_touching_the_live_one(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, old_secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "correct-password", "totp_code": pyotp.TOTP(old_secret).now()},
        headers=auth(user_id),
    )
    assert response.status_code == 200, response.text
    new_secret = response.json()["secret"]
    assert new_secret != old_secret

    # The OLD authenticator must still work for login -- the live totp_secret
    # must be untouched until confirm.
    login = client.post("/api/v1/auth/login", json={"email": "enrolled@example.com", "password": "correct-password"})
    mfa_token = login.json()["mfa_token"]
    verify = client.post(
        "/api/v1/auth/totp/verify", json={"code": pyotp.TOTP(old_secret).now()}, headers={"Authorization": f"Bearer {mfa_token}"}
    )
    assert verify.status_code == 200


def test_reenroll_start_rejects_wrong_current_password(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "wrong", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_reenroll_confirm_without_a_prior_start_is_rejected(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, _secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/totp/reenroll/confirm", json={"code": "000000"}, headers=auth(user_id)
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_state"


def test_reenroll_confirm_with_correct_code_promotes_the_new_secret_and_returns_recovery_codes(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, old_secret = make_enrolled_user(db_url)

    start = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "correct-password", "totp_code": pyotp.TOTP(old_secret).now()},
        headers=auth(user_id),
    )
    new_secret = start.json()["secret"]

    confirm = client.post(
        "/api/v1/users/me/totp/reenroll/confirm",
        json={"code": pyotp.TOTP(new_secret).now()},
        headers=auth(user_id),
    )
    assert confirm.status_code == 200
    recovery_codes = confirm.json()["recovery_codes"]
    assert len(recovery_codes) == 10
    assert len(set(recovery_codes)) == 10

    # The OLD authenticator no longer works -- the new secret is now live.
    login = client.post("/api/v1/auth/login", json={"email": "enrolled@example.com", "password": "correct-password"})
    mfa_token = login.json()["mfa_token"]
    old_code_attempt = client.post(
        "/api/v1/auth/totp/verify", json={"code": pyotp.TOTP(old_secret).now()}, headers={"Authorization": f"Bearer {mfa_token}"}
    )
    assert old_code_attempt.status_code == 401


def test_reenroll_confirm_invalidates_previous_unused_recovery_codes(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, old_secret = make_enrolled_user(db_url)
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    session.add(RecoveryCode(user_id=user_id, code_hash=hash_token("old-recovery-code")))
    session.commit()
    session.close()

    start = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "correct-password", "totp_code": pyotp.TOTP(old_secret).now()},
        headers=auth(user_id),
    )
    new_secret = start.json()["secret"]
    client.post(
        "/api/v1/users/me/totp/reenroll/confirm",
        json={"code": pyotp.TOTP(new_secret).now()},
        headers=auth(user_id),
    )

    login = client.post("/api/v1/auth/login", json={"email": "enrolled@example.com", "password": "correct-password"})
    mfa_token = login.json()["mfa_token"]
    old_recovery_attempt = client.post(
        "/api/v1/auth/totp/verify", json={"code": "old-recovery-code"}, headers={"Authorization": f"Bearer {mfa_token}"}
    )
    assert old_recovery_attempt.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_account.py -k reenroll -v`
Expected: FAIL with 404s.

- [ ] **Step 3: Add the schemas**

In `core-api/core_api/schemas.py`, add after `ConfirmEmailChangeIn`:

```python
class StartTotpReenrollIn(BaseModel):
    current_password: str
    totp_code: str


class TotpReenrollStartOut(BaseModel):
    secret: str
    otpauth_uri: str


class ConfirmTotpReenrollIn(BaseModel):
    code: str


class TotpReenrollConfirmOut(BaseModel):
    recovery_codes: list[str]
```

- [ ] **Step 4: Implement the endpoints**

In `core-api/core_api/routers/account.py`, update the imports:

```python
from core_api.models.identity import EmailChangeToken, RecoveryCode, User
from core_api.schemas import (
    ChangePasswordIn,
    ConfirmEmailChangeIn,
    ConfirmTotpReenrollIn,
    RequestEmailChangeIn,
    RequestEmailChangeOut,
    StartTotpReenrollIn,
    TotpReenrollConfirmOut,
    TotpReenrollStartOut,
    UpdateAmberApiKeyIn,
    UserMeOut,
)
from core_api.security import (
    generate_recovery_codes,
    generate_totp_secret,
    hash_password,
    hash_token,
    totp_provisioning_uri,
    verify_password,
    verify_totp_code,
)
```

Add at the end of the file:

```python
@router.post("/totp/reenroll/start", response_model=TotpReenrollStartOut)
def start_totp_reenroll(
    payload: StartTotpReenrollIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> TotpReenrollStartOut:
    _verify_step_up(current_user, payload.current_password, payload.totp_code)
    secret = generate_totp_secret()
    current_user.pending_totp_secret = secret
    db.commit()
    return TotpReenrollStartOut(secret=secret, otpauth_uri=totp_provisioning_uri(secret, current_user.email))


@router.post("/totp/reenroll/confirm", response_model=TotpReenrollConfirmOut)
def confirm_totp_reenroll(
    payload: ConfirmTotpReenrollIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> TotpReenrollConfirmOut:
    if current_user.pending_totp_secret is None:
        raise AppError(400, "invalid_state", "No authenticator replacement is in progress")
    if not verify_totp_code(current_user.pending_totp_secret, payload.code):
        raise AppError(401, "invalid_code", "Incorrect verification code")

    current_user.totp_secret = current_user.pending_totp_secret
    current_user.pending_totp_secret = None

    # Burn every previously-unused recovery code: a lost-phone re-enrollment
    # must not leave the OLD 10 codes live alongside the new 10 (same
    # used_at-as-invalidation convention as password_reset's outstanding-token
    # burn, reused here for a different table).
    now = datetime.datetime.now(datetime.timezone.utc)
    stale_codes = (
        db.execute(
            select(RecoveryCode).where(
                RecoveryCode.user_id == current_user.id,
                RecoveryCode.used_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for stale_code in stale_codes:
        stale_code.used_at = now

    raw_codes = generate_recovery_codes()
    for raw_code in raw_codes:
        db.add(RecoveryCode(user_id=current_user.id, code_hash=hash_token(raw_code)))
    db.commit()
    return TotpReenrollConfirmOut(recovery_codes=raw_codes)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/ -v`
Expected: PASS, full suite.

- [ ] **Step 6: Commit**

```bash
cd core-api && git add core_api/schemas.py core_api/routers/account.py tests/test_account.py
git commit -m "Add TOTP re-enrollment endpoints (reenroll/start, reenroll/confirm)"
```

---

## Task 5: Frontend — Change Password section

**Files:**
- Modify: `webapp/src/account/useAccount.ts`
- Modify: `webapp/src/pages/AccountPage.tsx`
- Modify: `webapp/src/pages/AccountPage.test.tsx`

**Interfaces:**
- Produces: `useChangePassword()` (TanStack mutation, payload
  `{current_password, new_password, totp_code}`, resolves `void`);
  component `ChangePasswordSection`.

- [ ] **Step 1: Write the failing tests**

Append to `webapp/src/pages/AccountPage.test.tsx` (inside the existing `describe` block;
the existing `beforeEach` already resets the `apiFetch` mock):

```typescript
  it("changes the password when current password and TOTP code are correct", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockResolvedValueOnce(undefined);
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("Current password"), "old-password");
    await userEvent.type(screen.getByLabelText("New password"), "new-password");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "new-password");
    await userEvent.type(screen.getByLabelText("Authenticator code"), "123456");
    await userEvent.click(screen.getByText("Change password"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/password",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ current_password: "old-password", new_password: "new-password", totp_code: "123456" }),
      }),
    );
    expect(await screen.findByText("Password changed.")).toBeInTheDocument();
  });

  it("rejects a mismatched password confirmation without calling the API", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false });
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("Current password"), "old-password");
    await userEvent.type(screen.getByLabelText("New password"), "new-password");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "something-else");
    await userEvent.type(screen.getByLabelText("Authenticator code"), "123456");
    await userEvent.click(screen.getByText("Change password"));

    expect(await screen.findByRole("alert")).toHaveTextContent(/do not match/i);
    expect(apiFetch).toHaveBeenCalledTimes(1); // only the initial useMe call
  });

  it("shows the API error when the password change is rejected", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(401, "invalid_credentials", "Current password is incorrect", null));
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("Current password"), "wrong-password");
    await userEvent.type(screen.getByLabelText("New password"), "new-password");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "new-password");
    await userEvent.type(screen.getByLabelText("Authenticator code"), "123456");
    await userEvent.click(screen.getByText("Change password"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Current password is incorrect");
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webapp && npx vitest run src/pages/AccountPage.test.tsx -t "password"`
Expected: FAIL — `getByLabelText("Current password")` finds nothing yet.

- [ ] **Step 3: Add the hook**

Append to `webapp/src/account/useAccount.ts`:

```typescript
interface ChangePasswordPayload {
  current_password: string;
  new_password: string;
  totp_code: string;
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (payload: ChangePasswordPayload) =>
      apiFetch<void>("/api/v1/users/me/password", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  });
}
```

- [ ] **Step 4: Add the component**

In `webapp/src/pages/AccountPage.tsx`, add the import and the new section, and wire it
into `AccountPage`:

```typescript
import { useChangePassword, useMe, useUpdateAmberApiKey } from "../account/useAccount";
```

```tsx
function ChangePasswordSection() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [mismatchError, setMismatchError] = useState<string | null>(null);
  const changePassword = useChangePassword();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setMismatchError(null);
    if (newPassword !== confirmPassword) {
      setMismatchError("New password and confirmation do not match.");
      return;
    }
    changePassword.mutate(
      { current_password: currentPassword, new_password: newPassword, totp_code: totpCode },
      {
        onSuccess: () => {
          setCurrentPassword("");
          setNewPassword("");
          setConfirmPassword("");
          setTotpCode("");
        },
      },
    );
  }

  const apiError =
    changePassword.isError && changePassword.error instanceof Error
      ? changePassword.error.message
      : changePassword.isError
        ? "Failed to change password. Please try again."
        : null;
  const error = mismatchError ?? apiError;

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold">Change password</h2>
      {error && (
        <p role="alert" className="text-red-600">
          {error}
        </p>
      )}
      {changePassword.isSuccess && <p className="text-green-700">Password changed.</p>}
      <form onSubmit={handleSubmit} className="space-y-2">
        <label className="flex flex-col text-sm">
          Current password
          <input
            aria-label="Current password"
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            className="border p-2"
            required
          />
        </label>
        <label className="flex flex-col text-sm">
          New password
          <input
            aria-label="New password"
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className="border p-2"
            required
          />
        </label>
        <label className="flex flex-col text-sm">
          Confirm new password
          <input
            aria-label="Confirm new password"
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className="border p-2"
            required
          />
        </label>
        <label className="flex flex-col text-sm">
          Authenticator code
          <input
            aria-label="Authenticator code"
            value={totpCode}
            onChange={(e) => setTotpCode(e.target.value)}
            placeholder="6-digit code"
            className="border p-2"
            required
          />
        </label>
        <button
          type="submit"
          disabled={changePassword.isPending}
          className="bg-blue-600 p-2 text-white disabled:opacity-50"
        >
          Change password
        </button>
      </form>
    </section>
  );
}
```

In `AccountPage`, add the section above `AmberApiKeySection`:

```tsx
export function AccountPage() {
  return (
    <div className="mx-auto mt-8 max-w-2xl space-y-8 px-4">
      <h1 className="text-xl font-semibold">Account</h1>
      <ChangePasswordSection />
      <AmberApiKeySection />
    </div>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd webapp && npx vitest run src/pages/AccountPage.test.tsx`
Expected: PASS, full file.

- [ ] **Step 6: Commit**

```bash
cd webapp && git add src/account/useAccount.ts src/pages/AccountPage.tsx src/pages/AccountPage.test.tsx
git commit -m "Add change-password section to AccountPage"
```

---

## Task 6: Frontend — Change Email section + confirmation page

**Files:**
- Modify: `webapp/src/account/useAccount.ts`
- Modify: `webapp/src/pages/AccountPage.tsx`
- Modify: `webapp/src/pages/AccountPage.test.tsx`
- Create: `webapp/src/pages/ConfirmEmailChangePage.tsx`
- Create: `webapp/src/pages/ConfirmEmailChangePage.test.tsx`
- Modify: `webapp/src/App.tsx`

**Interfaces:**
- Produces: `useRequestEmailChange()` (payload `{new_email, current_password,
  totp_code}`, resolves `{message: string}`), `useConfirmEmailChange()` (payload
  `token: string`, resolves `void`); components `ChangeEmailSection`,
  `ConfirmEmailChangePage`; route `/confirm-email-change`.

- [ ] **Step 1: Write the failing tests**

Append to `webapp/src/pages/AccountPage.test.tsx`:

```typescript
  it("requests an email change when current password and TOTP code are correct", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockResolvedValueOnce({ message: "Check your new email address for a confirmation link." });
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("New email"), "new@example.com");
    await userEvent.type(screen.getByLabelText("Current password for email change"), "old-password");
    await userEvent.type(screen.getByLabelText("Authenticator code for email change"), "123456");
    await userEvent.click(screen.getByText("Send confirmation email"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/email/change/request",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ new_email: "new@example.com", current_password: "old-password", totp_code: "123456" }),
      }),
    );
    expect(await screen.findByText("Check your new email address for a confirmation link.")).toBeInTheDocument();
  });

  it("shows the API error when the email change request is rejected", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(409, "email_taken", "An account with this email already exists", null));
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("New email"), "taken@example.com");
    await userEvent.type(screen.getByLabelText("Current password for email change"), "old-password");
    await userEvent.type(screen.getByLabelText("Authenticator code for email change"), "123456");
    await userEvent.click(screen.getByText("Send confirmation email"));

    expect(await screen.findByRole("alert")).toHaveTextContent("An account with this email already exists");
  });
```

Create `webapp/src/pages/ConfirmEmailChangePage.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { ConfirmEmailChangePage } from "./ConfirmEmailChangePage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage(token = "a-valid-token") {
  return render(
    <MemoryRouter initialEntries={[`/confirm-email-change?token=${token}`]}>
      <ConfirmEmailChangePage />
    </MemoryRouter>,
  );
}

describe("ConfirmEmailChangePage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("confirms the email change and shows a success message", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(undefined);
    renderPage("a-valid-token");

    await userEvent.click(screen.getByText("Confirm email change"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/email/change/confirm",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ token: "a-valid-token" }) }),
    );
    expect(await screen.findByText(/email address has been updated/i)).toBeInTheDocument();
  });

  it("shows an error when the token is invalid or expired", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockRejectedValueOnce(
      new ApiError(400, "invalid_token", "This confirmation link is invalid or has expired", null),
    );
    renderPage("a-bad-token");

    await userEvent.click(screen.getByText("Confirm email change"));

    expect(await screen.findByRole("alert")).toHaveTextContent("This confirmation link is invalid or has expired");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webapp && npx vitest run src/pages/AccountPage.test.tsx src/pages/ConfirmEmailChangePage.test.tsx`
Expected: FAIL — new labels/module don't exist yet.

- [ ] **Step 3: Add the hooks**

Append to `webapp/src/account/useAccount.ts`:

```typescript
interface RequestEmailChangePayload {
  new_email: string;
  current_password: string;
  totp_code: string;
}

interface RequestEmailChangeResponse {
  message: string;
}

export function useRequestEmailChange() {
  return useMutation({
    mutationFn: (payload: RequestEmailChangePayload) =>
      apiFetch<RequestEmailChangeResponse>("/api/v1/users/me/email/change/request", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  });
}

export function useConfirmEmailChange() {
  return useMutation({
    mutationFn: (token: string) =>
      apiFetch<void>("/api/v1/users/me/email/change/confirm", {
        method: "POST",
        body: JSON.stringify({ token }),
      }),
  });
}
```

- [ ] **Step 4: Add `ChangeEmailSection` to `AccountPage.tsx`**

Add the import and component, and wire it into `AccountPage` below
`ChangePasswordSection`:

```typescript
import { useChangePassword, useMe, useRequestEmailChange, useUpdateAmberApiKey } from "../account/useAccount";
```

```tsx
function ChangeEmailSection() {
  const { data: me } = useMe();
  const [newEmail, setNewEmail] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const requestEmailChange = useRequestEmailChange();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    requestEmailChange.mutate(
      { new_email: newEmail, current_password: currentPassword, totp_code: totpCode },
      {
        onSuccess: () => {
          setNewEmail("");
          setCurrentPassword("");
          setTotpCode("");
        },
      },
    );
  }

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold">Change email</h2>
      {me && <p className="text-gray-600">Current email: {me.email}</p>}
      {requestEmailChange.isError && (
        <p role="alert" className="text-red-600">
          {requestEmailChange.error instanceof Error
            ? requestEmailChange.error.message
            : "Failed to request an email change. Please try again."}
        </p>
      )}
      {requestEmailChange.isSuccess && <p className="text-green-700">{requestEmailChange.data.message}</p>}
      <form onSubmit={handleSubmit} className="space-y-2">
        <label className="flex flex-col text-sm">
          New email
          <input
            aria-label="New email"
            type="email"
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            className="border p-2"
            required
          />
        </label>
        <label className="flex flex-col text-sm">
          Current password
          <input
            aria-label="Current password for email change"
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            className="border p-2"
            required
          />
        </label>
        <label className="flex flex-col text-sm">
          Authenticator code
          <input
            aria-label="Authenticator code for email change"
            value={totpCode}
            onChange={(e) => setTotpCode(e.target.value)}
            placeholder="6-digit code"
            className="border p-2"
            required
          />
        </label>
        <button
          type="submit"
          disabled={requestEmailChange.isPending}
          className="bg-blue-600 p-2 text-white disabled:opacity-50"
        >
          Send confirmation email
        </button>
      </form>
    </section>
  );
}
```

```tsx
export function AccountPage() {
  return (
    <div className="mx-auto mt-8 max-w-2xl space-y-8 px-4">
      <h1 className="text-xl font-semibold">Account</h1>
      <ChangePasswordSection />
      <ChangeEmailSection />
      <AmberApiKeySection />
    </div>
  );
}
```

- [ ] **Step 5: Create `ConfirmEmailChangePage.tsx`**

```tsx
import { type FormEvent, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useConfirmEmailChange } from "../account/useAccount";

export function ConfirmEmailChangePage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const navigate = useNavigate();
  const confirmEmailChange = useConfirmEmailChange();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await confirmEmailChange.mutateAsync(token);
      setConfirmed(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "This confirmation link is invalid or has expired");
    }
  }

  if (confirmed) {
    return (
      <div className="mx-auto mt-24 max-w-sm space-y-4">
        <p>Your email address has been updated.</p>
        <button onClick={() => navigate("/login")} className="w-full bg-blue-600 p-2 text-white">
          Go to login
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="mx-auto mt-24 max-w-sm space-y-4">
      <h1 className="text-xl font-semibold">Confirm your new email address</h1>
      {error && (
        <p role="alert" className="text-red-600">
          {error}
        </p>
      )}
      <button type="submit" className="w-full bg-blue-600 p-2 text-white">
        Confirm email change
      </button>
    </form>
  );
}
```

- [ ] **Step 6: Wire the route**

In `webapp/src/App.tsx`, add the import and the unauthenticated route (alongside
`/reset-password`):

```typescript
import { ConfirmEmailChangePage } from "./pages/ConfirmEmailChangePage";
```

```tsx
      <Route path="/confirm-email-change" element={<ConfirmEmailChangePage />} />
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd webapp && npx vitest run`
Expected: PASS, full suite.

- [ ] **Step 8: Commit**

```bash
cd webapp && git add src/account/useAccount.ts src/pages/AccountPage.tsx src/pages/AccountPage.test.tsx src/pages/ConfirmEmailChangePage.tsx src/pages/ConfirmEmailChangePage.test.tsx src/App.tsx
git commit -m "Add change-email section, confirm-email-change page and route"
```

---

## Task 7: Frontend — Manage two-factor authentication section

**Files:**
- Modify: `webapp/src/account/useAccount.ts`
- Modify: `webapp/src/pages/AccountPage.tsx`
- Modify: `webapp/src/pages/AccountPage.test.tsx`

**Interfaces:**
- Produces: `useStartTotpReenroll()` (payload `{current_password, totp_code}`, resolves
  `{secret: string, otpauth_uri: string}`), `useConfirmTotpReenroll()` (payload
  `code: string`, resolves `{recovery_codes: string[]}`); component `ManageTotpSection`.

- [ ] **Step 1: Write the failing tests**

Append to `webapp/src/pages/AccountPage.test.tsx`:

```typescript
  it("walks through replacing the authenticator and shows new recovery codes", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockResolvedValueOnce({ secret: "NEWSECRET234567", otpauth_uri: "otpauth://totp/Enphase%20Data:me@example.com?secret=NEWSECRET234567&issuer=Enphase%20Data" })
      .mockResolvedValueOnce({ recovery_codes: Array.from({ length: 10 }, (_, i) => `code${i}`) });
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("Current password for two-factor reset"), "old-password");
    await userEvent.type(screen.getByLabelText("Current authenticator code"), "111111");
    await userEvent.click(screen.getByText("Replace authenticator"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/totp/reenroll/start",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ current_password: "old-password", totp_code: "111111" }),
      }),
    );
    await screen.findByText(/manual entry key: NEWSECRET234567/i);

    await userEvent.type(screen.getByLabelText("New authenticator code"), "222222");
    await userEvent.click(screen.getByText("Confirm"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/totp/reenroll/confirm",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ code: "222222" }) }),
    );
    expect(await screen.findByText("code0")).toBeInTheDocument();
    expect(screen.getByText("Done")).toBeDisabled();

    await userEvent.click(screen.getByLabelText(/i've saved these codes/i));
    expect(screen.getByText("Done")).toBeEnabled();
  });

  it("shows an error when the authenticator reset start is rejected", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(401, "invalid_code", "Incorrect verification code", null));
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("Current password for two-factor reset"), "old-password");
    await userEvent.type(screen.getByLabelText("Current authenticator code"), "000000");
    await userEvent.click(screen.getByText("Replace authenticator"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect verification code");
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && npx vitest run src/pages/AccountPage.test.tsx -t "authenticator"`
Expected: FAIL — new labels don't exist yet.

- [ ] **Step 3: Add the hooks**

Append to `webapp/src/account/useAccount.ts`:

```typescript
interface StartTotpReenrollPayload {
  current_password: string;
  totp_code: string;
}

interface StartTotpReenrollResponse {
  secret: string;
  otpauth_uri: string;
}

interface ConfirmTotpReenrollResponse {
  recovery_codes: string[];
}

export function useStartTotpReenroll() {
  return useMutation({
    mutationFn: (payload: StartTotpReenrollPayload) =>
      apiFetch<StartTotpReenrollResponse>("/api/v1/users/me/totp/reenroll/start", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  });
}

export function useConfirmTotpReenroll() {
  return useMutation({
    mutationFn: (code: string) =>
      apiFetch<ConfirmTotpReenrollResponse>("/api/v1/users/me/totp/reenroll/confirm", {
        method: "POST",
        body: JSON.stringify({ code }),
      }),
  });
}
```

- [ ] **Step 4: Add `ManageTotpSection` to `AccountPage.tsx`**

Add the `QRCode` import alongside the existing imports:

```typescript
import QRCode from "qrcode";
import { useChangePassword, useConfirmTotpReenroll, useMe, useRequestEmailChange, useStartTotpReenroll, useUpdateAmberApiKey } from "../account/useAccount";
```

Add the component (mirrors `TotpSetupPage`'s single-component, conditional-return shape
— all state is local, so no prop-drilling is needed):

```tsx
function ManageTotpSection() {
  const [step, setStep] = useState<"start" | "verify" | "codes">("start");
  const [currentPassword, setCurrentPassword] = useState("");
  const [gateCode, setGateCode] = useState("");
  const [gateError, setGateError] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [newCode, setNewCode] = useState("");
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const startReenroll = useStartTotpReenroll();
  const confirmReenroll = useConfirmTotpReenroll();

  async function handleStartSubmit(event: FormEvent) {
    event.preventDefault();
    setGateError(null);
    try {
      const result = await startReenroll.mutateAsync({ current_password: currentPassword, totp_code: gateCode });
      setSecret(result.secret);
      setQrDataUrl(await QRCode.toDataURL(result.otpauth_uri));
      setStep("verify");
    } catch (err) {
      setGateError(err instanceof Error ? err.message : "Failed to start authenticator reset. Please try again.");
    }
  }

  async function handleVerifySubmit(event: FormEvent) {
    event.preventDefault();
    setVerifyError(null);
    try {
      const result = await confirmReenroll.mutateAsync(newCode);
      setRecoveryCodes(result.recovery_codes);
      setStep("codes");
    } catch (err) {
      setVerifyError(err instanceof Error ? err.message : "Incorrect code");
    }
  }

  function handleDone() {
    setStep("start");
    setCurrentPassword("");
    setGateCode("");
    setSecret(null);
    setQrDataUrl(null);
    setNewCode("");
    setRecoveryCodes(null);
    setAcknowledged(false);
  }

  if (step === "codes" && recoveryCodes) {
    return (
      <section className="space-y-4">
        <h2 className="text-lg font-semibold">Two-factor authentication</h2>
        <p>Store these somewhere safe. Each works once if you lose your authenticator device.</p>
        <ul className="bg-gray-100 p-4 font-mono">
          {recoveryCodes.map((rc) => (
            <li key={rc}>{rc}</li>
          ))}
        </ul>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
          I've saved these codes
        </label>
        <button disabled={!acknowledged} onClick={handleDone} className="bg-blue-600 p-2 text-white disabled:opacity-50">
          Done
        </button>
      </section>
    );
  }

  if (step === "verify") {
    return (
      <section className="space-y-4">
        <h2 className="text-lg font-semibold">Two-factor authentication</h2>
        {qrDataUrl && <img src={qrDataUrl} alt="New TOTP QR code" />}
        {secret && <p className="font-mono text-sm">Manual entry key: {secret}</p>}
        <form onSubmit={handleVerifySubmit} className="space-y-2">
          {verifyError && (
            <p role="alert" className="text-red-600">
              {verifyError}
            </p>
          )}
          <label className="flex flex-col text-sm">
            New authenticator code
            <input
              aria-label="New authenticator code"
              value={newCode}
              onChange={(e) => setNewCode(e.target.value)}
              placeholder="6-digit code"
              className="border p-2"
              required
            />
          </label>
          <button type="submit" className="bg-blue-600 p-2 text-white">
            Confirm
          </button>
        </form>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold">Two-factor authentication</h2>
      <p className="text-gray-600">Lost your device? Replace your authenticator and get new recovery codes.</p>
      <form onSubmit={handleStartSubmit} className="space-y-2">
        {gateError && (
          <p role="alert" className="text-red-600">
            {gateError}
          </p>
        )}
        <label className="flex flex-col text-sm">
          Current password for two-factor reset
          <input
            aria-label="Current password for two-factor reset"
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            className="border p-2"
            required
          />
        </label>
        <label className="flex flex-col text-sm">
          Current authenticator code
          <input
            aria-label="Current authenticator code"
            value={gateCode}
            onChange={(e) => setGateCode(e.target.value)}
            placeholder="6-digit code"
            className="border p-2"
            required
          />
        </label>
        <button
          type="submit"
          disabled={startReenroll.isPending}
          className="bg-blue-600 p-2 text-white disabled:opacity-50"
        >
          Replace authenticator
        </button>
      </form>
    </section>
  );
}
```

Wire it into `AccountPage`:

```tsx
export function AccountPage() {
  return (
    <div className="mx-auto mt-8 max-w-2xl space-y-8 px-4">
      <h1 className="text-xl font-semibold">Account</h1>
      <ChangePasswordSection />
      <ChangeEmailSection />
      <ManageTotpSection />
      <AmberApiKeySection />
    </div>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd webapp && npx vitest run`
Expected: PASS, full suite.

- [ ] **Step 6: Commit**

```bash
cd webapp && git add src/account/useAccount.ts src/pages/AccountPage.tsx src/pages/AccountPage.test.tsx
git commit -m "Add manage-two-factor-authentication section to AccountPage"
```

---

## Task 8: Final review pass

**Files:** none fixed in advance — whatever the review surfaces.

- [ ] **Step 1: Run the full backend test suite**

Run: `cd core-api && uv run pytest tests/ -v`
Expected: PASS, full suite (includes every test added in Tasks 1-4).

- [ ] **Step 2: Run the full frontend test suite**

Run: `cd webapp && npm test`
Expected: PASS, full suite (includes every test added in Tasks 5-7).

- [ ] **Step 3: Run /simplify over the changed files**

Invoke the `/simplify` skill against every file touched by Tasks 1-7 (`core_api/schemas.py`,
`core_api/routers/account.py`, `core_api/models/identity.py`, `core_api/models/__init__.py`,
`webapp/src/account/useAccount.ts`, `webapp/src/pages/AccountPage.tsx`,
`webapp/src/pages/ConfirmEmailChangePage.tsx`, `webapp/src/App.tsx`), per this project's
`CLAUDE.md` Code Review Standards (functions over 30 lines, logic duplicated more than
twice, `any` usage, components with more than 3 ungrouped props, missing async error
handling). Apply any fixes it proposes, then re-run Steps 1-2 to confirm nothing broke.

- [ ] **Step 4: Run /code-review over the branch**

Invoke the `/code-review` skill (high effort) against the full diff for this feature.
Apply any CONFIRMED findings, then re-run Steps 1-2 again.

- [ ] **Step 5: Commit any fixes from Steps 3-4**

```bash
git add -A
git commit -m "Apply simplify/code-review fixes to account settings feature"
```

(Skip this step entirely if Steps 3-4 found nothing to change.)
