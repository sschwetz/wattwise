"""Dashboard alerts for problems the owner has to fix.

An alert is sent to Core API once and kept until cleared. The state lives in
memory, so after a restart an alert still showing is sent again, and one that
should have cleared is cleared by the first success.
"""

from __future__ import annotations

import logging

from local_pricing_collector.config import Settings
from local_pricing_collector.core_api_client import CoreApiError, clear_source_alert, set_source_alert

logger = logging.getLogger("local_pricing_collector")

# A failure that may be a passing network blip raises its alert only after this many in a row.
ALERT_AFTER_FAILURES = 5

_Key = tuple[int, str]
_raised: dict[_Key, str] = {}
_known_clear: set[_Key] = set()
_failures: dict[_Key, int] = {}


def reset() -> None:
    _raised.clear()
    _known_clear.clear()
    _failures.clear()


def raise_alert(settings: Settings, source_id: int, code: str, message: str) -> None:
    """Shows `message` on the Dashboard. Core API is only told when the message differs from the last one sent."""
    key = (source_id, code)
    _known_clear.discard(key)
    if _raised.get(key) == message:
        return
    try:
        set_source_alert(settings.core_api_url, source_id, settings.collector_key, code, message)
    except CoreApiError:
        logger.exception("Failed to report the %s alert to Core API for source %s", code, source_id)
        return
    _raised[key] = message


def clear_alert(settings: Settings, source_id: int, code: str) -> None:
    """Removes the alert, and ends any failure streak counting towards it."""
    key = (source_id, code)
    _failures.pop(key, None)
    if key in _known_clear:
        return
    try:
        clear_source_alert(settings.core_api_url, source_id, settings.collector_key, code)
    except CoreApiError:
        logger.exception("Failed to clear the %s alert in Core API for source %s", code, source_id)
        return
    _raised.pop(key, None)
    _known_clear.add(key)


def report_failure(settings: Settings, source_id: int, code: str, message: str) -> None:
    """Counts one failure and raises the alert once ALERT_AFTER_FAILURES have come in a row."""
    key = (source_id, code)
    _failures[key] = _failures.get(key, 0) + 1
    if _failures[key] >= ALERT_AFTER_FAILURES:
        raise_alert(settings, source_id, code, message)
