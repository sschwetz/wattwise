from __future__ import annotations

import datetime
import logging
import secrets
import time

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_api import login_attempts
from core_api.config import get_settings
from core_api.deps import get_db_session, get_email_sender
from core_api.email_sender import EmailSender
from core_api.errors import AppError
from core_api.models.identity import EmailConfirmationToken, User
from core_api.schemas import LoginChallenge, LoginRequest, RegisterConfirmIn, RegisterOut, RegisterRequest
from core_api.security import create_scoped_token, hash_password, hash_token, verify_password
from core_api.timing import pad_to_minimum_duration

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# A fixed, precomputed hash with no corresponding user: verify_password is run
# against this for an unknown email so a nonexistent-account login costs the
# same bcrypt time as a real one, instead of short-circuiting instantly and
# leaking account existence through response timing.
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-password-used-only-for-timing")


@router.post("/login", response_model=LoginChallenge)
def login(payload: LoginRequest, db: Session = Depends(get_db_session)) -> LoginChallenge:
    if login_attempts.is_locked(payload.email):
        raise AppError(401, "too_many_attempts", "Too many failed login attempts. Try again later.")

    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    # Falls back to the dummy hash not just for an unknown email but also for
    # a known one with no password yet (a fresh, unconfirmed registration --
    # see User.password_hash), so this still runs a real bcrypt comparison
    # either way. Skipping straight to `False` for a NULL password_hash
    # would be faster than bcrypt and reopen exactly the timing oracle this
    # function exists to close, this time distinguishing "registered but
    # unconfirmed" from "wrong password on a confirmed account".
    password_hash = user.password_hash if user is not None and user.password_hash is not None else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, password_hash)
    # An unconfirmed account (registered but never confirmed) is rejected the
    # SAME way as a wrong password: both raise invalid_credentials with no
    # distinguishing detail, so this can't become a second enumeration oracle
    # for exactly the thing /register's generic response was built to hide.
    if user is None or not password_ok or not user.email_confirmed:
        login_attempts.register_failure(payload.email)
        raise AppError(401, "invalid_credentials", "Email or password is incorrect")

    login_attempts.clear(payload.email)
    if not user.totp_enabled:
        setup_token, _jti = create_scoped_token(user.id, "totp_setup")
        return LoginChallenge(status="totp_setup_required", setup_token=setup_token)
    mfa_token, _jti = create_scoped_token(user.id, "mfa")
    return LoginChallenge(status="totp_required", mfa_token=mfa_token)


_GENERIC_REGISTER_MESSAGE = "Check your email to confirm your account before logging in."
_CONFIRMATION_TOKEN_EXPIRY = datetime.timedelta(hours=24)

# A new (or still-unconfirmed) email pays for a bcrypt hash plus a DB write;
# an already-confirmed one does neither and would otherwise return almost
# instantly. Padding every response up to this floor closes that wall-clock
# enumeration oracle even though the JSON body is already identical -- same
# reasoning as password_reset.request_reset. The email send itself is
# scheduled as a background task (see register()) rather than awaited here,
# so its network latency can't blow through this floor and reopen the same
# oracle from the other direction -- a floor bounds the fast path, but
# nothing bounded the slow path's upper tail until the send moved off the
# request.
_MIN_RESPONSE_SECONDS = 0.3


def _issue_confirmation_token(db: Session, user: User) -> str:
    """Store a hashed, 24-hour confirmation token for `user` and return the
    raw value (the only place it exists in plaintext -- it goes in the email
    and is never recoverable from the DB)."""
    raw_token = secrets.token_urlsafe(32)
    now = datetime.datetime.now(datetime.timezone.utc)
    db.add(
        EmailConfirmationToken(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=now + _CONFIRMATION_TOKEN_EXPIRY,
        )
    )
    db.commit()
    return raw_token


