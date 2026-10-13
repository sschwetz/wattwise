from __future__ import annotations

import httpx


class CoreApiError(Exception):
    """Raised on any failure talking to Core API — a non-success response or
    a transport failure (connection refused, timeout, DNS, etc.). Callers
    only ever need to catch this one type, never httpx's own exceptions."""


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return httpx.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise CoreApiError(f"Failed to reach Core API: {exc}") from exc


def fetch_source_config(base_url: str, source_id: int, collector_key: str) -> dict:
    response = _request(
        "GET",
        f"{base_url}/api/v1/sources/{source_id}/config",
        headers={"X-Collector-Key": collector_key},
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch source config: {response.status_code} {response.text}")
    return response.json()["config"]


def fetch_collector_sources(base_url: str, collector_key: str) -> list[dict]:
    response = _request(
        "GET",
        f"{base_url}/api/v1/collector/sources",
        params={"kind": "energy"},
        headers={"X-Collector-Key": collector_key},
        timeout=10,
    )
    if response.status_code != 200:
        raise CoreApiError(f"Failed to fetch collector sources: {response.status_code} {response.text}")
    return response.json()


def report_discovered_config(
    base_url: str,
    source_id: int,
    collector_key: str,
    *,
    serial_num: str | None = None,
    cert_fingerprint: str | None = None,
    token: str | None = None,
) -> None:
    fields = {"serial_num": serial_num, "cert_fingerprint": cert_fingerprint, "token": token}
    payload = {k: v for k, v in fields.items() if v is not None}
    response = _request(
        "PATCH",
        f"{base_url}/api/v1/sources/{source_id}/discovered-config",
        headers={"X-Collector-Key": collector_key},
        json=payload,
        timeout=10,
    )
    if response.status_code != 204:
        raise CoreApiError(
            f"Failed to report discovered config: {response.status_code} {response.text}"
        )


def push_energy_readings(base_url: str, source_id: int, collector_key: str, readings: list[dict]) -> None:
    payload = {
        "readings": [
            {"ts": r["ts"].isoformat(), "metric": r["metric"], "value": r["value"]} for r in readings
        ]
    }
    response = _request(
        "POST",
        f"{base_url}/api/v1/sources/{source_id}/readings/energy",
        headers={"X-Collector-Key": collector_key},
        json=payload,
        timeout=10,
    )
    if response.status_code != 202:
        raise CoreApiError(f"Failed to push readings: {response.status_code} {response.text}")
