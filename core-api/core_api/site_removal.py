"""What removing a person from a site does to their invitations. The owner's
removal and the administrator's removal both call it."""

from __future__ import annotations

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from core_api.models.identity import InviteStatus, SiteInvite, User


def revoke_invites_of_removed_person(db: Session, site_id: int, person: User, by_user_id: int) -> None:
    """Revoke the person's invites to the site: accepted ones they accepted,
    so the Sharing tab stays accurate, and unanswered ones to their email
    (any case) or their user id, so they cannot accept one later and get
    access back. Does not commit."""
    accepted = and_(SiteInvite.status == InviteStatus.ACCEPTED, SiteInvite.invitee_user_id == person.id)
    unanswered = and_(
        SiteInvite.status == InviteStatus.INVITED,
        or_(func.lower(SiteInvite.email) == person.email.lower(), SiteInvite.invitee_user_id == person.id),
    )
    for invite in db.scalars(select(SiteInvite).where(SiteInvite.site_id == site_id, or_(accepted, unanswered))):
        invite.revoke(by_user_id)
