from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, get_source_by_collector_key
from core_api.errors import AppError
from core_api.models.identity import Site, User
from core_api.models.local_rates import LocalRateWindow, LocalRepriceMarker
from core_api.models.sources import Source
from core_api.schemas import DiscoveredSourceConfig, SourceConfigOut
from core_api.time_utils import as_utc

router = APIRouter(prefix="/sources/{source_id}", tags=["collector-config"])


def _rate_window_config(window: LocalRateWindow) -> dict:
    return {
        "channel": window.channel.value,
        "effective_from": as_utc(window.effective_from).isoformat(),
        "effective_to": as_utc(window.effective_to).isoformat() if window.effective_to else None,
        "months": window.months,
        "days_of_week": window.days_of_week,
        "start_time": window.start_time.isoformat(),
        "end_time": window.end_time.isoformat(),
        "rate_c_per_kwh": str(window.rate_c_per_kwh),
    }


def _local_config(db: Session, source: Source, config: dict) -> None:
    site = db.get(Site, source.site_id)
    # Read the marker before the windows. If an owner's edit commits between
    # the two reads, the collector then gets new windows with the old version:
    # its clear is ignored and the next run reprices again. The other order
    # would give old windows with the new version, and the clear would match.
    marker = db.get(LocalRepriceMarker, source.id)
    windows = db.execute(
        select(LocalRateWindow).where(LocalRateWindow.source_id == source.id).order_by(LocalRateWindow.id)
    ).scalars().all()
    if site is None:
        raise AppError(404, "not_found", "Site not found")
    config["timezone"] = site.timezone
    config["rate_windows"] = [_rate_window_config(w) for w in windows]
    if marker is not None:
        config["reprice"] = {"from": as_utc(marker.reprice_from).isoformat(), "version": marker.version}


def resolve_source_config(db: Session, source: Source) -> dict:
    if source.provider == "local":
        # The local collector reads exactly {timezone, rate_windows, reprice},
        # with reprice present only while a marker exists, so any stray keys
        # in source.config are left out.
        config: dict = {}
        _local_config(db, source, config)
        return config
    config = dict(source.config or {})
    if source.provider == "amber":
        site = db.get(Site, source.site_id)
        owner = db.get(User, site.owner_user_id) if site else None
        config["amber_api_key"] = owner.amber_api_key if owner else None
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
    discovered = payload.model_dump(mode="json", exclude_none=True)
    source.config = {**(source.config or {}), **discovered}
    db.commit()
