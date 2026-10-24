from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import pathlib
import threading

import pytest

from envoy_collector import main as main_module
from envoy_collector.config import Settings
from envoy_collector.core_api_client import CoreApiError
from envoy_collector.enlighten_client import EnlightenAuthError, EnlightenError
from envoy_collector.envoy_client import CertificateInfo, EnvoyAuthError, EnvoyCertificateMismatch
from envoy_collector.failure_log import FAILURE_LOG_INTERVAL_SECONDS
from envoy_collector.mapping import LiveSample, map_live_sample_values
from tests.test_enlighten_client import make_jwt

FAR_EXPIRY = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=300)).isoformat()


def _cert(fingerprint: str, days_left: int) -> CertificateInfo:
    return CertificateInfo(
        fingerprint=fingerprint,
        expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days_left),
        serial="ABC123",
    )


SETTINGS = Settings(
    core_api_url="http://localhost:8000", collector_key="key", poll_interval_seconds=60, discovery_refresh_seconds=300
)
# discovery_refresh_seconds=0 makes main()'s "now - last_refresh >= discovery_refresh_seconds"
# check true on every cycle, without needing to mock time.monotonic -- used by tests below
# that need a refresh to happen every single loop iteration.
SETTINGS_ALWAYS_REFRESH = Settings(
    core_api_url="http://localhost:8000", collector_key="key", poll_interval_seconds=60, discovery_refresh_seconds=0
)


def test_discover_and_sync_serial_reports_when_changed(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_serial_number", lambda host, cert_fingerprint: "new-serial")
    reported = {}
    monkeypatch.setattr(
        main_module,
        "report_discovered_config",
        lambda base_url, source_id, key, **kwargs: reported.update(kwargs),
    )

    config = {"host": "envoy.local", "serial_num": "old", "cert_fingerprint": "pinned", "cert_expires_at": FAR_EXPIRY, "cert_serial": "ABC123"}
    main_module.discover_and_sync_serial(SETTINGS, 1, config)

    assert reported == {"serial_num": "new-serial"}


def test_discover_and_sync_serial_skips_report_when_unchanged(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_serial_number", lambda host, cert_fingerprint: "same")
    called = []
    monkeypatch.setattr(
        main_module, "report_discovered_config", lambda *a, **k: called.append((a, k))
    )

    config = {"host": "envoy.local", "serial_num": "same", "cert_fingerprint": "pinned", "cert_expires_at": FAR_EXPIRY, "cert_serial": "ABC123"}
    main_module.discover_and_sync_serial(SETTINGS, 1, config)

    assert called == []


def test_discover_and_sync_serial_does_not_raise_when_envoy_unreachable(monkeypatch):
    def _raise(host, cert_fingerprint):
        raise OSError("network unreachable")

    monkeypatch.setattr(main_module, "fetch_serial_number", _raise)
    config = {"host": "envoy.local", "cert_fingerprint": "pinned", "cert_expires_at": FAR_EXPIRY, "cert_serial": "ABC123"}
    main_module.discover_and_sync_serial(SETTINGS, 1, config)  # must not raise


def test_discover_and_sync_serial_does_not_raise_when_pinning_fails(monkeypatch):
    def _raise(host):
        raise OSError("network unreachable")

    monkeypatch.setattr(main_module, "fetch_certificate_info", _raise)
    main_module.discover_and_sync_serial(SETTINGS, 1, {"host": "envoy.local"})  # must not raise


def test_discover_and_sync_serial_pins_the_certificate_before_fetching_the_serial(monkeypatch):
    """A brand-new source has no cert_fingerprint yet -- discovery must pin
    one (and persist it to Core API) before it can call fetch_serial_number,
    which now requires a pin to verify against."""
    monkeypatch.setattr(main_module, "fetch_certificate_info", lambda host: _cert("new-fingerprint", 365))
    received_serial_args = []

    def fake_fetch_serial_number(host, cert_fingerprint):
        received_serial_args.append(cert_fingerprint)
        return "123456789012"

    monkeypatch.setattr(main_module, "fetch_serial_number", fake_fetch_serial_number)
    reported = []
    monkeypatch.setattr(
        main_module, "report_discovered_config", lambda base_url, source_id, key, **kwargs: reported.append(kwargs)
    )

    config = {"host": "envoy.local"}
    main_module.discover_and_sync_serial(SETTINGS, 1, config)

    assert config["cert_fingerprint"] == "new-fingerprint"
    assert received_serial_args == ["new-fingerprint"]
    assert config["cert_expires_at"]
    assert config["cert_serial"] == "ABC123"
    assert any(
        r.get("cert_fingerprint") == "new-fingerprint" and r.get("cert_expires_at") and r.get("cert_serial") == "ABC123"
        for r in reported
    )
    assert {"serial_num": "123456789012"} in reported


def test_poll_once_pushes_mapped_readings_on_success(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_meter_readings", lambda host, token, cert_fingerprint: [])
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(main_module, "map_meter_power_readings", lambda meters, data, ts, **_kwargs: [{"ts": ts, "metric": "production_w", "value": 1.0}])
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])
    pushed = []
    monkeypatch.setattr(
        main_module, "push_energy_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )

    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}
    result_config = main_module.poll_once(SETTINGS, 1, config)

    assert result_config == config
    assert len(pushed) == 1
    assert pushed[0][0]["metric"] == "production_w"


def test_poll_once_passes_invert_consumption_ct_from_config(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_meter_readings", lambda host, token, cert_fingerprint: [])
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    received = {}

    def fake_map_meter_power_readings(meters, data, ts, **kwargs):
        received.update(kwargs)
        return []

    monkeypatch.setattr(main_module, "map_meter_power_readings", fake_map_meter_power_readings)
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])
    monkeypatch.setattr(main_module, "push_energy_readings", lambda base_url, source_id, key, readings: None)

    config = {"host": "envoy.local", "token": "abc", "invert_consumption_ct": True, "cert_fingerprint": "pinned"}
    main_module.poll_once(SETTINGS, 1, config)

    assert received == {"invert_consumption_ct": True, "battery_net_w": None}


