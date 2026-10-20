from __future__ import annotations

import datetime
import os

import pytest

from amber_pricing_collector import live_poll
from amber_pricing_collector.config import PricingSource
from amber_pricing_collector.core_api_client import CoreApiError

BASE_URL = "http://localhost:8000"


def test_poll_one_site_pushes_mapped_intervals_on_success(monkeypatch):
    monkeypatch.setattr(
        live_poll, "fetch_source_config", lambda base_url, sid, key: {"amber_site_id": "example-site-id", "amber_api_key": "psk_token"}
    )
    monkeypatch.setattr(
        live_poll,
        "fetch_current_prices",
        lambda site_id, api_key, **kwargs: [
            {"channelType": "general", "startTime": "2026-01-01T00:00:01Z", "endTime": "2026-01-01T00:05:00Z", "perKwh": 25.0}
        ],
    )
    pushed = []
    monkeypatch.setattr(live_poll, "push_price_intervals", lambda base_url, sid, key, intervals: pushed.append(intervals))

    live_poll.poll_one_site(BASE_URL, 1, "collector-key")

    assert len(pushed) == 1
    assert pushed[0] == [{"channel": "buy", "interval_start": "2026-01-01T00:00:01Z", "interval_end": "2026-01-01T00:05:00Z", "spike_status": "none", "price": "25.0"}]


def test_poll_one_site_requests_enough_forecast_to_cover_the_rest_of_any_day(monkeypatch):
    """Amber's 'current prices' endpoint only returns forecast intervals out to
    `next` * 5 minutes ahead; the webapp's today chart needs the rest of the
    calendar day, so this must stay comfortably >= 288 (24h) regardless of what
    time the poll happens to run."""
    monkeypatch.setattr(live_poll, "fetch_source_config", lambda base_url, sid, key: {"amber_site_id": "x", "amber_api_key": "y"})
    calls = []
    monkeypatch.setattr(
        live_poll,
        "fetch_current_prices",
        lambda site_id, api_key, **kwargs: calls.append(kwargs) or [],
    )
    monkeypatch.setattr(live_poll, "push_price_intervals", lambda *a, **k: None)

    live_poll.poll_one_site(BASE_URL, 1, "collector-key")

    assert len(calls) == 1
    assert calls[0]["next_count"] >= 288


def test_poll_one_site_skips_when_config_fetch_fails(monkeypatch, caplog):
    def _raise(*a, **k):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(live_poll, "fetch_source_config", _raise)
    pushed = []
    monkeypatch.setattr(live_poll, "push_price_intervals", lambda *a, **k: pushed.append(a))

    with caplog.at_level("ERROR"):
        live_poll.poll_one_site(BASE_URL, 1, "collector-key")  # must not raise

    assert pushed == []


def test_poll_one_site_skips_when_amber_fetch_fails(monkeypatch, caplog):
    monkeypatch.setattr(live_poll, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})

    def _raise(*a, **k):
        raise OSError("network unreachable")

    monkeypatch.setattr(live_poll, "fetch_current_prices", _raise)
    pushed = []
    monkeypatch.setattr(live_poll, "push_price_intervals", lambda *a, **k: pushed.append(a))

    with caplog.at_level("ERROR"):
        live_poll.poll_one_site(BASE_URL, 1, "collector-key")  # must not raise

    assert pushed == []


