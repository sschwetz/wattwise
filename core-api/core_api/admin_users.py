from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from core_api.admin_schemas import AdminUserOut, AdminUserPatch, AdminUserSite
from core_api.db import rows_affected
from core_api.errors import AppError
from core_api.models.identity import (
    EmailChangeToken,
    InviteStatus,
    PasswordResetToken,
    RecoveryCode,
    Site,
    SiteAccess,
    SiteAccessRole,
    SiteInvite,
    SiteRole,
    User,
)

logger = logging.getLogger(__name__)

_ROLE_ORDER = {SiteRole.OWNER: 0, SiteRole.MANAGER: 1, SiteRole.VIEWER: 2}


def _sites_by_user(db: Session, user_ids: list[int] | None = None) -> dict[int, list[AdminUserSite]]:
    """Owned sites and access grants for the given users (all users when None),
    two queries, with each user's sites ordered by role then name. A grant on a
    site the user owns is dropped; the owner entry covers it."""
    owned = select(Site.owner_user_id, Site.id, Site.name)
    granted = (
        select(SiteAccess.user_id, Site.id, Site.name, SiteAccess.role)
        .join(Site, Site.id == SiteAccess.site_id)
        .where(Site.owner_user_id != SiteAccess.user_id)
    )
    if user_ids is not None:
        owned = owned.where(Site.owner_user_id.in_(user_ids))
        granted = granted.where(SiteAccess.user_id.in_(user_ids))

    entries: dict[int, list[AdminUserSite]] = defaultdict(list)
    for user_id, site_id, name in db.execute(owned):
        entries[user_id].append(AdminUserSite(site_id=site_id, site_name=name, role=SiteRole.OWNER))
    for user_id, site_id, name, role in db.execute(granted):
        entries[user_id].append(AdminUserSite(site_id=site_id, site_name=name, role=SiteRole(role.value)))
    for sites in entries.values():
        sites.sort(key=lambda s: (_ROLE_ORDER[s.role], s.site_name, s.site_id))
    return entries


def _to_out(user: User, sites: list[AdminUserSite]) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
        has_avatar=user.avatar_content_type is not None,
        totp_enabled=user.totp_enabled,
        email_confirmed=user.email_confirmed,
        has_amber_api_key=user.amber_api_key is not None,
        sites=sites,
    )


def list_admin_users(db: Session) -> list[AdminUserOut]:
    users = db.scalars(select(User).order_by(User.id)).all()
    sites = _sites_by_user(db)
    return [_to_out(user, sites.get(user.id, [])) for user in users]


def admin_user_out(db: Session, user: User) -> AdminUserOut:
    return _to_out(user, _sites_by_user(db, [user.id]).get(user.id, []))


def log_admin_action(admin: User, target_user_id: int | None, action: str) -> None:
    """One line per admin write: who, on whom, which action. Never pass values.
    A write with no target user (mail settings) logs `target=none`."""
    if target_user_id is None:
        logger.info("admin action: admin_user_id=%s target=none action=%s", admin.id, action)
        return
    logger.info("admin action: admin_user_id=%s target_user_id=%s action=%s", admin.id, target_user_id, action)


def get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise AppError(404, "not_found", f"User {user_id} not found")
    return user


def _set_email(db: Session, user: User, email: str) -> None:
    """Sets the address directly and leaves email_confirmed as it is. Access
    tokens carry only the user id, so tokens already issued keep working."""
    email = email.lower()
    taken = db.scalar(select(User.id).where(func.lower(User.email) == email, User.id != user.id))
    if taken is not None:
        raise AppError(409, "email_taken", "An account with this email already exists")
    changed = user.email.lower() != email
    user.email = email
    if not changed:
        return
    # A staged address change or a reset link mailed to the old address must not
    # override or bypass the admin's change.
    db.execute(delete(EmailChangeToken).where(EmailChangeToken.user_id == user.id))
    db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))


def _clear_admin(db: Session, user: User) -> None:
    """Clears is_admin unless the user is the last admin. The count and the
    write are one conditional UPDATE. On SQLite, which has a single writer,
    two concurrent demotions cannot both pass. On a database with concurrent
    writers, two transactions can each see the other admin and both pass."""
    if not user.is_admin:
        return
    admin_count = select(func.count()).select_from(User).where(User.is_admin.is_(True)).scalar_subquery()
    result = db.execute(
        update(User)
        .where(User.id == user.id, admin_count > 1)
        .values(is_admin=False)
        .execution_options(synchronize_session=False)
    )
    if rows_affected(result) == 0:
        raise AppError(409, "last_admin", "The last administrator cannot lose the admin flag")
    db.refresh(user)


def update_user(db: Session, user: User, patch: AdminUserPatch) -> None:
    """Applies the fields present in the body. Does not commit."""
    if patch.is_admin is False:
        _clear_admin(db, user)
    elif patch.is_admin:
        user.is_admin = True
    if patch.email is not None:
        _set_email(db, user, patch.email)
    if "display_name" in patch.model_fields_set:
        user.display_name = patch.display_name


def _grantable_site_or_404(db: Session, user: User, site_id: int) -> Site:
    """The site, or 404; 409 when the user owns it, since ownership is not a grant."""
    site = db.get(Site, site_id)
    if site is None:
        raise AppError(404, "not_found", f"Site {site_id} not found")
    if site.owner_user_id == user.id:
        raise AppError(409, "is_owner", "The site owner's access cannot be changed")
    return site


def grant_site_access(db: Session, user: User, site_id: int, role: SiteAccessRole) -> None:
    """Creates or changes the user's one grant on the site. Does not commit."""
    _grantable_site_or_404(db, user, site_id)
    grant = db.get(SiteAccess, (site_id, user.id))
    if grant is None:
        db.add(SiteAccess(site_id=site_id, user_id=user.id, role=role))
    else:
        grant.role = role


def remove_site_access(db: Session, admin: User, user: User, site_id: int) -> None:
    """Deletes the user's grant on the site, or 404 if there is none, and marks
    their accepted invites for the site revoked, as the owner's revoke does.
    Does not commit."""
    _grantable_site_or_404(db, user, site_id)
    grant = db.get(SiteAccess, (site_id, user.id))
    if grant is None:
        raise AppError(404, "not_found", "This user has no access to that site")
    db.delete(grant)
    accepted = db.scalars(
        select(SiteInvite).where(
            SiteInvite.site_id == site_id,
            SiteInvite.invitee_user_id == user.id,
            SiteInvite.status == InviteStatus.ACCEPTED,
        )
    )
    for invite in accepted:
        invite.revoke(admin.id)


def reset_totp(db: Session, user: User) -> None:
    """Returns the user to the enrolment step at next login. Old recovery codes
    go too: they bypass the code check and belong to the secret being cleared.
    Does not commit."""
    user.totp_secret = None
    user.pending_totp_secret = None
    user.last_totp_step = None
    user.totp_enabled = False
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
