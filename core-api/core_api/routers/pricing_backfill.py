from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, get_source_by_collector_key, require_source_kind
from core_api.models.pricing import PriceChannel, PriceGapMarker, PriceInterval, PriceReading
from core_api.models.readings import EnergyReading, WeatherReading
from core_api.models.sources import Source, SourceKind
from core_api.pricing_gaps import coalesce_missing_days, missing_days_in_range
from core_api.schemas import GapMarkerCreate, SiteDataFloorOut, UtcDatetime

router = APIRouter(prefix="/sources/{source_id}/prices", tags=["pricing-backfill"])


@router.post("/gap-markers", status_code=status.HTTP_204_NO_CONTENT)
def create_gap_marker(
    payload: GapMarkerCreate,
    source: Source = Depends(require_source_kind(SourceKind.PRICING)),
    db: Session = Depends(get_db_session),
) -> None:
    stmt = sqlite_insert(PriceGapMarker).values(
        pricing_source_id=source.id, channel=payload.channel, gap_date=payload.gap_date
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["pricing_source_id", "channel", "gap_date"])
    db.execute(stmt)
    db.commit()


@router.get("/gaps")
def get_price_gaps(
    since: UtcDatetime,
    source: Source = Depends(require_source_kind(SourceKind.PRICING)),
    db: Session = Depends(get_db_session),
) -> dict:
    since_date = since.date()
    today = datetime.datetime.now(datetime.timezone.utc).date()

    channels = list(
        db.execute(
            select(PriceInterval.channel).where(PriceInterval.pricing_source_id == source.id).distinct()
        ).scalars()
    )

    gaps = []
    for channel in channels:
        covered_days = {
            ts.date()
            for ts in db.execute(
                select(PriceInterval.interval_start).where(
                    PriceInterval.pricing_source_id == source.id,
                    PriceInterval.channel == channel,
                    PriceInterval.interval_start >= since,
                )
            ).scalars()
        }
        marked_days = set(
            db.execute(
                select(PriceGapMarker.gap_date).where(
                    PriceGapMarker.pricing_source_id == source.id,
                    PriceGapMarker.channel == channel,
                )
            ).scalars()
        )
        missing = missing_days_in_range(since_date, today, covered_days, marked_days)
        for run_start, run_end in coalesce_missing_days(missing):
            gaps.append({"channel": channel.value, "start_date": run_start.isoformat(), "end_date": run_end.isoformat()})

    return {"gaps": gaps}


site_router = APIRouter(prefix="/sources/{source_id}", tags=["pricing-backfill"])


@site_router.get("/site-data-floor", response_model=SiteDataFloorOut)
def get_site_data_floor(
    source: Source = Depends(get_source_by_collector_key), db: Session = Depends(get_db_session)
) -> SiteDataFloorOut:
    source_ids = list(db.execute(select(Source.id).where(Source.site_id == source.site_id)).scalars().all())
    if not source_ids:
        return SiteDataFloorOut(floor_ts=None)

    candidates = []
    for model, key_col in (
        (EnergyReading, EnergyReading.source_id),
        (WeatherReading, WeatherReading.source_id),
        (PriceReading, PriceReading.pricing_source_id),
    ):
        earliest = db.execute(select(func.min(model.ts)).where(key_col.in_(source_ids))).scalar()
        if earliest is not None:
            candidates.append(earliest)

    if not candidates:
        return SiteDataFloorOut(floor_ts=None)
    return SiteDataFloorOut(floor_ts=min(candidates))
