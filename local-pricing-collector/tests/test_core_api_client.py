from __future__ import annotations

import json

import httpx
import pytest
import respx

from local_pricing_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_source_config,
    push_price_intervals,
)

BASE_URL = "http://core-api:8000"


@respx.mock
def test_fetch_collector_sources_asks_for_local_pricing_sources():
    route = respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(
        return_value=httpx.Response(200, json=[{"source_id": 4, "site_id": 1, "config": {}}])
    )

    assert fetch_collector_sources(BASE_URL, "k") == [4]
    request = route.calls[0].request
    assert request.url.params["kind"] == "pricing"
    assert request.url.params["provider"] == "local"
    assert request.headers["X-Collector-Key"] == "k"
    assert request.headers["X-Collector-Provider"] == "local"


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
def test_fetch_source_config_returns_the_config_and_sends_the_provider():
    route = respx.get(f"{BASE_URL}/api/v1/sources/4/config").mock(
        return_value=httpx.Response(200, json={"config": {"timezone": "UTC", "rate_windows": []}})
    )

    assert fetch_source_config(BASE_URL, 4, "k") == {"timezone": "UTC", "rate_windows": []}
    assert route.calls[0].request.headers["X-Collector-Provider"] == "local"


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
