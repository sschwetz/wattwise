from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

# Matches the design spec's 1-year raw retention window (see 'Retention & rollup').
RAW_RETENTION = datetime.timedelta(days=365)


def _query_one_table(
    db: Session,
    model: type[Any],
    key_column: InstrumentedAttribute,
    key_value: int,
    ts_start: datetime.datetime,
    ts_end: datetime.datetime,
    metric_column: InstrumentedAttribute | None,
    metric_value,
) -> list:
    # key_column/metric_column are always passed bound to model_raw (see
    # callers), but when `model` is model_rollup they must be rebound to
    # that class's same-named attribute — otherwise the query references two
    # unrelated tables (model_rollup from `select(model)`, model_raw from the
    # bound column) and SQLAlchemy emits a cartesian product between them
    # instead of filtering the intended table.
    key_col = getattr(model, key_column.key)
    stmt = select(model).where(key_col == key_value, model.ts >= ts_start, model.ts <= ts_end)
    if metric_column is not None:
        metric_col = getattr(model, metric_column.key)
        stmt = stmt.where(metric_col == metric_value)
    return list(db.execute(stmt).scalars().all())


def query_readings(
    db: Session,
    model_raw: type,
    model_rollup: type,
    *,
    key_column: InstrumentedAttribute,
    key_value: int,
    ts_start: datetime.datetime,
    ts_end: datetime.datetime,
    metric_column: InstrumentedAttribute | None = None,
    metric_value=None,
) -> list:
    """Which table(s) does this range span? A range can straddle the raw
    retention cutoff (e.g. [now-2years, now]) — the portion at or after the
    cutoff must be read from raw, and the portion before it from rollup.
    Querying only one table based on ts_start alone would silently drop the
    raw-side data for any range that starts more than RAW_RETENTION ago."""
    cutoff = datetime.datetime.now(datetime.UTC) - RAW_RETENTION
    results: list = []
    if ts_end >= cutoff:
        raw_window_start = max(ts_start, cutoff)
        results.extend(
            _query_one_table(
                db, model_raw, key_column, key_value, raw_window_start, ts_end, metric_column, metric_value
            )
        )
    if ts_start < cutoff:
        rollup_window_end = min(ts_end, cutoff)
        results.extend(
            _query_one_table(
                db, model_rollup, key_column, key_value, ts_start, rollup_window_end, metric_column, metric_value
            )
        )
    return results
