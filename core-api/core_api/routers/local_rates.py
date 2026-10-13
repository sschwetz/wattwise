from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, require_site_access, require_site_owner
from core_api.errors import AppError
from core_api.local_rates import find_overlap
from core_api.models.identity import Site
from core_api.models.local_rates import LocalRateWindow
from core_api.models.sources import Source, SourceKind
from core_api.schemas import LocalRateWindowCreate, LocalRateWindowOut

router = APIRouter(prefix="/sites/{site_id}/sources/{source_id}/rate-windows", tags=["local-rates"])


def _get_local_source(db: Session, site: Site, source_id: int) -> Source:
    source = db.get(Source, source_id)
    if source is None or source.site_id != site.id:
        raise AppError(404, "not_found", f"Source {source_id} not found")
    if source.kind != SourceKind.PRICING or source.provider != "local":
        raise AppError(422, "not_a_local_source", "Rate windows can only be added to a local pricing source")
    return source


def _get_window(db: Session, source: Source, window_id: int) -> LocalRateWindow:
    window = db.get(LocalRateWindow, window_id)
    if window is None or window.source_id != source.id:
        raise AppError(404, "not_found", f"Rate window {window_id} not found")
    return window


def _reject_overlap(
    db: Session, source: Source, payload: LocalRateWindowCreate, exclude_id: int | None = None
) -> None:
    same_channel = db.execute(
        select(LocalRateWindow).where(
            LocalRateWindow.source_id == source.id, LocalRateWindow.channel == payload.channel
        )
    ).scalars().all()
    clash = find_overlap(payload, [w for w in same_channel if w.id != exclude_id])
    if clash is not None:
        raise AppError(
            422,
            "rate_window_overlap",
            f'This window overlaps "{clash.label}" (id {clash.id}) on the same channel',
        )


@router.post("", response_model=LocalRateWindowOut, status_code=status.HTTP_201_CREATED)
def create_rate_window(
    source_id: int,
    payload: LocalRateWindowCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> LocalRateWindow:
    source = _get_local_source(db, site, source_id)
    _reject_overlap(db, source, payload)
    window = LocalRateWindow(source_id=source.id, **payload.model_dump())
    db.add(window)
    db.commit()
    db.refresh(window)
    return window


@router.get("", response_model=list[LocalRateWindowOut])
def list_rate_windows(
    source_id: int,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
) -> list[LocalRateWindow]:
    source = _get_local_source(db, site, source_id)
    query = (
        select(LocalRateWindow)
        .where(LocalRateWindow.source_id == source.id)
        .order_by(LocalRateWindow.channel, LocalRateWindow.effective_from, LocalRateWindow.start_time)
    )
    return db.execute(query).scalars().all()


@router.patch("/{window_id}", response_model=LocalRateWindowOut)
def replace_rate_window(
    source_id: int,
    window_id: int,
    payload: LocalRateWindowCreate,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> LocalRateWindow:
    """Replaces every field. Sending the full window keeps the overlap check
    simple: the merged result is exactly the payload."""
    source = _get_local_source(db, site, source_id)
    window = _get_window(db, source, window_id)
    _reject_overlap(db, source, payload, exclude_id=window.id)
    for field, value in payload.model_dump().items():
        setattr(window, field, value)
    db.commit()
    db.refresh(window)
    return window


@router.delete("/{window_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rate_window(
    source_id: int,
    window_id: int,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
) -> None:
    source = _get_local_source(db, site, source_id)
    db.delete(_get_window(db, source, window_id))
    db.commit()
