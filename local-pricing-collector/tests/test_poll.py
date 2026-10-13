from __future__ import annotations

import datetime

from local_pricing_collector import poll
from local_pricing_collector.config import Settings
from local_pricing_collector.core_api_client import CoreApiError
from local_pricing_collector.scheduling import next_aligned_tick

SETTINGS = Settings(core_api_url="http://core-api:8000", collector_key="k", horizon_hours=2)
NOW = datetime.datetime(2026, 6, 15, 10, 30, tzinfo=datetime.timezone.utc)
CONFIG = {
    "timezone": "UTC",
    "rate_windows": [
        {
            "channel": "buy",
            "effective_from": "2026-01-01T00:00:00+00:00",
            "effective_to": None,
            "months": list(range(1, 13)),
            "days_of_week": list(range(7)),
            "start_time": "00:00:00",
            "end_time": "00:00:00",
            "rate_c_per_kwh": "30.0000",
        }
    ],
}


def test_sync_pushes_the_built_intervals(monkeypatch):
    pushed = []
    monkeypatch.setattr(poll, "fetch_source_config", lambda url, source_id, key: CONFIG)
    monkeypatch.setattr(
        poll, "push_price_intervals", lambda url, source_id, key, intervals, **kw: pushed.append((source_id, intervals))
    )

    poll.sync_one_source(SETTINGS, 7, now=NOW)

    assert pushed[0][0] == 7
    assert len(pushed[0][1]) == 2


def test_a_source_with_no_windows_pushes_nothing(monkeypatch):
    pushed = []
    monkeypatch.setattr(poll, "fetch_source_config", lambda url, source_id, key: {"timezone": "UTC", "rate_windows": []})
    monkeypatch.setattr(poll, "push_price_intervals", lambda *a, **kw: pushed.append(a))

    poll.sync_one_source(SETTINGS, 7, now=NOW)

    assert pushed == []


def test_a_config_fetch_failure_is_logged_and_does_not_raise(monkeypatch, caplog):
    def boom(url, source_id, key):
        raise CoreApiError("down")

    monkeypatch.setattr(poll, "fetch_source_config", boom)

    poll.sync_one_source(SETTINGS, 7, now=NOW)

    assert "Failed to fetch config" in caplog.text


def test_an_unknown_time_zone_is_logged_and_does_not_raise(monkeypatch, caplog):
    monkeypatch.setattr(poll, "fetch_source_config", lambda url, source_id, key: {**CONFIG, "timezone": "Mars/Base"})
    monkeypatch.setattr(poll, "push_price_intervals", lambda *a, **kw: None)

    poll.sync_one_source(SETTINGS, 7, now=NOW)

    assert "Failed to read config" in caplog.text


def test_a_push_failure_is_logged_and_does_not_raise(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise CoreApiError("422")

    monkeypatch.setattr(poll, "fetch_source_config", lambda url, source_id, key: CONFIG)
    monkeypatch.setattr(poll, "push_price_intervals", boom)

    poll.sync_one_source(SETTINGS, 7, now=NOW)

    assert "Failed to push" in caplog.text


def test_an_unexpected_error_is_logged_and_does_not_raise(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("surprise")

    monkeypatch.setattr(poll, "fetch_source_config", lambda url, source_id, key: CONFIG)
    monkeypatch.setattr(poll, "build_intervals", boom)

    poll.sync_one_source(SETTINGS, 7, now=NOW)

    assert "Unexpected error syncing pricing source 7" in caplog.text


def test_a_config_of_the_wrong_shape_is_logged_and_does_not_raise(monkeypatch, caplog):
    monkeypatch.setattr(poll, "fetch_source_config", lambda url, source_id, key: None)

    poll.sync_one_source(SETTINGS, 7, now=NOW)

    assert "pricing source 7" in caplog.text


def test_next_aligned_tick_is_the_next_five_minute_boundary_plus_buffer():
    now = datetime.datetime(2026, 6, 15, 10, 31, 0, tzinfo=datetime.timezone.utc)
    assert next_aligned_tick(now) == datetime.datetime(2026, 6, 15, 10, 35, 5, tzinfo=datetime.timezone.utc)


def test_next_aligned_tick_is_always_after_now():
    now = datetime.datetime(2026, 6, 15, 10, 35, 5, tzinfo=datetime.timezone.utc)
    assert next_aligned_tick(now) > now
