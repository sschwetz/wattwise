from __future__ import annotations

import logging
import threading
import time

import pytest

from weatherlink_local_collector import main as main_module
from weatherlink_local_collector.config import Settings
from weatherlink_local_collector.core_api_client import CoreApiError
from weatherlink_local_collector.mapping import RainState

REAL_RUN_LIVE_LOOP = main_module.run_live_loop

SETTINGS = Settings(
    core_api_url="http://localhost:8000", collector_key="key", poll_interval_seconds=60, discovery_refresh_seconds=300
)
# discovery_refresh_seconds=0 makes main()'s "now - last_refresh >= discovery_refresh_seconds"
# check true on every cycle, without needing to mock time.monotonic -- used by tests below
# that need a refresh to happen every single loop iteration.
SETTINGS_ALWAYS_REFRESH = Settings(
    core_api_url="http://localhost:8000", collector_key="key", poll_interval_seconds=60, discovery_refresh_seconds=0
)

CURRENT_CONDITIONS = {
    "data": {
        "conditions": [
            {
                "data_structure_type": 1,
                "temp": 72.8,
                "hum": 57.3,
                "wind_speed_avg_last_1_min": 4.43,
                "wind_dir_scalar_avg_last_1_min": 41,
                "rain_size": 2,
                "rainfall_daily": 0,
                "solar_rad": None,
            },
            {"data_structure_type": 3, "bar_sea_level": 30.422, "bar_absolute": 30.336},
        ]
    },
    "error": None,
}


def test_poll_once_pushes_iss_and_barometer_readings_on_success(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: CURRENT_CONDITIONS)
    pushed = []
    monkeypatch.setattr(
        main_module, "push_weather_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )

    main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, RainState())

    assert len(pushed) == 1
    metrics = {r["metric"] for r in pushed[0]}
    assert "temp_c" in metrics
    assert "barometric_pressure_hpa" in metrics
    assert "solar_radiation_wm2" not in metrics  # null in this fixture
    assert "rain_mm" not in metrics  # first poll, no baseline yet


def test_poll_once_floors_timestamp_to_whole_minute(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: CURRENT_CONDITIONS)
    pushed = []
    monkeypatch.setattr(
        main_module, "push_weather_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )

    main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, RainState())

    ts = pushed[0][0]["ts"]
    assert ts.second == 0
    assert ts.microsecond == 0


def test_poll_once_isolates_iss_failure_from_barometer(monkeypatch, caplog):
    broken = {
        "data": {
            "conditions": [
                {"data_structure_type": 1, "temp": 72.8, "hum": 57.3, "wind_speed_avg_last_1_min": 1.0,
                 "wind_dir_scalar_avg_last_1_min": 1, "rain_size": 0, "rainfall_daily": 0, "solar_rad": None},
                {"data_structure_type": 3, "bar_sea_level": 30.422, "bar_absolute": 30.336},
            ]
        },
        "error": None,
    }
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: broken)
    pushed = []
    monkeypatch.setattr(
        main_module, "push_weather_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )

    with caplog.at_level("ERROR"):
        main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, RainState())

    assert len(pushed) == 1
    metrics = {r["metric"] for r in pushed[0]}
    assert metrics == {"barometric_pressure_hpa"}  # ISS dropped (rain_size=0), barometer still pushed


def test_poll_once_isolates_entirely_missing_iss_entry(monkeypatch):
    # No data_structure_type: 1 entry at all (not just malformed) -- the
    # Envoy collector's C2 lesson: a genuinely missing/unexpected response
    # shape must not crash the cycle, real (non-monkeypatched) mapping code
    # is exercised via the real _map_iss_or_empty/map_barometer_readings path.
    no_iss = {
        "data": {
            "conditions": [
                {"data_structure_type": 3, "bar_sea_level": 30.422, "bar_absolute": 30.336},
            ]
        },
        "error": None,
    }
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: no_iss)
    pushed = []
    monkeypatch.setattr(
        main_module, "push_weather_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )

    main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, RainState())

    assert len(pushed) == 1
    metrics = {r["metric"] for r in pushed[0]}
    assert metrics == {"barometric_pressure_hpa"}  # ISS entirely absent, barometer still pushed


