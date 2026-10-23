from __future__ import annotations

import copy
import json
import logging
import pathlib
import threading
import time

import pytest

from envoy_collector.failure_log import FAILURE_LOG_INTERVAL_SECONDS
from envoy_collector.live_sampler import MAX_SAMPLES, LiveSampler
from envoy_collector.mapping import LiveSample

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
BASE = json.loads((FIXTURES / "livedata_status.json").read_text())


def status(last_update: int) -> dict:
    data = copy.deepcopy(BASE)
    data["meters"]["last_update"] = last_update
    return data


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Harness:
    """A sampler wired to a scripted fetch, a recording keepalive and a fake clock."""

    def __init__(self, stale_seconds: float = 10.0) -> None:
        self.clock = FakeClock()
        self.results: list[object] = []
        self.configs: list[dict] = []
        self.keepalives: list[dict] = []
        self.sampler = LiveSampler(
            {"token": "t1"},
            self.fetch,
            self.keepalives.append,
            label="source 7",
            interval_seconds=1.0,
            stale_seconds=stale_seconds,
            clock=self.clock,
        )

    def fetch(self, config: dict) -> dict:
        self.configs.append(config)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[return-value]

    def step(self, result: object) -> None:
        self.results.append(result)
        self.sampler.sample_once()


def test_new_last_update_is_kept():
    h = Harness()
    h.step(status(1))
    h.step(status(2))
    assert [s.last_update for s in h.sampler.drain()] == [1, 2]


def test_repeated_last_update_is_dropped():
    h = Harness()
    h.step(status(1))
    h.step(status(1))
    assert [s.last_update for s in h.sampler.drain()] == [1]


def _no_battery_status(last_update: int) -> dict:
    data = status(last_update)
    meters = data["meters"]
    meters.update(enc_agg_energy=0, acb_agg_energy=0, soc=0)
    meters["storage"] = {"agg_p_mw": 0, "agg_p_ph_a_mw": 0, "agg_p_ph_b_mw": 0, "agg_p_ph_c_mw": 0}
    return data


def test_battery_presence_is_latched():
    h = Harness()
    h.step(status(1))
    h.step(_no_battery_status(2))
    assert [s.has_battery for s in h.sampler.drain()] == [True, True]


def test_batteryless_site_stays_without_battery():
    h = Harness()
    h.step(_no_battery_status(1))
    h.step(_no_battery_status(2))
    assert [s.has_battery for s in h.sampler.drain()] == [False, False]


def test_sample_list_is_capped_keeping_the_newest():
    h = Harness()
    for n in range(1, MAX_SAMPLES + 6):
        h.step(status(n))
    drained = h.sampler.drain()
    assert len(drained) == MAX_SAMPLES
    assert drained[0].last_update == 6
    assert drained[-1].last_update == MAX_SAMPLES + 5


def test_drain_returns_samples_once_and_clears():
    h = Harness()
    h.step(status(1))
    first = h.sampler.drain()
    assert len(first) == 1 and isinstance(first[0], LiveSample)
    assert h.sampler.drain() == []


def test_is_fresh_false_before_first_sample():
    assert Harness().sampler.is_fresh() is False


def test_is_fresh_expires_after_stale_seconds():
    h = Harness(stale_seconds=10)
    h.step(status(1))
    assert h.sampler.is_fresh() is True
    h.clock.now += 9
    assert h.sampler.is_fresh() is True
    h.clock.now += 2
    assert h.sampler.is_fresh() is False


def test_frozen_feed_triggers_keepalive_once_per_stale_window():
    h = Harness(stale_seconds=10)
    h.step(status(1))
    for _ in range(3):
        h.clock.now += 3
        h.step(status(1))
    assert h.keepalives == []
    h.clock.now += 3
    for _ in range(3):
        h.step(status(1))
    assert len(h.keepalives) == 1
    h.clock.now += 11
    h.step(status(1))
    h.step(status(1))
    assert len(h.keepalives) == 2


def test_fresh_sampler_waits_a_window_before_keepalive():
    h = Harness(stale_seconds=10)
    h.clock.now += 5
    h.step(status(1))
    h.step(status(1))
    assert h.keepalives == []


def test_keepalive_receives_current_config():
    h = Harness(stale_seconds=10)
    h.step(status(1))
    h.clock.now += 11
    h.step(status(1))
    assert h.keepalives == [{"token": "t1"}]


def test_fetch_failure_does_not_raise_and_is_not_fresh():
    h = Harness(stale_seconds=10)
    h.step(status(1))
    assert h.sampler.is_fresh() is True
    h.step(RuntimeError("boom"))
    assert h.sampler.is_fresh() is False
    h.clock.now += 100
    h.step(RuntimeError("boom again"))
    assert h.keepalives == []


