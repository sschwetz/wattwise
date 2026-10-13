from __future__ import annotations

import datetime

import httpx
import pytest
import respx

from amber_pricing_collector.config import PricingSource
from amber_pricing_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_price_gaps,
    fetch_site_data_floor,
    fetch_source_config,
    push_price_intervals,
    report_gap_marker,
)

BASE_URL = "http://localhost:8000"


@respx.mock
def test_fetch_collector_sources_sends_kind_and_collector_key():
    route = respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(
        return_value=httpx.Response(
            200, json=[{"source_id": 1, "site_id": 10, "config": {"amber_site_id": "abc"}}]
        )
    )
    result = fetch_collector_sources(BASE_URL, "my-key")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    assert route.calls[0].request.url.params["kind"] == "pricing"
    assert result == [PricingSource(source_id=1)]


@respx.mock
def test_fetch_collector_sources_raises_on_non_200():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(return_value=httpx.Response(401))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "wrong-key")


@respx.mock
def test_fetch_collector_sources_raises_core_api_error_on_connection_failure():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "my-key")


@respx.mock
def test_fetch_source_config_sends_collector_key_header():
    route = respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(
        return_value=httpx.Response(200, json={"config": {"amber_site_id": "example-site-id"}})
    )
    result = fetch_source_config(BASE_URL, 1, "my-key")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    assert result == {"amber_site_id": "example-site-id"}


@respx.mock
def test_fetch_source_config_raises_core_api_error_on_connection_failure():
    respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        fetch_source_config(BASE_URL, 1, "my-key")


@respx.mock
def test_fetch_source_config_raises_on_non_200():
    respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(return_value=httpx.Response(401))
    with pytest.raises(CoreApiError):
        fetch_source_config(BASE_URL, 1, "wrong-key")


@respx.mock
def test_push_price_intervals_sends_batch():
    route = respx.post(f"{BASE_URL}/api/v1/sources/1/prices").mock(return_value=httpx.Response(202, json={"ingested": 1}))
    push_price_intervals(BASE_URL, 1, "my-key", [{"channel": "buy", "interval_start": "2026-01-01T00:00:00Z", "interval_end": "2026-01-01T00:05:00Z", "price": "25.0"}])
    import json as _json

    body = _json.loads(route.calls[0].request.content)
    assert body == {"intervals": [{"channel": "buy", "interval_start": "2026-01-01T00:00:00Z", "interval_end": "2026-01-01T00:05:00Z", "price": "25.0"}]}


@respx.mock
def test_push_price_intervals_raises_on_non_202():
    respx.post(f"{BASE_URL}/api/v1/sources/1/prices").mock(return_value=httpx.Response(422))
    with pytest.raises(CoreApiError):
        push_price_intervals(BASE_URL, 1, "my-key", [])


@respx.mock
def test_push_price_intervals_raises_core_api_error_on_connection_failure():
    respx.post(f"{BASE_URL}/api/v1/sources/1/prices").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        push_price_intervals(BASE_URL, 1, "my-key", [])


@respx.mock
def test_push_price_intervals_defaults_to_ten_second_timeout():
    """Backward compatibility for the live-poll call site, which pushes at most one
    5-minute tick's worth of intervals and should stay on the short timeout."""
    route = respx.post(f"{BASE_URL}/api/v1/sources/1/prices").mock(return_value=httpx.Response(202, json={"ingested": 0}))
    push_price_intervals(BASE_URL, 1, "my-key", [])
    assert route.calls[0].request.extensions["timeout"] == {"connect": 10, "read": 10, "write": 10, "pool": 10}


@respx.mock
def test_push_price_intervals_honours_explicit_timeout():
    """Final review Important finding: a backfill chunk can be up to 4,032 intervals
    (~20,160 server-side PriceReading upserts), which can exceed the 10s default, so
    backfill must be able to ask for a more generous timeout."""
    route = respx.post(f"{BASE_URL}/api/v1/sources/1/prices").mock(return_value=httpx.Response(202, json={"ingested": 0}))
    push_price_intervals(BASE_URL, 1, "my-key", [], timeout=120)
    assert route.calls[0].request.extensions["timeout"] == {"connect": 120, "read": 120, "write": 120, "pool": 120}


@respx.mock
def test_fetch_site_data_floor_parses_iso_datetime():
    respx.get(f"{BASE_URL}/api/v1/sources/1/site-data-floor").mock(
        return_value=httpx.Response(200, json={"floor_ts": "2025-01-01T00:00:00+00:00"})
    )
    result = fetch_site_data_floor(BASE_URL, 1, "my-key")
    assert result == datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)


@respx.mock
def test_fetch_site_data_floor_returns_none_when_null():
    respx.get(f"{BASE_URL}/api/v1/sources/1/site-data-floor").mock(return_value=httpx.Response(200, json={"floor_ts": None}))
    assert fetch_site_data_floor(BASE_URL, 1, "my-key") is None


@respx.mock
def test_fetch_site_data_floor_raises_core_api_error_on_connection_failure():
    respx.get(f"{BASE_URL}/api/v1/sources/1/site-data-floor").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        fetch_site_data_floor(BASE_URL, 1, "my-key")


@respx.mock
def test_fetch_price_gaps_sends_since_param_and_parses_response():
    since = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    route = respx.get(f"{BASE_URL}/api/v1/sources/1/prices/gaps").mock(
        return_value=httpx.Response(200, json={"gaps": [{"channel": "buy", "start_date": "2025-01-01", "end_date": "2025-01-03"}]})
    )
    result = fetch_price_gaps(BASE_URL, 1, "my-key", since)
    assert route.calls[0].request.url.params["since"] == "2025-01-01T00:00:00+00:00"
    assert result == [{"channel": "buy", "start_date": "2025-01-01", "end_date": "2025-01-03"}]


@respx.mock
def test_fetch_price_gaps_raises_core_api_error_on_connection_failure():
    respx.get(f"{BASE_URL}/api/v1/sources/1/prices/gaps").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        fetch_price_gaps(BASE_URL, 1, "my-key", datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc))


@respx.mock
def test_report_gap_marker_sends_channel_and_date():
    route = respx.post(f"{BASE_URL}/api/v1/sources/1/prices/gap-markers").mock(return_value=httpx.Response(204))
    report_gap_marker(BASE_URL, 1, "my-key", "buy", datetime.date(2025, 3, 1))
    import json as _json

    body = _json.loads(route.calls[0].request.content)
    assert body == {"channel": "buy", "gap_date": "2025-03-01"}


@respx.mock
def test_report_gap_marker_raises_core_api_error_on_connection_failure():
    respx.post(f"{BASE_URL}/api/v1/sources/1/prices/gap-markers").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        report_gap_marker(BASE_URL, 1, "my-key", "buy", datetime.date(2025, 3, 1))
