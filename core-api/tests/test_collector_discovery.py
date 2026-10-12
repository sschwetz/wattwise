from __future__ import annotations

import datetime
import secrets

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, User
from core_api.models.sources import Source, SourceKind
from tests.conftest import ENVOY_COLLECTOR_KEY, PRICING_COLLECTOR_KEY, WEATHER_COLLECTOR_KEY


def make_source(
    db_url: str,
    kind: SourceKind,
    provider: str,
    *,
    enabled: bool = True,
    effective_to: datetime.datetime | None = None,
    config: dict | None = None,
    amber_api_key: str | None = None,
) -> tuple[int, int]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(
        email=f"disc-owner-{secrets.token_hex(4)}@example.com", password_hash="x", amber_api_key=amber_api_key
    )
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=kind,
        provider=provider,
        config=config or {},
        enabled=enabled,
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
        effective_to=effective_to,
    )
    session.add(source)
    session.commit()
    source_id, site_id = source.id, site.id
    session.close()
    return source_id, site_id


def test_discovery_returns_only_matching_kind(client, app_and_db_url):
    _, db_url = app_and_db_url
    energy_id, energy_site = make_source(db_url, SourceKind.ENERGY, "envoy", config={"host": "envoy.local"})
    make_source(db_url, SourceKind.WEATHER, "weatherlink_live")

    response = client.get(
        "/api/v1/collector/sources", params={"kind": "energy"}, headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == [{"source_id": energy_id, "site_id": energy_site, "config": {"host": "envoy.local"}}]


def test_discovery_excludes_disabled_sources(client, app_and_db_url):
    _, db_url = app_and_db_url
    make_source(db_url, SourceKind.WEATHER, "weatherlink_live", enabled=False)

    response = client.get(
        "/api/v1/collector/sources", params={"kind": "weather"}, headers={"X-Collector-Key": WEATHER_COLLECTOR_KEY}
    )
    assert response.status_code == 200
    assert response.json() == []


def test_discovery_excludes_expired_sources(client, app_and_db_url):
    _, db_url = app_and_db_url
    past = datetime.datetime(2020, 6, 1, tzinfo=datetime.timezone.utc)
    make_source(db_url, SourceKind.WEATHER, "weatherlink_live", effective_to=past)

    response = client.get(
        "/api/v1/collector/sources", params={"kind": "weather"}, headers={"X-Collector-Key": WEATHER_COLLECTOR_KEY}
    )
    assert response.status_code == 200
    assert response.json() == []


def test_discovery_rejects_wrong_kind_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    make_source(db_url, SourceKind.ENERGY, "envoy")

    response = client.get(
        "/api/v1/collector/sources", params={"kind": "energy"}, headers={"X-Collector-Key": WEATHER_COLLECTOR_KEY}
    )
    assert response.status_code == 401


def test_discovery_rejects_non_ascii_key_with_401_not_500(client, app_and_db_url):
    _, db_url = app_and_db_url
    make_source(db_url, SourceKind.ENERGY, "envoy")

    # Non-ASCII header value, sent as the raw latin-1 bytes Starlette decodes headers
    # as -- this used to crash compare_digest with a TypeError (500) instead of a
    # clean 401.
    response = client.get(
        "/api/v1/collector/sources",
        params={"kind": "energy"},
        headers={"X-Collector-Key": "café".encode("latin-1")},
    )
    assert response.status_code == 401


def test_discovery_rejects_invalid_kind(client, app_and_db_url):
    response = client.get(
        "/api/v1/collector/sources", params={"kind": "not-a-real-kind"}, headers={"X-Collector-Key": "anything"}
    )
    assert response.status_code == 422


def test_discovery_merges_owner_amber_key_for_amber_provider(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id, site_id = make_source(
        db_url, SourceKind.PRICING, "amber", config={"amber_site_id": "abc"}, amber_api_key="psk_token"
    )

    response = client.get(
        "/api/v1/collector/sources", params={"kind": "pricing"}, headers={"X-Collector-Key": PRICING_COLLECTOR_KEY}
    )
    assert response.status_code == 200
    assert response.json() == [
        {"source_id": source_id, "site_id": site_id, "config": {"amber_site_id": "abc", "amber_api_key": "psk_token"}}
    ]