def test_poll_one_site_logs_but_does_not_raise_when_push_fails(monkeypatch, caplog):
    monkeypatch.setattr(live_poll, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    monkeypatch.setattr(
        live_poll,
        "fetch_current_prices",
        lambda *a, **k: [{"channelType": "general", "startTime": "2026-01-01T00:00:01Z", "endTime": "2026-01-01T00:05:00Z", "perKwh": 25.0}],
    )

    def _raise(*a, **k):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(live_poll, "push_price_intervals", _raise)

    with caplog.at_level("ERROR"):
        live_poll.poll_one_site(BASE_URL, 1, "collector-key")  # must not raise


def test_poll_one_site_pushes_nothing_when_all_intervals_unmappable(monkeypatch):
    monkeypatch.setattr(live_poll, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    monkeypatch.setattr(live_poll, "fetch_current_prices", lambda *a, **k: [{"channelType": "unknownType"}])
    pushed = []
    monkeypatch.setattr(live_poll, "push_price_intervals", lambda *a, **k: pushed.append(a))

    live_poll.poll_one_site(BASE_URL, 1, "collector-key")

    assert pushed == []  # never calls push with an empty batch


def test_poll_one_site_requests_one_previous_interval_and_the_full_forecast(monkeypatch):
    monkeypatch.setattr(live_poll, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    calls = []
    monkeypatch.setattr(live_poll, "fetch_current_prices", lambda site_id, api_key, **kwargs: calls.append(kwargs) or [])
    monkeypatch.setattr(live_poll, "push_price_intervals", lambda *a, **k: None)

    live_poll.poll_one_site(BASE_URL, 1, "collector-key")

    assert calls == [{"previous": 1, "next_count": live_poll.FORECAST_INTERVALS}]


def test_poll_one_site_recent_requests_the_trailing_window_and_no_forecast(monkeypatch):
    """poll_one_site_recent must re-send the last RECENT_PREVIOUS_INTERVALS closed
    intervals (so a post-close Amber revision is picked up) and skip the forecast
    entirely -- it runs every minute and must stay a cheap, narrow push."""
    monkeypatch.setattr(live_poll, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    calls = []
    monkeypatch.setattr(live_poll, "fetch_current_prices", lambda site_id, api_key, **kwargs: calls.append(kwargs) or [])
    monkeypatch.setattr(live_poll, "push_price_intervals", lambda *a, **k: None)

    live_poll.poll_one_site_recent(BASE_URL, 1, "collector-key")

    assert calls == [{"previous": live_poll.RECENT_PREVIOUS_INTERVALS, "next_count": 0}]


def test_poll_one_site_recent_pushes_mapped_intervals_on_success(monkeypatch):
    monkeypatch.setattr(
        live_poll, "fetch_source_config", lambda base_url, sid, key: {"amber_site_id": "example-site-id", "amber_api_key": "psk_token"}
    )
    monkeypatch.setattr(
        live_poll,
        "fetch_current_prices",
        lambda site_id, api_key, **kwargs: [
            {"channelType": "feedIn", "startTime": "2026-01-01T00:00:01Z", "endTime": "2026-01-01T00:05:00Z", "perKwh": -5.0}
        ],
    )
    pushed = []
    monkeypatch.setattr(live_poll, "push_price_intervals", lambda base_url, sid, key, intervals: pushed.append(intervals))

    live_poll.poll_one_site_recent(BASE_URL, 1, "collector-key")

    assert pushed == [[{"channel": "sell", "interval_start": "2026-01-01T00:00:01Z", "interval_end": "2026-01-01T00:05:00Z", "spike_status": "none", "price": "-5.0"}]]


def test_poll_one_site_recent_skips_when_config_fetch_fails(monkeypatch, caplog):
    def _raise(*a, **k):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(live_poll, "fetch_source_config", _raise)
    pushed = []
    monkeypatch.setattr(live_poll, "push_price_intervals", lambda *a, **k: pushed.append(a))

    with caplog.at_level("ERROR"):
        live_poll.poll_one_site_recent(BASE_URL, 1, "collector-key")  # must not raise

    assert pushed == []


class FakeProcess:
    """Stands in for multiprocessing.Process so tests don't actually fork --
    records what it was constructed with and what was called on it."""

    instances: list["FakeProcess"] = []

    def __init__(self, target, args):
        self.target = target
        self.args = args
        self.started = False
        self.joined_with_timeout = None
        self.terminated = False
        FakeProcess.instances.append(self)

    def start(self):
        self.started = True

    def join(self, timeout=None):
        self.joined_with_timeout = timeout

    def is_alive(self):
        return False


def test_run_live_poll_forever_forks_one_child_per_source(monkeypatch):
    FakeProcess.instances = []
    monkeypatch.setattr(live_poll.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now: now)
    monkeypatch.setattr(
        live_poll,
        "fetch_collector_sources",
        lambda base_url, key: [PricingSource(source_id=1), PricingSource(source_id=2)],
    )

    calls = {"count": 0}

    def _fake_sleep_then_stop(seconds):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep_then_stop)

    with pytest.raises(SystemExit):
        live_poll.run_live_poll_forever(BASE_URL, "shared-key")

    assert len(FakeProcess.instances) == 2
    assert {p.args[1] for p in FakeProcess.instances} == {1, 2}
    assert all(p.args[2] == "shared-key" for p in FakeProcess.instances)
    assert all(p.started for p in FakeProcess.instances)
    assert all(p.joined_with_timeout == live_poll.CHILD_JOIN_TIMEOUT_SECONDS for p in FakeProcess.instances)


def test_run_live_poll_forever_terminates_child_that_does_not_join_in_time(monkeypatch):
    class StuckProcess(FakeProcess):
        def is_alive(self):
            return not self.terminated

        def terminate(self):
            self.terminated = True

    FakeProcess.instances = []
    monkeypatch.setattr(live_poll.multiprocessing, "Process", StuckProcess)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now: now)
    monkeypatch.setattr(live_poll, "fetch_collector_sources", lambda base_url, key: [PricingSource(source_id=1)])

    calls = {"count": 0}

    def _fake_sleep_then_stop(seconds):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep_then_stop)

    with pytest.raises(SystemExit):
        live_poll.run_live_poll_forever(BASE_URL, "shared-key")

    assert FakeProcess.instances[0].terminated is True


def test_run_live_poll_forever_survives_unexpected_exception(monkeypatch):
    def _raise_process(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(live_poll.multiprocessing, "Process", _raise_process)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now: now)
    monkeypatch.setattr(live_poll, "fetch_collector_sources", lambda base_url, key: [PricingSource(source_id=1)])

    calls = {"count": 0}

    def _fake_sleep_then_stop(seconds):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep_then_stop)

    with pytest.raises(SystemExit):
        live_poll.run_live_poll_forever(BASE_URL, "shared-key")

    assert calls["count"] == 2


