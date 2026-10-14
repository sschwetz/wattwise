from __future__ import annotations

import datetime
import logging
import os
import secrets
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_api.config import get_settings
from core_api.deps import get_current_user, get_db_session, get_email_sender
from core_api.email_sender import EmailSender
from core_api.errors import AppError
from core_api.models.identity import EmailChangeToken, RecoveryCode, User
from core_api.routers.totp import _claim_totp_step, _consume_recovery_code
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
    UpdateDisplayNameIn,
    UserMeOut,
)
from core_api.security import (
    generate_recovery_codes,
    generate_totp_secret,
    hash_password,
    hash_recovery_code,
    hash_token,
    totp_provisioning_uri,
    verify_password,
    verify_totp_code,
    verify_totp_code_once,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users/me", tags=["account"])

_EMAIL_CHANGE_TOKEN_EXPIRY = datetime.timedelta(hours=1)


def _to_user_me_out(user: User) -> UserMeOut:
    return UserMeOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        has_amber_api_key=user.amber_api_key is not None,
        has_avatar=user.avatar_content_type is not None,
    )


_ALLOWED_AVATAR_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
_MAX_AVATAR_BYTES = 2 * 1024 * 1024
# Slack above _MAX_AVATAR_BYTES for the multipart boundary/field headers
# surrounding the file's own bytes, so a legitimate ~2MB photo isn't rejected
# by the Content-Length pre-check below.
_MULTIPART_OVERHEAD_BYTES = 10 * 1024


def _reject_oversized_content_length(request: Request) -> None:
    """Rejects a request whose declared Content-Length is already too large,
    before FastAPI resolves the `file: UploadFile` parameter below -- that
    resolution is what triggers Starlette to buffer the entire multipart body
    (to a SpooledTemporaryFile, spilling to disk past 1MB) with no size cap of
    its own. Without this, upload_avatar's own read-and-cap logic only runs
    *after* an arbitrarily large body has already been fully received and
    written to the container's temp storage -- the same disk the database
    lives on. A dependency raising here runs before that body-parsing step, as
    FastAPI resolves Depends() dependencies before request_body_to_args()."""
    declared = request.headers.get("content-length")
    if declared is None:
        return
    try:
        declared_bytes = int(declared)
    except ValueError:
        return
    if declared_bytes > _MAX_AVATAR_BYTES + _MULTIPART_OVERHEAD_BYTES:
        raise AppError(400, "file_too_large", "Avatar must be 2MB or smaller")


def _avatar_path(user_id: int) -> Path:
    """The caller's own avatar file, keyed only by the id already proven by
    get_current_user -- no route in this feature accepts a user id as input,
    so there is no way to name another account's file through this API."""
    return Path(get_settings().avatar_storage_dir) / str(user_id)


def _verify_step_up(db: Session, user: User, current_password: str, totp_code: str) -> None:
    """Re-auth gate shared by every security-sensitive account mutation: proves
    the caller still holds BOTH the password and a second factor, even though
    they already carry a valid access_token. A bare access_token must never be
    sufficient on its own to change a credential (see
    docs/superpowers/specs/2026-10-08-account-settings-design.md). Accepts
    either a live TOTP code or an unused recovery code as the second factor --
    the same two factors /auth/totp/verify already treats as equivalent for
    login -- so a user who reached this page via recovery-code login (the
    actual lost-device scenario) can still replace their authenticator instead
    of being permanently locked out. Consuming a recovery code here costs one
    of the user's 10, exactly as using one to log in does; the bar is unchanged
    at password + one valid second factor.

    Does not commit: marking the recovery code used (or, via _claim_totp_step,
    the TOTP code's new last_totp_step) is persisted by the caller's own
    db.commit(), the same way every mutation in this file batches its
    writes into one commit."""
    if not verify_password(current_password, user.password_hash):
        raise AppError(401, "invalid_credentials", "Current password is incorrect")
    if user.totp_secret:
        step = verify_totp_code_once(user.totp_secret, totp_code, user.last_totp_step)
        if step is not None and _claim_totp_step(db, user.id, step):
            return
    if not _consume_recovery_code(db, user.id, totp_code):
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