def test_fetch_recovery_makes_sampler_fresh_again():
    h = Harness(stale_seconds=10)
    h.step(status(1))
    h.step(RuntimeError("boom"))
    h.step(status(2))
    assert h.sampler.is_fresh() is True


def test_malformed_response_is_swallowed_and_not_a_frozen_feed():
    h = Harness(stale_seconds=10)
    h.step(status(1))
    bad = status(2)
    del bad["meters"]["pv"]
    h.step(bad)
    assert h.sampler.is_fresh() is False
    h.clock.now += 100
    h.step(bad)
    assert h.keepalives == []
    assert [s.last_update for s in h.sampler.drain()] == [1]


def test_null_power_in_live_feed_is_treated_as_malformed():
    h = Harness()
    bad = status(1)
    bad["meters"]["grid"]["agg_p_mw"] = None
    h.step(bad)
    assert h.sampler.is_fresh() is False
    assert h.sampler.drain() == []


def test_keepalive_failure_does_not_raise():
    h = Harness(stale_seconds=10)
    calls: list[dict] = []

    def failing(config: dict) -> None:
        calls.append(config)
        raise RuntimeError("no")

    h.sampler._enable_stream = failing
    h.step(status(1))
    h.clock.now += 11
    h.step(status(1))
    assert len(calls) == 1


def test_replaced_config_is_passed_to_fetch():
    h = Harness()
    h.step(status(1))
    h.sampler.config = {"token": "t2"}
    h.step(status(2))
    assert h.configs == [{"token": "t1"}, {"token": "t2"}]


def test_start_and_stop_run_the_thread():
    calls: list[dict] = []
    counter = iter(range(1, 10_000))

    def fetch(config: dict) -> dict:
        calls.append(config)
        return status(next(counter))

    sampler = LiveSampler({}, fetch, lambda c: None, label="test", interval_seconds=0.01, stale_seconds=5)
    before = threading.active_count()
    sampler.start()
    sampler.start()
    assert threading.active_count() == before + 1
    deadline = time.monotonic() + 2
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(calls) >= 2
    sampler.stop()
    assert threading.active_count() == before


def _records(caplog: pytest.LogCaptureFixture) -> list[tuple[int, bool]]:
    return [(r.levelno, r.exc_info is not None) for r in caplog.records]


ERROR_TB = (logging.ERROR, True)
WARN = (logging.WARNING, False)


def _failing_keepalive(config: dict) -> None:
    raise RuntimeError("no")


def test_repeated_fetch_failures_are_rate_limited(caplog):
    caplog.set_level(logging.WARNING, logger="envoy_collector")
    h = Harness()
    h.step(RuntimeError("a"))
    h.step(RuntimeError("b"))
    assert _records(caplog) == [ERROR_TB]
    h.clock.now += FAILURE_LOG_INTERVAL_SECONDS
    h.step(RuntimeError("c"))
    assert _records(caplog) == [ERROR_TB, WARN]
    assert "2 failures since last log" in caplog.records[-1].getMessage()


def test_log_messages_and_thread_name_carry_the_label(caplog):
    caplog.set_level(logging.WARNING, logger="envoy_collector")
    h = Harness()
    h.step(RuntimeError("a"))
    assert "source 7" in caplog.records[0].getMessage()
    h.results.extend(status(n) for n in range(1, 10_000))
    h.sampler._interval = 0.01
    h.sampler.start()
    try:
        assert h.sampler._thread is not None
        assert h.sampler._thread.name == "live-sampler-source-7"
    finally:
        h.sampler.stop()


def test_failure_after_success_logs_error_again(caplog):
    caplog.set_level(logging.WARNING, logger="envoy_collector")
    h = Harness()
    h.step(RuntimeError("a"))
    h.step(status(1))
    h.step(RuntimeError("b"))
    assert _records(caplog) == [ERROR_TB, ERROR_TB]


def test_repeated_keepalive_failures_are_rate_limited(caplog):
    caplog.set_level(logging.WARNING, logger="envoy_collector")
    h = Harness(stale_seconds=10)
    h.sampler._enable_stream = _failing_keepalive
    h.step(status(1))
    h.clock.now += 11
    h.step(status(1))
    h.clock.now += 11
    h.step(status(1))
    assert _records(caplog) == [ERROR_TB]
    h.clock.now += FAILURE_LOG_INTERVAL_SECONDS
    h.step(status(1))
    assert _records(caplog) == [ERROR_TB, WARN]


def test_fetch_failure_after_keepalive_failure_logs_error(caplog):
    caplog.set_level(logging.WARNING, logger="envoy_collector")
    h = Harness(stale_seconds=10)
    h.sampler._enable_stream = _failing_keepalive
    h.step(status(1))
    h.clock.now += 11
    h.step(status(1))
    h.step(RuntimeError("fetch"))
    assert _records(caplog) == [ERROR_TB, ERROR_TB]