def test_poll_once_isolates_missing_barometer_entry(monkeypatch):
    no_barometer = {
        "data": {
            "conditions": [
                {"data_structure_type": 1, "temp": 72.8, "hum": 57.3, "wind_speed_avg_last_1_min": 1.0,
                 "wind_dir_scalar_avg_last_1_min": 1, "rain_size": 2, "rainfall_daily": 0, "solar_rad": None},
            ]
        },
        "error": None,
    }
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: no_barometer)
    pushed = []
    monkeypatch.setattr(
        main_module, "push_weather_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )

    main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, RainState())

    assert len(pushed) == 1
    metrics = {r["metric"] for r in pushed[0]}
    assert "barometric_pressure_hpa" not in metrics
    assert "temp_c" in metrics  # ISS readings still pushed


def test_poll_once_skips_cycle_when_weatherlink_unreachable(monkeypatch, caplog):
    def _raise(host):
        raise OSError("network unreachable")

    monkeypatch.setattr(main_module, "fetch_current_conditions", _raise)

    with caplog.at_level("ERROR"):
        main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, RainState())  # must not raise


def test_poll_once_logs_but_does_not_raise_when_core_api_unreachable(monkeypatch, caplog):
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: CURRENT_CONDITIONS)

    def _raise_core_api(*a, **k):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(main_module, "push_weather_readings", _raise_core_api)

    with caplog.at_level("ERROR"):
        main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, RainState())  # must not raise


def test_rain_state_persists_across_poll_once_calls(monkeypatch):
    monkeypatch.setattr(main_module, "push_weather_readings", lambda *a, **k: None)
    rain_state = RainState()

    first = {"data": {"conditions": [
        {"data_structure_type": 1, "temp": 70, "hum": 50, "wind_speed_avg_last_1_min": 1.0,
         "wind_dir_scalar_avg_last_1_min": 1, "rain_size": 2, "rainfall_daily": 50, "solar_rad": None},
    ]}, "error": None}
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: first)
    main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, rain_state)
    assert rain_state.last_daily_mm == pytest.approx(10.0)

    second = {"data": {"conditions": [
        {"data_structure_type": 1, "temp": 70, "hum": 50, "wind_speed_avg_last_1_min": 1.0,
         "wind_dir_scalar_avg_last_1_min": 1, "rain_size": 2, "rainfall_daily": 60, "solar_rad": None},
    ]}, "error": None}
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: second)
    main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, rain_state)
    assert rain_state.last_daily_mm == pytest.approx(12.0)


def _rain_conditions(rainfall_daily: int) -> dict:
    return {"data": {"conditions": [
        {"data_structure_type": 1, "temp": 70, "hum": 50, "wind_speed_avg_last_1_min": 1.0,
         "wind_dir_scalar_avg_last_1_min": 1, "rain_size": 2, "rainfall_daily": rainfall_daily,
         "solar_rad": None},
    ]}, "error": None}


def test_failed_push_does_not_advance_rain_baseline(monkeypatch):
    rain_state = RainState(last_daily_mm=10.0)
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: _rain_conditions(60))

    def _raise_core_api(*a, **k):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(main_module, "push_weather_readings", _raise_core_api)

    main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, rain_state)

    assert rain_state.last_daily_mm == pytest.approx(10.0)  # unchanged: the 12.0mm batch never landed