def test_poll_once_floors_timestamp_to_whole_minute(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_meter_readings", lambda host, token, cert_fingerprint: [])
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(main_module, "map_meter_power_readings", lambda meters, data, ts, **_kwargs: [{"ts": ts, "metric": "production_w", "value": 1.0}])
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])
    pushed = []
    monkeypatch.setattr(
        main_module, "push_energy_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )

    main_module.poll_once(SETTINGS, 1, {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"})

    ts = pushed[0][0]["ts"]
    assert ts.second == 0
    assert ts.microsecond == 0


def test_poll_once_refetches_config_once_on_envoy_401_then_succeeds(monkeypatch):
    calls = {"production": 0}

    def fake_fetch_meter_readings(host, token, cert_fingerprint):
        calls["production"] += 1
        if token == "old-token":
            raise EnvoyAuthError("nope")
        return []

    monkeypatch.setattr(main_module, "fetch_meter_readings", fake_fetch_meter_readings)
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(main_module, "map_meter_power_readings", lambda meters, data, ts, **_kwargs: [])
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])
    monkeypatch.setattr(
        main_module,
        "fetch_source_config",
        lambda base_url, source_id, key: {"host": "envoy.local", "token": "new-token", "cert_fingerprint": "pinned"},
    )
    monkeypatch.setattr(main_module, "push_energy_readings", lambda *a, **k: None)

    config = {"host": "envoy.local", "token": "old-token", "cert_fingerprint": "pinned"}
    result_config = main_module.poll_once(SETTINGS, 1, config)

    assert result_config["token"] == "new-token"
    # one failed power poll, the retry after the refetch, and the lifetime-counter read
    assert calls["production"] == 3


def test_poll_once_logs_and_returns_unchanged_config_when_still_401_after_refetch(monkeypatch, caplog):
    monkeypatch.setattr(
        main_module, "fetch_meter_readings", lambda host, token, cert_fingerprint: (_ for _ in ()).throw(EnvoyAuthError("nope"))
    )
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(
        main_module,
        "fetch_source_config",
        lambda base_url, source_id, key: {
            "host": "envoy.local",
            "token": "old-token",
            "serial_num": "s",
            "cert_fingerprint": "pinned",
        },
    )

    config = {"host": "envoy.local", "token": "old-token", "cert_fingerprint": "pinned"}
    with caplog.at_level("ERROR"):
        result_config = main_module.poll_once(SETTINGS, 1, config)

    assert result_config == {
        "host": "envoy.local",
        "token": "old-token",
        "serial_num": "s",
        "cert_fingerprint": "pinned",
    }
    assert any("entrez-auth-token" in message for message in caplog.messages)


def test_poll_once_skips_cycle_when_envoy_unreachable(monkeypatch, caplog):
    def _raise(host, token, cert_fingerprint):
        raise OSError("network unreachable")

    monkeypatch.setattr(main_module, "fetch_meter_readings", _raise)
    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}

    with caplog.at_level("ERROR"):
        result_config = main_module.poll_once(SETTINGS, 1, config)

    assert result_config == config  # unchanged, no crash


def test_poll_once_skips_cycle_when_no_cert_fingerprint_is_pinned_yet(monkeypatch, caplog):
    called = []
    monkeypatch.setattr(main_module, "fetch_meter_readings", lambda *a, **k: called.append(True))

    config = {"host": "envoy.local", "token": "abc"}
    with caplog.at_level("WARNING"):
        result_config = main_module.poll_once(SETTINGS, 1, config)

    assert result_config == config
    assert called == []  # the real token must never be sent to an unpinned host
    assert any("no pinned TLS certificate" in message for message in caplog.messages)


def test_poll_once_skips_cycle_when_meter_response_is_malformed(monkeypatch, caplog):
    # Uses the REAL map_meter_power_readings (not monkeypatched): readings
    # with no production or net-consumption meter make the real mapping code
    # raise ValueError. Before the fix, mapping ran outside poll_once's
    # try/except and this crashed the whole cycle instead of being logged
    # and skipped.
    monkeypatch.setattr(
        main_module, "fetch_meter_readings", lambda host, token, cert_fingerprint: []
    )
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}

    with caplog.at_level("ERROR"):
        result_config = main_module.poll_once(SETTINGS, 1, config)

    assert result_config == config


def test_poll_once_passes_battery_net_w_computed_from_real_battery_fetch(monkeypatch):
    # Uses the real map_battery_readings/battery_net_w (not monkeypatched) on
    # a realistic ensemble payload, to verify the computed net watt figure
    # actually reaches map_meter_power_readings, not just that some kwarg is
    # passed.
    monkeypatch.setattr(main_module, "fetch_meter_readings", lambda host, token, cert_fingerprint: [])
    monkeypatch.setattr(
        main_module,
        "fetch_battery_power",
        lambda host, token, cert_fingerprint: {"devices:": [{"serial_num": "x", "real_power_mw": 2000000, "soc": 50}]},
    )
    received = {}

    def fake_map_meter_power_readings(meters, data, ts, **kwargs):
        received.update(kwargs)
        return []

    monkeypatch.setattr(main_module, "map_meter_power_readings", fake_map_meter_power_readings)
    monkeypatch.setattr(main_module, "push_energy_readings", lambda base_url, source_id, key, readings: None)

    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}
    main_module.poll_once(SETTINGS, 1, config)

    assert received["battery_net_w"] == pytest.approx(2000.0)


def test_poll_once_requests_meters_and_battery_at_the_same_time(monkeypatch):
    # Both fakes wait for the other at a barrier. Run one after the other, the
    # first would time out and the cycle would fail.
    barrier = threading.Barrier(2, timeout=2)
    met = []

    def fake_fetch_meter_readings(host, token, cert_fingerprint):
        barrier.wait()
        met.append("production")
        return []

    def fake_fetch_battery_power(host, token, cert_fingerprint):
        barrier.wait()
        met.append("battery")
        return {"devices:": []}

    monkeypatch.setattr(main_module, "fetch_meter_readings", fake_fetch_meter_readings)
    monkeypatch.setattr(main_module, "fetch_battery_power", fake_fetch_battery_power)
    monkeypatch.setattr(main_module, "push_energy_readings", lambda base_url, source_id, key, readings: None)

    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}
    main_module.poll_once(SETTINGS, 1, config)

    assert sorted(met) == ["battery", "production"]


def test_poll_once_passes_none_battery_net_w_when_battery_fetch_fails(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_meter_readings", lambda host, token, cert_fingerprint: [])

    def _raise_battery(host, token, cert_fingerprint):
        raise ValueError("no battery installed on this Envoy")

    monkeypatch.setattr(main_module, "fetch_battery_power", _raise_battery)
    received = {}

    def fake_map_meter_power_readings(meters, data, ts, **kwargs):
        received.update(kwargs)
        return []

    monkeypatch.setattr(main_module, "map_meter_power_readings", fake_map_meter_power_readings)
    monkeypatch.setattr(main_module, "push_energy_readings", lambda base_url, source_id, key, readings: None)

    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}
    main_module.poll_once(SETTINGS, 1, config)

    assert received["battery_net_w"] is None


