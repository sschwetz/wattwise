from __future__ import annotations

import httpx


# Core API waits up to 30s for a held SQLite write lock (busy_timeout in
# core_api/db.py). A shorter timeout abandons pushes the server is about to
# complete, which happens whenever the host's disk stalls.
PUSH_TIMEOUT_SECONDS = 35


class CoreApiError(Exception):
    """Raised on any failure talking to Core API — a non-success response or
    a transport failure (connection refused, timeout, DNS, etc.). Callers
    only ever need to catch this one type, never httpx's own exceptions."""


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return httpx.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise CoreApiError(f"Failed to reach Core API: {exc}") from exc


def fetch_collector_sources(base_url: str, collector_key: str) -> list[dict]:
    response = _request(
        "GET",
        f"{base_url}/api/v1/collector/sources",
        params={"kind": "weather"},
        headers={"X-Collector-Key": collector_key},
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch collector sources: {response.status_code} {response.text}")
    return response.json()


def push_weather_readings(base_url: str, source_id: int, collector_key: str, readings: list[dict]) -> None:
    payload = {
        "readings": [
            {"ts": r["ts"].isoformat(), "metric": r["metric"], "value": r["value"]} for r in readings
        ]
    }
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/readings/weather",
        headers={"X-Collector-Key": collector_key},
        json=payload,
        timeout=PUSH_TIMEOUT_SECONDS,
    )
    if response.status_code != 202:
        raise CoreApiError(f"Failed to push readings: {response.status_code} {response.text}")