def test_rain_delta_recovers_after_failed_push(monkeypatch):
    rain_state = RainState()

    monkeypatch.setattr(main_module, "push_weather_readings", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: _rain_conditions(50))
    main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, rain_state)
    assert rain_state.last_daily_mm == pytest.approx(10.0)

    def _raise_core_api(*a, **k):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(main_module, "push_weather_readings", _raise_core_api)
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: _rain_conditions(60))
    main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, rain_state)
    assert rain_state.last_daily_mm == pytest.approx(10.0)  # still unchanged after the failed push

    pushed = []
    monkeypatch.setattr(
        main_module, "push_weather_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: _rain_conditions(70))
    main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, rain_state)

    by_metric = {r["metric"]: r["value"] for r in pushed[0]}
    assert by_metric["rain_mm"] == pytest.approx(4.0)  # (70-50)*0.2mm, combining the dropped cycle
    assert rain_state.last_daily_mm == pytest.approx(14.0)


def test_failed_push_resends_non_rain_readings_with_the_next_cycle(monkeypatch):
    main_module._pending_readings.clear()
    rain_state = RainState(last_daily_mm=10.0)
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: _rain_conditions(60))

    def _raise_core_api(*a, **k):
        raise CoreApiError("timed out")

    monkeypatch.setattr(main_module, "push_weather_readings", _raise_core_api)
    main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, rain_state)

    pushed = []
    monkeypatch.setattr(
        main_module, "push_weather_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )
    main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, rain_state)

    metrics = [r["metric"] for r in pushed[0]]
    # Rain is recovered through the baseline rollback, so resending the failed
    # cycle's rain_mm as well would count it twice.
    assert metrics.count("rain_mm") == 1
    assert metrics.count("temp_c") == 2
    assert pushed[0][0]["ts"] <= pushed[0][-1]["ts"]


def test_a_403_push_drops_the_readings_instead_of_resending(monkeypatch, caplog):
    main_module._pending_readings.clear()
    main_module._dropping.clear()
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: CURRENT_CONDITIONS)
    sizes = []

    def _push(base_url, source_id, key, readings):
        sizes.append(len(readings))
        if len(sizes) <= 2:
            raise CoreApiError("Failed to push readings: 403 site_disabled", status_code=403)

    monkeypatch.setattr(main_module, "push_weather_readings", _push)
    with caplog.at_level(logging.WARNING, logger="weatherlink_local_collector"):
        for _ in range(3):
            main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, RainState())

    assert sizes[0] == sizes[1] == sizes[2]
    assert main_module._pending_readings == {}
    dropped = [r for r in caplog.records if "dropping" in r.getMessage()]
    assert len(dropped) == 1
    assert dropped[0].levelno == logging.WARNING


def test_failed_pushes_drop_the_oldest_readings_past_the_backlog_cap(monkeypatch):
    main_module._pending_readings.clear()
    monkeypatch.setattr(main_module, "MAX_PENDING_READINGS", 5)
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: CURRENT_CONDITIONS)

    def _raise_core_api(*a, **k):
        raise CoreApiError("down")

    monkeypatch.setattr(main_module, "push_weather_readings", _raise_core_api)
    for _ in range(5):
        main_module.poll_once(SETTINGS, 1, {"host": "weatherlink.local"}, RainState())

    assert len(main_module._pending_readings[1]) == 5


