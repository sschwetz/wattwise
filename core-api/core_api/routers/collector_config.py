from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, get_source_by_collector_key
from core_api.models.identity import Site, User
from core_api.models.sources import Source
from core_api.schemas import DiscoveredSourceConfig, SourceConfigOut

router = APIRouter(prefix="/sources/{source_id}", tags=["collector-config"])


def resolve_source_config(db: Session, source: Source) -> dict:
    config = dict(source.config or {})
    if source.provider == "amber":
        site = db.get(Site, source.site_id)
        owner = db.get(User, site.owner_user_id)
        config["amber_api_key"] = owner.amber_api_key
    return config


@router.get("/config", response_model=SourceConfigOut)
def get_source_config(
    source: Source = Depends(get_source_by_collector_key), db: Session = Depends(get_db_session)
) -> SourceConfigOut:
    return SourceConfigOut(config=resolve_source_config(db, source))


@router.patch("/discovered-config", status_code=status.HTTP_204_NO_CONTENT)
def report_discovered_config(
    payload: DiscoveredSourceConfig,
    source: Source = Depends(get_source_by_collector_key),
    db: Session = Depends(get_db_session),
) -> None:
    discovered = payload.model_dump(exclude_none=True)
    source.config = {**(source.config or {}), **discovered}
    db.commit()