def test_poll_once_still_pushes_production_when_battery_fetch_raises_non_auth_error(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_meter_readings", lambda host, token, cert_fingerprint: [])
    monkeypatch.setattr(
        main_module, "map_meter_power_readings", lambda meters, data, ts, **_kwargs: [{"ts": ts, "metric": "production_w", "value": 42.0}]
    )

    def _raise_battery(host, token, cert_fingerprint):
        raise ValueError("no battery installed on this Envoy")

    monkeypatch.setattr(main_module, "fetch_battery_power", _raise_battery)

    pushed = []
    monkeypatch.setattr(
        main_module, "push_energy_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )

    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}
    result_config = main_module.poll_once(SETTINGS, 1, config)

    assert result_config == config
    assert len(pushed) == 1
    assert pushed[0][0]["metric"] == "production_w"
    assert pushed[0][0]["value"] == 42.0


def test_main_retries_discovery_until_a_source_appears(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    attempts = {"n": 0}

    def fake_fetch(base_url, key):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return []
        return [{"source_id": 1, "site_id": 10, "config": {"host": "envoy.local", "token": "a"}}]

    monkeypatch.setattr(main_module, "fetch_collector_sources", fake_fetch)
    monkeypatch.setattr(main_module, "discover_and_sync_serial", lambda settings, source_id, config: None)
    monkeypatch.setattr(main_module, "poll_once", lambda settings, source_id, config: config)
    sleeps = []
    # Needs 3 fetch attempts to happen before SystemExit: 2 sleeps from the
    # discovery-retry loop's failed attempts, then the 3rd attempt succeeds
    # (no sleep there), then the main poll loop runs once and sleeps a 3rd
    # time -- that 3rd sleep is where we stop.
    monkeypatch.setattr(main_module.time, "sleep", lambda s: (sleeps.append(s), (_ for _ in ()).throw(SystemExit) if len(sleeps) > 2 else None)[-1])

    with pytest.raises(SystemExit):
        main_module.main()

    assert attempts["n"] >= 3


def test_main_loop_survives_unexpected_exception_from_poll_once(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(
        main_module, "fetch_collector_sources", lambda base_url, key: [{"source_id": 1, "site_id": 10, "config": {"host": "envoy.local", "token": "abc"}}]
    )
    monkeypatch.setattr(main_module, "discover_and_sync_serial", lambda settings, source_id, config: None)
    monkeypatch.setattr(main_module, "poll_once", lambda settings, source_id, config: (_ for _ in ()).throw(RuntimeError("boom")))

    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert calls["sleep"] == 2


def test_poll_once_skips_push_but_does_not_raise_when_core_api_unreachable(monkeypatch, caplog):
    monkeypatch.setattr(main_module, "fetch_meter_readings", lambda host, token, cert_fingerprint: [])
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(main_module, "map_meter_power_readings", lambda meters, data, ts, **_kwargs: [{"ts": ts, "metric": "production_w", "value": 1.0}])
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])

    def _raise_core_api(*a, **k):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(main_module, "push_energy_readings", _raise_core_api)

    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}
    with caplog.at_level("ERROR"):
        result_config = main_module.poll_once(SETTINGS, 1, config)

    assert result_config == config  # no crash, cycle just skipped


def _stub_one_reading(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_meter_readings", lambda host, token, cert_fingerprint: [])
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(
        main_module,
        "map_meter_power_readings",
        lambda meters, data, ts, **_kwargs: [{"ts": ts, "metric": "production_w", "value": 1.0}],
    )
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])


def test_poll_once_resends_readings_from_a_failed_push_with_the_next_cycle(monkeypatch):
    _stub_one_reading(monkeypatch)
    main_module._pending_readings.clear()
    batch_sizes = []

    def _push(base_url, source_id, key, readings):
        batch_sizes.append(len(readings))
        if len(batch_sizes) == 1:
            raise CoreApiError("timed out")

    monkeypatch.setattr(main_module, "push_energy_readings", _push)
    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}

    for _ in range(3):
        main_module.poll_once(SETTINGS, 1, config)

    assert batch_sizes == [1, 2, 1]


def test_poll_once_keeps_failed_readings_per_source(monkeypatch):
    _stub_one_reading(monkeypatch)
    main_module._pending_readings.clear()
    pushed = {}

    def _push(base_url, source_id, key, readings):
        pushed.setdefault(source_id, []).append(len(readings))
        if source_id == 1 and len(pushed[1]) == 1:
            raise CoreApiError("timed out")

    monkeypatch.setattr(main_module, "push_energy_readings", _push)
    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}

    main_module.poll_once(SETTINGS, 1, config)
    main_module.poll_once(SETTINGS, 2, config)

    assert pushed == {1: [1], 2: [1]}


def test_poll_once_drops_the_oldest_readings_past_the_backlog_cap(monkeypatch):
    _stub_one_reading(monkeypatch)
    main_module._pending_readings.clear()
    monkeypatch.setattr(main_module, "MAX_PENDING_READINGS", 3)
    sent = []

    def _push(base_url, source_id, key, readings):
        sent.append(len(readings))
        raise CoreApiError("down")

    monkeypatch.setattr(main_module, "push_energy_readings", _push)
    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}

    for _ in range(6):
        main_module.poll_once(SETTINGS, 1, config)

    # The backlog is capped at 3; each batch adds the cycle's fresh reading.
    assert sent == [1, 2, 3, 4, 4, 4]


