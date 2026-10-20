from __future__ import annotations

import datetime

import httpx

AMBER_BASE_URL = "https://api.amber.com.au/v1"


def fetch_current_prices(
    amber_site_id: str, amber_api_key: str, *, previous: int = 1, next_count: int = 12, resolution: int = 5
) -> list[dict]:
    response = httpx.get(
        f"{AMBER_BASE_URL}/sites/{amber_site_id}/prices/current",
        params={"previous": previous, "next": next_count, "resolution": resolution},
        headers={"Authorization": f"Bearer {amber_api_key}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def fetch_historical_prices(
    amber_site_id: str,
    amber_api_key: str,
    start_date: datetime.date,
    end_date: datetime.date,
    *,
    resolution: int = 5,
) -> list[dict]:
    response = httpx.get(
        f"{AMBER_BASE_URL}/sites/{amber_site_id}/prices",
        params={"startDate": start_date.isoformat(), "endDate": end_date.isoformat(), "resolution": resolution},
        headers={"Authorization": f"Bearer {amber_api_key}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
