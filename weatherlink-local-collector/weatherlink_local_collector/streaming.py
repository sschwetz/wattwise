from __future__ import annotations

import json
import logging
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from weatherlink_local_collector.config import Settings
from weatherlink_local_collector.mapping import map_stream_values
from weatherlink_local_collector.weatherlink_client import request_real_time

logger = logging.getLogger("weatherlink_local_collector")

# The device broadcasts every 2.5 seconds. A stream with no packet for this long
# is treated as down, and the poll goes back to pushing live values.
STREAM_STALE_SECONDS = 10
# A failed renewal is retried this soon, well inside the half-duration the
# device still has left, so a dropped request does not end the broadcast.
RETRY_SECONDS = 30


def parse_packet(data: bytes) -> tuple[str, list[dict]] | None:
    """Returns the device id and the ISS (type 1) records of a broadcast
    packet, or None for anything that is not one."""
    try:
        packet = json.loads(data)
        did = packet["did"]
        records = [c for c in packet["conditions"] if c["data_structure_type"] == 1]
    except (ValueError, KeyError, TypeError):
        return None
    return did, records


class LiveState:
    """What the live tiles show between pushes, per source: the values from the
    last poll, and when the UDP stream last delivered. The stream carries only
    wind, so each packet is laid over the last poll's values. Core API keeps one
    live sample per source, and a wind-only sample would blank the other tiles."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._polled: dict[int, tuple[dict[str, float], int | None]] = {}
        self._source_by_did: dict[str, int] = {}
        self._last_packet: dict[int, float] = {}

    def record_poll(self, source_id: int, values: dict[str, float], txid: int | None, did: str | None) -> None:
        """`txid` is the polled ISS transmitter. The stream is limited to it, so a
        second transmitter's packets cannot swap in its wind. `did` is the
        device id the packets carry, which is how a packet finds its source
        even when a relay on the way has changed the sender's address."""
        with self._lock:
            self._polled[source_id] = (values, txid)
            if did is not None:
                self._source_by_did[did] = source_id

    def source_for_did(self, did: str) -> int | None:
        with self._lock:
            return self._source_by_did.get(did)

    def stream_is_live(self, source_id: int) -> bool:
        with self._lock:
            last = self._last_packet.get(source_id)
        return last is not None and self._clock() - last <= STREAM_STALE_SECONDS

    def record_stream(self, source_id: int, iss: dict) -> dict[str, float] | None:
        """Returns the values to push for this packet, or None when there is
        nothing to push: no poll has run yet, or the packet is from another transmitter."""
        with self._lock:
            polled = self._polled.get(source_id)
            if polled is None:
                return None
            base, txid = polled
            if txid is not None and iss.get("txid") not in (None, txid):
                return None
            self._last_packet[source_id] = self._clock()
        return {**base, **map_stream_values(iss)}


class StreamListener:
    """Asks each source's device to broadcast wind and rain over UDP, renews the
    request before it runs out, and pushes each packet as a live sample. A
    packet is matched to its source by the device id it carries, not by who sent it.
    Everything runs on one thread: the socket's 1 second timeout is the tick that
    drives renewals."""

    def __init__(
        self,
        settings: Settings,
        get_sources: Callable[[], dict[int, Any]],
        state: LiveState,
        push: Callable[[int, dict[str, float]], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._get_sources = get_sources
        self._state = state
        self._push = push
        self._clock = clock
        self._next_renewal: dict[int, float] = {}
        self._failing: set[int] = set()
        self._port_warned: set[int] = set()

    def run(self, stop: threading.Event) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("", self._settings.stream_port))
        except OSError:
            logger.exception(
                "Cannot listen on UDP port %s; live values update with each poll instead", self._settings.stream_port
            )
            return
        with sock:
            sock.settimeout(1.0)
            while not stop.is_set():
                self.renew_due()
                try:
                    data, _sender = sock.recvfrom(65535)
                except TimeoutError:
                    continue
                self.handle_datagram(data)

    def renew_due(self) -> None:
        now = self._clock()
        for source_id, source in list(self._get_sources().items()):
            if self._next_renewal.get(source_id, 0.0) > now:
                continue
            renewed = self._request_broadcast(source_id, source.config["host"])
            delay = self._settings.stream_duration_seconds / 2 if renewed else RETRY_SECONDS
            self._next_renewal[source_id] = now + delay

    def _request_broadcast(self, source_id: int, host: str) -> bool:
        try:
            port = request_real_time(host, self._settings.stream_duration_seconds)
        except Exception:
            if source_id not in self._failing:
                self._failing.add(source_id)
                logger.exception("Cannot start the live stream for source %s; retrying", source_id)
            return False
        if source_id in self._failing:
            self._failing.discard(source_id)
            logger.info("Live stream request recovered for source %s", source_id)
        if port != self._settings.stream_port and source_id not in self._port_warned:
            self._port_warned.add(source_id)
            logger.warning(
                "Source %s broadcasts on UDP port %s, but this collector listens on %s (STREAM_PORT)",
                source_id,
                port,
                self._settings.stream_port,
            )
        return True

    def handle_datagram(self, data: bytes) -> None:
        packet = parse_packet(data)
        if packet is None:
            return
        did, records = packet
        source_id = self._state.source_for_did(did)
        if source_id is None or source_id not in self._get_sources():
            return
        for iss in records:
            was_live = self._state.stream_is_live(source_id)
            values = self._state.record_stream(source_id, iss)
            if values is None:
                continue
            if not was_live:
                logger.info("Receiving the live stream for source %s", source_id)
            self._push(source_id, values)