def test_main_polls_every_discovered_source_each_cycle(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(
        main_module,
        "fetch_collector_sources",
        lambda base_url, key: [
            {"source_id": 1, "site_id": 10, "config": {"host": "envoy-a.local", "token": "a"}},
            {"source_id": 2, "site_id": 11, "config": {"host": "envoy-b.local", "token": "b"}},
        ],
    )
    monkeypatch.setattr(main_module, "discover_and_sync_serial", lambda settings, source_id, config: None)
    polled = []
    monkeypatch.setattr(
        main_module, "poll_once", lambda settings, source_id, config, sampler=None: (polled.append(config), config)[1]
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
    assert {c["host"] for c in polled} == {"envoy-a.local", "envoy-b.local"}


def test_main_keeps_last_known_sources_when_discovery_refresh_fails(monkeypatch):
    """Review Focus: a discovery-refresh failure must not crash the process
    or wipe the in-memory source list -- the next poll cycle keeps using the
    last-known-good list. Uses SETTINGS_ALWAYS_REFRESH so a refresh is
    attempted every cycle, making the 2nd cycle's failure actually exercise
    the fallback path (rather than relying on timing)."""
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS_ALWAYS_REFRESH)

    call_count = {"n": 0}

    def fake_fetch(base_url, key):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [{"source_id": 1, "site_id": 10, "config": {"host": "envoy-a.local", "token": "a"}}]
        raise CoreApiError("core-api unreachable")

    monkeypatch.setattr(main_module, "fetch_collector_sources", fake_fetch)
    monkeypatch.setattr(main_module, "discover_and_sync_serial", lambda settings, source_id, config: None)
    polled = []
    monkeypatch.setattr(
        main_module, "poll_once", lambda settings, source_id, config, sampler=None: (polled.append(config), config)[1]
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
            return [{"source_id": 1, "site_id": 10, "config": {"host": "envoy-a.local", "token": "a"}}]
        raise ValueError("malformed response")

    monkeypatch.setattr(main_module, "fetch_collector_sources", fake_fetch)
    monkeypatch.setattr(main_module, "discover_and_sync_serial", lambda settings, source_id, config: None)
    polled = []
    monkeypatch.setattr(
        main_module, "poll_once", lambda settings, source_id, config, sampler=None: (polled.append(config), config)[1]
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


def test_refresh_sources_uses_freshly_discovered_config_for_already_known_source(monkeypatch):
    """Review Focus: a source already in `known` must pick up its
    freshly-discovered config (e.g. a host edited in the webapp), not the
    stale cached config -- discovery's config comes from the same DB row
    /sources/{id}/config would return."""
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(
        main_module,
        "fetch_collector_sources",
        lambda base_url, key: [
            {
                "source_id": 1,
                "site_id": 10,
                "config": {"host": "new-host.local", "token": "a", "cert_fingerprint": "pinned", "cert_expires_at": FAR_EXPIRY, "cert_serial": "ABC123"},
            },
        ],
    )
    sync_calls = []
    monkeypatch.setattr(
        main_module, "discover_and_sync_serial", lambda settings, source_id, config: sync_calls.append(source_id)
    )

    known = {1: {"host": "stale-host.local", "token": "a", "cert_fingerprint": "pinned", "cert_expires_at": FAR_EXPIRY, "cert_serial": "ABC123"}}
    refreshed = main_module._refresh_sources(SETTINGS, known)

    assert refreshed[1] == {"host": "new-host.local", "token": "a", "cert_fingerprint": "pinned", "cert_expires_at": FAR_EXPIRY, "cert_serial": "ABC123"}
    # Already pinned -- discovery only needs to re-run while a pin is missing.
    assert sync_calls == []


def test_refresh_sources_retries_discovery_for_a_known_source_that_still_lacks_a_pin(monkeypatch):
    """Review Focus: the reliability fix this pairs with -- a source whose
    first discovery attempt failed (host briefly unreachable) must keep
    retrying on every refresh, not just the first time its source_id is
    seen, or it would be silently skipped by poll_once forever."""
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(
        main_module,
        "fetch_collector_sources",
        lambda base_url, key: [
            {"source_id": 1, "site_id": 10, "config": {"host": "envoy.local", "token": "a"}},
        ],
    )
    sync_calls = []
    monkeypatch.setattr(
        main_module, "discover_and_sync_serial", lambda settings, source_id, config: sync_calls.append(source_id)
    )

    # source_id 1 is already "known" from a previous cycle, but its config
    # never got a cert_fingerprint (e.g. that first discovery attempt failed).
    known = {1: {"host": "envoy.local", "token": "a"}}
    main_module._refresh_sources(SETTINGS, known)

    assert sync_calls == [1]


@pytest.fixture(autouse=True)
def _clear_renewal_state():
    main_module._last_renewal_attempt.clear()
    yield
    main_module._last_renewal_attempt.clear()


CREDS = {"username": "u@example.com", "password": "pw-secret", "serial_num": "123456789012"}


def _stub_reporting(monkeypatch):
    reported = []
    monkeypatch.setattr(
        main_module, "report_discovered_config", lambda base_url, source_id, key, **kwargs: reported.append(kwargs)
    )
    return reported


def _stub_envoy_accepting_only_fresh_token(monkeypatch) -> list[str]:
    """Envoy answers 401 to every token except "fresh". Returns the list of
    tokens it was called with."""
    seen: list[str] = []

    def fake_fetch_meter_readings(host, token, cert_fingerprint):
        seen.append(token)
        if token != "fresh":
            raise EnvoyAuthError("nope")
        return []

    monkeypatch.setattr(main_module, "fetch_meter_readings", fake_fetch_meter_readings)
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(main_module, "map_meter_power_readings", lambda meters, data, ts, **_kwargs: [])
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])
    return seen


def test_renew_token_fetches_and_reports_a_new_token(monkeypatch):
    reported = _stub_reporting(monkeypatch)
    monkeypatch.setattr(main_module, "fetch_token", lambda u, p, s: "fresh")
    config = {"host": "h", "token": "old", **CREDS}

    result = main_module.renew_token(SETTINGS, 1, config)

    assert result["token"] == "fresh"
    assert config["token"] == "old"  # input not mutated
    assert reported == [{"token": "fresh"}]


@pytest.mark.parametrize("missing", ["username", "password", "serial_num"])
def test_renew_token_skips_without_credentials_or_serial(monkeypatch, missing):
    calls = []
    monkeypatch.setattr(main_module, "fetch_token", lambda *a: calls.append(a) or "fresh")
    config = {k: v for k, v in CREDS.items() if k != missing}

    assert main_module.renew_token(SETTINGS, 1, config) == config
    assert calls == []
    assert 1 not in main_module._last_renewal_attempt  # a skip does not start the wait


def test_renew_token_waits_an_hour_after_a_failed_login(monkeypatch, caplog):
    calls = []

    def reject(u, p, s):
        calls.append(1)
        raise EnlightenAuthError("Enlighten rejected the username or password")

    monkeypatch.setattr(main_module, "fetch_token", reject)
    clock = {"now": 1000.0}
    monkeypatch.setattr(main_module.time, "monotonic", lambda: clock["now"])
    config = {"token": "old", **CREDS}

    with caplog.at_level("ERROR"):
        assert main_module.renew_token(SETTINGS, 1, config) == config
        clock["now"] += 60
        assert main_module.renew_token(SETTINGS, 1, config) == config
        assert len(calls) == 1
        clock["now"] += 3600
        main_module.renew_token(SETTINGS, 1, config)
    assert len(calls) == 2
    assert not any("pw-secret" in m for m in caplog.messages)


def test_renew_token_returns_config_unchanged_on_enlighten_outage(monkeypatch):
    def down(u, p, s):
        raise EnlightenError("Enlighten login returned HTTP 503")

    monkeypatch.setattr(main_module, "fetch_token", down)
    config = {"token": "old", **CREDS}
    assert main_module.renew_token(SETTINGS, 1, config) == config


def test_renew_token_keeps_the_new_token_when_core_api_rejects_the_write(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_token", lambda u, p, s: "fresh")

    def fail(*a, **k):
        raise CoreApiError("down")

    monkeypatch.setattr(main_module, "report_discovered_config", fail)
    assert main_module.renew_token(SETTINGS, 1, {"token": "old", **CREDS})["token"] == "fresh"


def test_poll_once_renews_after_a_double_401_and_retries(monkeypatch):
    _stub_reporting(monkeypatch)
    monkeypatch.setattr(main_module, "fetch_token", lambda u, p, s: "fresh")
    seen = _stub_envoy_accepting_only_fresh_token(monkeypatch)
    stored = {"host": "envoy.local", "token": "old", "cert_fingerprint": "pinned", **CREDS}
    monkeypatch.setattr(main_module, "fetch_source_config", lambda base_url, source_id, key: dict(stored))
    pushed = []
    monkeypatch.setattr(main_module, "push_energy_readings", lambda *a, **k: pushed.append(a))

    result = main_module.poll_once(SETTINGS, 1, dict(stored))

    assert result["token"] == "fresh"
    # two rejected power polls, the accepted retry, then the lifetime-counter read
    assert seen == ["old", "old", "fresh", "fresh"]
    assert len(pushed) == 1


def test_poll_once_renews_a_source_with_no_token_key(monkeypatch):
    _stub_reporting(monkeypatch)
    monkeypatch.setattr(main_module, "fetch_token", lambda u, p, s: "fresh")
    seen = _stub_envoy_accepting_only_fresh_token(monkeypatch)
    stored = {"host": "envoy.local", "cert_fingerprint": "pinned", **CREDS}  # no "token" key
    monkeypatch.setattr(main_module, "fetch_source_config", lambda base_url, source_id, key: dict(stored))
    monkeypatch.setattr(main_module, "push_energy_readings", lambda *a, **k: None)

    result = main_module.poll_once(SETTINGS, 1, dict(stored))

    assert result["token"] == "fresh"
    assert seen[0] == ""  # first attempt went out with an empty token and got the 401


def test_poll_once_falls_back_to_the_manual_message_when_renewal_fails(monkeypatch, caplog):
    def reject(u, p, s):
        raise EnlightenAuthError("Enlighten rejected the username or password")

    monkeypatch.setattr(main_module, "fetch_token", reject)
    monkeypatch.setattr(
        main_module, "fetch_meter_readings", lambda host, token, cert_fingerprint: (_ for _ in ()).throw(EnvoyAuthError("x"))
    )
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    stored = {"host": "envoy.local", "token": "old", "cert_fingerprint": "pinned", **CREDS}
    monkeypatch.setattr(main_module, "fetch_source_config", lambda base_url, source_id, key: dict(stored))

    with caplog.at_level("ERROR"):
        result = main_module.poll_once(SETTINGS, 1, dict(stored))

    assert result == stored
    assert any("entrez-auth-token" in m for m in caplog.messages)
    assert not any("pw-secret" in m for m in caplog.messages)


NOW = datetime.datetime(2026, 10, 13, tzinfo=datetime.timezone.utc)


@pytest.mark.parametrize(
    ("days_left", "expected"), [(13, True), (14.5, False), (-1, True), (400, False)]
)
def test_token_expiring_uses_a_14_day_window(days_left, expected):
    exp = (NOW + datetime.timedelta(days=days_left)).timestamp()
    assert main_module._token_expiring({"token": make_jwt({"exp": exp})}, NOW) is expected


@pytest.mark.parametrize("token", [None, "", "opaque-token", make_jwt({"exp": "soon"}), make_jwt({})])
def test_token_expiring_ignores_unreadable_tokens(token):
    assert main_module._token_expiring({"token": token}, NOW) is False


def test_refresh_sources_renews_a_token_close_to_expiry(monkeypatch):
    soon = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)).timestamp()
    expiring = {"host": "h", "token": make_jwt({"exp": soon}), "cert_fingerprint": "pinned", "cert_expires_at": FAR_EXPIRY, "cert_serial": "ABC123", **CREDS}
    monkeypatch.setattr(
        main_module, "fetch_collector_sources", lambda base_url, key: [{"source_id": 1, "config": dict(expiring)}]
    )
    _stub_reporting(monkeypatch)
    monkeypatch.setattr(main_module, "fetch_token", lambda u, p, s: "fresh")

    refreshed = main_module._refresh_sources(SETTINGS, {})

    assert refreshed[1]["token"] == "fresh"


