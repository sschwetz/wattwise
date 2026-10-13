from __future__ import annotations

import datetime

import pytest

from envoy_collector import main as main_module
from envoy_collector.config import Settings
from envoy_collector.core_api_client import CoreApiError
from envoy_collector.enlighten_client import EnlightenAuthError, EnlightenError
from envoy_collector.envoy_client import CertificateInfo, EnvoyAuthError, EnvoyCertificateMismatch
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
    monkeypatch.setattr(main_module, "fetch_production", lambda host, token, cert_fingerprint: {"production": []})
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(main_module, "map_production_readings", lambda data, ts, **_kwargs: [{"ts": ts, "metric": "production_w", "value": 1.0}])
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
    monkeypatch.setattr(main_module, "fetch_production", lambda host, token, cert_fingerprint: {"production": []})
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    received = {}

    def fake_map_production_readings(data, ts, **kwargs):
        received.update(kwargs)
        return []

    monkeypatch.setattr(main_module, "map_production_readings", fake_map_production_readings)
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])
    monkeypatch.setattr(main_module, "push_energy_readings", lambda base_url, source_id, key, readings: None)

    config = {"host": "envoy.local", "token": "abc", "invert_consumption_ct": True, "cert_fingerprint": "pinned"}
    main_module.poll_once(SETTINGS, 1, config)

    assert received == {"invert_consumption_ct": True, "battery_net_w": None}


def test_poll_once_floors_timestamp_to_whole_minute(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_production", lambda host, token, cert_fingerprint: {"production": []})
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(main_module, "map_production_readings", lambda data, ts, **_kwargs: [{"ts": ts, "metric": "production_w", "value": 1.0}])
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

    def fake_fetch_production(host, token, cert_fingerprint):
        calls["production"] += 1
        if token == "old-token":
            raise EnvoyAuthError("nope")
        return {"production": []}

    monkeypatch.setattr(main_module, "fetch_production", fake_fetch_production)
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(main_module, "map_production_readings", lambda data, ts, **_kwargs: [])
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
    assert calls["production"] == 2  # one failed attempt, one retry after refetch


def test_poll_once_logs_and_returns_unchanged_config_when_still_401_after_refetch(monkeypatch, caplog):
    monkeypatch.setattr(
        main_module, "fetch_production", lambda host, token, cert_fingerprint: (_ for _ in ()).throw(EnvoyAuthError("nope"))
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

    monkeypatch.setattr(main_module, "fetch_production", _raise)
    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}

    with caplog.at_level("ERROR"):
        result_config = main_module.poll_once(SETTINGS, 1, config)

    assert result_config == config  # unchanged, no crash


def test_poll_once_skips_cycle_when_no_cert_fingerprint_is_pinned_yet(monkeypatch, caplog):
    called = []
    monkeypatch.setattr(main_module, "fetch_production", lambda *a, **k: called.append(True))

    config = {"host": "envoy.local", "token": "abc"}
    with caplog.at_level("WARNING"):
        result_config = main_module.poll_once(SETTINGS, 1, config)

    assert result_config == config
    assert called == []  # the real token must never be sent to an unpinned host
    assert any("no pinned TLS certificate" in message for message in caplog.messages)


def test_poll_once_skips_cycle_when_production_response_is_malformed(monkeypatch, caplog):
    # Uses the REAL map_production_readings (not monkeypatched): a
    # production.json missing the "eim" entry makes the real mapping code
    # raise StopIteration. Before the fix, mapping ran outside poll_once's
    # try/except and this crashed the whole cycle instead of being logged
    # and skipped.
    monkeypatch.setattr(
        main_module, "fetch_production", lambda host, token, cert_fingerprint: {"production": [], "consumption": []}
    )
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}

    with caplog.at_level("ERROR"):
        result_config = main_module.poll_once(SETTINGS, 1, config)

    assert result_config == config


