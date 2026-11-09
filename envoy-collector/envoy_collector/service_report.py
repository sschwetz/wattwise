"""Tell core-api which commit this service was built from.

The About dialog lists each service with its commit. This module has no
service-specific code; each service keeps its own copy.
"""

from __future__ import annotations

import logging
import os
import re
import threading

import httpx

logger = logging.getLogger(__name__)

REPORT_PATH = "/api/v1/services/report"
REQUEST_TIMEOUT_SECONDS = 10.0
COMMIT_LENGTH = 7
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{7}|unknown|dev")


def report_commit_once(base_url: str, key_header: str, key: str, service: str, commit: str) -> str | None:
    """POST the commit to core-api. Returns None on 204, else a short reason."""
    try:
        response = httpx.post(
            f"{base_url}{REPORT_PATH}",
            headers={key_header: key},
            json={"service": service, "commit": commit},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return f"transport error: {exc}"
    if response.status_code != 204:
        return f"HTTP {response.status_code}"
    return None


def _build_commit() -> str:
    raw = os.environ.get("GIT_COMMIT", "")
    commit = raw.strip().lower()[:COMMIT_LENGTH] or "unknown"
    if not _COMMIT_PATTERN.fullmatch(commit):
        logger.warning("GIT_COMMIT %r is not a hex commit; reporting unknown", raw)
        return "unknown"
    return commit


def _report_forever(
    base_url: str, key_header: str, key: str, service: str, interval_seconds: float, stop: threading.Event
) -> None:
    commit = _build_commit()
    failing = False
    while not stop.is_set():
        try:
            reason = report_commit_once(base_url, key_header, key, service, commit)
        except Exception:  # the reporter must never take the service down
            logger.exception("Commit reporter hit an unexpected error")
            reason = "unexpected error"
        if reason and not failing:
            logger.warning("Could not report commit %s to core-api (%s); will keep retrying", commit, reason)
        failing = reason is not None
        stop.wait(interval_seconds)


def start_commit_reporter(
    base_url: str,
    key_header: str,
    key: str,
    service: str,
    interval_seconds: float = 60.0,
    stop: threading.Event | None = None,
) -> threading.Thread:
    """Report the build commit now and every interval_seconds, on a daemon thread.

    Setting `stop` ends the loop; tests use it to avoid leaving threads behind.
    """
    thread = threading.Thread(
        target=_report_forever,
        args=(base_url, key_header, key, service, interval_seconds, stop or threading.Event()),
        name="commit-reporter",
        daemon=True,
    )
    thread.start()
    return thread
