from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from envoy_collector.mapping import LiveSample, parse_livedata

logger = logging.getLogger("envoy_collector")


class LiveSampler:
    """Polls the gateway's live feed on a background thread and keeps new samples.

    The feed repeats its last reading until the gateway refreshes it, so a
    sample is kept only when its last_update differs from the previous one.
    The owner calls drain() once a minute, replaces `config` when the token
    or pinned address changes, and checks is_fresh() to choose between live
    and polled readings.

    Battery presence is latched: once a kept sample has has_battery set,
    every later sample from this sampler is stored with has_battery=True,
    so an idle, drained battery whose readings are all zero still gets
    battery rows.
    """

    def __init__(
        self,
        config: dict,
        fetch: Callable[[dict], dict],
        enable_stream: Callable[[dict], None],
        interval_seconds: float,
        stale_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._fetch = fetch
        self._enable_stream = enable_stream
        self._interval = interval_seconds
        self._stale = stale_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._samples: list[LiveSample] = []
        self._last_update: int | None = None
        self._last_sample_at: float | None = None
        self._has_battery = False
        # Start of the current keepalive window: the latest of creation, the
        # last new sample and the last keepalive.
        self._window_start = clock()
        self._last_call_ok = False
        self._fetch_failing = False
        self._keepalive_failing = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="live-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample_once()
            self._stop.wait(self._interval)

    def sample_once(self) -> None:
        """Run one poll. Never raises."""
        try:
            sample = parse_livedata(self._fetch(self.config))
        except Exception:
            self._last_call_ok = False
            self._fetch_failing = self._log_failure("live-feed call failed", self._fetch_failing)
            return
        self._fetch_failing = False
        self._last_call_ok = True
        if sample.last_update != self._last_update:
            self._keep(sample)
        else:
            self._maybe_keepalive()

    def _keep(self, sample: LiveSample) -> None:
        """Store a new sample, applying the battery latch, and restart the keepalive window."""
        now = self._clock()
        if sample.has_battery:
            self._has_battery = True
        elif self._has_battery:
            sample = replace(sample, has_battery=True)
        with self._lock:
            self._samples.append(sample)
        self._last_update = sample.last_update
        self._last_sample_at = now
        self._window_start = now

    def _maybe_keepalive(self) -> None:
        now = self._clock()
        if now - self._window_start < self._stale:
            return
        self._window_start = now
        try:
            self._enable_stream(self.config)
        except Exception:
            self._keepalive_failing = self._log_failure(
                "live-feed keepalive failed", self._keepalive_failing
            )
        else:
            self._keepalive_failing = False

    @staticmethod
    def _log_failure(message: str, already_failing: bool) -> bool:
        """Log with a traceback on the first failure of a streak, else a warning. Returns True."""
        if already_failing:
            logger.warning("%s (still failing)", message)
        else:
            logger.exception(message)
        return True

    def drain(self) -> list[LiveSample]:
        """Return the samples collected since the last call and clear them."""
        with self._lock:
            samples, self._samples = self._samples, []
        return samples

    def is_fresh(self) -> bool:
        """True when the last call worked and a new sample arrived within stale_seconds."""
        if not self._last_call_ok or self._last_sample_at is None:
            return False
        return self._clock() - self._last_sample_at <= self._stale