def test_poll_once_passes_battery_net_w_computed_from_real_battery_fetch(monkeypatch):
    # Uses the real map_battery_readings/battery_net_w (not monkeypatched) on
    # a realistic ensemble payload, to verify the computed net watt figure
    # actually reaches map_production_readings, not just that some kwarg is
    # passed.
    monkeypatch.setattr(main_module, "fetch_production", lambda host, token, cert_fingerprint: {"production": []})
    monkeypatch.setattr(
        main_module,
        "fetch_battery_power",
        lambda host, token, cert_fingerprint: {"devices:": [{"serial_num": "x", "real_power_mw": 2000000, "soc": 50}]},
    )
    received = {}

    def fake_map_production_readings(data, ts, **kwargs):
        received.update(kwargs)
        return []

    monkeypatch.setattr(main_module, "map_production_readings", fake_map_production_readings)
    monkeypatch.setattr(main_module, "push_energy_readings", lambda base_url, source_id, key, readings: None)

    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}
    main_module.poll_once(SETTINGS, 1, config)

    assert received["battery_net_w"] == pytest.approx(2000.0)


def test_poll_once_passes_none_battery_net_w_when_battery_fetch_fails(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_production", lambda host, token, cert_fingerprint: {"production": []})

    def _raise_battery(host, token, cert_fingerprint):
        raise ValueError("no battery installed on this Envoy")

    monkeypatch.setattr(main_module, "fetch_battery_power", _raise_battery)
    received = {}

    def fake_map_production_readings(data, ts, **kwargs):
        received.update(kwargs)
        return []

    monkeypatch.setattr(main_module, "map_production_readings", fake_map_production_readings)
    monkeypatch.setattr(main_module, "push_energy_readings", lambda base_url, source_id, key, readings: None)

    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}
    main_module.poll_once(SETTINGS, 1, config)

    assert received["battery_net_w"] is None


def test_poll_once_still_pushes_production_when_battery_fetch_raises_non_auth_error(monkeypatch):
    monkeypatch.setattr(main_module, "fetch_production", lambda host, token, cert_fingerprint: {"production": []})
    monkeypatch.setattr(
        main_module, "map_production_readings", lambda data, ts, **_kwargs: [{"ts": ts, "metric": "production_w", "value": 42.0}]
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
    monkeypatch.setattr(main_module, "fetch_production", lambda host, token, cert_fingerprint: {"production": []})
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(main_module, "map_production_readings", lambda data, ts, **_kwargs: [{"ts": ts, "metric": "production_w", "value": 1.0}])
    monkeypatch.setattr(main_module, "map_battery_readings", lambda data, ts: [])

    def _raise_core_api(*a, **k):
        raise CoreApiError("unreachable")

    monkeypatch.setattr(main_module, "push_energy_readings", _raise_core_api)

    config = {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pinned"}
    with caplog.at_level("ERROR"):
        result_config = main_module.poll_once(SETTINGS, 1, config)

    assert result_config == config  # no crash, cycle just skipped


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
        main_module, "poll_once", lambda settings, source_id, config: (polled.append(config), config)[1]
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
        main_module, "poll_once", lambda settings, source_id, config: (polled.append(config), config)[1]
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
        main_module, "poll_once", lambda settings, source_id, config: (polled.append(config), config)[1]
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

    def fake_fetch_production(host, token, cert_fingerprint):
        seen.append(token)
        if token != "fresh":
            raise EnvoyAuthError("nope")
        return {"production": []}

    monkeypatch.setattr(main_module, "fetch_production", fake_fetch_production)
    monkeypatch.setattr(main_module, "fetch_battery_power", lambda host, token, cert_fingerprint: {"devices:": []})
    monkeypatch.setattr(main_module, "map_production_readings", lambda data, ts, **_kwargs: [])
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
    assert seen == ["old", "old", "fresh"]
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
        main_module, "fetch_production", lambda host, token, cert_fingerprint: (_ for _ in ()).throw(EnvoyAuthError("x"))
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
    monkeypatch.setattr(main_module, "fetch_production", _mismatch)
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
    monkeypatch.setattr(main_module, "fetch_production", _mismatch)
    monkeypatch.setattr(main_module, "fetch_certificate_info", lambda host: _cert("new", 365))
    monkeypatch.setattr(main_module, "fetch_serial_number", lambda host, fp: "123456789012")
    _reports(monkeypatch)

    result = main_module.poll_once(SETTINGS, 1, _expiring_config(days_left=-3))

    assert result["cert_fingerprint"] == "new"


def test_poll_once_refuses_to_repin_a_changed_certificate_with_more_than_100_days_left(monkeypatch, caplog):
    monkeypatch.setattr(main_module, "fetch_production", _mismatch)
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
    monkeypatch.setattr(main_module, "fetch_production", _mismatch)
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
