from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass

from core_api.models.readings import EnergyMetric, WeatherMetric
from core_api.models.sources import SourceKind

ENERGY_LIVE_METRICS: frozenset[str] = frozenset(m.value for m in EnergyMetric)
# rain_mm is a running total that only makes sense as a stored interval value.
WEATHER_LIVE_METRICS: frozenset[str] = frozenset(m.value for m in WeatherMetric if m is not WeatherMetric.RAIN_MM)
MAX_LIVE_VALUES = 16


@dataclass(frozen=True)
class LiveSample:
    source_id: int
    kind: SourceKind
    ts: datetime.datetime
    values: dict[str, float]


class LiveSubscription:
    def __init__(self, hub: LiveHub, site_id: int, queue_size: int) -> None:
        self.queue: asyncio.Queue[LiveSample] = asyncio.Queue(maxsize=queue_size)
        self._hub = hub
        self._site_id = site_id

    def close(self) -> None:
        self._hub._unsubscribe(self._site_id, self)


class LiveHub:
    """Latest live sample per source, plus per-site subscriber queues.

    Nothing here touches the database. asyncio.Queue is not thread-safe, so
    every caller must run on the event loop.
    """

    def __init__(self, queue_size: int = 50) -> None:
        self._queue_size = queue_size
        self._latest: dict[int, dict[int, LiveSample]] = {}
        self._subscribers: dict[int, set[LiveSubscription]] = {}

    def publish(self, site_id: int, sample: LiveSample) -> bool:
        """Store the sample and fan it out. Returns False if it is older than the stored one."""
        per_source = self._latest.setdefault(site_id, {})
        current = per_source.get(sample.source_id)
        if current is not None and sample.ts < current.ts:
            return False
        per_source[sample.source_id] = sample
        for sub in list(self._subscribers.get(site_id, ())):
            _put_dropping_oldest(sub.queue, sample)
        return True

    def latest_for_site(self, site_id: int) -> list[LiveSample]:
        return list(self._latest.get(site_id, {}).values())

    def subscribe(self, site_id: int) -> LiveSubscription:
        sub = LiveSubscription(self, site_id, self._queue_size)
        self._subscribers.setdefault(site_id, set()).add(sub)
        return sub

    def _unsubscribe(self, site_id: int, sub: LiveSubscription) -> None:
        subs = self._subscribers.get(site_id)
        if subs is None:
            return
        subs.discard(sub)
        if not subs:
            del self._subscribers[site_id]


def _put_dropping_oldest(queue: asyncio.Queue[LiveSample], sample: LiveSample) -> None:
    try:
        queue.put_nowait(sample)
    except asyncio.QueueFull:
        queue.get_nowait()
        queue.put_nowait(sample)