def _invalidate_outstanding_confirmation_tokens(db: Session, user_id: int, now: datetime.datetime) -> None:
    """Burn every unused confirmation token for this user, not just a newly
    issued one -- same reasoning as password_reset._invalidate_outstanding_tokens.
    A leaked or stale older link should stop working as soon as a fresher one
    is issued, rather than staying live for the rest of its 24-hour window."""
    outstanding = (
        db.execute(
            select(EmailConfirmationToken).where(
                EmailConfirmationToken.user_id == user_id,
                EmailConfirmationToken.used_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for token in outstanding:
        token.used_at = now


def _try_send_confirmation_email(email_sender: EmailSender, to: str, raw_token: str) -> None:
    """Send the confirmation link, swallowing any delivery failure -- same
    reasoning as password_reset._try_send_reset_email: a send failure must
    not change the caller's response, or it becomes an enumeration oracle in
    its own right."""
    confirm_url = f"{get_settings().frontend_base_url}/confirm-registration?token={raw_token}"
    try:
        email_sender.send(
            to=to,
            subject="Confirm your account",
            body=f"Click the link to confirm your account: {confirm_url}\nThis link expires in 24 hours.",
        )
    except Exception:
        logger.exception("Failed to send registration-confirmation email")


@router.post("/register", response_model=RegisterOut)
def register(
    payload: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_session),
    email_sender: EmailSender = Depends(get_email_sender),
) -> RegisterOut:
    started = time.monotonic()
    existing = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if existing is None:
        # password_hash is left NULL -- the real password is never chosen
        # here at all, it's supplied at /register/confirm, by whoever holds
        # the token -- see confirm_registration -- so /register itself has
        # no credential to leak or race over, by construction rather than
        # by locking.
        user = User(email=payload.email, password_hash=None, email_confirmed=False)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            # Lost a race against a concurrent registration for the same
            # email that committed first -- fall through to the generic
            # response exactly as the already-registered case does below.
            db.rollback()
        else:
            raw_token = _issue_confirmation_token(db, user)
            background_tasks.add_task(_try_send_confirmation_email, email_sender, user.email, raw_token)
    elif not existing.email_confirmed:
        # The existing row is still unconfirmed, so nobody has yet proven
        # they control this inbox -- including whoever registered it first.
        # Treat this call like a fresh registration (new confirmation link)
        # instead of leaving it a no-op: a no-op here would let anyone
        # permanently squat an email they don't own simply by registering it
        # and never confirming, since `existing` above matches on email
        # regardless of confirmation state and nothing else ever lets the
        # real owner in.
        #
        # This never touches existing.password_hash, and no password from
        # this call ever reaches a token either -- /register/confirm is the
        # only place a password is chosen, by whoever actually submits that
        # call holding a valid token. That's what keeps this safe even
        # though /register itself is unauthenticated: an attacker can keep
        # re-triggering a fresh confirmation link to the real inbox, but
        # every one of those links just asks "confirm and choose a
        # password" -- there's never a pre-baked attacker password for the
        # real owner to unknowingly adopt by clicking it.
        now = datetime.datetime.now(datetime.timezone.utc)
        _invalidate_outstanding_confirmation_tokens(db, existing.id, now)
        raw_token = _issue_confirmation_token(db, existing)
        background_tasks.add_task(_try_send_confirmation_email, email_sender, existing.email, raw_token)
    # Identical response whether this registered a new (unconfirmed) account,
    # reissued a confirmation link for one that was still unconfirmed, found
    # the email already confirmed, or lost a commit race -- distinguishing
    # any of those would tell an unauthenticated caller which emails have
    # ever been registered.
    pad_to_minimum_duration(started, _MIN_RESPONSE_SECONDS)
    return RegisterOut(message=_GENERIC_REGISTER_MESSAGE)


def _claim_confirmation_token(db: Session, raw_token: str, now: datetime.datetime) -> EmailConfirmationToken:
    """The presented token, atomically marked used, or a uniform 400 if it is
    unknown, already used, or expired -- same atomic-claim pattern as
    password_reset._claim_token, for the same double-submit reason."""
    token = db.execute(
        select(EmailConfirmationToken).where(EmailConfirmationToken.token_hash == hash_token(raw_token))
    ).scalar_one_or_none()
    if token is None:
        raise AppError(400, "invalid_token", "This confirmation link is invalid or has expired")

    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    if expires_at <= now:
        raise AppError(400, "invalid_token", "This confirmation link is invalid or has expired")

    result = db.execute(
        update(EmailConfirmationToken)
        .where(EmailConfirmationToken.id == token.id, EmailConfirmationToken.used_at.is_(None))
        .values(used_at=now)
    )
    if result.rowcount == 0:
        raise AppError(400, "invalid_token", "This confirmation link is invalid or has expired")
    return token


@router.post("/register/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_registration(payload: RegisterConfirmIn, db: Session = Depends(get_db_session)) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    token = _claim_confirmation_token(db, payload.token, now)
    user = db.get(User, token.user_id)
    assert user is not None  # FK guarantees the referenced user still exists
    # The password is chosen HERE, by whoever actually submits this call
    # holding a valid token -- never earlier, at register() time, by
    # whoever happened to call /register last. That's what makes setting a
    # credential require controlling the inbox -- see register()'s
    # elif-branch comment.
    user.password_hash = hash_password(payload.password)
    user.email_confirmed = True
    db.commit()
