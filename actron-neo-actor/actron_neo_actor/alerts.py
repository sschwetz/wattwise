"""Dashboard alerts for problems the owner has to fix.

An alert is sent to Core API once and kept until cleared. The state lives in
memory, so after a restart an alert still showing is sent again, and one that
should have cleared is cleared by the first success.
"""

from __future__ import annotations

import logging

from actron_neo_actor.async_core import AsyncCore
from actron_neo_actor.core_api_client import CoreApiError

_LOG = logging.getLogger(__name__)

# A failure that may be a passing network blip raises its alert only after this many in a row.
ALERT_AFTER_FAILURES = 5


class InstanceAlerts:
    def __init__(self, core: AsyncCore) -> None:
        self._core = core
        self._raised: dict[tuple[int, str], tuple[str, str]] = {}
        self._known_clear: set[tuple[int, str]] = set()
        self._failures: dict[tuple[int, str], int] = {}

    async def failure(self, instance_id: int, code: str, message: str, severity: str = "failure") -> None:
        """Counts one failure and raises the alert once ALERT_AFTER_FAILURES have come in a row."""
        key = (instance_id, code)
        self._failures[key] = self._failures.get(key, 0) + 1
        if self._failures[key] >= ALERT_AFTER_FAILURES:
            await self._raise(key, message, severity)

    async def ok(self, instance_id: int, code: str) -> None:
        """Ends a failure streak and removes the alert."""
        key = (instance_id, code)
        self._failures.pop(key, None)
        if key in self._known_clear:
            return
        try:
            await self._core.clear_alert(instance_id, code)
        except CoreApiError as err:
            _LOG.warning("Could not clear the %s alert (instance %s): %s", code, instance_id, type(err).__name__)
            return
        self._raised.pop(key, None)
        self._known_clear.add(key)

    async def _raise(self, key: tuple[int, str], message: str, severity: str, sticky: bool = False) -> None:
        self._known_clear.discard(key)
        if self._raised.get(key) == (message, severity):
            return
        try:
            await self._core.set_alert(key[0], key[1], message, severity, sticky)
        except CoreApiError as err:
            _LOG.warning("Could not report the %s alert (instance %s): %s", key[1], key[0], type(err).__name__)
            return
        self._raised[key] = (message, severity)
