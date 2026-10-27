from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from core_api.db import make_engine, make_session_factory
from core_api.models.readings import EnergyReading
from core_api.models.sources import Source, SourceKind
from tests.conftest import ENVOY_COLLECTOR_KEY, PRICING_COLLECTOR_KEY, WEATHER_COLLECTOR_KEY
from tests.test_ingestion import make_energy_source_full
from tests.test_live_push import make_weather_source
from tests.test_sources import auth

ENERGY_BATCH = {"readings": [{"ts": "2026-01-01T00:00:00Z", "metric": "production_w", "value": 1500.0}]}
WEATHER_BATCH = {"readings": [{"ts": "2026-01-01T00:00:00Z", "metric": "temp_c", "value": 21.0}]}
PRICE_BATCH = {
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
LIVE_SAMPLE = {"ts": "2026-01-01T10:00:00Z", "values": {"production_w": 1500.5}}
WEATHER_LIVE_SAMPLE = {"ts": "2026-01-01T10:00:00Z", "values": {"temp_c": 21.0}}
GAP_MARKER = {"channel": "buy", "gap_date": "2025-03-01"}


def make_pricing_source_on_site(db_url: str, site_id: int) -> int:
    session = make_session_factory(make_engine(db_url))()
    source = Source(
        site_id=site_id,
        kind=SourceKind.PRICING,
        provider="amber",
        config={"amber_site_id": "example-site-id"},
        effective_from=datetime.now(UTC),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id


def set_enabled(client, owner_id: int, site_id: int, enabled: bool) -> None:
    response = client.patch(f"/api/v1/sites/{site_id}", json={"enabled": enabled}, headers=auth(owner_id))
    assert response.status_code == 200, response.text


def test_collector_source_list_omits_disabled_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id, source_id = make_energy_source_full(db_url)
    url = "/api/v1/collector/sources?kind=energy"
    headers = {"X-Collector-Key": ENVOY_COLLECTOR_KEY}

    assert [s["source_id"] for s in client.get(url, headers=headers).json()] == [source_id]
    set_enabled(client, owner_id, site_id, False)
    assert client.get(url, headers=headers).json() == []
    set_enabled(client, owner_id, site_id, True)
    assert [s["source_id"] for s in client.get(url, headers=headers).json()] == [source_id]


@pytest.mark.parametrize(
    "method,family,suffix,body,key",
    [
        ("POST", "energy", "readings/energy", ENERGY_BATCH, ENVOY_COLLECTOR_KEY),
        ("POST", "weather", "readings/weather", WEATHER_BATCH, WEATHER_COLLECTOR_KEY),
        ("POST", "pricing", "prices", PRICE_BATCH, PRICING_COLLECTOR_KEY),
        ("POST", "energy", "live/energy", LIVE_SAMPLE, ENVOY_COLLECTOR_KEY),
        ("POST", "weather", "live/weather", WEATHER_LIVE_SAMPLE, WEATHER_COLLECTOR_KEY),
        ("POST", "pricing", "prices/gap-markers", GAP_MARKER, PRICING_COLLECTOR_KEY),
        ("POST", "pricing", "prices/reprice-marker/clear", {"version": 1}, PRICING_COLLECTOR_KEY),
        ("PATCH", "energy", "discovered-config", {"serial_num": "123456789012"}, ENVOY_COLLECTOR_KEY),
        ("GET", "energy", "config", None, ENVOY_COLLECTOR_KEY),
    ],
)
def test_ingestion_rejected_for_disabled_site(client, app_and_db_url, method, family, suffix, body, key):
    _, db_url = app_and_db_url
    owner_id, site_id, energy_id = make_energy_source_full(db_url)
    source_ids = {
        "energy": energy_id,
        "weather": make_weather_source(db_url, site_id),
        "pricing": make_pricing_source_on_site(db_url, site_id),
    }
    url = f"/api/v1/sources/{source_ids[family]}/{suffix}"
    set_enabled(client, owner_id, site_id, False)

    response = client.request(method, url, json=body, headers={"X-Collector-Key": key})

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "site_disabled"


def count_energy_rows(db_url: str) -> int:
    session = make_session_factory(make_engine(db_url))()
    count = session.execute(select(func.count()).select_from(EnergyReading)).scalar_one()
    session.close()
    return count


def test_rejected_push_for_disabled_site_stores_nothing(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id, source_id = make_energy_source_full(db_url)
    set_enabled(client, owner_id, site_id, False)
    before = count_energy_rows(db_url)

    response = client.post(
        f"/api/v1/sources/{source_id}/readings/energy", json=ENERGY_BATCH, headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    )

    assert response.json()["code"] == "site_disabled"
    assert count_energy_rows(db_url) == before


def test_ingestion_accepted_after_reenable(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id, source_id = make_energy_source_full(db_url)
    url = f"/api/v1/sources/{source_id}/readings/energy"
    headers = {"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    set_enabled(client, owner_id, site_id, False)
    assert client.post(url, json=ENERGY_BATCH, headers=headers).status_code == 403

    set_enabled(client, owner_id, site_id, True)

    assert client.post(url, json=ENERGY_BATCH, headers=headers).status_code == 202
