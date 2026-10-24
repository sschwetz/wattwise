from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import replace

from envoy_collector.failure_log import FailureLog
from envoy_collector.mapping import LiveSample, parse_livedata

# Most samples held between drains; the oldest are dropped past this. At the
# default 3-second interval that is 30 minutes, so it only matters if
# poll_once stops draining.
MAX_SAMPLES = 600


class LiveSampler:
    """Polls the gateway's live feed on a background thread and keeps new samples.

    The feed repeats its last reading until the gateway refreshes it, so a
    sample is kept only when its last_update differs from the previous one.
    The owner calls drain() once a minute, replaces `config` when the token
    or pinned address changes, and checks is_fresh() to choose between live
    and polled readings. `label` (for example "source 3") appears in every
    log line and in the thread name. Repeated failures are rate-limited by
    FailureLog, with separate streaks for the fetch and the keepalive.
    on_sample, when given, is called with the current config and the newest
    sample after every successful poll, so a dashboard sees a push each time
    a poll finishes. A poll that returns an unchanged reading repeats the
    last sample, but only while the feed is fresh: once the gateway has not
    refreshed for stale_seconds nothing is pushed. Its exceptions are logged
    and dropped.

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
        *,
        label: str,
        interval_seconds: float,
        stale_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        on_sample: Callable[[LiveSample, dict], None] | None = None,
    ) -> None:
        self.config = config
        self.label = label
        self._fetch = fetch
        self._enable_stream = enable_stream
        self._interval = interval_seconds
        self._stale = stale_seconds
        self._clock = clock
        self._on_sample = on_sample
        self._lock = threading.Lock()
        self._samples: deque[LiveSample] = deque(maxlen=MAX_SAMPLES)
        self._last_update: int | None = None
        self._latest: LiveSample | None = None
        self._last_sample_at: float | None = None
        self._has_battery = False
        # Start of the current keepalive window: the latest of creation, the
        # last new sample and the last keepalive.
        self._window_start = clock()
        self._last_call_ok = False
        self._fetch_log = FailureLog(f"Live-feed call failed for {label}", clock)
        self._keepalive_log = FailureLog(f"Live-feed keepalive failed for {label}", clock)
        self._push_log = FailureLog(f"Live push failed for {label}", clock)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        name = f"live-sampler-{self.label.replace(' ', '-')}"
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
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
            self._fetch_log.failure()
            return
        self._fetch_log.success()
        self._last_call_ok = True
        if sample.last_update != self._last_update:
            self._keep(sample)
        else:
            self._maybe_keepalive()
            if self._latest is not None and self.is_fresh():
                self._notify(self._latest)

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
        self._latest = sample
        self._last_sample_at = now
        self._window_start = now
        self._notify(sample)

    def _notify(self, sample: LiveSample) -> None:
        """Hand a kept sample to on_sample. A callback failure never stops sampling."""
        if self._on_sample is None:
            return
        try:
            self._on_sample(sample, self.config)
        except Exception:
            self._push_log.failure()
        else:
            self._push_log.success()

    def _maybe_keepalive(self) -> None:
        now = self._clock()
        if now - self._window_start < self._stale:
            return
        self._window_start = now
        try:
            self._enable_stream(self.config)
        except Exception:
            self._keepalive_log.failure()
        else:
            self._keepalive_log.success()

    def drain(self) -> list[LiveSample]:
        """Return the samples collected since the last call and clear them."""
        with self._lock:
            samples = list(self._samples)
            self._samples.clear()
        return samples

    def is_fresh(self) -> bool:
        """True when the last call worked and a new sample arrived within stale_seconds."""
        if not self._last_call_ok or self._last_sample_at is None:
            return False
        return self._clock() - self._last_sample_at <= self._stale
