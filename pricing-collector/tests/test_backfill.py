from __future__ import annotations

import datetime

import pytest

from pricing_collector import backfill
from pricing_collector.config import PricingSource
from pricing_collector.core_api_client import CoreApiError

BASE_URL = "http://localhost:8000"


def test_chunk_date_range_splits_into_max_7_day_chunks():
    chunks = backfill._chunk_date_range(datetime.date(2025, 1, 1), datetime.date(2025, 1, 20))
    assert chunks == [
        (datetime.date(2025, 1, 1), datetime.date(2025, 1, 7)),
        (datetime.date(2025, 1, 8), datetime.date(2025, 1, 14)),
        (datetime.date(2025, 1, 15), datetime.date(2025, 1, 20)),
    ]


def test_chunk_date_range_single_day():
    assert backfill._chunk_date_range(datetime.date(2025, 1, 1), datetime.date(2025, 1, 1)) == [
        (datetime.date(2025, 1, 1), datetime.date(2025, 1, 1))
    ]


def test_chunk_date_range_exactly_7_days():
    assert backfill._chunk_date_range(datetime.date(2025, 1, 1), datetime.date(2025, 1, 7)) == [
        (datetime.date(2025, 1, 1), datetime.date(2025, 1, 7))
    ]


def test_days_in_range_inclusive():
    assert backfill._days_in_range(datetime.date(2025, 1, 1), datetime.date(2025, 1, 3)) == [
        datetime.date(2025, 1, 1),
        datetime.date(2025, 1, 2),
        datetime.date(2025, 1, 3),
    ]


