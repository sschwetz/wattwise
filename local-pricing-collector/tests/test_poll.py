from __future__ import annotations

import datetime

import pytest

from local_pricing_collector import poll
from local_pricing_collector.config import Settings
from local_pricing_collector.core_api_client import CoreApiError
from local_pricing_collector.models import Coverage, LocalSource, RepriceMarker
from local_pricing_collector.triggers import SourceState, Triggers

UTC = datetime.UTC
SETTINGS = Settings(core_api_url="http://core-api:8000", collector_key="k")
# 00:00Z on 15 June is 10:00 in Sydney. Local midnights: 2026-06-15T14:00Z and 2026-06-16T14:00Z.
NOW = datetime.datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
AT_2359 = datetime.datetime(2026, 6, 15, 13, 59, tzinfo=UTC)
CATCH_UP = Triggers(catch_up=True)
SCHEDULED = Triggers(catch_up=False, scheduled_for=datetime.date(2026, 6, 16))
FLAT_BUY = {
    "channel": "buy",
    "effective_from": "2026-01-01T00:00:00+00:00",
    "effective_to": None,
    "months": list(range(1, 13)),
    "days_of_week": list(range(7)),
    "start_time": "00:00:00",
    "end_time": "00:00:00",
    "rate_c_per_kwh": "30.0000",
}


def source(windows=None, timezone="Australia/Sydney", source_id=7) -> LocalSource:
    return LocalSource(source_id, {"timezone": timezone, "rate_windows": [FLAT_BUY] if windows is None else windows})


class FakeApi:
    """Stands in for the three core-api calls the sync makes."""

    def __init__(self, coverage=None, floor=None, fail_push_after=None, fail_clear=False):
        self.coverage = coverage or {}
        self.floor = floor
        self.fail_push_after = fail_push_after
        self.coverage_calls = 0
        self.pushes = []
        self.fail_clear = fail_clear
        self.clears = []

    def fetch_price_coverage(self, url, source_id, key):
        self.coverage_calls += 1
        return self.coverage

    def fetch_site_data_floor(self, url, source_id, key):
        return self.floor

    def push_price_intervals(self, url, source_id, key, intervals, **kwargs):
        if self.fail_push_after is not None and len(self.pushes) >= self.fail_push_after:
            raise CoreApiError("down")
        self.pushes.append((source_id, intervals))

    def clear_reprice_marker(self, url, source_id, key, version):
        if self.fail_clear:
            raise CoreApiError("down")
        self.clears.append((source_id, version))

    def install(self, monkeypatch):
        for name in ("fetch_price_coverage", "fetch_site_data_floor", "push_price_intervals", "clear_reprice_marker"):
            monkeypatch.setattr(poll, name, getattr(self, name))
        return self


def test_a_new_source_is_filled_from_the_floor_to_the_end_of_tomorrow(monkeypatch):
    api = FakeApi(floor=datetime.datetime(2026, 6, 14, 0, 0, tzinfo=UTC)).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW) is True

    assert len(api.pushes) == 1
    source_id, intervals = api.pushes[0]
    assert source_id == 7
    assert {i["channel"] for i in intervals} == {"buy"}
    assert intervals[0]["interval_start"] == "2026-06-14T00:00:00+00:00"
    assert intervals[-1]["interval_end"] == "2026-06-16T14:00:00+00:00"
    assert len(intervals) == 62


def test_the_scheduled_push_prices_only_the_next_local_day_and_asks_for_no_coverage(monkeypatch):
    api = FakeApi().install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), SCHEDULED, AT_2359) is True

    assert api.coverage_calls == 0
    intervals = api.pushes[0][1]
    assert intervals[0]["interval_start"] == "2026-06-15T14:00:00+00:00"
    assert intervals[-1]["interval_end"] == "2026-06-16T14:00:00+00:00"
    assert len(intervals) == 24


def test_a_source_that_is_fully_covered_pushes_nothing(monkeypatch):
    covered = {
        "buy": Coverage(
            first=datetime.datetime(2026, 6, 1, tzinfo=UTC), last=datetime.datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
        )
    }
    api = FakeApi(coverage=covered, floor=datetime.datetime(2026, 6, 1, tzinfo=UTC)).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW) is True
    assert api.pushes == []


