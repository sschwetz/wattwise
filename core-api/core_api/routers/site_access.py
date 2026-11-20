"""Who has access to a site. The owner and managers see the list; only the
owner removes someone, which also covers grants an administrator made
without an invitation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.deps import get_current_user, get_db_session, require_site_manager, require_site_owner
from core_api.errors import AppError
from core_api.models.identity import InviteStatus, Site, SiteAccess, SiteInvite, SiteRole, User
from core_api.schemas import SitePersonOut

router = APIRouter(prefix="/sites/{site_id}/access", tags=["sites"])

_ROLE_ORDER = {SiteRole.OWNER: 0, SiteRole.MANAGER: 1, SiteRole.VIEWER: 2}


def _person(user: User, role: SiteRole) -> SitePersonOut:
    return SitePersonOut(user_id=user.id, email=user.email, display_name=user.display_name, role=role)


@router.get("", response_model=list[SitePersonOut])
def list_site_people(
    site: Site = Depends(require_site_manager), db: Session = Depends(get_db_session)
) -> list[SitePersonOut]:
    """The owner, then managers, then viewers, each group by email."""
    owner = db.execute(select(User).where(User.id == site.owner_user_id)).scalar_one()
    rows = db.execute(
        select(User, SiteAccess.role)
        .join(SiteAccess, SiteAccess.user_id == User.id)
        .where(SiteAccess.site_id == site.id)
    ).all()
    people = [_person(owner, SiteRole.OWNER)] + [_person(user, SiteRole(role.value)) for user, role in rows]
    return sorted(people, key=lambda p: (_ROLE_ORDER[p.role], p.email.lower()))


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_site_person(
    user_id: int,
    site: Site = Depends(require_site_owner),
    owner: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> Response:
    """Delete the user's grant and revoke their accepted invitations to the site."""
    if user_id == site.owner_user_id:
        raise AppError(409, "is_owner", "The site owner's access cannot be removed")
    grant = db.get(SiteAccess, (site.id, user_id))
    if grant is None:
        raise AppError(404, "not_found", "This person has no access to the site")
    db.delete(grant)
    accepted = select(SiteInvite).where(
        SiteInvite.site_id == site.id, SiteInvite.invitee_user_id == user_id, SiteInvite.status == InviteStatus.ACCEPTED
    )
    for invite in db.scalars(accepted):
        invite.revoke(owner.id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
