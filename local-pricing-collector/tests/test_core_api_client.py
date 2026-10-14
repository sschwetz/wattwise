from __future__ import annotations

import datetime
import json

import httpx
import pytest
import respx

from local_pricing_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_price_coverage,
    fetch_site_data_floor,
    push_price_intervals,
)
from local_pricing_collector.models import Coverage, LocalSource

BASE_URL = "http://core-api:8000"


@respx.mock
def test_fetch_collector_sources_asks_for_local_pricing_sources_and_returns_their_config():
    config = {"timezone": "UTC", "rate_windows": []}
    route = respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(
        return_value=httpx.Response(200, json=[{"source_id": 4, "site_id": 1, "config": config}])
    )

    assert fetch_collector_sources(BASE_URL, "k") == [LocalSource(4, config)]
    request = route.calls[0].request
    assert request.url.params["kind"] == "pricing"
    assert request.url.params["provider"] == "local"
    assert request.headers["X-Collector-Key"] == "k"
    assert request.headers["X-Collector-Provider"] == "local"


@respx.mock
def test_fetch_collector_sources_raises_when_an_entry_has_no_config():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(
        return_value=httpx.Response(200, json=[{"source_id": 4, "site_id": 1}])
    )
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "k")


@respx.mock
def test_fetch_collector_sources_raises_on_non_200():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(return_value=httpx.Response(401, text="no"))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "k")


@respx.mock
def test_fetch_collector_sources_wraps_connection_errors():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "k")


@respx.mock
def test_fetch_collector_sources_raises_on_an_unexpected_shape():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(return_value=httpx.Response(200, json=[{"id": 4}]))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "k")


@respx.mock
def test_push_price_intervals_posts_the_batch():
    route = respx.post(f"{BASE_URL}/api/v1/sources/4/prices").mock(return_value=httpx.Response(202, json={}))
    intervals = [{"channel": "buy", "interval_start": "a", "interval_end": "b", "price": "1"}]

    push_price_intervals(BASE_URL, 4, "k", intervals)

    assert route.calls[0].request.headers["X-Collector-Provider"] == "local"
    assert json.loads(route.calls[0].request.content) == {"intervals": intervals}


@respx.mock
def test_push_price_intervals_raises_on_failure():
    respx.post(f"{BASE_URL}/api/v1/sources/4/prices").mock(return_value=httpx.Response(422, text="bad"))
    with pytest.raises(CoreApiError):
        push_price_intervals(BASE_URL, 4, "k", [])


UTC = datetime.timezone.utc


@respx.mock
def test_fetch_site_data_floor_reads_an_offset_less_timestamp_as_utc():
    """core-api serialises the floor without an offset."""
    respx.get(f"{BASE_URL}/api/v1/sources/4/site-data-floor").mock(
        return_value=httpx.Response(200, json={"floor_ts": "2026-06-01T10:00:00"})
    )
    assert fetch_site_data_floor(BASE_URL, 4, "k") == datetime.datetime(2026, 6, 1, 10, 0, tzinfo=UTC)


@respx.mock
def test_fetch_site_data_floor_keeps_an_explicit_offset():
    respx.get(f"{BASE_URL}/api/v1/sources/4/site-data-floor").mock(
        return_value=httpx.Response(200, json={"floor_ts": "2026-06-01T20:00:00+10:00"})
    )
    assert fetch_site_data_floor(BASE_URL, 4, "k") == datetime.datetime(2026, 6, 1, 10, 0, tzinfo=UTC)


@respx.mock
def test_fetch_site_data_floor_is_none_for_a_site_with_no_data():
    route = respx.get(f"{BASE_URL}/api/v1/sources/4/site-data-floor").mock(
        return_value=httpx.Response(200, json={"floor_ts": None})
    )
    assert fetch_site_data_floor(BASE_URL, 4, "k") is None
    assert route.calls[0].request.headers["X-Collector-Provider"] == "local"


@respx.mock
def test_fetch_site_data_floor_raises_on_a_bad_response():
    respx.get(f"{BASE_URL}/api/v1/sources/4/site-data-floor").mock(return_value=httpx.Response(500, text="no"))
    with pytest.raises(CoreApiError):
        fetch_site_data_floor(BASE_URL, 4, "k")


@respx.mock
def test_fetch_price_coverage_parses_each_channel():
    route = respx.get(f"{BASE_URL}/api/v1/sources/4/prices/coverage").mock(
        return_value=httpx.Response(
            200,
            json={"channels": {"buy": {"first": "2026-06-01T10:00:00Z", "last": "2026-06-16T14:00:00Z"}}},
        )
    )

    assert fetch_price_coverage(BASE_URL, 4, "k") == {
        "buy": Coverage(
            first=datetime.datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            last=datetime.datetime(2026, 6, 16, 14, 0, tzinfo=UTC),
        )
    }
    assert route.calls[0].request.headers["X-Collector-Provider"] == "local"


@respx.mock
def test_fetch_price_coverage_is_empty_for_a_source_with_no_prices():
    respx.get(f"{BASE_URL}/api/v1/sources/4/prices/coverage").mock(
        return_value=httpx.Response(200, json={"channels": {}})
    )
    assert fetch_price_coverage(BASE_URL, 4, "k") == {}


@respx.mock
@pytest.mark.parametrize("body", [{"nope": 1}, {"channels": {"buy": {"first": "x"}}}, {"channels": []}])
def test_fetch_price_coverage_raises_on_an_unexpected_body(body):
    respx.get(f"{BASE_URL}/api/v1/sources/4/prices/coverage").mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(CoreApiError):
        fetch_price_coverage(BASE_URL, 4, "k")