def test_a_source_with_no_windows_makes_no_requests(monkeypatch):
    api = FakeApi().install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(windows=[]), CATCH_UP, NOW) is True
    assert api.coverage_calls == 0
    assert api.pushes == []


def test_a_long_range_is_pushed_in_seven_day_chunks(monkeypatch):
    api = FakeApi(floor=datetime.datetime(2026, 5, 1, tzinfo=UTC)).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW) is True

    assert len(api.pushes) == 7


def test_a_failed_chunk_stops_the_rest_and_reports_failure(monkeypatch, caplog):
    api = FakeApi(floor=datetime.datetime(2026, 5, 1, tzinfo=UTC), fail_push_after=1).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW) is False

    assert len(api.pushes) == 1
    assert "will retry" in caplog.text


def test_a_coverage_failure_is_logged_and_reported(monkeypatch, caplog):
    def boom(url, source_id, key):
        raise CoreApiError("down")

    FakeApi().install(monkeypatch)
    monkeypatch.setattr(poll, "fetch_price_coverage", boom)

    assert poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW) is False
    assert "will retry" in caplog.text


def test_an_unknown_time_zone_is_logged_and_reported(monkeypatch, caplog):
    FakeApi().install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(timezone="Mars/Base"), CATCH_UP, NOW) is False
    assert "Failed to read config" in caplog.text


def test_an_unexpected_error_is_logged_and_does_not_escape(monkeypatch, caplog):
    FakeApi().install(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(poll, "plan_ranges", boom)

    assert poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW) is False
    assert "Unexpected error" in caplog.text


def test_a_range_with_no_matching_window_pushes_nothing_and_succeeds(monkeypatch):
    december_only = dict(FLAT_BUY, months=[12])
    api = FakeApi(floor=datetime.datetime(2026, 6, 14, tzinfo=UTC)).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(windows=[december_only]), CATCH_UP, NOW) is True
    assert api.pushes == []


def test_a_history_range_is_pushed_newest_chunk_first(monkeypatch):
    first = datetime.datetime(2026, 6, 10, tzinfo=UTC)
    coverage = {"buy": Coverage(first=first, last=datetime.datetime(2026, 6, 16, 14, 0, tzinfo=UTC))}
    api = FakeApi(coverage=coverage, floor=datetime.datetime(2026, 5, 1, tzinfo=UTC), fail_push_after=1)
    api.install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW) is False

    assert len(api.pushes) == 1
    assert api.pushes[0][1][-1]["interval_end"] == first.isoformat()


def test_sync_child_exits_with_the_sync_result(monkeypatch):
    for result, code in ((True, 0), (False, 1)):
        monkeypatch.setattr(poll, "sync_one_source", lambda *args, result=result: result)
        with pytest.raises(SystemExit) as exit_info:
            poll.sync_child(SETTINGS, source(), CATCH_UP, NOW)
        assert exit_info.value.code == code


class FakeProcess:
    instances: list[FakeProcess] = []

    def __init__(self, target, args):
        self.target = target
        self.args = args
        self.started = False
        self.alive = True
        self.exitcode = None
        self.terminated = False
        self.killed = False
        self.ignore_terminate = False
        FakeProcess.instances.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        pass

    def terminate(self):
        self.terminated = True
        if not self.ignore_terminate:
            self.alive = False
            self.exitcode = -15

    def kill(self):
        self.killed = True
        self.alive = False
        self.exitcode = -9

    def finish(self, exitcode=0):
        self.alive = False
        self.exitcode = exitcode


@pytest.fixture(autouse=True)
def _reset_fake_processes():
    FakeProcess.instances = []


def fake_discovery(monkeypatch, sources):
    monkeypatch.setattr(poll, "fetch_collector_sources", lambda url, key: sources)


def tick(state, now=NOW):
    poll.run_tick(SETTINGS, state, now, process_factory=FakeProcess)


