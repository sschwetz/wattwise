from __future__ import annotations

import datetime
import enum

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, func, true
from sqlalchemy.orm import Mapped, mapped_column

from core_api.encrypted_types import EncryptedString
from core_api.models.base import Base, enum_check, str_enum


class SiteAccessRole(str, enum.Enum):
    """Non-owner grants only. Ownership lives solely in Site.owner_user_id —
    see docs/superpowers/specs/2026-09-25-core-api-design.md."""

    VIEWER = "viewer"


class InviteStatus(str, enum.Enum):
    INVITED = "invited"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    REVOKED = "revoked"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    avatar_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # NULL until /register/confirm sets it -- see auth.register and
    # auth.confirm_registration. A fresh, unconfirmed registration has no
    # password at all rather than a placeholder hash of some fixed value,
    # so nothing could ever authenticate as it even if a future code path
    # checked the password without separately checking email_confirmed.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amber_api_key: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    totp_secret: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    pending_totp_secret: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The 30s TOTP step number last accepted for this user (login or
    # step-up), so a code observed once (shoulder-surfing, a compromised
    # MFA relay) can't be replayed again within its own ~90s valid_window.
    last_totp_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Defaults to True so every OTHER way a User row comes into existence
    # (the admin bootstrap, test fixtures constructing one directly) is
    # unaffected -- only /auth/register explicitly passes False, since it's
    # the only path where "prove you own this email" actually matters.
    email_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )


class SiteAccess(Base):
    """Additional read-only grants beyond the site's owner."""

    __tablename__ = "site_access"
    __table_args__ = (enum_check("role", SiteAccessRole, "ck_site_access_role"),)

    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[SiteAccessRole] = mapped_column(
        str_enum(SiteAccessRole, length=32),
        nullable=False,
        default=SiteAccessRole.VIEWER,
    )


class SiteInvite(Base):
    """Append-only record of an emailed invitation to view a site. Rows are
    never deleted; status moves forward and its timestamp is set."""

    __tablename__ = "site_invites"
    __table_args__ = (enum_check("status", InviteStatus, "ck_site_invites_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    inviter_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    status: Mapped[InviteStatus] = mapped_column(
        str_enum(InviteStatus, length=16), nullable=False, default=InviteStatus.INVITED
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    accepted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    declined_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    invitee_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class RecoveryCode(Base):
    """One-time TOTP-recovery codes, generated 10-at-a-time when a user
    completes TOTP enrollment. code_hash is a keyed HMAC-SHA256
    (security.hash_recovery_code) -- deterministic for direct-lookup
    verification, but not recoverable without SECRETS_ENCRYPTION_KEY."""

    __tablename__ = "recovery_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordResetToken(Base):
    """Single-use, expiring token emailed to a user who requested a password
    reset. token_hash is SHA-256, same reasoning as RecoveryCode.code_hash."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailConfirmationToken(Base):
    """Single-use, expiring token emailed on registration to confirm the
    account owns the address before it can log in. token_hash is SHA-256,
    same reasoning as PasswordResetToken.token_hash.

    Carries no password of its own: the password is submitted alongside
    THIS token at /register/confirm, by whoever actually holds it, the same
    way PasswordResetToken's confirm step works. That keeps password choice
    out of /register entirely, so no unauthenticated re-registration against
    a still-unconfirmed email can ever inject a credential for the real
    owner to unknowingly adopt. See auth.register and auth.confirm_registration."""

    __tablename__ = "email_confirmation_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
