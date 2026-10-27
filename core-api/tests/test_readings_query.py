# tests/test_readings_query.py
from __future__ import annotations

import datetime

from core_api.models.readings import EnergyMetric, EnergyReading, EnergyReadingRollup5m
from core_api.models.sources import Source, SourceKind
from core_api.readings_query import RAW_RETENTION, query_readings

UTC = datetime.UTC


def test_query_readings_uses_raw_table_within_retention_window(db, site):
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()

    recent_ts = datetime.datetime.now(UTC) - datetime.timedelta(days=1)
    db.add(EnergyReading(source_id=source.id, ts=recent_ts, metric=EnergyMetric.PRODUCTION_W, value=100.0))
    db.commit()

    rows = query_readings(
        db,
        EnergyReading,
        EnergyReadingRollup5m,
        key_column=EnergyReading.source_id,
        key_value=source.id,
        ts_start=recent_ts - datetime.timedelta(minutes=1),
        ts_end=recent_ts + datetime.timedelta(minutes=1),
    )
    assert len(rows) == 1
    assert rows[0].value == 100.0


def test_query_readings_uses_rollup_table_beyond_retention_window(db, site):
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()

    old_ts = datetime.datetime.now(UTC) - RAW_RETENTION - datetime.timedelta(days=1)
    db.add(
        EnergyReadingRollup5m(source_id=source.id, ts=old_ts, metric=EnergyMetric.PRODUCTION_W, value=50.0)
    )
    db.commit()

    rows = query_readings(
        db,
        EnergyReading,
        EnergyReadingRollup5m,
        key_column=EnergyReading.source_id,
        key_value=source.id,
        ts_start=old_ts - datetime.timedelta(minutes=1),
        ts_end=old_ts + datetime.timedelta(minutes=1),
    )
    assert len(rows) == 1
    assert rows[0].value == 50.0


def test_query_readings_range_spanning_cutoff_still_returns_raw_data(db, site):
    """Finding 2/C1: a range like [now-400days, now] must not silently
    route entirely to the (currently always-empty) rollup table just
    because ts_start predates the retention cutoff — the raw-side portion
    of the range must still be queried and returned."""
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()

    recent_ts = datetime.datetime.now(UTC) - datetime.timedelta(days=7)
    db.add(EnergyReading(source_id=source.id, ts=recent_ts, metric=EnergyMetric.PRODUCTION_W, value=42.0))
    db.commit()

    rows = query_readings(
        db,
        EnergyReading,
        EnergyReadingRollup5m,
        key_column=EnergyReading.source_id,
        key_value=source.id,
        ts_start=datetime.datetime.now(UTC) - datetime.timedelta(days=400),
        ts_end=datetime.datetime.now(UTC),
    )
    assert len(rows) == 1
    assert rows[0].value == 42.0