def test_run_live_poll_forever_skips_tick_when_discovery_fails(monkeypatch):
    FakeProcess.instances = []
    monkeypatch.setattr(live_poll.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now: now)

    def _raise(base_url, key):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(live_poll, "fetch_collector_sources", _raise)

    calls = {"count": 0}

    def _fake_sleep_then_stop(seconds):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep_then_stop)

    with pytest.raises(SystemExit):
        live_poll.run_live_poll_forever(BASE_URL, "shared-key")

    assert FakeProcess.instances == []  # no children forked this tick, but the loop kept going


def _real_child_target(core_api_url: str, source_id: int, collector_key: str) -> None:
    """Real, module-level, picklable function for a real (non-mocked)
    multiprocessing test -- must not be a closure/lambda so it survives
    both 'fork' (Linux/Docker) and 'spawn' (macOS dev) start methods.
    `collector_key` carries the marker-file path so the child can prove it
    ran, without needing any shared/mocked state with the parent."""
    if source_id == 1:
        os._exit(1)  # hard crash, no Python-level unwind -- simulates a fatal child failure
    with open(collector_key, "w") as f:
        f.write("done")


def test_run_recent_poll_forever_forks_one_child_per_source_running_the_recent_worker(monkeypatch):
    FakeProcess.instances = []
    monkeypatch.setattr(live_poll.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now, **kwargs: now)
    monkeypatch.setattr(
        live_poll,
        "fetch_collector_sources",
        lambda base_url, key: [PricingSource(source_id=1), PricingSource(source_id=2)],
    )

    calls = {"count": 0}

    def _fake_sleep_then_stop(seconds):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep_then_stop)

    with pytest.raises(SystemExit):
        live_poll.run_recent_poll_forever(BASE_URL, "shared-key")

    assert len(FakeProcess.instances) == 2
    assert all(p.target is live_poll.poll_one_site_recent for p in FakeProcess.instances)
    assert {p.args[1] for p in FakeProcess.instances} == {1, 2}


def test_run_live_poll_forever_real_fork_isolates_sibling_crash(monkeypatch, tmp_path):
    """Uses REAL multiprocessing.Process (not FakeProcess) to prove actual
    OS-level process isolation, per Review Focus item 5: one site's child
    crashing must not prevent another site's child from completing the same
    tick."""
    monkeypatch.setattr(live_poll, "poll_one_site", _real_child_target)
    monkeypatch.setattr(live_poll, "next_aligned_tick", lambda now: now)
    monkeypatch.setattr(
        live_poll,
        "fetch_collector_sources",
        lambda base_url, key: [PricingSource(source_id=1), PricingSource(source_id=2)],
    )

    marker_path = tmp_path / "sibling_done.txt"

    calls = {"n": 0}

    def _fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise SystemExit

    monkeypatch.setattr(live_poll.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        live_poll.run_live_poll_forever(BASE_URL, str(marker_path))

    assert marker_path.read_text() == "done"
