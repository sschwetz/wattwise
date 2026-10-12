from __future__ import annotations

import datetime
from collections.abc import Sequence

from fastapi import APIRouter, Depends, status
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, require_source_kind
from core_api.models.pricing import PriceInterval, PriceReading
from core_api.models.readings import EnergyReading, WeatherReading
from core_api.models.sources import Source, SourceKind
from core_api.schemas import (
    EnergyReadingBatch,
    EnergyReadingIn,
    PriceIntervalBatch,
    PriceIntervalIn,
    WeatherReadingBatch,
    WeatherReadingIn,
)

router = APIRouter(prefix="/sources/{source_id}", tags=["ingestion"])


def _upsert_readings(
    db: Session,
    model: type[EnergyReading] | type[WeatherReading],
    source_id: int,
    readings: Sequence[EnergyReadingIn] | Sequence[WeatherReadingIn],
) -> int:
    """Upsert a batch of readings keyed by (source_id, ts, metric), last write wins.

    Shared by both energy and weather ingestion below, since both readings shapes
    are (source_id, ts, metric, value) — see the mixins in models/readings.py.
    """
    for reading in readings:
        stmt = sqlite_insert(model).values(
            source_id=source_id, ts=reading.ts, metric=reading.metric, value=reading.value
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id", "ts", "metric"], set_={"value": stmt.excluded.value}
        )
        db.execute(stmt)
    return len(readings)


@router.post("/readings/energy", status_code=status.HTTP_202_ACCEPTED)
def ingest_energy_readings(
    batch: EnergyReadingBatch,
    source: Source = Depends(require_source_kind(SourceKind.ENERGY)),
    db: Session = Depends(get_db_session),
) -> dict:
    count = _upsert_readings(db, EnergyReading, source.id, batch.readings)
    db.commit()
    return {"ingested": count}


@router.post("/readings/weather", status_code=status.HTTP_202_ACCEPTED)
def ingest_weather_readings(
    batch: WeatherReadingBatch,
    source: Source = Depends(require_source_kind(SourceKind.WEATHER)),
    db: Session = Depends(get_db_session),
) -> dict:
    count = _upsert_readings(db, WeatherReading, source.id, batch.readings)
    db.commit()
    return {"ingested": count}


def _upsert_price_interval(db: Session, source_id: int, interval: PriceIntervalIn) -> None:
    stmt = sqlite_insert(PriceInterval).values(
        pricing_source_id=source_id,
        channel=interval.channel,
        interval_start=interval.interval_start,
        interval_end=interval.interval_end,
        price=interval.price,
        is_demand_window=interval.is_demand_window,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["pricing_source_id", "channel", "interval_start"],
        set_={
            "interval_end": stmt.excluded.interval_end,
            "price": stmt.excluded.price,
            "is_demand_window": stmt.excluded.is_demand_window,
        },
    )
    db.execute(stmt)


def _fan_out_price_readings(db: Session, source_id: int, interval: PriceIntervalIn) -> int:
    """Upsert one PriceReading per minute covered by the interval, [start, end).

    The start is floored to the whole minute: providers stagger interval_start by a
    second (Amber publishes e.g. 23:55:01Z), which PriceInterval stores verbatim, but
    cost.py joins EnergyReading to PriceReading on exact ts equality — so a derived
    reading at :01s could never match a whole-minute energy reading.
    """
    fanned_out = 0
    minute = interval.interval_start.replace(second=0, microsecond=0)
    while minute < interval.interval_end:
        reading_stmt = sqlite_insert(PriceReading).values(
            pricing_source_id=source_id, ts=minute, channel=interval.channel, value=interval.price
        )
        reading_stmt = reading_stmt.on_conflict_do_update(
            index_elements=["pricing_source_id", "ts", "channel"],
            set_={"value": reading_stmt.excluded.value},
        )
        db.execute(reading_stmt)
        fanned_out += 1
        minute += datetime.timedelta(minutes=1)
    return fanned_out


@router.post("/prices", status_code=status.HTTP_202_ACCEPTED)
def ingest_prices(
    batch: PriceIntervalBatch,
    source: Source = Depends(require_source_kind(SourceKind.PRICING)),
    db: Session = Depends(get_db_session),
) -> dict:
    fanned_out = 0
    for interval in batch.intervals:
        _upsert_price_interval(db, source.id, interval)
        fanned_out += _fan_out_price_readings(db, source.id, interval)
    db.commit()
    return {"ingested": len(batch.intervals), "readings_fanned_out": fanned_out}
