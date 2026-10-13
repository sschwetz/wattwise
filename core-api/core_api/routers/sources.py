from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_api.deps import get_current_user, get_db_session, require_site_access, require_site_owner
from core_api.errors import AppError
from core_api.models.identity import Site, User
from core_api.models.sources import Source, SourceKind
from core_api.schemas import SourceCreate, SourceOut, SourceUpdate
from core_api.time_utils import as_utc

router = APIRouter(prefix="/sites/{site_id}/sources", tags=["sources"])

# A site has exactly one active pricing source at a time (spec: 'Sources'
# invariants) — ranges are enforced application-side since SQLite has no
# native range-exclusion constraint.
_FAR_FUTURE = datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)


def _pricing_source_overlaps(
    db: Session,
    site_id: int,
    exclude_id: int | None,
    effective_from: datetime.datetime,
    effective_to: datetime.datetime | None,
) -> bool:
    query = select(Source).where(Source.site_id == site_id, Source.kind == SourceKind.PRICING)
    if exclude_id is not None:
        query = query.where(Source.id != exclude_id)
    new_to = effective_to or _FAR_FUTURE
    for existing in db.execute(query).scalars():
        existing_from = as_utc(existing.effective_from)
        existing_to = as_utc(existing.effective_to) if existing.effective_to else _FAR_FUTURE
        if effective_from < existing_to and existing_from < new_to:
            return True
    return False


@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def create_source(
    payload: SourceCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> SourceOut:
    now = datetime.datetime.now(datetime.timezone.utc)
    if payload.kind == SourceKind.PRICING and _pricing_source_overlaps(db, site.id, None, now, None):
        raise AppError(409, "conflict", "This site already has an active pricing source; close it before adding a new one")
    source = Source(
        site_id=site.id,
        kind=payload.kind,
        provider=payload.provider,
        config=payload.config,
        name=payload.name,
        effective_from=now,
    )
    db.add(source)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "This site already has an active source of this kind") from exc
    db.refresh(source)
    return _to_source_out(source, include_secrets=True)


# Device/provider credentials living in a source's config (an envoy source's
# local-API token and the Enlighten username/password the collector uses to
# renew it) must only reach the site owner: a read-only collaborator's
# SiteAccess grant is meant to expose energy data, not a credential that can
# reach the owner's device or Enphase account.
_SENSITIVE_CONFIG_KEYS = {"token", "api_key", "password", "secret", "username"}


def _to_source_out(source: Source, *, include_secrets: bool) -> SourceOut:
    renewal_url = None
    if source.provider == "envoy":
        serial_num = (source.config or {}).get("serial_num")
        if serial_num:
            renewal_url = f"https://enlighten.enphaseenergy.com/entrez-auth-token?serial_num={serial_num}"
    config = source.config or {}
    if not include_secrets:
        config = {k: v for k, v in config.items() if k not in _SENSITIVE_CONFIG_KEYS}
    return SourceOut(
        id=source.id,
        site_id=source.site_id,
        kind=source.kind,
        provider=source.provider,
        name=source.name,
        enabled=source.enabled,
        effective_from=source.effective_from,
        effective_to=source.effective_to,
        renewal_url=renewal_url,
        config=config,
    )


def _is_owner(site: Site, user: User) -> bool:
    return site.owner_user_id == user.id


@router.get("", response_model=list[SourceOut])
def list_sources(
    site: Site = Depends(require_site_access),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> list[SourceOut]:
    sources = db.execute(select(Source).where(Source.site_id == site.id)).scalars().all()
    include_secrets = _is_owner(site, current_user)
    return [_to_source_out(s, include_secrets=include_secrets) for s in sources]


@router.get("/{source_id}", response_model=SourceOut)
def get_source(
    source_id: int,
    site: Site = Depends(require_site_access),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> SourceOut:
    source = db.get(Source, source_id)
    if source is None or source.site_id != site.id:
        raise AppError(404, "not_found", f"Source {source_id} not found")
    include_secrets = _is_owner(site, current_user)
    return _to_source_out(source, include_secrets=include_secrets)


@router.patch("/{source_id}", response_model=SourceOut)
def update_source(
    source_id: int,
    payload: SourceUpdate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> SourceOut:
    source = db.get(Source, source_id)
    if source is None or source.site_id != site.id:
        raise AppError(404, "not_found", f"Source {source_id} not found")
    if payload.enabled is not None:
        source.enabled = payload.enabled
    if payload.name is not None:
        source.name = payload.name
    if payload.effective_to is not None:
        if source.kind == SourceKind.PRICING and _pricing_source_overlaps(
            db, source.site_id, source.id, as_utc(source.effective_from), payload.effective_to
        ):
            raise AppError(409, "conflict", "That effective_to would overlap another pricing source for this site")
        source.effective_to = payload.effective_to
    if payload.config is not None:
        merged_config = {**(source.config or {}), **payload.config}
        new_host = payload.config.get("host")
        if new_host is not None and new_host != (source.config or {}).get("host"):
            # The collector's cert_fingerprint/serial_num are pinned to the
            # PREVIOUS host's device identity -- carrying them over would
            # make it reject (or misattribute) a legitimately repointed
            # device forever instead of re-discovering/re-pinning it.
            merged_config.pop("cert_fingerprint", None)
            merged_config.pop("cert_expires_at", None)
            merged_config.pop("cert_serial", None)
            # A serial_num the owner changed in this same save is for the new
            # device. One that matches the stored value is the webapp form
            # resending the old device's serial, so it still gets cleared.
            old_serial = (source.config or {}).get("serial_num")
            if payload.config.get("serial_num") in (None, old_serial):
                merged_config.pop("serial_num", None)
        source.config = merged_config
    db.commit()
    db.refresh(source)
    return _to_source_out(source, include_secrets=True)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    source_id: int,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> None:
    source = db.get(Source, source_id)
    if source is None or source.site_id != site.id:
        raise AppError(404, "not_found", f"Source {source_id} not found")
    db.delete(source)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            409,
            "source_has_readings",
            "This source has existing readings and cannot be deleted. Disable it instead.",
        ) from exc
