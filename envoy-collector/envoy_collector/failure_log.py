from __future__ import annotations

import logging
import time
from collections.abc import Callable

logger = logging.getLogger("envoy_collector")

# Within a streak of failures, at most one repeat warning per this many seconds.
FAILURE_LOG_INTERVAL_SECONDS = 600


class FailureLog:
    """Logs a repeating failure without writing a line per attempt.

    The first failure of a streak logs ERROR with the traceback, so
    failure() must be called inside an except block. Later failures log a
    WARNING with a count at most once per FAILURE_LOG_INTERVAL_SECONDS.
    success() ends the streak.
    """

    def __init__(self, message: str, clock: Callable[[], float] = time.monotonic) -> None:
        self._message = message
        self._clock = clock
        self._failing = False
        self._last_logged_at = 0.0
        self._unlogged = 0

    def failure(self) -> None:
        """Record one failure and log it if the streak rules allow."""
        now = self._clock()
        if not self._failing:
            self._failing = True
            self._last_logged_at = now
            self._unlogged = 0
            logger.exception("%s", self._message)
            return
        self._unlogged += 1
        if now - self._last_logged_at >= FAILURE_LOG_INTERVAL_SECONDS:
            logger.warning("%s: still failing, %d failures since last log", self._message, self._unlogged)
            self._last_logged_at = now
            self._unlogged = 0

    def success(self) -> None:
        """End the current streak, so the next failure logs a traceback again."""
        self._failing = False
