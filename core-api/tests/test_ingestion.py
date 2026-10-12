from __future__ import annotations

import secrets
from datetime import datetime, timezone

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, User
from core_api.models.sources import Source, SourceKind
from core_api.security import create_access_token
from tests.conftest import ENVOY_COLLECTOR_KEY, PRICING_COLLECTOR_KEY, WEATHER_COLLECTOR_KEY


def make_energy_source(db_url: str) -> int:
    _owner_id, _site_id, source_id = make_energy_source_full(db_url)
    return source_id


def make_energy_source_full(db_url: str) -> tuple[int, int, int]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email=f"ing-owner-{secrets.token_hex(4)}@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        effective_from=datetime.now(timezone.utc),
    )
    session.add(source)
    session.commit()
    owner_id, site_id, source_id = owner.id, site.id, source.id
    session.close()
    return owner_id, site_id, source_id


def test_ingest_energy_readings_batch(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    batch = {
        "readings": [
            {"ts": "2026-01-01T00:00:00Z", "metric": "production_w", "value": 1500.0},
            {"ts": "2026-01-01T00:01:00Z", "metric": "production_w", "value": 1600.0},
        ]
    }
    response = client.post(
        f"/api/v1/sources/{source_id}/readings/energy", json=batch, headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    )
    assert response.status_code == 202, response.text


def test_ingest_energy_readings_retry_is_idempotent(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    batch = {"readings": [{"ts": "2026-01-01T00:00:00Z", "metric": "production_w", "value": 1500.0}]}
    headers = {"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    first = client.post(f"/api/v1/sources/{source_id}/readings/energy", json=batch, headers=headers)
    second = client.post(f"/api/v1/sources/{source_id}/readings/energy", json=batch, headers=headers)
    assert first.status_code == 202
    assert second.status_code == 202

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.readings import EnergyReading

    rows = session.query(EnergyReading).all()
    session.close()
    assert len(rows) == 1


def test_ingest_energy_readings_rejects_malformed_batch(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    batch = {"readings": [{"ts": "2026-01-01T00:00:00Z", "metric": "not_a_real_metric", "value": 1.0}]}
    response = client.post(
        f"/api/v1/sources/{source_id}/readings/energy", json=batch, headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    )
    assert response.status_code == 422


def test_ingest_energy_readings_same_instant_different_offsets_is_one_row(client, app_and_db_url):
    """Finding 1/C2: '2026-03-01T10:00:00+10:00' and '2026-03-01T00:00:00Z'
    are the same UTC instant and must upsert to a single row, not two."""
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    headers = {"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    first = client.post(
        f"/api/v1/sources/{source_id}/readings/energy",
        json={"readings": [{"ts": "2026-03-01T10:00:00+10:00", "metric": "production_w", "value": 1.0}]},
        headers=headers,
    )
    second = client.post(
        f"/api/v1/sources/{source_id}/readings/energy",
        json={"readings": [{"ts": "2026-03-01T00:00:00Z", "metric": "production_w", "value": 2.0}]},
        headers=headers,
    )
    assert first.status_code == 202
    assert second.status_code == 202

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.readings import EnergyReading

    rows = session.query(EnergyReading).all()
    session.close()
    assert len(rows) == 1
    assert rows[0].value == 2.0


def test_ingest_energy_readings_wrong_kind_key_rejected(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    batch = {"readings": [{"ts": "2026-01-01T00:00:00Z", "metric": "production_w", "value": 1.0}]}
    response = client.post(
        f"/api/v1/sources/{source_id}/readings/energy", json=batch, headers={"X-Collector-Key": WEATHER_COLLECTOR_KEY}
    )
    assert response.status_code == 401


def test_ingestion_rejected_after_source_disabled(client, app_and_db_url):
    """Finding 6/I4: disabling a source via PATCH must stop further
    ingestion through its still-valid collector key."""
    _, db_url = app_and_db_url
    owner_id, site_id, source_id = make_energy_source_full(db_url)
    patch = client.patch(
        f"/api/v1/sites/{site_id}/sources/{source_id}",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert patch.status_code == 200

    batch = {"readings": [{"ts": "2026-01-01T00:00:00Z", "metric": "production_w", "value": 1.0}]}
    response = client.post(
        f"/api/v1/sources/{source_id}/readings/energy", json=batch, headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    )
    assert response.status_code == 403


def test_energy_source_key_rejected_on_weather_endpoint(client, app_and_db_url):
    """Finding 5/I3: an energy source's key must not be usable to write to
    the weather ingestion endpoint (silent misfiling of rows)."""
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    batch = {"readings": [{"ts": "2026-01-01T00:00:00Z", "metric": "temp_c", "value": 21.0}]}
    response = client.post(
        f"/api/v1/sources/{source_id}/readings/weather", json=batch, headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    )
    assert response.status_code == 403


def make_pricing_source(db_url: str) -> int:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="price-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.PRICING,
        provider="amber",
        config={},
        effective_from=datetime.now(timezone.utc),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id


def test_ingest_price_interval_fans_out_to_per_minute_readings(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)
    batch = {
        "intervals": [
            {
                "channel": "buy",
                "interval_start": "2026-01-01T00:00:00Z",
                "interval_end": "2026-01-01T00:05:00Z",
                "price": "25.5000",
                "is_demand_window": False,
            }
        ]
    }
    response = client.post(
        f"/api/v1/sources/{source_id}/prices", json=batch, headers={"X-Collector-Key": PRICING_COLLECTOR_KEY}
    )
    assert response.status_code == 202, response.text

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.pricing import PriceInterval, PriceReading

    intervals = session.query(PriceInterval).all()
    readings = session.query(PriceReading).all()
    session.close()
    assert len(intervals) == 1
    assert len(readings) == 5  # one per covered minute: :00 :01 :02 :03 :04
    assert all(r.value == readings[0].value for r in readings)


def test_ingest_price_interval_inverted_range_rejected(client, app_and_db_url):
    """Finding 7/I5: interval_end <= interval_start must be a 422, not a
    silently-persisted nonsensical row."""
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)
    batch = {
        "intervals": [
            {
                "channel": "buy",
                "interval_start": "2026-01-01T00:05:00Z",
                "interval_end": "2026-01-01T00:00:00Z",
                "price": "25.5000",
                "is_demand_window": False,
            }
        ]
    }
    response = client.post(
        f"/api/v1/sources/{source_id}/prices", json=batch, headers={"X-Collector-Key": PRICING_COLLECTOR_KEY}
    )
    assert response.status_code == 422


def test_ingest_price_interval_exceeding_cap_rejected(client, app_and_db_url):
    """Finding 7/I5: an interval longer than the 1440-minute cap must be
    rejected with a 422 rather than fanning out to hundreds of thousands of
    per-minute inserts in one blocking request."""
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)
    batch = {
        "intervals": [
            {
                "channel": "buy",
                "interval_start": "2026-01-01T00:00:00Z",
                "interval_end": "2027-01-01T00:00:00Z",
                "price": "25.5000",
                "is_demand_window": False,
            }
        ]
    }
    response = client.post(
        f"/api/v1/sources/{source_id}/prices", json=batch, headers={"X-Collector-Key": PRICING_COLLECTOR_KEY}
    )
    assert response.status_code == 422


def test_ingest_price_interval_retry_is_idempotent(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)
    batch = {
        "intervals": [
            {
                "channel": "buy",
                "interval_start": "2026-01-01T00:00:00Z",
                "interval_end": "2026-01-01T00:01:00Z",
                "price": "25.5000",
                "is_demand_window": False,
            }
        ]
    }
    headers = {"X-Collector-Key": PRICING_COLLECTOR_KEY}
    client.post(f"/api/v1/sources/{source_id}/prices", json=batch, headers=headers)
    client.post(f"/api/v1/sources/{source_id}/prices", json=batch, headers=headers)

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.pricing import PriceInterval, PriceReading

    assert session.query(PriceInterval).count() == 1
    assert session.query(PriceReading).count() == 1
    session.close()


def test_ingest_price_interval_forecast_overwritten_by_actual(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)
    headers = {"X-Collector-Key": PRICING_COLLECTOR_KEY}
    interval_common = {
        "channel": "buy",
        "interval_start": "2026-01-01T00:00:00Z",
        "interval_end": "2026-01-01T00:05:00Z",
    }

    forecast_batch = {"intervals": [{**interval_common, "price": "6.35178"}]}
    client.post(f"/api/v1/sources/{source_id}/prices", json=forecast_batch, headers=headers)

    actual_batch = {"intervals": [{**interval_common, "price": "7.37398"}]}
    response = client.post(f"/api/v1/sources/{source_id}/prices", json=actual_batch, headers=headers)
    assert response.status_code == 202, response.text

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.pricing import PriceInterval, PriceReading

    intervals = session.query(PriceInterval).all()
    readings = session.query(PriceReading).all()
    session.close()
    assert len(intervals) == 1
    assert str(intervals[0].price) == "7.3740"  # the actual's price, not the forecast's
    assert len(readings) == 5  # still one row per covered minute, not 10 (no duplicate fan-out)
    assert all(str(r.value) == "7.3740" for r in readings)


def test_ingest_price_interval_fan_out_floors_staggered_start_to_whole_minute(client, app_and_db_url):
    """Final review Critical finding: Amber's interval_start values are staggered by a
    second (e.g. 23:55:01Z). PriceInterval.interval_start must keep that raw value
    (spec passthrough), but the derived per-minute PriceReading rows must land on whole
    minutes, or cost.py's exact-ts join to EnergyReading can never match."""
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url)
    batch = {
        "intervals": [
            {
                "channel": "buy",
                "interval_start": "2026-01-01T23:55:01Z",
                "interval_end": "2026-01-02T00:00:00Z",
                "price": "25.5000",
                "is_demand_window": False,
            }
        ]
    }
    response = client.post(
        f"/api/v1/sources/{source_id}/prices", json=batch, headers={"X-Collector-Key": PRICING_COLLECTOR_KEY}
    )
    assert response.status_code == 202, response.text

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.pricing import PriceInterval, PriceReading

    intervals = session.query(PriceInterval).all()
    readings = session.query(PriceReading).order_by(PriceReading.ts).all()
    session.close()

    # The authoritative interval keeps the provider's raw staggered value.
    assert len(intervals) == 1
    assert intervals[0].interval_start.second == 1

    # The derived per-minute readings are floored, and the count is unchanged.
    assert len(readings) == 5
    assert all(r.ts.second == 0 and r.ts.microsecond == 0 for r in readings), [
        r.ts.isoformat() for r in readings
    ]
    assert [r.ts.minute for r in readings] == [55, 56, 57, 58, 59]
