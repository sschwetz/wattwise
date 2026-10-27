from __future__ import annotations

import datetime

import httpx

# Core API waits up to 30s for a held SQLite write lock (busy_timeout in
# core_api/db.py). A shorter timeout abandons pushes the server is about to
# complete, which happens whenever the host's disk stalls.
PUSH_TIMEOUT_SECONDS = 35

# A live sample is replaced by the next one seconds later, so a slow push is
# abandoned rather than waited on.
LIVE_PUSH_TIMEOUT_SECONDS = 2


class CoreApiError(Exception):
    """Raised on any failure talking to Core API — a non-success response or
    a transport failure (connection refused, timeout, DNS, etc.). Callers
    only ever need to catch this one type, never httpx's own exceptions.
    `status_code` is set when Core API answered and None on a transport failure."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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
        raise CoreApiError(
            f"Failed to push readings: {response.status_code} {response.text}", status_code=response.status_code
        )


def push_live_sample(
    base_url: str, source_id: int, collector_key: str, ts: datetime.datetime, values: dict[str, float]
) -> None:
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/live/weather",
        headers={"X-Collector-Key": collector_key},
        json={"ts": ts.isoformat(), "values": values},
        timeout=LIVE_PUSH_TIMEOUT_SECONDS,
    )
    if response.status_code != 202:
        raise CoreApiError(f"Failed to push live sample: {response.status_code} {response.text}")
