from __future__ import annotations

import httpx
import pytest
import respx

from weatherlink_local_collector.weatherlink_client import fetch_current_conditions, request_real_time


@respx.mock
def test_fetch_current_conditions_uses_plain_http_no_auth():
    route = respx.get("http://weatherlink.local/v1/current_conditions").mock(
        return_value=httpx.Response(200, json={"data": {"conditions": []}, "error": None})
    )
    result = fetch_current_conditions("weatherlink.local")
    assert route.called
    assert "Authorization" not in route.calls[0].request.headers
    assert result == {"data": {"conditions": []}, "error": None}


@respx.mock
def test_fetch_current_conditions_raises_on_http_error_status():
    respx.get("http://weatherlink.local/v1/current_conditions").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_current_conditions("weatherlink.local")


@respx.mock
def test_fetch_current_conditions_raises_when_device_reports_error_field():
    respx.get("http://weatherlink.local/v1/current_conditions").mock(
        return_value=httpx.Response(200, json={"data": None, "error": "some device fault"})
    )
    with pytest.raises(ValueError):
        fetch_current_conditions("weatherlink.local")


@respx.mock
def test_request_real_time_asks_for_the_duration_and_returns_the_broadcast_port():
    route = respx.get("http://weatherlink.local/v1/real_time", params={"duration": "300"}).mock(
        return_value=httpx.Response(200, json={"data": {"broadcast_port": 22222, "duration": 300}, "error": None})
    )

    assert request_real_time("weatherlink.local", 300) == 22222
    assert route.called


@respx.mock
def test_request_real_time_raises_when_the_device_has_no_iss_transmitter():
    respx.get("http://weatherlink.local/v1/real_time").mock(
        return_value=httpx.Response(
            200,
            json={"data": None, "error": {"code": 409, "message": "No ISS Transmitters. Real Time broadcast not enabled"}},
        )
    )

    with pytest.raises(ValueError, match="No ISS Transmitters"):
        request_real_time("weatherlink.local", 300)