def test_main_retries_discovery_until_a_source_appears(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    attempts = {"n": 0}

    def fake_fetch(base_url, key):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return []
        return [{"source_id": 1, "site_id": 10, "config": {"host": "weatherlink.local"}}]

    monkeypatch.setattr(main_module, "fetch_collector_sources", fake_fetch)
    monkeypatch.setattr(main_module, "poll_once", lambda settings, source_id, config, rain_state: None)
    sleeps = []

    def _fake_sleep(seconds):
        sleeps.append(seconds)
        # 2 sleeps for the 2 failed discovery attempts, then the 3rd attempt
        # succeeds (no sleep), then one poll cycle runs and sleeps a 3rd
        # time -- that's where we stop.
        if len(sleeps) > 2:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert attempts["n"] >= 3


def test_main_loop_survives_unexpected_exception_from_poll_once(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(
        main_module,
        "fetch_collector_sources",
        lambda base_url, key: [{"source_id": 1, "site_id": 10, "config": {"host": "weatherlink.local"}}],
    )
    monkeypatch.setattr(
        main_module, "poll_once", lambda settings, source_id, config, rain_state: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert calls["sleep"] == 2


def test_main_polls_every_discovered_source_each_cycle(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(
        main_module,
        "fetch_collector_sources",
        lambda base_url, key: [
            {"source_id": 1, "site_id": 10, "config": {"host": "wl-a.local"}},
            {"source_id": 2, "site_id": 11, "config": {"host": "wl-b.local"}},
        ],
    )
    polled = []
    monkeypatch.setattr(
        main_module, "poll_once", lambda settings, source_id, config, rain_state: polled.append(config)
    )

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        # The loop now sleeps BEFORE polling, so the 1st sleep is the wait
        # for the first trigger; let it through and stop on the 2nd sleep,
        # after one full poll cycle has run.
        if calls["sleep"] >= 2:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert len(polled) == 2
    assert {c["host"] for c in polled} == {"wl-a.local", "wl-b.local"}


def test_main_keeps_last_known_sources_when_discovery_refresh_fails(monkeypatch):
    """Review Focus: a discovery-refresh failure must not crash the process
    or wipe the in-memory source list. SETTINGS_ALWAYS_REFRESH makes a
    refresh attempted every cycle, so the 2nd cycle's failure actually
    exercises the fallback path."""
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS_ALWAYS_REFRESH)

    call_count = {"n": 0}

    def fake_fetch(base_url, key):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [{"source_id": 1, "site_id": 10, "config": {"host": "wl.local"}}]
        raise CoreApiError("core-api unreachable")

    monkeypatch.setattr(main_module, "fetch_collector_sources", fake_fetch)
    polled = []
    monkeypatch.setattr(
        main_module, "poll_once", lambda settings, source_id, config, rain_state: polled.append(config)
    )

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        # The loop now sleeps BEFORE each cycle's refresh+poll, so 2 full
        # cycles need 3 sleeps to complete (sleep, cycle, sleep, cycle, then
        # the 3rd sleep is where we stop).
        if calls["sleep"] >= 3:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert len(polled) == 2  # both cycles still polled the one known source, despite the 2nd refresh failing


def test_main_keeps_last_known_sources_when_discovery_refresh_raises_unexpected_error(monkeypatch):
    """Review Focus: a non-CoreApiError exception during discovery (e.g. a
    malformed-but-200 response body raising JSONDecodeError/KeyError) must be
    survived exactly the same way a CoreApiError already is -- not just that
    specific exception type."""
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS_ALWAYS_REFRESH)

    call_count = {"n": 0}

    def fake_fetch(base_url, key):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [{"source_id": 1, "site_id": 10, "config": {"host": "wl.local"}}]
        raise ValueError("malformed response")

    monkeypatch.setattr(main_module, "fetch_collector_sources", fake_fetch)
    polled = []
    monkeypatch.setattr(
        main_module, "poll_once", lambda settings, source_id, config, rain_state: polled.append(config)
    )

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        # The loop now sleeps BEFORE each cycle's refresh+poll, so 2 full
        # cycles need 3 sleeps to complete (sleep, cycle, sleep, cycle, then
        # the 3rd sleep is where we stop).
        if calls["sleep"] >= 3:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert len(polled) == 2  # both cycles still polled the one known source, despite the 2nd refresh raising


def test_main_gives_a_reappearing_source_a_fresh_rain_state(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS_ALWAYS_REFRESH)
    # First two entries both show the source present (one consumed by
    # main()'s bootstrap discovery call, one by the main loop's 1st-cycle
    # refresh) and reuse the same RainState -- the persistence half of the
    # Review Focus item. Then it disappears, then reappears with a fresh
    # RainState -- the other half.
    discovery_results = [
        [{"source_id": 1, "site_id": 10, "config": {"host": "wl.local"}}],
        [{"source_id": 1, "site_id": 10, "config": {"host": "wl.local"}}],
        [],  # source disappears
        [{"source_id": 1, "site_id": 10, "config": {"host": "wl.local"}}],  # reappears
    ]
    monkeypatch.setattr(main_module, "fetch_collector_sources", lambda base_url, key: discovery_results.pop(0))
    seen_states = []
    monkeypatch.setattr(
        main_module,
        "poll_once",
        lambda settings, source_id, config, rain_state: seen_states.append(rain_state),
    )

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        # The loop now sleeps BEFORE each cycle's refresh+poll, so all 3
        # main-loop cycles (present, disappears, reappears) need 4 sleeps to
        # complete -- the 4th sleep is where we stop.
        if calls["sleep"] >= 4:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert len(seen_states) == 2  # cycle 2 had no sources to poll
    assert seen_states[0] is not seen_states[1]  # the reappearing source got a brand-new RainState


@pytest.fixture(autouse=True)
def _reset_live_failure_state():
    main_module._live_failing.clear()
    yield
    main_module._live_failing.clear()


@pytest.fixture(autouse=True)
def _no_live_thread_from_main(monkeypatch):
    # main() starts the live loop on a thread; keep it from polling in the tests above.
    monkeypatch.setattr(main_module, "run_live_loop", lambda settings, get_sources, stop: None)


def test_live_poll_once_pushes_values(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: CURRENT_CONDITIONS)
    pushed = []
    monkeypatch.setattr(
        main_module,
        "push_live_sample",
        lambda base_url, source_id, key, ts, values: pushed.append((source_id, ts, values)),
    )

    main_module.live_poll_once(SETTINGS, 7, {"host": "weatherlink.local"})

    assert len(pushed) == 1
    source_id, ts, values = pushed[0]
    assert source_id == 7
    assert ts.tzinfo is not None
    assert values["temp_c"] == 22.7
    assert "rain_mm" not in values


def test_live_poll_failure_is_logged_once_per_streak(monkeypatch, caplog):
    outcomes = [False, False, False, True, False]

    def fake_fetch(host):
        if outcomes.pop(0):
            return CURRENT_CONDITIONS
        raise OSError("unreachable")

    monkeypatch.setattr(main_module, "fetch_current_conditions", fake_fetch)
    monkeypatch.setattr(main_module, "push_live_sample", lambda *args: None)

    with caplog.at_level(logging.INFO, logger="weatherlink_local_collector"):
        for _ in range(5):
            main_module.live_poll_once(SETTINGS, 1, {"host": "h"})

    failures = [r for r in caplog.records if r.levelno == logging.ERROR]
    recoveries = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(failures) == 2
    assert len(recoveries) == 1


def test_live_poll_leaves_stored_poll_state_alone(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_current_conditions", lambda host: CURRENT_CONDITIONS)
    monkeypatch.setattr(main_module, "push_live_sample", lambda *args: (_ for _ in ()).throw(CoreApiError("x")))
    main_module._pending_readings.clear()

    main_module.live_poll_once(SETTINGS, 1, {"host": "h"})

    assert main_module._pending_readings == {}


def test_run_live_loop_polls_every_source_and_stops(monkeypatch):
    polled = set()
    monkeypatch.setattr(main_module, "live_poll_once", lambda settings, sid, config: polled.add(sid))
    settings = Settings(
        core_api_url="http://x", collector_key="k", poll_interval_seconds=60, discovery_refresh_seconds=300,
        live_poll_seconds=2,
    )
    sources = {1: main_module._Source({"host": "a"}, RainState()), 2: main_module._Source({"host": "b"}, RainState())}
    stop = threading.Event()
    thread = threading.Thread(target=REAL_RUN_LIVE_LOOP, args=(settings, lambda: sources, stop))
    thread.start()
    deadline = time.monotonic() + 3
    while polled != {1, 2} and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=1)

    assert polled == {1, 2}
    assert not thread.is_alive()
