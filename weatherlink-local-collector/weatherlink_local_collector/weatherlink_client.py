from __future__ import annotations

import httpx


def fetch_current_conditions(host: str) -> dict:
    """WeatherLink Live's local API needs no authentication at all — plain
    HTTP, no headers. A non-null top-level "error" field is the device's own
    signal that something's wrong (e.g. a sensor fault) even on an HTTP 200,
    so it's treated as a failure the same as a bad status code."""
    response = httpx.get(f"http://{host}/v1/current_conditions", timeout=10)
    response.raise_for_status()
    data = response.json()
    if data.get("error") is not None:
        raise ValueError(f"WeatherLink reported an error: {data['error']}")
    return data


def request_real_time(host: str, duration_seconds: int) -> int:
    """Asks the device to broadcast live wind and rain over UDP for
    `duration_seconds`, and returns the UDP port it will use. A request for
    less time than a running broadcast has left is ignored by the device, and
    a longer one extends it, so renewing with the same duration is safe. With
    no ISS transmitter configured the device answers 409 in the error field."""
    response = httpx.get(f"http://{host}/v1/real_time", params={"duration": duration_seconds}, timeout=5)
    response.raise_for_status()
    body = response.json()
    if body.get("error") is not None:
        raise ValueError(f"WeatherLink refused the real-time broadcast: {body['error'].get('message')}")
    return body["data"]["broadcast_port"]