def test_refresh_sources_leaves_a_healthy_token_alone(monkeypatch):
    later = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=200)).timestamp()
    healthy = {"host": "h", "token": make_jwt({"exp": later}), "cert_fingerprint": "pinned", "cert_expires_at": FAR_EXPIRY, "cert_serial": "ABC123", **CREDS}
    monkeypatch.setattr(
        main_module, "fetch_collector_sources", lambda base_url, key: [{"source_id": 1, "config": dict(healthy)}]
    )
    calls = []
    monkeypatch.setattr(main_module, "fetch_token", lambda *a: calls.append(a) or "fresh")

    assert main_module._refresh_sources(SETTINGS, {})[1]["token"] == healthy["token"]
    assert calls == []


def _reports(monkeypatch) -> list[dict]:
    reported: list[dict] = []
    monkeypatch.setattr(
        main_module, "report_discovered_config", lambda base_url, source_id, key, **kwargs: reported.append(kwargs)
    )
    return reported


def _mismatch(*_args):
    raise EnvoyCertificateMismatch("changed")


def _expiring_config(days_left: int, **extra) -> dict:
    expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days_left)
    return {
        "host": "envoy.local",
        "token": "abc",
        "serial_num": "123456789012",
        "cert_fingerprint": "old",
        "cert_expires_at": expires.isoformat(),
        "cert_serial": "OLD",
        **extra,
    }


