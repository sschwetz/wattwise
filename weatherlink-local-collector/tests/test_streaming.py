from __future__ import annotations

import json
import logging
import socket
import threading
import time

import pytest

from weatherlink_local_collector import streaming
from weatherlink_local_collector.config import Settings
from weatherlink_local_collector.streaming import LiveState, StreamListener, parse_packet

ISS = {"lsid": 1, "data_structure_type": 1, "txid": 1, "wind_speed_last": 10.0, "wind_dir_last": 90}
OTHER = {"lsid": 2, "data_structure_type": 1, "txid": 2, "wind_speed_last": 3.0, "wind_dir_last": 10}
POLLED = {"temp_c": 22.7, "wind_speed_kph": 7.1, "wind_direction_deg": 41}
DID = "001D0A700002"


def packet(*conditions: dict) -> bytes:
    return json.dumps({"did": DID, "ts": 1532031640, "conditions": list(conditions)}).encode()


def settings(**overrides) -> Settings:
    values = {"core_api_url": "http://x", "collector_key": "k", "poll_interval_seconds": 60, "discovery_refresh_seconds": 300}
    return Settings(**{**values, **overrides})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_parse_packet_returns_the_device_id_and_the_iss_records():
    assert parse_packet(packet(ISS, {"data_structure_type": 2}, OTHER)) == (DID, [ISS, OTHER])


@pytest.mark.parametrize(
    "data",
    [b"not json", b"[]", b'{"conditions": 5, "did": "x"}', b'{"did": "x"}', b'{"conditions": []}', b"\xff\xfe"],
)
def test_parse_packet_ignores_anything_that_is_not_a_packet(data):
    assert parse_packet(data) is None


def test_live_state_finds_the_source_by_device_id():
    state = LiveState()
    state.record_poll(7, POLLED, txid=1, did=DID)

    assert state.source_for_did(DID) == 7
    assert state.source_for_did("001D0A7FFFFF") is None


def test_live_state_holds_the_stream_back_until_a_poll_gives_it_a_base():
    state = LiveState()

    assert state.record_stream(1, ISS) is None
    assert not state.stream_is_live(1)


def test_live_state_overlays_the_stream_wind_on_the_last_polled_values():
    state = LiveState()
    state.record_poll(1, POLLED, txid=1, did=DID)

    merged = state.record_stream(1, ISS)

    assert merged == {"temp_c": 22.7, "wind_speed_kph": 16.1, "wind_direction_deg": 90}
    assert POLLED["wind_speed_kph"] == 7.1  # the polled base is not changed


def test_live_state_ignores_a_transmitter_other_than_the_polled_one():
    state = LiveState()
    state.record_poll(1, POLLED, txid=1, did=DID)

    assert state.record_stream(1, OTHER) is None
    assert not state.stream_is_live(1)


def test_live_state_stream_goes_stale_when_packets_stop():
    clock = Clock()
    state = LiveState(clock=clock)
    state.record_poll(1, POLLED, txid=1, did=DID)
    state.record_stream(1, ISS)
    assert state.stream_is_live(1)

    clock.now += streaming.STREAM_STALE_SECONDS + 1

    assert not state.stream_is_live(1)


def _listener(sources, state=None, pushes=None, clock=None, **settings_overrides):
    state = state or LiveState()
    pushes = pushes if pushes is not None else []
    listener = StreamListener(
        settings(**settings_overrides),
        lambda: sources,
        state,
        lambda source_id, values: pushes.append((source_id, values)),
        clock=clock or Clock(),
    )
    return listener, state, pushes


class _Source:
    def __init__(self, host: str) -> None:
        self.config = {"host": host}


def test_listener_pushes_the_merged_values_for_a_known_device():
    listener, state, pushes = _listener({7: _Source("127.0.0.1")})
    state.record_poll(7, POLLED, txid=1, did=DID)

    listener.handle_datagram(packet(ISS))

    assert pushes == [(7, {"temp_c": 22.7, "wind_speed_kph": 16.1, "wind_direction_deg": 90})]


