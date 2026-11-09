"""Tell core-api which commit this service was built from.

The About dialog lists each service with its commit. This module has no
service-specific code; each service keeps its own copy.
"""

from __future__ import annotations

import logging
import os
import threading
import time

import httpx

logger = logging.getLogger(__name__)

REPORT_PATH = "/api/v1/services/report"
REQUEST_TIMEOUT_SECONDS = 10.0
COMMIT_LENGTH = 7


def report_commit_once(base_url: str, key_header: str, key: str, service: str, commit: str) -> bool:
    """POST the commit to core-api. True on 204, False on any other outcome."""
    try:
        response = httpx.post(
            f"{base_url}{REPORT_PATH}",
            headers={key_header: key},
            json={"service": service, "commit": commit},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.debug("Commit report failed: %s", exc)
        return False
    if response.status_code != 204:
        logger.debug("Commit report answered %s", response.status_code)
        return False
    return True


def _build_commit() -> str:
    return os.environ.get("GIT_COMMIT", "")[:COMMIT_LENGTH] or "unknown"


def _report_forever(base_url: str, key_header: str, key: str, service: str, interval_seconds: float) -> None:
    commit = _build_commit()
    failing = False
    while True:
        try:
            ok = report_commit_once(base_url, key_header, key, service, commit)
        except Exception:  # the reporter must never take the service down
            logger.exception("Commit reporter hit an unexpected error")
            ok = False
        if not ok and not failing:
            logger.warning("Could not report commit %s to core-api; will keep retrying", commit)
        failing = not ok
        time.sleep(interval_seconds)


def start_commit_reporter(
    base_url: str, key_header: str, key: str, service: str, interval_seconds: float = 60.0
) -> threading.Thread:
    """Report the build commit now and every interval_seconds, on a daemon thread."""
    thread = threading.Thread(
        target=_report_forever,
        args=(base_url, key_header, key, service, interval_seconds),
        name="commit-reporter",
        daemon=True,
    )
    thread.start()
    return thread
