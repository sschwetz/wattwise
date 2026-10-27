from __future__ import annotations

import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.config import get_settings
from core_api.deps import get_current_user, get_db_session, get_email_sender, require_site_access, require_site_owner
from core_api.email_sender import EmailSender, try_send_email
from core_api.errors import AppError
from core_api.models.identity import InviteStatus, Site, SiteAccess, SiteInvite, User
from core_api.schemas import InviteCreate, InviteOut, PendingInviteOut

router = APIRouter(prefix="/sites/{site_id}/invites", tags=["invites"])

recipient_router = APIRouter(prefix="/invites", tags=["invites"])

MAX_PENDING_INVITES = 20


def status_changed_at(invite: SiteInvite) -> datetime.datetime:
    """Latest transition time: the timestamp matching the current status."""
    stamps = {
        InviteStatus.ACCEPTED: invite.accepted_at,
        InviteStatus.DECLINED: invite.declined_at,
        InviteStatus.REVOKED: invite.revoked_at,
    }
    return stamps.get(invite.status) or invite.created_at


def _invite_email(inviter: User, site: Site) -> tuple[str, str]:
    """Subject and body. Identical whether or not the address has an account."""
    inviter_name = inviter.display_name or inviter.email
    link = f"{get_settings().frontend_base_url}/sites"
    subject = f"{inviter_name} invited you to view {site.name} on WattWise"
    body = (
        f"{inviter_name} invited you to view the site \"{site.name}\" on WattWise.\n\n"
        f"Sign in or register with this email address, then open the sites page to accept or decline: {link}"
    )
    return subject, body


def _check_can_invite(db: Session, site: Site, owner: User, email: str) -> None:
    if email == owner.email.lower():
        raise AppError(400, "cannot_invite_self", "You cannot invite yourself")
    rows = db.execute(
        select(SiteInvite.email, SiteInvite.status).where(
            SiteInvite.site_id == site.id,
            SiteInvite.status.in_([InviteStatus.INVITED, InviteStatus.ACCEPTED]),
        )
    ).all()
    if any(row.email == email for row in rows):
        raise AppError(409, "already_invited", "This address already has an invitation to this site")
    pending = sum(1 for row in rows if row.status == InviteStatus.INVITED)
    if pending >= MAX_PENDING_INVITES:
        raise AppError(409, "invite_limit", f"A site can have at most {MAX_PENDING_INVITES} pending invitations")


@router.post("", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
def create_invite(
    payload: InviteCreate,
    background_tasks: BackgroundTasks,
    site: Site = Depends(require_site_owner),
    owner: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
    email_sender: EmailSender = Depends(get_email_sender),
) -> SiteInvite:
    email = payload.email.lower()
    _check_can_invite(db, site, owner, email)
    invite = SiteInvite(site_id=site.id, inviter_user_id=owner.id, email=email)
    db.add(invite)
    db.commit()
    db.refresh(invite)
    subject, body = _invite_email(owner, site)
    background_tasks.add_task(try_send_email, email_sender, email, subject, body, "site-invite")
    return invite


@router.get("", response_model=list[InviteOut])
def list_invites(
    site: Site = Depends(require_site_access),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> list[SiteInvite]:
    query = select(SiteInvite).where(SiteInvite.site_id == site.id)
    if site.owner_user_id != user.id:
        query = query.where(SiteInvite.invitee_user_id == user.id)
    query = query.order_by(SiteInvite.created_at.desc(), SiteInvite.id.desc())
    return list(db.execute(query).scalars().all())


def _is_for(invite: SiteInvite, user: User) -> bool:
    return user.email_confirmed and invite.email == user.email.lower()


def _get_open_invite(db: Session, user: User, invite_id: int) -> SiteInvite:
    """404 unless the invite is addressed to this confirmed user; 409 unless still invited."""
    invite = db.get(SiteInvite, invite_id)
    if invite is None or not _is_for(invite, user):
        raise AppError(404, "invite_not_found", "Invitation not found")
    if invite.status != InviteStatus.INVITED:
        raise AppError(409, "invite_not_pending", "This invitation has already been answered or withdrawn")
    return invite


@recipient_router.get("/pending", response_model=list[PendingInviteOut])
def list_pending_invites(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> list[PendingInviteOut]:
    if not user.email_confirmed:
        return []
    rows = db.execute(
        select(SiteInvite, Site.name, User.email)
        .join(Site, Site.id == SiteInvite.site_id)
        .join(User, User.id == SiteInvite.inviter_user_id)
        .where(SiteInvite.email == user.email.lower(), SiteInvite.status == InviteStatus.INVITED)
        .order_by(SiteInvite.created_at.desc(), SiteInvite.id.desc())
    ).all()
    return [
        PendingInviteOut(
            id=invite.id,
            site_id=invite.site_id,
            site_name=site_name,
            inviter_email=inviter_email,
            created_at=invite.created_at,
        )
        for invite, site_name, inviter_email in rows
    ]


@recipient_router.post("/{invite_id}/accept", response_model=InviteOut)
def accept_invite(
    invite_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> SiteInvite:
    invite = _get_open_invite(db, user, invite_id)
    invite.status = InviteStatus.ACCEPTED
    invite.accepted_at = datetime.datetime.now(datetime.UTC)
    invite.invitee_user_id = user.id
    if db.get(SiteAccess, (invite.site_id, user.id)) is None:
        db.add(SiteAccess(site_id=invite.site_id, user_id=user.id))
    db.commit()
    db.refresh(invite)
    return invite


@recipient_router.post("/{invite_id}/decline", response_model=InviteOut)
def decline_invite(
    invite_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> SiteInvite:
    invite = _get_open_invite(db, user, invite_id)
    invite.status = InviteStatus.DECLINED
    invite.declined_at = datetime.datetime.now(datetime.UTC)
    invite.invitee_user_id = user.id
    db.commit()
    db.refresh(invite)
    return invite