def test_listener_ignores_a_device_that_is_not_a_source():
    listener, state, pushes = _listener({7: _Source("127.0.0.1")})
    state.record_poll(7, POLLED, txid=1, did="001D0A7FFFFF")

    listener.handle_datagram(packet(ISS))

    assert pushes == []


def test_listener_ignores_a_source_that_has_been_removed():
    listener, state, pushes = _listener({})
    state.record_poll(7, POLLED, txid=1, did=DID)

    listener.handle_datagram(packet(ISS))

    assert pushes == []


def test_listener_renews_at_half_the_duration(monkeypatch):
    asked = []
    monkeypatch.setattr(streaming, "request_real_time", lambda host, duration: asked.append((host, duration)) or 22222)
    clock = Clock()
    listener, _, _ = _listener({7: _Source("127.0.0.1")}, clock=clock, stream_duration_seconds=300)

    listener.renew_due()
    clock.now += 149
    listener.renew_due()
    assert asked == [("127.0.0.1", 300)]

    clock.now += 2
    listener.renew_due()
    assert asked == [("127.0.0.1", 300)] * 2


def test_listener_retries_a_failed_renewal_sooner_and_logs_the_streak_once(monkeypatch, caplog):
    outcomes = [False, False, True, False]

    def request(host, duration):
        if outcomes.pop(0):
            return 22222
        raise OSError("device dropped the connection")

    monkeypatch.setattr(streaming, "request_real_time", request)
    clock = Clock()
    listener, _, _ = _listener({7: _Source("127.0.0.1")}, clock=clock)

    with caplog.at_level(logging.INFO, logger="weatherlink_local_collector"):
        for _ in range(3):
            listener.renew_due()
            clock.now += streaming.RETRY_SECONDS
    failures = [r for r in caplog.records if r.levelno == logging.ERROR]
    recoveries = [r for r in caplog.records if r.levelno == logging.INFO]

    assert len(failures) == 1
    assert len(recoveries) == 1


def test_listener_warns_once_when_the_device_uses_another_port(monkeypatch, caplog):
    monkeypatch.setattr(streaming, "request_real_time", lambda host, duration: 33333)
    clock = Clock()
    listener, _, _ = _listener({7: _Source("127.0.0.1")}, clock=clock, stream_port=22222)

    with caplog.at_level(logging.WARNING, logger="weatherlink_local_collector"):
        for _ in range(3):
            listener.renew_due()
            clock.now += 1000

    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_run_receives_a_real_udp_packet_and_stops(monkeypatch):
    monkeypatch.setattr(streaming, "request_real_time", lambda host, duration: 22222)
    port = _free_udp_port()
    state = LiveState()
    state.record_poll(7, POLLED, txid=1, did=DID)
    pushes: list = []
    listener = StreamListener(
        settings(stream_port=port), lambda: {7: _Source("127.0.0.1")}, state, lambda sid, v: pushes.append((sid, v))
    )
    stop = threading.Event()
    thread = threading.Thread(target=listener.run, args=(stop,))
    thread.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            deadline = time.monotonic() + 3
            while not pushes and time.monotonic() < deadline:
                sender.sendto(packet(ISS), ("127.0.0.1", port))
                time.sleep(0.05)
    finally:
        stop.set()
        thread.join(timeout=3)

    assert pushes and pushes[0][0] == 7
    assert pushes[0][1]["wind_speed_kph"] == 16.1
    assert not thread.is_alive()


def test_run_gives_up_quietly_when_the_port_is_taken(caplog):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as taken:
        taken.bind(("0.0.0.0", 0))
        port = taken.getsockname()[1]
        listener = StreamListener(settings(stream_port=port), lambda: {}, LiveState(), lambda sid, v: None)

        with caplog.at_level(logging.ERROR, logger="weatherlink_local_collector"):
            listener.run(threading.Event())

    assert any("UDP" in r.getMessage() for r in caplog.records)
