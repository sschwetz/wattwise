from __future__ import annotations

import asyncio
import datetime

import pytest
from sqlalchemy import func, select

from core_api.db import make_engine, make_session_factory
from core_api.live import LiveHub, LiveSample
from core_api.models.readings import EnergyReading, WeatherReading
from core_api.models.sources import Source, SourceKind
from tests.conftest import ENVOY_COLLECTOR_KEY, WEATHER_COLLECTOR_KEY
from tests.test_ingestion import make_energy_source_full

T0 = datetime.datetime(2026, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)


def sample(source_id: int = 1, seconds: int = 0, value: float = 1.0) -> LiveSample:
    return LiveSample(source_id, SourceKind.ENERGY, T0 + datetime.timedelta(seconds=seconds), {"production_w": value})


def test_publish_stores_latest_per_source():
    async def run():
        hub = LiveHub()
        a, b = sample(1), sample(2)
        assert hub.publish(1, a) and hub.publish(1, b)
        assert sorted(hub.latest_for_site(1), key=lambda s: s.source_id) == [a, b]

    asyncio.run(run())


def test_older_ts_is_ignored():
    async def run():
        hub = LiveHub()
        sub = hub.subscribe(1)
        first, second = sample(seconds=5), sample(seconds=1)
        assert hub.publish(1, first) is True
        assert hub.publish(1, second) is False
        assert hub.latest_for_site(1) == [first]
        assert sub.queue.qsize() == 1
        assert sub.queue.get_nowait() == first

    asyncio.run(run())


def test_subscriber_receives_only_its_site():
    async def run():
        hub = LiveHub()
        one, two = hub.subscribe(1), hub.subscribe(2)
        hub.publish(1, sample())
        assert one.queue.qsize() == 1
        assert two.queue.qsize() == 0

    asyncio.run(run())


def test_full_queue_drops_oldest():
    async def run():
        hub = LiveHub(queue_size=2)
        sub = hub.subscribe(1)
        samples = [sample(seconds=i) for i in range(3)]
        for s in samples:
            hub.publish(1, s)
        assert [sub.queue.get_nowait(), sub.queue.get_nowait()] == samples[1:]

    asyncio.run(run())


def test_close_removes_subscription():
    async def run():
        hub = LiveHub()
        sub = hub.subscribe(1)
        sub.close()
        hub.publish(1, sample())
        assert sub.queue.qsize() == 0

    asyncio.run(run())


def push(client, source_id, kind, key=ENVOY_COLLECTOR_KEY, content=None):
    content = content or '{"ts": "2026-01-01T10:00:00Z", "values": {"production_w": 1500.5}}'
    return client.post(
        f"/api/v1/sources/{source_id}/live/{kind}",
        content=content,
        headers={"X-Collector-Key": key, "Content-Type": "application/json"},
    )


def row_count(db_url: str, model) -> int:
    session = make_session_factory(make_engine(db_url))()
    try:
        return session.scalar(select(func.count()).select_from(model))
    finally:
        session.close()


def test_energy_push_returns_202_and_reaches_hub(client, app_and_db_url):
    app, db_url = app_and_db_url
    _, site_id, source_id = make_energy_source_full(db_url)
    response = push(client, source_id, "energy")
    assert response.status_code == 202
    assert response.json() == {}
    [latest] = app.state.live_hub.latest_for_site(site_id)
    assert latest.source_id == source_id
    assert latest.values == {"production_w": 1500.5}
    assert row_count(db_url, EnergyReading) == 0
    assert row_count(db_url, WeatherReading) == 0


def test_push_with_wrong_key_is_401(client, app_and_db_url):
    _, db_url = app_and_db_url
    _, _, source_id = make_energy_source_full(db_url)
    assert push(client, source_id, "energy", key="nope").status_code == 401


def test_weather_key_on_energy_source_is_rejected(client, app_and_db_url):
    _, db_url = app_and_db_url
    _, _, source_id = make_energy_source_full(db_url)
    assert push(client, source_id, "energy", key=WEATHER_COLLECTOR_KEY).status_code == 401


BAD_BODIES = [
    ("energy", '{"ts": "2026-01-01T10:00:00Z", "values": {"bogus_w": 1}}'),
    ("weather", '{"ts": "2026-01-01T10:00:00Z", "values": {"rain_mm": 1}}'),
    (
        "energy",
        '{"ts": "2026-01-01T10:00:00Z", "values": {%s}}' % ",".join(f'"production_w{i}": 1' for i in range(17)),
    ),
    ("energy", '{"ts": "2026-01-01T10:00:00Z", "values": {}}'),
    ("energy", '{"ts": "2026-01-01T10:00:00Z", "values": {"production_w": 1e999}}'),
]


def make_weather_source(db_url: str, site_id: int) -> int:
    session = make_session_factory(make_engine(db_url))()
    source = Source(
        site_id=site_id, kind=SourceKind.WEATHER, provider="ecowitt", config={}, effective_from=T0
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id


@pytest.mark.parametrize("kind,body", BAD_BODIES)
def test_invalid_live_body_is_422(client, app_and_db_url, kind, body):
    app, db_url = app_and_db_url
    _, site_id, energy_id = make_energy_source_full(db_url)
    if kind == "energy":
        response = push(client, energy_id, "energy", content=body)
    else:
        response = push(client, make_weather_source(db_url, site_id), "weather", key=WEATHER_COLLECTOR_KEY, content=body)
    assert response.status_code == 422
    assert app.state.live_hub.latest_for_site(site_id) == []


def test_weather_push_reaches_hub(client, app_and_db_url):
    app, db_url = app_and_db_url
    _, site_id, _ = make_energy_source_full(db_url)
    source_id = make_weather_source(db_url, site_id)
    body = '{"ts": "2026-01-01T10:00:00Z", "values": {"temp_c": 21.5}}'
    assert push(client, source_id, "weather", key=WEATHER_COLLECTOR_KEY, content=body).status_code == 202
    [latest] = app.state.live_hub.latest_for_site(site_id)
    assert latest.kind == SourceKind.WEATHER and latest.values == {"temp_c": 21.5}


def test_older_push_after_newer_is_accepted_but_ignored(client, app_and_db_url):
    app, db_url = app_and_db_url
    _, site_id, source_id = make_energy_source_full(db_url)
    newer = '{"ts": "2026-01-01T10:00:05Z", "values": {"production_w": 2}}'
    older = '{"ts": "2026-01-01T10:00:01Z", "values": {"production_w": 1}}'
    assert push(client, source_id, "energy", content=newer).status_code == 202
    assert push(client, source_id, "energy", content=older).status_code == 202
    [latest] = app.state.live_hub.latest_for_site(site_id)
    assert latest.values == {"production_w": 2}