def test_discovery_backfills_expiry_and_serial_for_a_source_pinned_before_they_were_tracked(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_certificate_info", lambda host: _cert("pinned", 200))
    monkeypatch.setattr(main_module, "fetch_serial_number", lambda host, fp: "123456789012")
    reported = _reports(monkeypatch)

    config = {"host": "envoy.local", "serial_num": "123456789012", "cert_fingerprint": "pinned"}
    main_module.discover_and_sync_serial(SETTINGS, 1, config)

    assert config["cert_serial"] == "ABC123"
    assert reported == [{"cert_expires_at": config["cert_expires_at"], "cert_serial": "ABC123"}]


def test_discovery_does_not_record_expiry_of_a_certificate_that_is_not_the_pinned_one(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_certificate_info", lambda host: _cert("someone-else", 200))
    monkeypatch.setattr(main_module, "fetch_serial_number", lambda host, fp: "123456789012")
    reported = _reports(monkeypatch)

    config = {"host": "envoy.local", "serial_num": "123456789012", "cert_fingerprint": "pinned"}
    main_module.discover_and_sync_serial(SETTINGS, 1, config)

    assert "cert_expires_at" not in config
    assert "cert_serial" not in config
    assert reported == []


def test_poll_once_repins_a_changed_certificate_within_100_days_of_expiry(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_meter_readings", _mismatch)
    monkeypatch.setattr(main_module, "fetch_certificate_info", lambda host: _cert("new", 365))
    monkeypatch.setattr(main_module, "fetch_serial_number", lambda host, fp: "123456789012")
    reported = _reports(monkeypatch)

    result = main_module.poll_once(SETTINGS, 1, _expiring_config(days_left=100))

    assert result["cert_fingerprint"] == "new"
    new_expiry = datetime.datetime.fromisoformat(result["cert_expires_at"])
    assert new_expiry > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=360)
    assert result["cert_serial"] == "ABC123"
    assert reported == [
        {"cert_fingerprint": "new", "cert_expires_at": result["cert_expires_at"], "cert_serial": "ABC123"}
    ]


def test_poll_once_repins_when_the_pinned_certificate_has_already_expired(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_meter_readings", _mismatch)
    monkeypatch.setattr(main_module, "fetch_certificate_info", lambda host: _cert("new", 365))
    monkeypatch.setattr(main_module, "fetch_serial_number", lambda host, fp: "123456789012")
    _reports(monkeypatch)

    result = main_module.poll_once(SETTINGS, 1, _expiring_config(days_left=-3))

    assert result["cert_fingerprint"] == "new"


def test_poll_once_refuses_to_repin_a_changed_certificate_with_more_than_100_days_left(monkeypatch, caplog):
    monkeypatch.setattr(main_module, "fetch_meter_readings", _mismatch)
    fetched = []
    monkeypatch.setattr(main_module, "fetch_certificate_info", lambda host: fetched.append(host))
    reported = _reports(monkeypatch)

    config = _expiring_config(days_left=101)
    with caplog.at_level("ERROR"):
        result = main_module.poll_once(SETTINGS, 1, config)

    assert result == config
    assert fetched == []
    assert reported == []
    assert any("not re-pinning" in m for m in caplog.messages)


def test_poll_once_refuses_to_repin_when_the_expiry_is_unknown(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_meter_readings", _mismatch)
    reported = _reports(monkeypatch)
    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "old"}

    assert main_module.poll_once(SETTINGS, 1, config) == config
    assert reported == []


def test_repin_refuses_when_the_serial_number_changed(monkeypatch, caplog):
    monkeypatch.setattr(main_module, "fetch_certificate_info", lambda host: _cert("new", 365))
    monkeypatch.setattr(main_module, "fetch_serial_number", lambda host, fp: "999999999999")
    reported = _reports(monkeypatch)

    config = _expiring_config(days_left=30)
    with caplog.at_level("ERROR"):
        result = main_module.repin_certificate(SETTINGS, 1, config)

    assert result == config
    assert reported == []
    assert any("serial" in m for m in caplog.messages)


def test_repin_refuses_when_no_serial_number_is_stored(monkeypatch, caplog):
    fetched = []
    monkeypatch.setattr(main_module, "fetch_certificate_info", lambda host: fetched.append(host))
    reported = _reports(monkeypatch)

    config = _expiring_config(days_left=30)
    del config["serial_num"]
    with caplog.at_level("ERROR"):
        result = main_module.repin_certificate(SETTINGS, 1, config)

    assert result == config
    assert fetched == []
    assert reported == []
    assert any("no serial number" in m for m in caplog.messages)


def test_repin_keeps_the_old_pin_when_the_new_certificate_cannot_be_read(monkeypatch):
    def _raise(host):
        raise OSError("unreachable")

    monkeypatch.setattr(main_module, "fetch_certificate_info", _raise)
    config = _expiring_config(days_left=30)

    assert main_module.repin_certificate(SETTINGS, 1, config) == config


# --- live sampler and meter readings ---------------------------------------

PINNED = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}


@pytest.fixture(autouse=True)
def _no_meter_calls(monkeypatch):
    """Keeps poll_once off the network for meters; tests that care override these."""
    monkeypatch.setattr(main_module, "fetch_meters", lambda host, token, fp: [])
    monkeypatch.setattr(main_module, "fetch_meter_readings", lambda host, token, fp: [])
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, fp: {"devices:": []})
    main_module._meters_cache.clear()
    main_module._samplers.clear()
    main_module._meter_failure_logs.clear()
    yield
    main_module._meters_cache.clear()
    main_module._samplers.clear()
    main_module._meter_failure_logs.clear()


def _sample(production_w: float, last_update: int = 1) -> LiveSample:
    return LiveSample(
        last_update=last_update,
        production_w=production_w,
        grid_w=100.0,
        battery_w=0.0,
        soc=50.0,
        relay_state=1,
        has_battery=False,
    )


class FakeSampler:
    def __init__(self, samples: list[LiveSample] | None = None, fresh: bool = True) -> None:
        self.samples = samples or []
        self.fresh = fresh
        self.drained = 0
        self.config: dict = {}

    def is_fresh(self) -> bool:
        return self.fresh

    def drain(self) -> list[LiveSample]:
        self.drained += 1
        samples, self.samples = self.samples, []
        return samples


def _capture_pushes(monkeypatch) -> list[list[dict]]:
    pushed: list[list[dict]] = []
    monkeypatch.setattr(
        main_module, "push_energy_readings", lambda base_url, source_id, key, readings: pushed.append(readings)
    )
    return pushed


def _stub_fetch_and_map(monkeypatch, readings: list[dict]) -> list[int]:
    calls: list[int] = []

    def fake(source_id, config, ts):
        calls.append(1)
        return list(readings)

    monkeypatch.setattr(main_module, "_fetch_and_map", fake)
    return calls


def _values(batch: list[dict]) -> dict[str, float]:
    return {r["metric"]: r["value"] for r in batch}


def test_poll_once_uses_live_samples_when_fresh(monkeypatch):
    pushed = _capture_pushes(monkeypatch)
    calls = _stub_fetch_and_map(monkeypatch, [])
    sampler = FakeSampler([_sample(1000.0), _sample(2000.0, 2)])

    main_module.poll_once(SETTINGS, 1, dict(PINNED), sampler)

    assert calls == []
    assert _values(pushed[0])["production_w"] == 1500.0


def test_poll_once_falls_back_when_sampler_is_stale(monkeypatch):
    pushed = _capture_pushes(monkeypatch)
    calls = _stub_fetch_and_map(monkeypatch, [{"ts": None, "metric": "production_w", "value": 7.0}])
    sampler = FakeSampler([_sample(1000.0)], fresh=False)

    main_module.poll_once(SETTINGS, 1, dict(PINNED), sampler)

    assert calls == [1]
    assert pushed[0] == [{"ts": None, "metric": "production_w", "value": 7.0}]
    assert sampler.drained == 1
    assert sampler.samples == []


def test_poll_once_with_empty_sampler_falls_back(monkeypatch):
    pushed = _capture_pushes(monkeypatch)
    calls = _stub_fetch_and_map(monkeypatch, [{"ts": None, "metric": "production_w", "value": 7.0}])
    sampler = FakeSampler([], fresh=True)

    main_module.poll_once(SETTINGS, 1, dict(PINNED), sampler)

    assert calls == [1]
    assert pushed[0][0]["value"] == 7.0


def test_poll_once_without_sampler_is_unchanged(monkeypatch):
    pushed = _capture_pushes(monkeypatch)
    calls = _stub_fetch_and_map(monkeypatch, [{"ts": None, "metric": "production_w", "value": 7.0}])

    config = dict(PINNED)
    assert main_module.poll_once(SETTINGS, 1, config) == config

    assert calls == [1]
    assert pushed == [[{"ts": None, "metric": "production_w", "value": 7.0}]]


ENSEMBLE = {"devices:": [{"real_power_mw": 1500000, "soc": 80}]}


def _battery_sample() -> LiveSample:
    return LiveSample(
        last_update=1, production_w=1000.0, grid_w=100.0, battery_w=-44.0, soc=10.0, relay_state=1, has_battery=True
    )


def test_poll_once_ensemble_mode_takes_live_battery_rows_from_ensemble_fetch(monkeypatch):
    pushed = _capture_pushes(monkeypatch)
    calls = _stub_fetch_and_map(monkeypatch, [])
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, fp: ENSEMBLE)
    settings = dataclasses.replace(SETTINGS, live_battery_source="ensemble")

    main_module.poll_once(settings, 1, dict(PINNED), FakeSampler([_battery_sample()]))

    assert calls == []
    values = _values(pushed[0])
    assert values["battery_discharge_w"] == 1500.0
    assert values["battery_soc_pct"] == 80
    assert "battery_charge_w" not in values
    assert values["consumption_w"] == 2600.0


