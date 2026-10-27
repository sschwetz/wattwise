from __future__ import annotations

import datetime
import json as _json

import httpx
import pytest
import respx

from weatherlink_local_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    push_live_sample,
    push_weather_readings,
)

BASE_URL = "http://localhost:8000"


@respx.mock
def test_fetch_collector_sources_sends_kind_and_collector_key():
    route = respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(
        return_value=httpx.Response(
            200, json=[{"source_id": 1, "site_id": 10, "config": {"host": "weatherlink.local"}}]
        )
    )
    result = fetch_collector_sources(BASE_URL, "my-key")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    assert route.calls[0].request.url.params["kind"] == "weather"
    assert result == [{"source_id": 1, "site_id": 10, "config": {"host": "weatherlink.local"}}]


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
def test_push_weather_readings_sends_batch_as_iso_timestamps():
    route = respx.post(f"{BASE_URL}/api/v1/sources/1/readings/weather").mock(
        return_value=httpx.Response(202, json={"ingested": 1})
    )
    ts = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=datetime.UTC)
    push_weather_readings(BASE_URL, 1, "my-key", [{"ts": ts, "metric": "temp_c", "value": 22.7}])

    body = _json.loads(route.calls[0].request.content)
    assert body == {"readings": [{"ts": "2026-09-29T12:00:00+00:00", "metric": "temp_c", "value": 22.7}]}


@respx.mock
def test_push_weather_readings_raises_on_non_202():
    respx.post(f"{BASE_URL}/api/v1/sources/1/readings/weather").mock(return_value=httpx.Response(422))
    with pytest.raises(CoreApiError):
        push_weather_readings(BASE_URL, 1, "my-key", [])


@respx.mock
def test_push_weather_readings_raises_core_api_error_on_connection_failure():
    respx.post(f"{BASE_URL}/api/v1/sources/1/readings/weather").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        push_weather_readings(BASE_URL, 1, "my-key", [])


@respx.mock
def test_push_weather_readings_error_carries_the_status_code():
    respx.post(f"{BASE_URL}/api/v1/sources/1/readings/weather").mock(
        return_value=httpx.Response(403, json={"code": "site_disabled"})
    )
    with pytest.raises(CoreApiError) as exc_info:
        push_weather_readings(BASE_URL, 1, "my-key", [])
    assert exc_info.value.status_code == 403


@respx.mock
def test_transport_failure_has_no_status_code():
    respx.post(f"{BASE_URL}/api/v1/sources/1/readings/weather").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError) as exc_info:
        push_weather_readings(BASE_URL, 1, "my-key", [])
    assert exc_info.value.status_code is None


@respx.mock
def test_push_live_sample_posts_ts_and_values_with_key_and_short_timeout():
    route = respx.post(f"{BASE_URL}/api/v1/sources/1/live/weather").mock(return_value=httpx.Response(202))
    ts = datetime.datetime(2026, 9, 29, 12, 0, 5, tzinfo=datetime.UTC)

    push_live_sample(BASE_URL, 1, "my-key", ts, {"temp_c": 22.7})

    request = route.calls[0].request
    assert request.headers["X-Collector-Key"] == "my-key"
    assert _json.loads(request.content) == {"ts": "2026-09-29T12:00:05+00:00", "values": {"temp_c": 22.7}}
    assert request.extensions["timeout"]["read"] == 2


@respx.mock
def test_push_live_sample_raises_on_non_202():
    respx.post(f"{BASE_URL}/api/v1/sources/1/live/weather").mock(return_value=httpx.Response(422))
    with pytest.raises(CoreApiError):
        push_live_sample(BASE_URL, 1, "my-key", datetime.datetime.now(datetime.UTC), {"temp_c": 1.0})


@respx.mock
def test_push_live_sample_raises_core_api_error_on_connection_failure():
    respx.post(f"{BASE_URL}/api/v1/sources/1/live/weather").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        push_live_sample(BASE_URL, 1, "my-key", datetime.datetime.now(datetime.UTC), {"temp_c": 1.0})
