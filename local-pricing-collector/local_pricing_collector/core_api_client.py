from __future__ import annotations

import datetime

import httpx

from local_pricing_collector.models import Coverage, LocalSource

PROVIDER = "local"


class CoreApiError(Exception):
    """Raised on any failure talking to Core API: a non-success response, a
    transport failure or a body of an unexpected shape. Callers only catch
    this one type."""


def _headers(collector_key: str) -> dict[str, str]:
    return {"X-Collector-Key": collector_key, "X-Collector-Provider": PROVIDER}


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return httpx.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise CoreApiError(f"Failed to reach Core API: {exc}") from exc


def _parse_utc(value: str) -> datetime.datetime:
    """An instant as aware UTC. core-api sends some timestamps without an
    offset (the site data floor); those are UTC."""
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _get_json(url: str, collector_key: str, what: str):
    response = _request("GET", url, headers=_headers(collector_key), timeout=10)
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch {what}: {response.status_code} {response.text}")
    try:
        return response.json()
    except ValueError as exc:
        raise CoreApiError(f"Unexpected {what} body: {response.text}") from exc


def fetch_collector_sources(base_url: str, collector_key: str) -> list[LocalSource]:
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
        return [LocalSource(entry["source_id"], entry["config"]) for entry in response.json()]
    except (ValueError, KeyError, TypeError) as exc:
        raise CoreApiError(f"Unexpected collector sources body: {response.text}") from exc


def fetch_site_data_floor(base_url: str, source_id: int, collector_key: str) -> datetime.datetime | None:
    body = _get_json(f"{base_url}/api/v1/sources/{source_id}/site-data-floor", collector_key, "site data floor")
    try:
        floor_ts = body["floor_ts"]
        return _parse_utc(floor_ts) if floor_ts else None
    except (KeyError, TypeError, ValueError) as exc:
        raise CoreApiError(f"Unexpected site data floor body: {body!r}") from exc


def fetch_price_coverage(base_url: str, source_id: int, collector_key: str) -> dict[str, Coverage]:
    body = _get_json(f"{base_url}/api/v1/sources/{source_id}/prices/coverage", collector_key, "price coverage")
    try:
        return {
            channel: Coverage(first=_parse_utc(entry["first"]), last=_parse_utc(entry["last"]))
            for channel, entry in body["channels"].items()
        }
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CoreApiError(f"Unexpected price coverage body: {body!r}") from exc


def push_price_intervals(
    base_url: str, source_id: int, collector_key: str, intervals: list[dict], *, timeout: float = 120
) -> None:
    """The server writes one row per covered minute, so a 7-day chunk is
    about 10,000 rows per channel. The timeout is longer than the 10 seconds
    used for the small reads."""
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/prices",
        headers=_headers(collector_key),
        json={"intervals": intervals},
        timeout=timeout,
    )
    if response.status_code != 202:
        raise CoreApiError(f"Failed to push price intervals: {response.status_code} {response.text}")


def clear_reprice_marker(base_url: str, source_id: int, collector_key: str, version: int) -> None:
    """Tells core-api the repricing for this marker version is done. core-api
    keeps the marker if a newer change has raised the version."""
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/prices/reprice-marker/clear",
        headers=_headers(collector_key),
        json={"version": version},
        timeout=10,
    )
    if response.status_code != 204:
        raise CoreApiError(f"Failed to clear the reprice marker: {response.status_code} {response.text}")
