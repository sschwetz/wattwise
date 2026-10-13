from __future__ import annotations

from core_api.models.sources import SourceKind
from tests.conftest import PRICING_COLLECTOR_KEY
from tests.test_collector_discovery import make_source


def _discover(client, **params):
    return client.get(
        "/api/v1/collector/sources",
        params={"kind": "pricing", **params},
        headers={"X-Collector-Key": PRICING_COLLECTOR_KEY},
    )


def test_discovery_filters_by_provider(client, app_and_db_url):
    _, db_url = app_and_db_url
    make_source(db_url, SourceKind.PRICING, "amber")
    local_id, _ = make_source(db_url, SourceKind.PRICING, "local")

    response = _discover(client, provider="local")

    assert response.status_code == 200, response.text
    assert [s["source_id"] for s in response.json()] == [local_id]


def test_discovery_without_provider_returns_every_source_of_the_kind(client, app_and_db_url):
    _, db_url = app_and_db_url
    amber_id, _ = make_source(db_url, SourceKind.PRICING, "amber")
    local_id, _ = make_source(db_url, SourceKind.PRICING, "local")

    response = _discover(client)

    assert sorted(s["source_id"] for s in response.json()) == sorted([amber_id, local_id])


def _config(client, source_id, **headers):
    return client.get(
        f"/api/v1/sources/{source_id}/config",
        headers={"X-Collector-Key": PRICING_COLLECTOR_KEY, **headers},
    )


def test_config_rejects_a_provider_header_that_does_not_match(client, app_and_db_url):
    _, db_url = app_and_db_url
    amber_id, _ = make_source(db_url, SourceKind.PRICING, "amber")

    response = _config(client, amber_id, **{"X-Collector-Provider": "local"})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_collector_key"


def test_config_accepts_a_matching_provider_header(client, app_and_db_url):
    _, db_url = app_and_db_url
    amber_id, _ = make_source(db_url, SourceKind.PRICING, "amber")

    assert _config(client, amber_id, **{"X-Collector-Provider": "amber"}).status_code == 200


def test_config_without_a_provider_header_still_works(client, app_and_db_url):
    _, db_url = app_and_db_url
    amber_id, _ = make_source(db_url, SourceKind.PRICING, "amber")

    assert _config(client, amber_id).status_code == 200