@pytest.mark.parametrize("error", [RuntimeError("down"), EnvoyAuthError("401")])
def test_poll_once_ensemble_fetch_failure_still_pushes_live_rows(monkeypatch, error):
    pushed = _capture_pushes(monkeypatch)
    _stub_fetch_and_map(monkeypatch, [])

    def failing(host, token, fp):
        raise error

    monkeypatch.setattr(main_module, "fetch_battery_power", failing)
    settings = dataclasses.replace(SETTINGS, live_battery_source="ensemble")

    main_module.poll_once(settings, 1, dict(PINNED), FakeSampler([_battery_sample()]))

    assert _values(pushed[0]) == {
        "production_w": 1000.0,
        "consumption_w": 1100.0,
        "grid_import_w": 100.0,
        "grid_relay_state": 1,
    }


def test_poll_once_livedata_mode_does_not_fetch_ensemble(monkeypatch):
    pushed = _capture_pushes(monkeypatch)
    _stub_fetch_and_map(monkeypatch, [])

    def unexpected(host, token, fp):
        raise AssertionError("ensemble fetched in livedata mode")

    monkeypatch.setattr(main_module, "fetch_battery_power", unexpected)
    settings = dataclasses.replace(SETTINGS, live_battery_source="livedata")

    main_module.poll_once(settings, 1, dict(PINNED), FakeSampler([_battery_sample()]))

    assert _values(pushed[0])["battery_charge_w"] == 44.0


METERS = [
    {"eid": 1, "state": "enabled", "measurementType": "production", "phaseCount": 1},
    {"eid": 2, "state": "enabled", "measurementType": "net-consumption", "phaseCount": 1},
]
METER_READINGS = [
    {"eid": 1, "actEnergyDlvd": 5000.0, "actEnergyRcvd": 0.0, "channels": []},
    {"eid": 2, "actEnergyDlvd": 300.0, "actEnergyRcvd": 40.0, "channels": [{"voltage": 240.0}]},
]


def _stub_meters(monkeypatch, meters=METERS, readings=METER_READINGS) -> list[str]:
    calls: list[str] = []

    def fake_meters(host, token, fp):
        calls.append("meters")
        return meters

    def fake_readings(host, token, fp):
        calls.append("readings")
        return readings

    monkeypatch.setattr(main_module, "fetch_meters", fake_meters)
    monkeypatch.setattr(main_module, "fetch_meter_readings", fake_readings)
    return calls


def test_meter_readings_are_pushed_with_power_readings(monkeypatch):
    pushed = _capture_pushes(monkeypatch)
    _stub_meters(monkeypatch)

    main_module.poll_once(SETTINGS, 1, dict(PINNED), FakeSampler([_sample(1000.0)]))

    values = _values(pushed[0])
    assert values["production_w"] == 1000.0
    assert values["production_wh_lifetime"] == 5000.0
    assert values["grid_import_wh_lifetime"] == 300.0
    assert values["grid_export_wh_lifetime"] == 40.0
    assert values["grid_voltage_ph_a_v"] == 240.0


def test_meter_readings_failure_still_pushes_power_readings(monkeypatch):
    pushed = _capture_pushes(monkeypatch)

    def boom(host, token, fp):
        raise RuntimeError("meters down")

    monkeypatch.setattr(main_module, "fetch_meter_readings", boom)
    monkeypatch.setattr(main_module, "fetch_meters", lambda host, token, fp: METERS)

    main_module.poll_once(SETTINGS, 1, dict(PINNED), FakeSampler([_sample(1000.0)]))

    assert _values(pushed[0]) == {
        "production_w": 1000.0,
        "consumption_w": 1100.0,
        "grid_import_w": 100.0,
        "grid_relay_state": 1,
    }


def test_unknown_meter_is_skipped_but_power_is_pushed(monkeypatch):
    pushed = _capture_pushes(monkeypatch)
    _stub_meters(monkeypatch, readings=[{"eid": 99, "actEnergyDlvd": 1.0, "actEnergyRcvd": 1.0, "channels": []}])

    main_module.poll_once(SETTINGS, 1, dict(PINNED), FakeSampler([_sample(1000.0)]))

    values = _values(pushed[0])
    assert values["production_w"] == 1000.0
    assert not any(m.endswith("_lifetime") for m in values)


def test_meter_readings_are_pushed_in_the_fallback_path(monkeypatch):
    pushed = _capture_pushes(monkeypatch)
    _stub_fetch_and_map(monkeypatch, [{"ts": None, "metric": "production_w", "value": 7.0}])
    _stub_meters(monkeypatch)

    main_module.poll_once(SETTINGS, 1, dict(PINNED))

    assert _values(pushed[0])["production_wh_lifetime"] == 5000.0


def test_meters_listing_is_fetched_once_per_source(monkeypatch):
    _capture_pushes(monkeypatch)
    calls = _stub_meters(monkeypatch)

    for _ in range(3):
        main_module.poll_once(SETTINGS, 1, dict(PINNED), FakeSampler([_sample(1000.0)]))

    assert calls.count("meters") == 1
    assert calls.count("readings") == 3


def test_meters_cache_cleared_on_auth_failure(monkeypatch):
    _stub_meters(monkeypatch)
    ts = datetime.datetime.now(datetime.timezone.utc)
    assert main_module._meter_readings_or_empty(1, dict(PINNED), ts)
    assert 1 in main_module._meters_cache

    def unauthorized(host, token, fp):
        raise EnvoyAuthError("401")

    monkeypatch.setattr(main_module, "fetch_meter_readings", unauthorized)

    assert main_module._meter_readings_or_empty(1, dict(PINNED), ts) == []
    assert 1 not in main_module._meters_cache


def test_meter_failures_are_rate_limited_per_source(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="envoy_collector")
    now = [1000.0]
    monkeypatch.setattr(main_module.time, "monotonic", lambda: now[0])

    def boom(host, token, fp):
        raise RuntimeError("meters down")

    monkeypatch.setattr(main_module, "fetch_meter_readings", boom)
    ts = datetime.datetime.now(datetime.timezone.utc)

    for source_id in (1, 1, 2):
        assert main_module._meter_readings_or_empty(source_id, dict(PINNED), ts) == []
    assert [(r.levelno, r.exc_info is not None) for r in caplog.records] == [
        (logging.ERROR, True),
        (logging.ERROR, True),
    ]
    now[0] += FAILURE_LOG_INTERVAL_SECONDS
    main_module._meter_readings_or_empty(1, dict(PINNED), ts)
    assert caplog.records[-1].levelno == logging.WARNING
    assert "2 failures since last log" in caplog.records[-1].getMessage()


