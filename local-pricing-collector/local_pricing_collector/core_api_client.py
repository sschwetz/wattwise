from __future__ import annotations

import httpx

PROVIDER = "local"


class CoreApiError(Exception):
    """Raised on any failure talking to Core API: a non-success response or a
    transport failure. Callers only catch this one type."""


def _headers(collector_key: str) -> dict[str, str]:
    return {"X-Collector-Key": collector_key, "X-Collector-Provider": PROVIDER}


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return httpx.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise CoreApiError(f"Failed to reach Core API: {exc}") from exc


def fetch_collector_sources(base_url: str, collector_key: str) -> list[int]:
    response = _request(
        "GET",
        f"{base_url}/api/v1/collector/sources",
        params={"kind": "pricing", "provider": PROVIDER},
        headers=_headers(collector_key),
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch collector sources: {response.status_code} {response.text}")
    try:
        return [entry["source_id"] for entry in response.json()]
    except (ValueError, KeyError, TypeError) as exc:
        raise CoreApiError(f"Unexpected collector sources body: {response.text}") from exc


def fetch_source_config(base_url: str, source_id: int, collector_key: str) -> dict:
    response = _request(
        "GET", f"{base_url}/api/v1/sources/{source_id}/config", headers=_headers(collector_key), timeout=10
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch source config: {response.status_code} {response.text}")
    try:
        return response.json()["config"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CoreApiError(f"Unexpected source config body: {response.text}") from exc


def push_price_intervals(
    base_url: str, source_id: int, collector_key: str, intervals: list[dict], *, timeout: float = 60
) -> None:
    """The server writes one row per covered minute, so a 48-hour push is a few
    thousand rows per channel. The timeout is longer than the 10 seconds used
    for small reads."""
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/prices",
        headers=_headers(collector_key),
        json={"intervals": intervals},
        timeout=timeout,
    )
    if response.status_code != 202:
        raise CoreApiError(f"Failed to push price intervals: {response.status_code} {response.text}")