@router.patch("/profile", response_model=UserMeOut)
def update_display_name(
    payload: UpdateDisplayNameIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> UserMeOut:
    current_user.display_name = payload.display_name
    db.commit()
    db.refresh(current_user)
    return _to_user_me_out(current_user)


@router.post("/avatar", response_model=UserMeOut)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
    _content_length_guard: None = Depends(_reject_oversized_content_length),
) -> UserMeOut:
    if file.content_type not in _ALLOWED_AVATAR_CONTENT_TYPES:
        raise AppError(400, "invalid_file_type", "Avatar must be a JPEG, PNG, or WebP image")

    # The Content-Length guard above catches a declared size that's already
    # too large. This second check is the authoritative one -- it does not
    # trust that header, and catches a body that's merely too large without
    # lying about it, by capping the bytes actually read.
    content = await file.read(_MAX_AVATAR_BYTES + 1)
    if len(content) > _MAX_AVATAR_BYTES:
        raise AppError(400, "file_too_large", "Avatar must be 2MB or smaller")

    path = _avatar_path(current_user.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-replace so a concurrent GET never observes a half-written file,
    # and so overwriting an existing avatar leaves no stale bytes behind. The
    # temp name is unique per request (not just per user) so two concurrent
    # uploads from the same account can't interleave writes into one shared
    # temp file; cleaned up on any failure so a crash never leaves it orphaned.
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_bytes(content)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

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


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> None:
    _verify_step_up(db, current_user, payload.current_password, payload.totp_code)
    current_user.password_hash = hash_password(payload.new_password)
    db.commit()


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
    _verify_step_up(db, current_user, payload.current_password, payload.totp_code)

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


def _claim_email_change_token(db: Session, raw_token: str, now: datetime.datetime) -> EmailChangeToken:
    """The presented token, atomically marked used, or a uniform 400 if it is
    unknown, already used, or expired -- matching password_reset._claim_token's
    reasoning and the same fix: a conditional UPDATE checked for rowcount,
    rather than a SELECT followed by a later ORM-attribute mutation, closes a
    double-submit race where two overlapping confirms could otherwise both
    pass the SELECT before either committed."""
    token = db.execute(
        select(EmailChangeToken).where(EmailChangeToken.token_hash == hash_token(raw_token))
    ).scalar_one_or_none()
    if token is None:
        raise AppError(400, "invalid_token", "This confirmation link is invalid or has expired")

    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    if expires_at <= now:
        raise AppError(400, "invalid_token", "This confirmation link is invalid or has expired")

    result = db.execute(
        update(EmailChangeToken)
        .where(EmailChangeToken.id == token.id, EmailChangeToken.used_at.is_(None))
        .values(used_at=now)
    )
    if result.rowcount == 0:
        raise AppError(400, "invalid_token", "This confirmation link is invalid or has expired")
    return token


@router.post("/email/change/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_email_change(payload: ConfirmEmailChangeIn, db: Session = Depends(get_db_session)) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    token = _claim_email_change_token(db, payload.token, now)
    target_user = db.get(User, token.user_id)
    assert target_user is not None  # FK guarantees the referenced user still exists

    target_user.email = token.new_email
    _invalidate_outstanding_email_change_tokens(db, token.user_id, now)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "email_taken", "An account with this email already exists") from exc


@router.post("/totp/reenroll/start", response_model=TotpReenrollStartOut)
def start_totp_reenroll(
    payload: StartTotpReenrollIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> TotpReenrollStartOut:
    _verify_step_up(db, current_user, payload.current_password, payload.totp_code)
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
    pending_secret = current_user.pending_totp_secret
    if pending_secret is None:
        raise AppError(400, "invalid_state", "No authenticator replacement is in progress")
    if not verify_totp_code(pending_secret, payload.code):
        raise AppError(401, "invalid_code", "Incorrect verification code")

    # Conditional UPDATE guards against a double-submit racing this same
    # request: verify_totp_code has no replay guard, so two overlapping
    # confirms can both pass the checks above. Only the first can still match
    # pending_totp_secret IS NOT NULL and actually flip the row; the
    # loser sees rowcount == 0 and is rejected instead of also generating a
    # second, redundant batch of 10 recovery codes the user never sees.
    result = db.execute(
        update(User)
        .where(User.id == current_user.id, User.pending_totp_secret.is_not(None))
        .values(totp_secret=pending_secret, pending_totp_secret=None)
    )
    if result.rowcount == 0:
        raise AppError(400, "invalid_state", "No authenticator replacement is in progress")

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
        db.add(RecoveryCode(user_id=current_user.id, code_hash=hash_recovery_code(raw_code)))
    db.commit()
    return TotpReenrollConfirmOut(recovery_codes=raw_codes)
