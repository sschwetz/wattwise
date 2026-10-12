from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Header
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from core_api.deps import check_collector_key, get_db_session
from core_api.errors import AppError
from core_api.models.sources import Source, SourceKind
from core_api.routers.collector_config import resolve_source_config
from core_api.schemas import CollectorSourceOut

router = APIRouter(prefix="/collector", tags=["collector-config"])


@router.get("/sources", response_model=list[CollectorSourceOut])
def list_collector_sources(
    kind: SourceKind,
    x_collector_key: str = Header(...),
    db: Session = Depends(get_db_session),
) -> list[CollectorSourceOut]:
    if not check_collector_key(kind, x_collector_key):
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this kind")
    now = datetime.datetime.now(datetime.timezone.utc)
    sources = db.execute(
        select(Source).where(
            Source.kind == kind,
            Source.enabled.is_(True),
            or_(Source.effective_to.is_(None), Source.effective_to > now),
        )
    ).scalars().all()
    return [
        CollectorSourceOut(source_id=s.id, site_id=s.site_id, config=resolve_source_config(db, s))
        for s in sources
    ]
