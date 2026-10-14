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
