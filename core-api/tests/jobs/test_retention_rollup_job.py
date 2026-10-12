from __future__ import annotations

import datetime

from sqlalchemy import select

from core_api.jobs.retention_rollup_job import run_retention_rollup_job
from core_api.models.pricing import PriceChannel, PriceReading, PriceReadingRollup5m
from core_api.models.readings import (
    EnergyMetric,
    EnergyReading,
    EnergyReadingRollup5m,
    WeatherMetric,
    WeatherReading,
    WeatherReadingRollup5m,
)
from core_api.models.sources import Source, SourceKind
from core_api.readings_query import RAW_RETENTION
from tests.conftest import UTC


def make_energy_source(db, site):
    source = Source(
        site_id=site.id, kind=SourceKind.ENERGY, provider="envoy", config={}, effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def make_pricing_source(db, site):
    source = Source(
        site_id=site.id, kind=SourceKind.PRICING, provider="amber", config={}, effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def make_weather_source(db, site):
    source = Source(
        site_id=site.id, kind=SourceKind.WEATHER, provider="weatherlink", config={}, effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def test_wind_direction_rolls_up_by_circular_mean_not_arithmetic_mean(db, site):
    """350 deg and 10 deg are 20 deg apart either side of north: the correct
    mean is ~0/360, while an arithmetic mean gives 180 (due south, exactly
    backwards). The raw rows are deleted straight after, so getting this
    wrong is unrecoverable."""
    source = make_weather_source(db, site)
    old_ts = datetime.datetime.now(UTC) - RAW_RETENTION - datetime.timedelta(days=10)
    bucket = old_ts.replace(minute=old_ts.minute - old_ts.minute % 5, second=0, microsecond=0)
    db.add(WeatherReading(source_id=source.id, ts=bucket, metric=WeatherMetric.WIND_DIRECTION_DEG, value=350.0))
    db.add(WeatherReading(
        source_id=source.id, ts=bucket + datetime.timedelta(minutes=1),
        metric=WeatherMetric.WIND_DIRECTION_DEG, value=10.0,
    ))
    db.commit()

    run_retention_rollup_job(db, now=datetime.datetime.now(UTC))

    rolled = db.execute(
        select(WeatherReadingRollup5m).where(WeatherReadingRollup5m.source_id == source.id)
    ).scalars().all()
    assert len(rolled) == 1
    assert rolled[0].value < 1.0 or rolled[0].value > 359.0
    remaining_raw = db.execute(select(WeatherReading).where(WeatherReading.source_id == source.id)).scalars().all()
    assert remaining_raw == []


def test_other_weather_metrics_still_roll_up_by_arithmetic_mean(db, site):
    source = make_weather_source(db, site)
    old_ts = datetime.datetime.now(UTC) - RAW_RETENTION - datetime.timedelta(days=10)
    bucket = old_ts.replace(minute=old_ts.minute - old_ts.minute % 5, second=0, microsecond=0)
    db.add(WeatherReading(source_id=source.id, ts=bucket, metric=WeatherMetric.TEMP_C, value=18.0))
    db.add(WeatherReading(
        source_id=source.id, ts=bucket + datetime.timedelta(minutes=2), metric=WeatherMetric.TEMP_C, value=22.0,
    ))
    db.commit()

    run_retention_rollup_job(db, now=datetime.datetime.now(UTC))

    rolled = db.execute(
        select(WeatherReadingRollup5m).where(WeatherReadingRollup5m.source_id == source.id)
    ).scalars().all()
    assert len(rolled) == 1
    assert rolled[0].value == 20.0


def test_cutoff_never_splits_a_five_minute_bucket(db, site):
    """With an unfloored cutoff, a run whose cutoff lands 2 minutes into a
    bucket would roll up only the first two readings (mean 150), delete them,
    and have the next run's upsert *replace* that with the mean of the
    remaining three (400) — the whole-bucket mean of 300 lost for good.
    Flooring the cutoff means the bucket is simply left alone until all of it
    is old enough."""
    source = make_energy_source(db, site)
    bucket = datetime.datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    for minute, value in enumerate([100.0, 200.0, 300.0, 400.0, 500.0]):
        db.add(EnergyReading(
            source_id=source.id, ts=bucket + datetime.timedelta(minutes=minute),
            metric=EnergyMetric.PRODUCTION_W, value=value,
        ))
    db.commit()

    # Cutoff falls 2 minutes into the bucket -> must not touch it at all.
    run_retention_rollup_job(db, now=bucket + datetime.timedelta(minutes=2) + RAW_RETENTION)

    assert db.execute(select(EnergyReadingRollup5m).where(EnergyReadingRollup5m.source_id == source.id)).scalars().all() == []
    remaining_raw = db.execute(select(EnergyReading).where(EnergyReading.source_id == source.id)).scalars().all()
    assert len(remaining_raw) == 5

    # Once the whole bucket is past retention, it rolls up to the true mean.
    run_retention_rollup_job(db, now=bucket + datetime.timedelta(minutes=5) + RAW_RETENTION)

    rolled = db.execute(
        select(EnergyReadingRollup5m).where(EnergyReadingRollup5m.source_id == source.id)
    ).scalars().all()
    assert len(rolled) == 1
    assert rolled[0].value == 300.0


def test_average_metric_rolls_up_to_mean_and_deletes_raw_rows(db, site):
    source = make_energy_source(db, site)
    old_ts = datetime.datetime.now(UTC) - RAW_RETENTION - datetime.timedelta(days=10)
    bucket = old_ts.replace(minute=old_ts.minute - old_ts.minute % 5, second=0, microsecond=0)
    db.add(EnergyReading(source_id=source.id, ts=bucket, metric=EnergyMetric.PRODUCTION_W, value=100.0))
    db.add(EnergyReading(source_id=source.id, ts=bucket + datetime.timedelta(minutes=1), metric=EnergyMetric.PRODUCTION_W, value=300.0))
    db.commit()

    run_retention_rollup_job(db, now=datetime.datetime.now(UTC))

    rolled = db.execute(
        select(EnergyReadingRollup5m).where(EnergyReadingRollup5m.source_id == source.id)
    ).scalars().all()
    assert len(rolled) == 1
    assert rolled[0].value == 200.0
    remaining_raw = db.execute(select(EnergyReading).where(EnergyReading.source_id == source.id)).scalars().all()
    assert remaining_raw == []


def test_last_value_metric_rolls_up_to_latest_reading_not_average(db, site):
    source = make_energy_source(db, site)
    old_ts = datetime.datetime.now(UTC) - RAW_RETENTION - datetime.timedelta(days=10)
    bucket = old_ts.replace(minute=old_ts.minute - old_ts.minute % 5, second=0, microsecond=0)
    db.add(EnergyReading(source_id=source.id, ts=bucket, metric=EnergyMetric.BATTERY_SOC_PCT, value=40.0))
    db.add(EnergyReading(source_id=source.id, ts=bucket + datetime.timedelta(minutes=3), metric=EnergyMetric.BATTERY_SOC_PCT, value=55.0))
    db.commit()

    run_retention_rollup_job(db, now=datetime.datetime.now(UTC))

    rolled = db.execute(
        select(EnergyReadingRollup5m).where(EnergyReadingRollup5m.source_id == source.id)
    ).scalars().all()
    assert len(rolled) == 1
    assert rolled[0].value == 55.0  # the later reading, not the average of 40/55


def test_rows_newer_than_cutoff_are_untouched(db, site):
    source = make_energy_source(db, site)
    recent_ts = datetime.datetime.now(UTC) - datetime.timedelta(days=1)
    db.add(EnergyReading(source_id=source.id, ts=recent_ts, metric=EnergyMetric.PRODUCTION_W, value=100.0))
    db.commit()

    run_retention_rollup_job(db, now=datetime.datetime.now(UTC))

    remaining_raw = db.execute(select(EnergyReading).where(EnergyReading.source_id == source.id)).scalars().all()
    assert len(remaining_raw) == 1
    rolled = db.execute(select(EnergyReadingRollup5m).where(EnergyReadingRollup5m.source_id == source.id)).scalars().all()
    assert rolled == []


def test_price_reading_rolls_up_by_channel_average(db, site):
    source = make_pricing_source(db, site)
    old_ts = datetime.datetime.now(UTC) - RAW_RETENTION - datetime.timedelta(days=10)
    bucket = old_ts.replace(minute=old_ts.minute - old_ts.minute % 5, second=0, microsecond=0)
    db.add(PriceReading(pricing_source_id=source.id, ts=bucket, channel=PriceChannel.BUY, value="20.0"))
    db.add(PriceReading(pricing_source_id=source.id, ts=bucket + datetime.timedelta(minutes=2), channel=PriceChannel.BUY, value="30.0"))
    db.commit()

    run_retention_rollup_job(db, now=datetime.datetime.now(UTC))

    rolled = db.execute(
        select(PriceReadingRollup5m).where(PriceReadingRollup5m.pricing_source_id == source.id)
    ).scalars().all()
    assert len(rolled) == 1
    assert rolled[0].value == 25.0
