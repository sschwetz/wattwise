from __future__ import annotations

import datetime

import httpx

from pricing_collector.config import PricingSource


class CoreApiError(Exception):
    """Raised on any failure talking to Core API -- a non-success response or
    a transport failure (connection refused, timeout, DNS, etc.). Callers
    only ever need to catch this one type, never httpx's own exceptions."""


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return httpx.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise CoreApiError(f"Failed to reach Core API: {exc}") from exc


def fetch_collector_sources(base_url: str, collector_key: str) -> list[PricingSource]:
    response = _request(
        "GET",
        f"{base_url}/api/v1/collector/sources",
        params={"kind": "pricing"},
        headers={"X-Collector-Key": collector_key},
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch collector sources: {response.status_code} {response.text}")
    return [PricingSource(source_id=entry["source_id"]) for entry in response.json()]


def fetch_source_config(base_url: str, source_id: int, collector_key: str) -> dict:
    response = _request(
        "GET", f"{base_url}/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": collector_key}, timeout=10
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch source config: {response.status_code} {response.text}")
    return response.json()["config"]


def push_price_intervals(
    base_url: str, source_id: int, collector_key: str, intervals: list[dict], *, timeout: float = 10
) -> None:
    """Push a batch of mapped intervals. `timeout` is configurable because the server
    fans each interval out to one row per covered minute: a live-poll tick is a handful
    of rows and fits the 10s default, but a backfill chunk can be thousands, and a
    timeout there is unrecoverable (the next cycle re-fetches the identical chunk)."""
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/prices",
        headers={"X-Collector-Key": collector_key},
        json={"intervals": intervals},
        timeout=timeout,
    )
    if response.status_code != 202:
        raise CoreApiError(f"Failed to push price intervals: {response.status_code} {response.text}")


def fetch_site_data_floor(base_url: str, source_id: int, collector_key: str) -> datetime.datetime | None:
    response = _request(
        "GET",
        f"{base_url}/api/v1/sources/{source_id}/site-data-floor",
        headers={"X-Collector-Key": collector_key},
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch site-data floor: {response.status_code} {response.text}")
    floor_ts = response.json()["floor_ts"]
    return datetime.datetime.fromisoformat(floor_ts) if floor_ts else None


def fetch_price_gaps(base_url: str, source_id: int, collector_key: str, since: datetime.datetime) -> list[dict]:
    response = _request(
        "GET",
        f"{base_url}/api/v1/sources/{source_id}/prices/gaps",
        params={"since": since.isoformat()},
        headers={"X-Collector-Key": collector_key},
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch price gaps: {response.status_code} {response.text}")
    return response.json()["gaps"]


def report_gap_marker(base_url: str, source_id: int, collector_key: str, channel: str, gap_date: datetime.date) -> None:
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/prices/gap-markers",
        headers={"X-Collector-Key": collector_key},
        json={"channel": channel, "gap_date": gap_date.isoformat()},
        timeout=10,
    )
    if response.status_code != 204:
        raise CoreApiError(f"Failed to report gap marker: {response.status_code} {response.text}")
