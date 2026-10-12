from __future__ import annotations

import dataclasses
import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core_api.jobs.demand_config import (
    any_configured_demand_window,
    energy_source_ids_for_site,
    first_matching_demand_window,
    price_interval_flags_demand,
    pricing_source_ids_for_site,
)
from core_api.jobs.time_buckets import BUCKET_WIDTH, floor_to_5min
from core_api.models.billing import DemandInterval, DemandWindow
from core_api.models.identity import Site
from core_api.models.readings import EnergyMetric, EnergyReading
from core_api.time_utils import as_utc

# How far back to recompute already-derived buckets on every run, so a
# backfilled/late-arriving EnergyReading lands in a bucket that gets
# re-upserted rather than being silently stranded past the forward resume
# point (I4). Cheap to redo given the upsert is idempotent; 3h comfortably
# covers a collector restart without re-walking the whole history each run.
RE_DERIVE_WINDOW = datetime.timedelta(hours=3)


@dataclasses.dataclass(frozen=True)
class _SlotInfo:
    raw_avg_w: float
    is_flagged: bool
    window: DemandWindow | None


def run_demand_interval_job(db: Session, now: datetime.datetime) -> None:
    for site in db.execute(select(Site)).scalars():
        _derive_for_site(db, site, now)
    db.commit()


def _raw_avg_w(db: Session, energy_source_ids: list[int], slot_start: datetime.datetime, slot_end: datetime.datetime) -> float:
    avg_w = db.execute(
        select(func.avg(EnergyReading.value)).where(
            EnergyReading.source_id.in_(energy_source_ids),
            EnergyReading.metric == EnergyMetric.GRID_IMPORT_W,
            EnergyReading.ts >= slot_start,
            EnergyReading.ts < slot_end,
        )
    ).scalar()
    return float(avg_w) if avg_w is not None else 0.0


def _slot_info(
    db: Session, site: Site, energy_source_ids: list[int], pricing_source_ids: list[int], slot_start: datetime.datetime
) -> _SlotInfo:
    slot_end = slot_start + BUCKET_WIDTH
    raw = _raw_avg_w(db, energy_source_ids, slot_start, slot_end)
    window = first_matching_demand_window(db, site.id, slot_start, site.timezone)
    is_flagged = window is not None or price_interval_flags_demand(db, pricing_source_ids, slot_start, slot_end)
    return _SlotInfo(raw_avg_w=raw, is_flagged=is_flagged, window=window)


def _rolling_n_for(db: Session, site: Site, bucket_start: datetime.datetime, info: _SlotInfo) -> int:
    """How many 5-minute slots the rolling sum for this slot spans.

    A slot flagged only by an is_demand_window PriceInterval has no matching
    DemandWindow pattern, but Job 2 still derives its N from
    any_configured_demand_window when finalizing — so N must come from the
    same place here, or the rolling sum is understated by that factor."""
    if info.window is not None:
        return info.window.measurement_interval_minutes // 5
    if info.is_flagged:
        fallback_window = any_configured_demand_window(db, site.id, bucket_start)
        if fallback_window is not None:
            return fallback_window.measurement_interval_minutes // 5
    return 1


def _derive_for_site(db: Session, site: Site, now: datetime.datetime) -> None:
    energy_source_ids = energy_source_ids_for_site(db, site.id)
    if not energy_source_ids:
        return
    pricing_source_ids = pricing_source_ids_for_site(db, site.id)

    earliest = db.execute(
        select(func.min(EnergyReading.ts)).where(EnergyReading.source_id.in_(energy_source_ids))
    ).scalar()

    latest = db.execute(
        select(func.max(DemandInterval.interval_start)).where(DemandInterval.site_id == site.id)
    ).scalar()
    last_allowed_start = floor_to_5min(now - BUCKET_WIDTH)
    if latest is not None:
        resume_start = floor_to_5min(as_utc(latest)) + BUCKET_WIDTH
        rederive_start = last_allowed_start - RE_DERIVE_WINDOW
        if earliest is None:
            bucket_start = resume_start
        else:
            bucket_start = max(floor_to_5min(as_utc(earliest)), min(resume_start, rederive_start))
    else:
        if earliest is None:
            return
        bucket_start = floor_to_5min(as_utc(earliest))

    info_cache: dict[datetime.datetime, _SlotInfo] = {}

    def info_at(slot_start: datetime.datetime) -> _SlotInfo:
        if slot_start not in info_cache:
            info_cache[slot_start] = _slot_info(db, site, energy_source_ids, pricing_source_ids, slot_start)
        return info_cache[slot_start]

    def contribution_at(slot_start: datetime.datetime) -> float:
        info = info_at(slot_start)
        return info.raw_avg_w if info.is_flagged else 0.0

    while bucket_start <= last_allowed_start:
        bucket_end = bucket_start + BUCKET_WIDTH
        info = info_at(bucket_start)
        rolling_n = _rolling_n_for(db, site, bucket_start, info)
        slots = [bucket_start - i * BUCKET_WIDTH for i in range(rolling_n)]
        rolling_sum_w = sum(contribution_at(slot) for slot in slots)
        rate_applied = info.window.rate_c_per_kw_per_day if (info.window is not None and info.is_flagged) else None

        stmt = sqlite_insert(DemandInterval).values(
            site_id=site.id,
            interval_start=bucket_start,
            interval_end=bucket_end,
            rolling_sum_w=rolling_sum_w,
            is_demand_window=info.is_flagged,
            rate_applied=rate_applied,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["site_id", "interval_start"],
            set_={
                "interval_end": stmt.excluded.interval_end,
                "rolling_sum_w": stmt.excluded.rolling_sum_w,
                "is_demand_window": stmt.excluded.is_demand_window,
                "rate_applied": stmt.excluded.rate_applied,
            },
        )
        db.execute(stmt)
        bucket_start = bucket_end
