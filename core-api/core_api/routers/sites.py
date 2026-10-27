from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.deps import get_current_user, get_db_session, require_site_access, require_site_owner
from core_api.models.identity import Site, SiteAccess, User
from core_api.schemas import SiteCreate, SiteOut, SiteUpdate

router = APIRouter(prefix="/sites", tags=["sites"])


@router.post("", response_model=SiteOut, status_code=status.HTTP_201_CREATED)
def create_site(
    payload: SiteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> Site:
    site = Site(owner_user_id=current_user.id, name=payload.name, timezone=payload.timezone)
    db.add(site)
    db.commit()
    db.refresh(site)
    return site


@router.get("", response_model=list[SiteOut])
def list_sites(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db_session)
) -> list[Site]:
    owned = select(Site).where(Site.owner_user_id == current_user.id)
    shared = (
        select(Site)
        .join(SiteAccess, SiteAccess.site_id == Site.id)
        .where(SiteAccess.user_id == current_user.id)
    )
    owned_sites = db.execute(owned).scalars().all()
    shared_sites = db.execute(shared).scalars().all()
    by_id = {s.id: s for s in (*owned_sites, *shared_sites)}
    return list(by_id.values())


@router.get("/{site_id}", response_model=SiteOut)
def get_site(site: Site = Depends(require_site_access)) -> Site:
    return site


@router.patch("/{site_id}", response_model=SiteOut)
def update_site(
    payload: SiteUpdate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> Site:
    if payload.name is not None:
        site.name = payload.name
    if payload.timezone is not None:
        site.timezone = payload.timezone
    if payload.enabled is not None:
        site.enabled = payload.enabled
    db.commit()
    db.refresh(site)
    return site
