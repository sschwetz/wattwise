from __future__ import annotations

import datetime

import httpx
import pytest
import respx

from amber_pricing_collector.amber_client import fetch_current_prices, fetch_historical_prices

AMBER_SITE_ID = "example-site-id"
AMBER_API_KEY = "psk_example_token"


@respx.mock
def test_fetch_current_prices_sends_bearer_auth_and_params():
    route = respx.get(f"https://api.amber.com.au/v1/sites/{AMBER_SITE_ID}/prices/current").mock(
        return_value=httpx.Response(200, json=[{"channelType": "general"}])
    )
    result = fetch_current_prices(AMBER_SITE_ID, AMBER_API_KEY, previous=1, next_count=12)
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {AMBER_API_KEY}"
    assert route.calls[0].request.url.params["previous"] == "1"
    assert route.calls[0].request.url.params["next"] == "12"
    assert result == [{"channelType": "general"}]


@respx.mock
def test_fetch_current_prices_raises_on_http_error_status():
    respx.get(f"https://api.amber.com.au/v1/sites/{AMBER_SITE_ID}/prices/current").mock(
        return_value=httpx.Response(401)
    )
    with pytest.raises(httpx.HTTPStatusError):
        fetch_current_prices(AMBER_SITE_ID, AMBER_API_KEY)


@respx.mock
def test_fetch_historical_prices_sends_date_range_and_resolution():
    route = respx.get(f"https://api.amber.com.au/v1/sites/{AMBER_SITE_ID}/prices").mock(
        return_value=httpx.Response(200, json=[{"channelType": "general"}])
    )
    result = fetch_historical_prices(
        AMBER_SITE_ID, AMBER_API_KEY, datetime.date(2025, 3, 1), datetime.date(2025, 3, 7), resolution=5
    )
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {AMBER_API_KEY}"
    assert route.calls[0].request.url.params["startDate"] == "2025-03-01"
    assert route.calls[0].request.url.params["endDate"] == "2025-03-07"
    assert route.calls[0].request.url.params["resolution"] == "5"
    assert result == [{"channelType": "general"}]


@respx.mock
def test_fetch_historical_prices_raises_on_http_error_status():
    respx.get(f"https://api.amber.com.au/v1/sites/{AMBER_SITE_ID}/prices").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_historical_prices(AMBER_SITE_ID, AMBER_API_KEY, datetime.date(2025, 3, 1), datetime.date(2025, 3, 7))
