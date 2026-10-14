from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, require_mfa_token, require_setup_token
from core_api.errors import AppError
from core_api.mfa_attempts import clear as clear_mfa_attempts
from core_api.mfa_attempts import register_failure as register_mfa_failure
from core_api.models.identity import RecoveryCode, User
from core_api.schemas import (
    LoginResponse,
    TotpSetupConfirmIn,
    TotpSetupConfirmOut,
    TotpSetupStartOut,
    TotpVerifyIn,
)
from core_api.security import (
    create_access_token,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    totp_provisioning_uri,
    verify_totp_code,
    verify_totp_code_once,
)

router = APIRouter(prefix="/auth/totp", tags=["auth"])


def _reject_if_already_enrolled(user: User) -> None:
    """Both setup endpoints are enrollment-only: once totp_enabled is True they
    must refuse. Without this, a replayed or stale setup_token (verify_totp_code
    has no replay guard, so a double-clicked "Verify" passes the same 6 digits
    twice) would re-roll totp_secret and append a SECOND batch of 10 recovery
    codes without invalidating the first — leaving 10 live codes the user never
    saw, each of which bypasses TOTP entirely.

    Deliberate re-enrollment ("reset my 2FA") is a separate, later feature; this
    only closes the accidental-double-confirm gap."""
    if user.totp_enabled:
        raise AppError(409, "already_enrolled", "Two-factor authentication is already enabled for this account")


@router.post("/setup/start", response_model=TotpSetupStartOut)
def start_setup(
    user: User = Depends(require_setup_token),
    db: Session = Depends(get_db_session),
) -> TotpSetupStartOut:
    _reject_if_already_enrolled(user)
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
    _reject_if_already_enrolled(user)
    if user.totp_secret is None or not verify_totp_code(user.totp_secret, payload.code):
        raise AppError(401, "invalid_code", "Incorrect verification code")
    user.totp_enabled = True
    raw_codes = generate_recovery_codes()
    for raw_code in raw_codes:
        db.add(RecoveryCode(user_id=user.id, code_hash=hash_recovery_code(raw_code)))
    db.commit()
    return TotpSetupConfirmOut(access_token=create_access_token(user.id), recovery_codes=raw_codes)


def _claim_totp_step(db: Session, user_id: int, step: int) -> bool:
    """Atomically advances last_totp_step via a conditional UPDATE, rather
    than the caller reading user.last_totp_step and writing it back later:
    two overlapping /verify calls presenting the same code each load their
    own User instance, so both can see the same pre-race last_totp_step,
    both pass verify_totp_code_once's Python-level replay check, and both
    would otherwise authenticate before either committed -- defeating the
    anti-replay guard this is meant to enforce. Only an UPDATE whose WHERE
    clause still holds when it runs (nobody has already advanced to this
    step or beyond) takes effect; a racing second UPDATE affects zero rows."""
    result = db.execute(
        update(User)
        .where(User.id == user_id, (User.last_totp_step.is_(None)) | (User.last_totp_step < step))
        .values(last_totp_step=step)
    )
    return result.rowcount > 0


def _consume_recovery_code(db: Session, user_id: int, raw_code: str) -> bool:
    """Atomically claims the code via a conditional UPDATE, rather than a
    SELECT followed by a later ORM-attribute mutation: two overlapping
    /verify calls presenting the same recovery code previously both passed a
    `used_at IS NULL` SELECT before either committed, so both could redeem
    the same single-use code. Only the first UPDATE to flip used_at can match
    `used_at IS NULL`; a racing second UPDATE affects zero rows."""
    code_hash = hash_recovery_code(raw_code)
    now = datetime.datetime.now(datetime.timezone.utc)
    result = db.execute(
        update(RecoveryCode)
        .where(
            RecoveryCode.user_id == user_id,
            RecoveryCode.code_hash == code_hash,
            RecoveryCode.used_at.is_(None),
        )
        .values(used_at=now)
    )
    return result.rowcount > 0


@router.post("/verify", response_model=LoginResponse)
def verify(
    payload: TotpVerifyIn,
    user: User = Depends(require_mfa_token),
    db: Session = Depends(get_db_session),
) -> LoginResponse:
    # Attempts are tracked per user, not per mfa_token: see core_api.mfa_attempts.
    if user.totp_secret:
        step = verify_totp_code_once(user.totp_secret, payload.code, user.last_totp_step)
        if step is not None and _claim_totp_step(db, user.id, step):
            db.commit()
            clear_mfa_attempts(user.id)
            return LoginResponse(access_token=create_access_token(user.id))

    if _consume_recovery_code(db, user.id, payload.code):
        db.commit()
        clear_mfa_attempts(user.id)
        return LoginResponse(access_token=create_access_token(user.id))

    register_mfa_failure(user.id)
    raise AppError(401, "invalid_code", "Incorrect code")