def test_a_new_source_gets_a_catch_up_child(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState()

    tick(state)

    assert len(FakeProcess.instances) == 1
    child = FakeProcess.instances[0]
    assert child.started
    assert child.args[2] == CATCH_UP
    assert child.args[1].source_id == 7


def test_a_source_that_is_not_due_gets_no_child(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState(states={7: SourceState(caught_up_date=datetime.date(2026, 6, 15), windows_key=poll.windows_key(source().config))})

    tick(state)

    assert FakeProcess.instances == []


def test_the_scheduled_push_starts_a_child_at_23_59_local(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState(states={7: SourceState(caught_up_date=datetime.date(2026, 6, 15), windows_key=poll.windows_key(source().config))})

    tick(state, now=AT_2359)

    assert FakeProcess.instances[0].args[2] == SCHEDULED


def test_a_running_child_is_not_started_twice(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState()

    tick(state)
    tick(state)

    assert len(FakeProcess.instances) == 1


def test_a_successful_child_is_recorded_so_the_next_tick_starts_nothing(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState()
    tick(state)
    FakeProcess.instances[0].finish(0)

    tick(state)

    assert len(FakeProcess.instances) == 1
    assert state.states[7].caught_up_date == datetime.date(2026, 6, 15)
    assert state.running == {}


def test_a_failed_child_is_retried_on_the_next_tick(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState()
    tick(state)
    FakeProcess.instances[0].finish(1)

    tick(state)

    assert len(FakeProcess.instances) == 2
    assert 7 not in state.states


def test_a_child_that_runs_too_long_is_terminated_and_retried(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState()
    tick(state)

    tick(state, now=NOW + poll.MAX_RUN + datetime.timedelta(minutes=1))

    assert FakeProcess.instances[0].terminated
    assert 7 not in state.states
    assert len(FakeProcess.instances) == 2


def test_state_is_forgotten_for_a_source_that_disappears(monkeypatch):
    fake_discovery(monkeypatch, [])
    state = poll.TickState(states={7: SourceState(caught_up_date=datetime.date(2026, 6, 15), windows_key=poll.windows_key(source().config))})

    tick(state)

    assert state.states == {}


def test_a_discovery_failure_is_logged_and_starts_nothing(monkeypatch, caplog):
    def boom(url, key):
        raise CoreApiError("down")

    monkeypatch.setattr(poll, "fetch_collector_sources", boom)

    tick(poll.TickState())

    assert "Failed to refresh" in caplog.text
    assert FakeProcess.instances == []


def test_a_source_with_a_bad_time_zone_is_skipped_and_the_others_still_run(monkeypatch, caplog):
    fake_discovery(monkeypatch, [source(timezone="Mars/Base", source_id=1), source(source_id=2)])

    tick(poll.TickState())

    assert [p.args[1].source_id for p in FakeProcess.instances] == [2]
    assert "time zone" in caplog.text


def test_a_child_that_ignores_terminate_is_killed(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState()
    tick(state)
    FakeProcess.instances[0].ignore_terminate = True

    tick(state, now=NOW + poll.MAX_RUN + datetime.timedelta(minutes=1))

    assert FakeProcess.instances[0].killed
    assert len(FakeProcess.instances) == 2


def test_finished_children_are_recorded_even_when_discovery_fails(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState()
    tick(state)
    FakeProcess.instances[0].finish(0)

    def boom(url, key):
        raise CoreApiError("down")

    monkeypatch.setattr(poll, "fetch_collector_sources", boom)
    tick(state)

    assert state.states[7].caught_up_date == datetime.date(2026, 6, 15)
    assert state.running == {}


def test_a_scheduled_only_trigger_prices_exactly_the_target_day_without_coverage(monkeypatch):
    api = FakeApi().install(monkeypatch)
    after_midnight = datetime.datetime(2026, 6, 15, 14, 30, tzinfo=UTC)

    assert poll.sync_one_source(SETTINGS, source(), SCHEDULED, after_midnight) is True

    assert api.coverage_calls == 0
    intervals = api.pushes[0][1]
    assert intervals[0]["interval_start"] == "2026-06-15T14:00:00+00:00"
    assert intervals[-1]["interval_end"] == "2026-06-16T14:00:00+00:00"


def caught_up_state() -> poll.TickState:
    key = poll.windows_key(source().config)
    return poll.TickState(states={7: SourceState(caught_up_date=datetime.date(2026, 6, 15), windows_key=key)})


def test_a_caught_up_source_with_unchanged_windows_gets_no_child(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    tick(caught_up_state())
    assert FakeProcess.instances == []


def test_an_added_window_starts_a_catch_up_child(monkeypatch):
    sell = dict(FLAT_BUY, channel="sell")
    fake_discovery(monkeypatch, [source(windows=[FLAT_BUY, sell])])

    tick(caught_up_state())

    assert len(FakeProcess.instances) == 1
    assert FakeProcess.instances[0].args[2] == CATCH_UP


def test_a_successful_catch_up_records_the_windows_key(monkeypatch):
    sell = dict(FLAT_BUY, channel="sell")
    changed = source(windows=[FLAT_BUY, sell])
    fake_discovery(monkeypatch, [changed])
    state = caught_up_state()
    tick(state)
    FakeProcess.instances[0].finish(0)

    tick(state)

    assert len(FakeProcess.instances) == 1
    assert state.states[7].windows_key == poll.windows_key(changed.config)


def test_windows_edited_while_a_child_runs_trigger_another_catch_up(monkeypatch):
    fake_discovery(monkeypatch, [source()])
    state = poll.TickState()
    tick(state)
    FakeProcess.instances[0].finish(0)
    fake_discovery(monkeypatch, [source(windows=[FLAT_BUY, dict(FLAT_BUY, channel="sell")])])

    tick(state)

    assert len(FakeProcess.instances) == 2


def test_windows_key_ignores_key_order_and_sees_window_changes():
    assert poll.windows_key({"rate_windows": {"a": 1, "b": 2}}) == poll.windows_key({"rate_windows": {"b": 2, "a": 1}})
    assert poll.windows_key({"rate_windows": [FLAT_BUY]}) != poll.windows_key({"rate_windows": [dict(FLAT_BUY, rate_c_per_kwh="31")]})


def test_a_window_starting_mid_minute_pushes_only_whole_minute_intervals(monkeypatch):
    off = dict(FLAT_BUY, effective_from="2026-06-15T00:00:12.345+00:00")
    api = FakeApi(floor=datetime.datetime(2026, 6, 14, tzinfo=UTC)).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(windows=[off]), CATCH_UP, NOW) is True

    intervals = [i for _, chunk in api.pushes for i in chunk]
    for i in intervals:
        for key in ("interval_start", "interval_end"):
            parsed = datetime.datetime.fromisoformat(i[key])
            assert parsed.second == 0 and parsed.microsecond == 0
    assert intervals[0]["interval_start"] == "2026-06-15T00:01:00+00:00"
    assert intervals[-1]["interval_end"] == "2026-06-16T14:00:00+00:00"


def test_forward_ranges_are_pushed_before_history_and_survive_a_history_failure(monkeypatch):
    buy_history = dict(FLAT_BUY, channel="buy")
    sell, load = dict(FLAT_BUY, channel="sell"), dict(FLAT_BUY, channel="controlled_load")
    first = datetime.datetime(2026, 6, 10, tzinfo=UTC)
    last = datetime.datetime(2026, 6, 16, 14, 0, tzinfo=UTC)
    coverage = {
        "buy": Coverage(first=first, last=last),
        "sell": Coverage(first=datetime.datetime(2026, 5, 1, tzinfo=UTC), last=datetime.datetime(2026, 6, 15, 0, 0, tzinfo=UTC)),
        "controlled_load": Coverage(first=datetime.datetime(2026, 5, 1, tzinfo=UTC), last=datetime.datetime(2026, 6, 15, 0, 0, tzinfo=UTC)),
    }
    api = FakeApi(coverage=coverage, floor=datetime.datetime(2026, 5, 1, tzinfo=UTC)).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(windows=[buy_history, sell, load]), CATCH_UP, NOW) is True
    channels = [chunk[0]["channel"] for _, chunk in api.pushes]
    assert channels[:2] == ["sell", "controlled_load"]
    assert set(channels[2:]) == {"buy"}

    failing = FakeApi(coverage=coverage, floor=datetime.datetime(2026, 5, 1, tzinfo=UTC), fail_push_after=2)
    failing.install(monkeypatch)
    assert poll.sync_one_source(SETTINGS, source(windows=[buy_history, sell, load]), CATCH_UP, NOW) is False
    assert [chunk[0]["channel"] for _, chunk in failing.pushes] == ["sell", "controlled_load"]


MARKER_FROM = datetime.datetime(2026, 6, 10, 0, 0, tzinfo=UTC)
MARKER = RepriceMarker(MARKER_FROM, 3)
REPRICE = Triggers(catch_up=False, reprice=MARKER)
FLOOR = datetime.datetime(2026, 6, 1, 0, 0, tzinfo=UTC)


def test_a_reprice_pushes_from_the_marker_then_clears_it(monkeypatch):
    api = FakeApi(floor=FLOOR).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), REPRICE, NOW) is True

    assert api.coverage_calls == 0
    intervals = api.pushes[0][1]
    assert intervals[0]["interval_start"] == "2026-06-10T00:00:00+00:00"
    assert intervals[-1]["interval_end"] == "2026-06-16T14:00:00+00:00"
    assert api.clears == [(7, 3)]


def test_a_reprice_only_run_fetches_the_site_data_floor_and_starts_no_earlier(monkeypatch):
    api = FakeApi(floor=FLOOR).install(monkeypatch)
    early = Triggers(catch_up=False, reprice=RepriceMarker(datetime.datetime(2026, 5, 1, tzinfo=UTC), 5))

    assert poll.sync_one_source(SETTINGS, source(), early, NOW) is True

    assert api.pushes[0][1][0]["interval_start"] == "2026-06-01T00:00:00+00:00"
    assert api.clears == [(7, 5)]


def test_a_failed_push_leaves_the_marker(monkeypatch):
    api = FakeApi(floor=FLOOR, fail_push_after=0).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), REPRICE, NOW) is False
    assert api.clears == []


def test_a_failed_clear_reports_failure(monkeypatch):
    api = FakeApi(floor=FLOOR, fail_clear=True).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), REPRICE, NOW) is False
    assert len(api.pushes) >= 1


def test_a_source_with_no_windows_clears_its_marker_without_pushing(monkeypatch):
    api = FakeApi().install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(windows=[]), REPRICE, NOW) is True
    assert api.pushes == []
    assert api.clears == [(7, 3)]


def test_a_marker_that_clamps_to_nothing_is_cleared_without_pushing(monkeypatch):
    api = FakeApi(floor=FLOOR).install(monkeypatch)
    later = Triggers(catch_up=False, reprice=RepriceMarker(datetime.datetime(2026, 6, 30, tzinfo=UTC), 4))

    assert poll.sync_one_source(SETTINGS, source(), later, NOW) is True
    assert api.pushes == []
    assert api.clears == [(7, 4)]


def test_a_catch_up_and_a_reprice_in_one_pass_clear_once(monkeypatch):
    api = FakeApi(floor=FLOOR).install(monkeypatch)

    assert poll.sync_one_source(SETTINGS, source(), Triggers(catch_up=True, reprice=MARKER), NOW) is True
    assert api.clears == [(7, 3)]
    assert api.coverage_calls == 1


def test_a_run_without_a_marker_never_clears(monkeypatch):
    api = FakeApi(floor=FLOOR).install(monkeypatch)

    poll.sync_one_source(SETTINGS, source(), CATCH_UP, NOW)

    assert api.clears == []


def marked_source(version=3, source_id=7) -> LocalSource:
    base = source(source_id=source_id)
    return LocalSource(source_id, {**base.config, "reprice": {"from": "2026-06-10T00:00:00+00:00", "version": version}})


def test_a_marker_starts_a_child_for_a_source_that_is_caught_up(monkeypatch):
    src = marked_source()
    fake_discovery(monkeypatch, [src])
    state = poll.TickState(
        states={7: SourceState(caught_up_date=datetime.date(2026, 6, 15), windows_key=poll.windows_key(src.config))}
    )

    tick(state)

    assert len(FakeProcess.instances) == 1
    assert FakeProcess.instances[0].args[2].reprice == MARKER


def test_a_malformed_marker_is_logged_and_the_source_still_gets_its_other_work(monkeypatch, caplog):
    bad = LocalSource(7, {**source().config, "reprice": {"from": "nope", "version": 1}})
    fake_discovery(monkeypatch, [bad])

    tick(poll.TickState())

    assert "reprice marker" in caplog.text
    assert FakeProcess.instances[0].args[2].reprice is None
    assert FakeProcess.instances[0].args[2].catch_up is True


def test_the_windows_fingerprint_ignores_the_marker():
    plain = source()
    assert poll.windows_key(plain.config) == poll.windows_key(marked_source().config)