def test_meter_success_resets_the_failure_streak(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="envoy_collector")
    ts = datetime.datetime.now(datetime.timezone.utc)
    failing = [True]

    def readings(host, token, fp):
        if failing[0]:
            raise RuntimeError("meters down")
        return []

    monkeypatch.setattr(main_module, "fetch_meter_readings", readings)
    main_module._meter_readings_or_empty(1, dict(PINNED), ts)
    failing[0] = False
    main_module._meter_readings_or_empty(1, dict(PINNED), ts)
    failing[0] = True
    main_module._meter_readings_or_empty(1, dict(PINNED), ts)

    assert [r.levelno for r in caplog.records] == [logging.ERROR, logging.ERROR]


def test_refresh_sources_clears_the_meters_cache(monkeypatch):
    main_module._meters_cache[1] = METERS
    monkeypatch.setattr(main_module, "fetch_collector_sources", lambda base_url, key: [])

    main_module._refresh_sources(SETTINGS, {})

    assert main_module._meters_cache == {}


def test_poll_once_runs_meters_after_auth_recovery(monkeypatch):
    pushed = _capture_pushes(monkeypatch)
    _stub_meters(monkeypatch)

    def unauthorized(source_id, config, ts):
        raise EnvoyAuthError("401")

    monkeypatch.setattr(main_module, "_fetch_and_map", unauthorized)
    recovered = dict(PINNED, token="new")
    ok = [{"ts": None, "metric": "production_w", "value": 7.0}]
    monkeypatch.setattr(main_module, "_recover_from_auth_failure", lambda s, sid, c, ts: (recovered, ok))

    result = main_module.poll_once(SETTINGS, 1, dict(PINNED))

    assert result == recovered
    assert _values(pushed[0])["production_wh_lifetime"] == 5000.0


class FakeLiveSampler:
    instances: list["FakeLiveSampler"] = []

    def __init__(
        self, config, fetch, enable_stream, *, label, interval_seconds, stale_seconds, on_sample=None
    ) -> None:
        self.on_sample = on_sample
        self.config = config
        self.fetch = fetch
        self.enable_stream = enable_stream
        self.label = label
        self.interval_seconds = interval_seconds
        self.stale_seconds = stale_seconds
        self.started = False
        self.stopped = False
        FakeLiveSampler.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


def test_sync_samplers_starts_one_per_pinned_source_and_stops_removed_ones(monkeypatch):
    FakeLiveSampler.instances = []
    monkeypatch.setattr(main_module, "LiveSampler", FakeLiveSampler)
    seen = []
    monkeypatch.setattr(
        main_module, "fetch_livedata", lambda host, token, fp: (seen.append((host, token, fp)), {})[1]
    )

    main_module._sync_samplers(SETTINGS, {1: dict(PINNED), 2: dict(PINNED, host="b.local")})

    assert set(main_module._samplers) == {1, 2}
    first = main_module._samplers[1]
    assert first.started
    assert first.label == "source 1"
    assert first.interval_seconds == SETTINGS.live_sample_seconds
    assert first.stale_seconds == SETTINGS.live_stale_seconds
    first.fetch(first.config)
    assert seen == [("envoy.local", "abc", "pinned")]

    new_config = dict(PINNED, token="renewed")
    main_module._sync_samplers(SETTINGS, {1: new_config})

    assert main_module._samplers[1] is first
    assert first.config is new_config
    assert len(FakeLiveSampler.instances) == 2
    assert FakeLiveSampler.instances[1].stopped
    assert set(main_module._samplers) == {1}


def test_sampler_on_sample_pushes_mapped_values_to_core_api(monkeypatch):
    FakeLiveSampler.instances = []
    monkeypatch.setattr(main_module, "LiveSampler", FakeLiveSampler)
    pushes = []
    monkeypatch.setattr(main_module, "push_live_sample", lambda *args: pushes.append(args))
    config = dict(PINNED, invert_consumption_ct=True)
    main_module._sync_samplers(SETTINGS, {4: config})
    sample = LiveSample(
        last_update=1, production_w=3000, grid_w=-3400, battery_w=-200, soc=80, relay_state=1, has_battery=True
    )

    main_module._samplers[4].on_sample(sample, config)

    [(url, source_id, key, ts, values)] = pushes
    assert (url, source_id, key) == (SETTINGS.core_api_url, 4, SETTINGS.collector_key)
    assert ts.tzinfo is not None
    assert values == map_live_sample_values(sample, invert_consumption_ct=True)


def test_live_push_failure_is_swallowed(monkeypatch, caplog):
    livedata = json.loads((pathlib.Path(__file__).parent / "fixtures" / "livedata_status.json").read_text())
    monkeypatch.setattr(main_module, "fetch_livedata", lambda host, token, fp: livedata)

    def down(*args):
        raise CoreApiError("down")

    monkeypatch.setattr(main_module, "push_live_sample", down)
    main_module._sync_samplers(SETTINGS, {4: dict(PINNED)})
    sampler = main_module._samplers[4]
    sampler.stop()

    with caplog.at_level(logging.ERROR, logger="envoy_collector"):
        sampler.sample_once()

    assert len(sampler.drain()) == 1
    assert "Live push failed for source 4" in caplog.text


def test_sync_samplers_skips_source_without_pin(monkeypatch):
    FakeLiveSampler.instances = []
    monkeypatch.setattr(main_module, "LiveSampler", FakeLiveSampler)

    main_module._sync_samplers(SETTINGS, {1: {"host": "envoy.local", "token": "abc"}})

    assert main_module._samplers == {}
    assert FakeLiveSampler.instances == []


def test_sync_samplers_stops_a_sampler_whose_pin_was_cleared(monkeypatch):
    FakeLiveSampler.instances = []
    monkeypatch.setattr(main_module, "LiveSampler", FakeLiveSampler)
    main_module._sync_samplers(SETTINGS, {1: dict(PINNED)})
    sampler = main_module._samplers[1]

    main_module._sync_samplers(SETTINGS, {1: {"host": "new.local", "token": "abc"}})

    assert sampler.stopped
    assert main_module._samplers == {}


def test_main_gives_the_poll_result_to_the_sampler(monkeypatch):
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    sampler = FakeSampler()
    monkeypatch.setattr(main_module, "_refresh_sources", lambda settings, known: {1: dict(PINNED)})
    monkeypatch.setattr(
        main_module, "_sync_samplers", lambda settings, sources: main_module._samplers.update({1: sampler})
    )
    renewed = dict(PINNED, token="renewed")
    monkeypatch.setattr(main_module, "poll_once", lambda settings, sid, config, s=None: renewed)
    calls = {"sleep": 0}

    def _fake_sleep(seconds):
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            raise SystemExit

    monkeypatch.setattr(main_module.time, "sleep", _fake_sleep)

    with pytest.raises(SystemExit):
        main_module.main()

    assert sampler.config is renewed
