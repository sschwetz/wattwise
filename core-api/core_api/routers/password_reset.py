from __future__ import annotations

import datetime
import secrets
import time

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core_api.config import get_settings
from core_api.db import rows_affected
from core_api.deps import get_db_session, get_email_sender
from core_api.email_sender import EmailSender, try_send_email
from core_api.errors import AppError
from core_api.models.identity import PasswordResetToken, User
from core_api.schemas import PasswordResetConfirmIn, PasswordResetRequestIn, PasswordResetRequestOut
from core_api.security import hash_password, hash_token
from core_api.timing import pad_to_minimum_duration

router = APIRouter(prefix="/auth/password-reset", tags=["auth"])

_GENERIC_MESSAGE = "If that email is registered, a password reset link has been sent."
_TOKEN_EXPIRY = datetime.timedelta(hours=1)

# A registered email pays for a DB insert; an unregistered one does neither
# and would otherwise return almost instantly. Padding every response up to
# this floor closes that wall-clock enumeration oracle even though the JSON
# body is already identical. The email send itself is scheduled as a
# background task (see request_reset()) rather than awaited here, so its
# network latency can't push a registered email's response past this floor
# and reopen the same oracle from the slow side.
_MIN_RESPONSE_SECONDS = 0.3


def _issue_reset_token(db: Session, user: User) -> str:
    """Store a hashed, 1-hour reset token for `user` and return the raw value
    (the only place it exists in plaintext — it goes in the email and is never
    recoverable from the DB)."""
    raw_token = secrets.token_urlsafe(32)
    now = datetime.datetime.now(datetime.UTC)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=now + _TOKEN_EXPIRY,
        )
    )
    db.commit()
    return raw_token


def _try_send_reset_email(email_sender: EmailSender, to: str, raw_token: str) -> None:
    """Send the reset link, swallowing any delivery failure.

    A send failure must NOT change the caller's response. Letting it propagate
    would make /request an account-enumeration oracle: a registered email would
    500 while an unregistered one still 200s, so the mere presence of an error
    reveals that the address exists — defeating the generic-message design. A
    relay outage is an operational problem, surfaced in the log here, not
    something to report to an unauthenticated caller."""
    reset_url = f"{get_settings().frontend_base_url}/reset-password?token={raw_token}"
    try_send_email(
        email_sender,
        to,
        "Reset your password",
        f"Click the link to reset your password: {reset_url}\nThis link expires in 1 hour.",
        "password-reset",
    )


@router.post("/request", response_model=PasswordResetRequestOut)
def request_reset(
    payload: PasswordResetRequestIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_session),
    email_sender: EmailSender = Depends(get_email_sender),
) -> PasswordResetRequestOut:
    started = time.monotonic()
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if user is not None:
        raw_token = _issue_reset_token(db, user)
        background_tasks.add_task(_try_send_reset_email, email_sender, user.email, raw_token)
    # Identical response whether the email was sent, failed to send, or was
    # never attempted because the address is not registered — see
    # _try_send_reset_email.
    pad_to_minimum_duration(started, _MIN_RESPONSE_SECONDS)
    return PasswordResetRequestOut(message=_GENERIC_MESSAGE)


def _claim_token(db: Session, raw_token: str, now: datetime.datetime) -> PasswordResetToken:
    """The presented token, atomically marked used, or a uniform 400 if it is
    unknown, already used, or expired. All three failures share one message
    deliberately: distinguishing them would tell a caller which tokens have
    ever existed.

    Marking it used here — via a conditional UPDATE checked for rowcount,
    rather than a plain SELECT followed by a later ORM-attribute mutation —
    closes a double-submit race: two overlapping confirms for the same token
    previously both passed the SELECT before either committed, so the second
    commit silently overwrote the first's password change with no error to
    either caller. Only the first UPDATE to actually flip used_at can match
    `used_at IS NULL`; a racing second UPDATE affects zero rows."""
    token = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw_token))
    ).scalar_one_or_none()
    if token is None:
        raise AppError(400, "invalid_token", "This reset link is invalid or has expired")

    # SQLite's DateTime column silently drops tzinfo on write/read (see the
    # same note on schemas._to_utc), so a token reloaded from the DB comes
    # back naive even though it was stored as an aware UTC value — normalize
    # before comparing against `now`, which is always aware.
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.UTC)
    if expires_at <= now:
        raise AppError(400, "invalid_token", "This reset link is invalid or has expired")

    result = db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.id == token.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    if rows_affected(result) == 0:
        raise AppError(400, "invalid_token", "This reset link is invalid or has expired")
    return token


def _invalidate_outstanding_tokens(db: Session, user_id: int, now: datetime.datetime) -> None:
    """Burn every unused reset token for this user, not just the one presented.
    A user who requested a reset twice (or an attacker who requested one against
    a known email before the legitimate user did) would otherwise leave the
    other link live for the rest of its 1-hour window — so a leaked old link
    keeps working even after the user believes the account is secured."""
    outstanding = (
        db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user_id,
                PasswordResetToken.used_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for token in outstanding:
        token.used_at = now


@router.post("/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_reset(payload: PasswordResetConfirmIn, db: Session = Depends(get_db_session)) -> None:
    now = datetime.datetime.now(datetime.UTC)
    token = _claim_token(db, payload.token, now)
    user = db.get(User, token.user_id)
    assert user is not None  # FK guarantees the referenced user still exists
    user.password_hash = hash_password(payload.new_password)
    _invalidate_outstanding_tokens(db, token.user_id, now)
    db.commit()
