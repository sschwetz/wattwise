from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.admin_schemas import AdminUserOut, AdminUserSite
from core_api.models.identity import Site, SiteAccess, SiteRole, User

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
