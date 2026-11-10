from __future__ import annotations

import datetime
from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from core_api.config import get_settings
from core_api.service_status import COMMIT_PATTERN, record_report

FETCH_TIMEOUT_SECONDS = 5


def _http_client() -> httpx.Client:
    return httpx.Client(timeout=FETCH_TIMEOUT_SECONDS)


def fetch_webapp_commit(base_url: str) -> str | None:
    """Read the commit from the webapp's GET /version. Returns None on any
    transport error, non-200 status, non-JSON body or invalid commit."""
    try:
        with _http_client() as client:
            response = client.get(f"{base_url.rstrip('/')}/version")
        if response.status_code != 200:
            return None
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    commit = body.get("commit") if isinstance(body, dict) else None
    if isinstance(commit, str) and COMMIT_PATTERN.match(commit):
        return commit
    return None


def run_service_status_job(
    db: Session,
    now: datetime.datetime,
    fetch_version: Callable[[str], str | None] = fetch_webapp_commit,
) -> None:
    """Record core-api's own commit, and the webapp's when /version answers."""
    settings = get_settings()
    record_report(db, "core-api", settings.git_commit, now)
    webapp_commit = fetch_version(settings.webapp_version_url)
    if webapp_commit is not None:
        record_report(db, "webapp", webapp_commit, now)