def test_backfill_one_site_does_nothing_when_floor_is_none(monkeypatch):
    monkeypatch.setattr(backfill, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    monkeypatch.setattr(backfill, "fetch_site_data_floor", lambda *a, **k: None)
    gaps_called = []
    monkeypatch.setattr(backfill, "fetch_price_gaps", lambda *a, **k: gaps_called.append(a))

    backfill.backfill_one_site(BASE_URL, 1, "collector-key")  # must not raise

    assert gaps_called == []


def test_backfill_one_site_fetches_gap_range_and_pushes(monkeypatch):
    monkeypatch.setattr(backfill, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    monkeypatch.setattr(
        backfill, "fetch_site_data_floor", lambda *a, **k: datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    )
    monkeypatch.setattr(
        backfill, "fetch_price_gaps", lambda *a, **k: [{"channel": "buy", "start_date": "2025-01-01", "end_date": "2025-01-02"}]
    )
    monkeypatch.setattr(
        backfill,
        "fetch_historical_prices",
        lambda site_id, api_key, start, end, **kwargs: [
            {"channelType": "general", "startTime": f"{start.isoformat()}T00:00:01Z", "endTime": f"{start.isoformat()}T00:05:00Z", "perKwh": 20.0}
        ],
    )
    pushed = []
    monkeypatch.setattr(backfill, "push_price_intervals", lambda base_url, sid, key, intervals, **kw: pushed.append(intervals))
    markers = []
    monkeypatch.setattr(backfill, "report_gap_marker", lambda *a, **k: markers.append(a))

    backfill.backfill_one_site(BASE_URL, 1, "collector-key")

    assert len(pushed) == 1
    assert pushed[0][0]["channel"] == "buy"
    assert markers == []  # the channel was present, nothing to mark


def test_backfill_one_site_pushes_with_generous_timeout(monkeypatch):
    """Final review Important finding: a 7-day chunk is up to 4,032 intervals, which
    the server fans out to ~20,160 PriceReading upserts in one request. The client's
    10s default would turn that into a permanently non-converging retry loop, so
    backfill must ask for a much longer timeout than the live-poll path."""
    monkeypatch.setattr(backfill, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    monkeypatch.setattr(
        backfill, "fetch_site_data_floor", lambda *a, **k: datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    )
    monkeypatch.setattr(
        backfill, "fetch_price_gaps", lambda *a, **k: [{"channel": "buy", "start_date": "2025-01-01", "end_date": "2025-01-02"}]
    )
    monkeypatch.setattr(
        backfill,
        "fetch_historical_prices",
        lambda site_id, api_key, start, end, **kwargs: [
            {"channelType": "general", "startTime": f"{start.isoformat()}T00:00:01Z", "endTime": f"{start.isoformat()}T00:05:00Z", "perKwh": 20.0}
        ],
    )
    timeouts = []
    monkeypatch.setattr(
        backfill, "push_price_intervals", lambda base_url, sid, key, intervals, timeout=None: timeouts.append(timeout)
    )
    monkeypatch.setattr(backfill, "report_gap_marker", lambda *a, **k: None)

    backfill.backfill_one_site(BASE_URL, 1, "collector-key")

    assert timeouts == [120]


def test_backfill_one_site_marks_gap_when_amber_confirms_no_data(monkeypatch):
    monkeypatch.setattr(backfill, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    monkeypatch.setattr(
        backfill, "fetch_site_data_floor", lambda *a, **k: datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    )
    monkeypatch.setattr(
        backfill, "fetch_price_gaps", lambda *a, **k: [{"channel": "buy", "start_date": "2025-01-01", "end_date": "2025-01-02"}]
    )
    monkeypatch.setattr(backfill, "fetch_historical_prices", lambda *a, **k: [])  # Amber has nothing at all
    pushed = []
    monkeypatch.setattr(backfill, "push_price_intervals", lambda *a, **k: pushed.append(a))
    markers = []
    monkeypatch.setattr(backfill, "report_gap_marker", lambda base_url, sid, key, channel, gap_date: markers.append((channel, gap_date)))

    backfill.backfill_one_site(BASE_URL, 1, "collector-key")

    assert pushed == []
    assert markers == [("buy", datetime.date(2025, 1, 1)), ("buy", datetime.date(2025, 1, 2))]


def test_backfill_one_site_isolates_one_gap_failure_from_the_next(monkeypatch, caplog):
    monkeypatch.setattr(backfill, "fetch_source_config", lambda *a, **k: {"amber_site_id": "x", "amber_api_key": "y"})
    monkeypatch.setattr(
        backfill, "fetch_site_data_floor", lambda *a, **k: datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    )
    monkeypatch.setattr(
        backfill,
        "fetch_price_gaps",
        lambda *a, **k: [
            {"channel": "buy", "start_date": "2025-01-01", "end_date": "2025-01-01"},
            {"channel": "sell", "start_date": "2025-01-01", "end_date": "2025-01-01"},
        ],
    )

    call_count = {"n": 0}

    def _flaky_fetch_historical(site_id, api_key, start, end, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("network unreachable")
        return [{"channelType": "feedIn", "startTime": "2025-01-01T00:00:01Z", "endTime": "2025-01-01T00:05:00Z", "perKwh": 5.0}]

    monkeypatch.setattr(backfill, "fetch_historical_prices", _flaky_fetch_historical)
    pushed = []
    monkeypatch.setattr(backfill, "push_price_intervals", lambda base_url, sid, key, intervals, **kw: pushed.append(intervals))
    monkeypatch.setattr(backfill, "report_gap_marker", lambda *a, **k: None)

    with caplog.at_level("ERROR"):
        backfill.backfill_one_site(BASE_URL, 1, "collector-key")  # must not raise

    assert len(pushed) == 1  # the second (sell) gap still got processed
    assert pushed[0][0]["channel"] == "sell"


def test_run_backfill_forever_visits_every_site_each_cycle(monkeypatch):
    monkeypatch.setattr(
        backfill, "fetch_collector_sources", lambda base_url, key: [PricingSource(source_id=1), PricingSource(source_id=2)]
    )
    visited = []
    monkeypatch.setattr(backfill, "backfill_one_site", lambda base_url, sid, key: visited.append(sid))

    calls = {"n": 0}

    def _fake_sleep(seconds):
        calls["n"] += 1
        # The loop now sleeps BEFORE the pass, so the 1st sleep is the wait
        # for the first trigger; let it through and stop on the 2nd sleep,
        # after one full pass has run.
        if calls["n"] >= 2:
            raise SystemExit

    monkeypatch.setattr(backfill.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        backfill.run_backfill_forever(BASE_URL, "shared-key")

    assert visited == [1, 2]


def test_run_backfill_forever_isolates_one_site_failure_from_the_next(monkeypatch):
    monkeypatch.setattr(
        backfill, "fetch_collector_sources", lambda base_url, key: [PricingSource(source_id=1), PricingSource(source_id=2)]
    )
    visited = []

    def _backfill_one_site(base_url, source_id, key):
        if source_id == 1:
            raise RuntimeError("boom")
        visited.append(source_id)

    monkeypatch.setattr(backfill, "backfill_one_site", _backfill_one_site)

    calls = {"n": 0}

    def _fake_sleep(seconds):
        calls["n"] += 1
        # The loop now sleeps BEFORE the pass, so the 1st sleep is the wait
        # for the first trigger; let it through and stop on the 2nd sleep,
        # after one full pass has run.
        if calls["n"] >= 2:
            raise SystemExit

    monkeypatch.setattr(backfill.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        backfill.run_backfill_forever(BASE_URL, "shared-key")

    assert visited == [2]  # site 1's crash didn't stop site 2 from running


def test_run_backfill_forever_skips_pass_when_discovery_fails(monkeypatch):
    def _raise(base_url, key):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(backfill, "fetch_collector_sources", _raise)
    visited = []
    monkeypatch.setattr(backfill, "backfill_one_site", lambda base_url, sid, key: visited.append(sid))

    calls = {"n": 0}

    def _fake_sleep(seconds):
        calls["n"] += 1
        # The loop now sleeps BEFORE the pass, so the 1st sleep is the wait
        # for the first trigger; let it through and stop on the 2nd sleep,
        # after one full pass has been attempted.
        if calls["n"] >= 2:
            raise SystemExit

    monkeypatch.setattr(backfill.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        backfill.run_backfill_forever(BASE_URL, "shared-key")

    assert visited == []  # no sites visited this pass, but the process did not crash


def test_run_backfill_forever_skips_pass_when_discovery_raises_unexpected_error(monkeypatch):
    """Review Focus: a non-CoreApiError exception during discovery (e.g. a
    malformed-but-200 response body raising JSONDecodeError/KeyError) must be
    survived exactly the same way a CoreApiError already is -- not just that
    specific exception type."""
    def _raise(base_url, key):
        raise ValueError("malformed response")

    monkeypatch.setattr(backfill, "fetch_collector_sources", _raise)
    visited = []
    monkeypatch.setattr(backfill, "backfill_one_site", lambda base_url, sid, key: visited.append(sid))

    calls = {"n": 0}

    def _fake_sleep(seconds):
        calls["n"] += 1
        # The loop now sleeps BEFORE the pass, so the 1st sleep is the wait
        # for the first trigger; let it through and stop on the 2nd sleep,
        # after one full pass has been attempted.
        if calls["n"] >= 2:
            raise SystemExit

    monkeypatch.setattr(backfill.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        backfill.run_backfill_forever(BASE_URL, "shared-key")

    assert visited == []  # no sites visited this pass, but the process did not crash
