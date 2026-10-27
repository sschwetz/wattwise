from __future__ import annotations

import collections
import datetime
import math
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core_api.jobs.time_buckets import floor_to_5min
from core_api.models.pricing import PRICE_CHANNEL_AGGREGATION, PriceReading, PriceReadingRollup5m
from core_api.models.readings import (
    ENERGY_METRIC_AGGREGATION,
    WEATHER_METRIC_AGGREGATION,
    AggregationMethod,
    EnergyReading,
    EnergyReadingRollup5m,
    WeatherReading,
    WeatherReadingRollup5m,
)
from core_api.readings_query import RAW_RETENTION
from core_api.time_utils import as_utc


def run_retention_rollup_job(db: Session, now: datetime.datetime) -> None:
    # Floored to a 5-minute boundary so the cutoff can never fall inside a
    # bucket: a straddling cutoff would roll up (and delete) only part of a
    # bucket, and the next run's upsert would replace that partial average
    # with one computed from the remaining rows alone, losing the deleted
    # rows' contribution for good.
    cutoff = floor_to_5min(as_utc(now) - RAW_RETENTION)
    _rollup_and_delete(db, EnergyReading, EnergyReadingRollup5m, "source_id", "metric", ENERGY_METRIC_AGGREGATION, cutoff)
    _rollup_and_delete(db, WeatherReading, WeatherReadingRollup5m, "source_id", "metric", WEATHER_METRIC_AGGREGATION, cutoff)
    _rollup_and_delete(
        db, PriceReading, PriceReadingRollup5m, "pricing_source_id", "channel", PRICE_CHANNEL_AGGREGATION, cutoff
    )
    db.commit()


def _rollup_and_delete(
    db: Session,
    model_raw: type[Any],
    model_rollup: type[Any],
    key_column_name: str,
    discriminator_column_name: str,
    aggregation_map: dict,
    cutoff: datetime.datetime,
) -> None:
    rows = db.execute(select(model_raw).where(model_raw.ts < cutoff)).scalars().all()
    if not rows:
        return

    buckets: dict[tuple, list] = collections.defaultdict(list)
    for row in rows:
        key_value = getattr(row, key_column_name)
        discriminator_value = getattr(row, discriminator_column_name)
        bucket_ts = floor_to_5min(as_utc(row.ts))
        buckets[(key_value, bucket_ts, discriminator_value)].append(row)

    for (key_value, bucket_ts, discriminator_value), group in buckets.items():
        method = aggregation_map[discriminator_value]
        if method == AggregationMethod.AVERAGE:
            value = sum(row.value for row in group) / len(group)
        elif method == AggregationMethod.CIRCULAR_MEAN:
            radians = [math.radians(row.value) for row in group]
            sin_sum = sum(math.sin(r) for r in radians)
            cos_sum = sum(math.cos(r) for r in radians)
            value = math.degrees(math.atan2(sin_sum, cos_sum)) % 360
        else:
            value = max(group, key=lambda row: row.ts).value

        stmt = sqlite_insert(model_rollup).values(
            **{
                key_column_name: key_value,
                "ts": bucket_ts,
                discriminator_column_name: discriminator_value,
                "value": value,
            }
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[key_column_name, "ts", discriminator_column_name],
            set_={"value": stmt.excluded.value},
        )
        db.execute(stmt)

    db.execute(delete(model_raw).where(model_raw.ts < cutoff), execution_options={"synchronize_session": False})
