import asyncio
import dataclasses
import datetime
import json
import logging
from collections.abc import Coroutine
from typing import Any

import pytest
from actron_neo_api import ActronAirAPIError, ActronAirStatus
from actron_neo_api.rt import RealtimeConnectionState

from actron_neo_actor.capabilities import build_capabilities
from actron_neo_actor.config import Settings
from actron_neo_actor.device import ActronDevice, DeviceError, DeviceState, RestoreResult
from actron_neo_actor.models import ActionSpec
from actron_neo_actor.unit_checks import restore_message
from tests.fakes import SERIAL, START, FakeActronApi, FakeClock, raw_state

SETTINGS = Settings(core_api_url="http://core", actor_key="k", poll_fallback_seconds=30, reconcile_seconds=300)


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def make(
    api: FakeActronApi | None = None, clock: FakeClock | None = None, **kwargs: Any
) -> tuple[ActronDevice, FakeActronApi, FakeClock]:
    api = api or FakeActronApi()
    clock = clock or FakeClock()
    device = ActronDevice(api, SERIAL, SETTINGS, clock=clock.now, sleep=clock.sleep, **kwargs)  # type: ignore[arg-type]
    return device, api, clock


def status_from(raw: dict) -> ActronAirStatus:
    return ActronAirStatus.model_validate({"isOnline": True, "lastKnownState": raw})


# State mapping


def test_state_uses_master_info_for_temperatures():
    device, api, _ = make()
    assert api.device["AirconSystem"]["OutdoorUnit"]["AmbTemp"] == 0.0
    state = run(device.refresh())
    assert state.outdoor_temp_c == 18.6
    assert state.indoor_temp_c == 24.1
    assert state.humidity_pct == 55.0


def test_state_maps_settings_and_strips_zone_titles():
    device, _, clock = make()
    state = run(device.refresh())
    assert state.is_on is True
    assert state.mode == "COOL"
    assert state.fan_mode == "AUTO+CONT"
    assert state.cool_setpoint_c == 23.0
    assert state.heat_setpoint_c == 21.0
    assert state.enabled_zones == {"Family": True, "Master": True, "Kids": False}
    assert state.zone_temps_c == {"Family": 24.0, "Master": 23.5, "Kids": 22.0}
    assert state.updated_at == clock.now()
    assert device.state() == state


def test_missing_master_info_gives_none_readings():
    raw = raw_state()
    del raw["MasterInfo"]
    device, _, _ = make(FakeActronApi(raw))
    state = run(device.refresh())
    assert state.indoor_temp_c is None
    assert state.outdoor_temp_c is None
    assert "indoor_temp_c" not in state.readings()


def test_payload_has_only_whitelisted_fields(caplog):
    caplog.set_level(logging.DEBUG)
    pin, email = "739164", "installer@example.com"
    raw = raw_state()
    raw["NV_SystemSettings"] = {"SystemName": "House", "WallControllerPIN": pin}
    raw["Installer"] = {"Name": "Bob Installs", "Email": email, "Phone": "0400 000 000"}
    device, api, _ = make(FakeActronApi(raw))
    state = run(device.refresh())
    run(device.apply("system_off", {}))
    payload = state.to_payload()
    assert set(payload) == {
        "is_on", "mode", "fan_mode", "cool_setpoint_c", "heat_setpoint_c", "enabled_zones",
        "indoor_temp_c", "outdoor_temp_c", "humidity_pct", "zone_temps_c", "updated_at",
        "setpoint_min_c", "setpoint_max_c", "zone_setpoints_c", "zone_ranges_c",
    }  # fmt: skip
    for text in (json.dumps(payload), repr(state), caplog.text, json.dumps(state.readings())):
        assert pin not in text
        assert email not in text
        assert "Bob Installs" not in text


def test_payload_is_json_safe_with_iso_utc_time():
    device, _, _ = make()
    payload = run(device.refresh()).to_payload()
    assert payload["updated_at"] == START.isoformat()
    assert json.loads(json.dumps(payload)) == payload


def test_readings_mapping():
    device, _, _ = make()
    readings = run(device.refresh()).readings()
    assert readings == {
        "indoor_temp_c": 24.1,
        "outdoor_temp_c": 18.6,
        "humidity_pct": 55.0,
        "is_on": 1.0,
        "zone_temp_c@Family": 24.0,
        "zone_temp_c@Master": 23.5,
        "zone_temp_c@Kids": 22.0,
    }


def test_state_equality_ignores_updated_at():
    device, _, clock = make()
    first = run(device.refresh())
    clock.current += datetime.timedelta(minutes=1)
    second = run(device.refresh())
    assert first == second
    assert second.updated_at > first.updated_at


# Capabilities


def test_capabilities_declare_zone_options_from_titles():
    caps = build_capabilities(status_from(raw_state()))
    zones_param = next(
        p for a in caps["actions"] if a["id"] == "set_zones" for p in a["params"] if p["name"] == "zones"
    )
    assert zones_param["type"] == "multi_choice"
    assert [o["value"] for o in zones_param["options"]] == ["Family", "Master", "Kids"]
    zone_reading = next(r for r in caps["readings"] if r["id"] == "zone_temp_c")
    assert [o["value"] for o in zone_reading["target_options"]] == ["Family", "Master", "Kids"]


def test_capabilities_modes_come_from_mode_support():
    caps = build_capabilities(status_from(raw_state()))
    for action_id in ("system_on", "set_mode"):
        action = next(a for a in caps["actions"] if a["id"] == action_id)
        assert [o["value"] for o in action["params"][0]["options"]] == ["COOL", "HEAT", "FAN", "AUTO"]


def test_capabilities_declare_the_agreed_actions_and_readings():
    caps = build_capabilities(status_from(raw_state()))
    assert caps["version"] == 1
    actions = {a["id"]: a for a in caps["actions"]}
    assert set(actions) == {
        "system_off", "system_on", "set_mode", "set_temperature", "set_fan_mode", "set_zones", "set_zone_temperature",
    }  # fmt: skip
    assert all(actions[a]["restorable"] for a in actions)
    temp = actions["set_temperature"]["params"][0]
    assert (temp["name"], temp["type"], temp["min"], temp["max"], temp["unit"]) == (
        "temperature_c",
        "number",
        16.0,  # the library's default limits when the unit sends no NV_Limits
        30.0,
        "C",
    )
    assert [p["name"] for p in actions["set_zones"]["params"]] == ["zones", "enabled"]
    readings = {r["id"]: r for r in caps["readings"]}
    assert set(readings) == {"indoor_temp_c", "outdoor_temp_c", "humidity_pct", "is_on", "zone_temp_c"}
    assert readings["humidity_pct"]["unit"] == "%"


# The shape rule of core-api/core_api/actor_schemas.py (Capabilities, ActionDecl,
# ParamDecl, ReadingDecl, Option). All models forbid extra keys.
_NUMBER = (int, float)
OPTION_SHAPE = ({"value": str, "label": str}, {"value", "label"})
PARAM_SHAPE = (
    {"name": str, "label": str, "type": str, "min": _NUMBER, "max": _NUMBER, "unit": str, "options": list},
    {"name", "label", "type"},
)
ACTION_SHAPE = ({"id": str, "label": str, "restorable": bool, "params": list}, {"id", "label"})
READING_SHAPE = ({"id": str, "label": str, "unit": str, "type": str, "target_options": list}, {"id", "label"})
CAPABILITIES_SHAPE = ({"version": int, "actions": list, "readings": list}, {"version"})
NULLABLE = {"min", "max", "unit", "target_options"}


def _check_shape(obj: dict, shape: tuple[dict, set]) -> None:
    fields, required = shape
    assert set(obj) <= set(fields), f"extra keys {set(obj) - set(fields)}"
    assert required <= set(obj), f"missing keys {required - set(obj)}"
    for key, value in obj.items():
        if value is None and key in NULLABLE:
            continue
        assert isinstance(value, fields[key]), f"{key} has type {type(value).__name__}"


def test_capabilities_match_core_api_schema():
    caps = build_capabilities(status_from(raw_state()))
    json.dumps(caps)
    _check_shape(caps, CAPABILITIES_SHAPE)
    assert caps["version"] == 1
    for action in caps["actions"]:
        _check_shape(action, ACTION_SHAPE)
        for param in action.get("params", []):
            _check_shape(param, PARAM_SHAPE)
            assert param["type"] in {"number", "choice", "bool", "multi_choice"}
            for option in param.get("options", []):
                _check_shape(option, OPTION_SHAPE)
    for reading in caps["readings"]:
        _check_shape(reading, READING_SHAPE)
        for option in reading.get("target_options") or []:
            _check_shape(option, OPTION_SHAPE)


def test_device_capabilities_use_the_synced_status():
    device, _, _ = make()
    run(device.refresh())
    assert device.capabilities() == build_capabilities(status_from(raw_state()))


# Apply


def test_system_off_sends_set_system_mode_off_and_confirms():
    device, api, clock = make()
    run(device.refresh())
    run(device.apply("system_off", {}))
    assert api.commands == [
        {"type": "set-settings", "UserAirconSettings.isOn": False, "UserAirconSettings.Mode": "COOL"}
    ]
    assert device.state().is_on is False
    assert clock.elapsed() < 45


def test_apply_raises_when_state_never_changes():
    device, api, clock = make()
    run(device.refresh())
    api.respond_to_commands = False
    with pytest.raises(DeviceError, match="not confirmed"):
        run(device.apply("system_off", {}))
    assert 45 <= clock.elapsed() <= 50


def test_apply_confirms_a_change_that_arrives_late_in_the_window():
    device, api, clock = make()
    run(device.refresh())
    api.respond_to_commands = False

    async def scenario() -> None:
        async def unit_catches_up() -> None:
            while clock.elapsed() < 40:
                await asyncio.sleep(0)
            api.owner_sets("UserAirconSettings.isOn", False)

        catch_up = asyncio.create_task(unit_catches_up())
        await device.apply("system_off", {})
        await catch_up

    run(scenario())
    assert 40 <= clock.elapsed() < 45


def test_apply_polls_while_push_is_not_running():
    device, api, _ = make()
    run(device.refresh())
    before = api.polls
    run(device.apply("system_off", {}))
    assert api.polls > before


def test_apply_wraps_library_errors_without_details():
    device, api, _ = make()
    run(device.refresh())
    api.command_error = ActronAirAPIError("API request failed. Response: secret-ish body")
    with pytest.raises(DeviceError) as excinfo:
        run(device.apply("system_off", {}))
    assert "secret-ish" not in str(excinfo.value)
    assert "ActronAirAPIError" in str(excinfo.value)


def test_apply_system_on_and_set_mode_send_the_mode():
    device, api, _ = make(FakeActronApi(raw_state(is_on=False)))
    run(device.refresh())
    run(device.apply("system_on", {"mode": "HEAT"}))
    assert api.commands[-1]["UserAirconSettings.isOn"] is True
    assert api.commands[-1]["UserAirconSettings.Mode"] == "HEAT"
    run(device.apply("set_mode", {"mode": "FAN"}))
    assert device.state().mode == "FAN"


def test_apply_set_temperature_and_fan_mode():
    device, api, _ = make()
    run(device.refresh())
    run(device.apply("set_temperature", {"temperature_c": 25.5}))
    assert api.commands[-1]["UserAirconSettings.TemperatureSetpoint_Cool_oC"] == 25.5
    run(device.apply("set_fan_mode", {"fan_mode": "HIGH"}))
    assert device.state().fan_mode == "HIGH+CONT"


def test_apply_set_zones_only_commands_zones_that_differ():
    device, api, _ = make()
    run(device.refresh())
    run(device.apply("set_zones", {"zones": ["Family", "Kids"], "enabled": False}))
    assert len(api.commands) == 1
    assert api.commands[0]["UserAirconSettings.EnabledZones"] == [False, True, False]
    assert device.state().enabled_zones == {"Family": False, "Master": True, "Kids": False}


def test_apply_unknown_zone_raises():
    device, api, _ = make()
    run(device.refresh())
    with pytest.raises(DeviceError, match="unknown zone"):
        run(device.apply("set_zones", {"zones": ["Garage"], "enabled": False}))
    assert api.commands == []


def test_apply_unknown_action_raises():
    device, _, _ = make()
    run(device.refresh())
    with pytest.raises(DeviceError, match="unknown action"):
        run(device.apply("make_coffee", {}))


# matches_action


def test_matches_action():
    device, _, _ = make()
    state = run(device.refresh())
    assert device.matches_action("system_on", {"mode": "COOL"}, state)
    assert not device.matches_action("system_on", {"mode": "HEAT"}, state)
    assert not device.matches_action("system_off", {}, state)
    assert device.matches_action("set_mode", {"mode": "COOL"}, state)
    assert device.matches_action("set_temperature", {"temperature_c": 23.04}, state)
    assert not device.matches_action("set_temperature", {"temperature_c": 23.1}, state)
    assert device.matches_action("set_fan_mode", {"fan_mode": "AUTO"}, state)
    assert device.matches_action("set_zones", {"zones": ["Family", "Master"], "enabled": True}, state)
    assert not device.matches_action("set_zones", {"zones": ["Family", "Kids"], "enabled": True}, state)


# Snapshots and restore


def test_snapshot_saves_only_what_the_action_changes():
    device, _, _ = make()
    run(device.refresh())
    assert device.snapshot_for("system_off", {})["saved"] == {"is_on": True, "mode": "COOL"}
    assert device.snapshot_for("set_mode", {"mode": "HEAT"})["saved"] == {"is_on": True, "mode": "COOL"}
    assert device.snapshot_for("set_temperature", {"temperature_c": 25})["saved"] == {
        "cool_setpoint_c": 23.0,
        "heat_setpoint_c": 21.0,
    }
    assert device.snapshot_for("set_fan_mode", {"fan_mode": "LOW"})["saved"] == {"fan_mode": "AUTO+CONT"}


def test_zone_snapshot_only_changed_zones():
    device, _, _ = make()
    run(device.refresh())
    snapshot = device.snapshot_for("set_zones", {"zones": ["Family", "Kids"], "enabled": False})
    assert snapshot["saved"] == {"zones": {"Family": True}}
    assert snapshot["applied"] == {"zones": {"Family": False}}
    json.dumps(snapshot)


def _act(device: ActronDevice, action_id: str, params: dict) -> dict:
    snapshot = device.snapshot_for(action_id, params)
    run(device.apply(action_id, params))
    return snapshot


def test_restore_sends_saved_mode_when_was_on():
    device, api, _ = make(FakeActronApi(raw_state(is_on=True, mode="HEAT")))
    run(device.refresh())
    snapshot = _act(device, "system_off", {})
    result = run(device.restore(snapshot, device.state()))
    assert api.commands[-1]["UserAirconSettings.isOn"] is True
    assert api.commands[-1]["UserAirconSettings.Mode"] == "HEAT"
    assert result == RestoreResult(restored=["power"], skipped=[])
    assert device.state().is_on is True


def test_restore_sends_off_when_was_off():
    device, api, _ = make(FakeActronApi(raw_state(is_on=False)))
    run(device.refresh())
    snapshot = _act(device, "system_on", {"mode": "COOL"})
    result = run(device.restore(snapshot, device.state()))
    assert api.commands[-1]["UserAirconSettings.isOn"] is False
    assert result.restored == ["power"]
    assert device.state().is_on is False


def test_restore_skips_field_owner_changed():
    device, api, _ = make()
    run(device.refresh())
    snapshot = _act(device, "system_off", {})
    api.owner_sets("UserAirconSettings.isOn", True)
    current = run(device.refresh())
    sent = len(api.commands)
    result = run(device.restore(snapshot, current))
    assert result == RestoreResult(restored=[], skipped=["power"])
    assert len(api.commands) == sent


def test_restore_zones_leaves_owner_changed_zone_alone():
    device, api, _ = make()
    run(device.refresh())
    snapshot = _act(device, "set_zones", {"zones": ["Family", "Master"], "enabled": False})
    api.owner_sets("UserAirconSettings.EnabledZones", [True, False, False])
    current = run(device.refresh())
    result = run(device.restore(snapshot, current))
    assert result == RestoreResult(restored=["zone:Master"], skipped=["zone:Family"])
    assert device.state().enabled_zones == {"Family": True, "Master": True, "Kids": False}


def test_restore_temperature_and_fan_mode():
    device, _, _ = make()
    run(device.refresh())
    temp_snapshot = _act(device, "set_temperature", {"temperature_c": 26})
    assert run(device.restore(temp_snapshot, device.state())).restored == ["setpoint"]
    assert device.state().cool_setpoint_c == 23.0
    fan_snapshot = _act(device, "set_fan_mode", {"fan_mode": "LOW"})
    assert run(device.restore(fan_snapshot, device.state())).restored == ["fan_mode"]
    assert device.state().fan_mode == "AUTO+CONT"


def test_restore_raises_when_not_confirmed():
    device, api, clock = make()
    run(device.refresh())
    snapshot = _act(device, "system_off", {})
    api.respond_to_commands = False
    start = clock.elapsed()
    with pytest.raises(DeviceError, match="not confirmed"):
        run(device.restore(snapshot, device.state()))
    assert 45 <= clock.elapsed() - start <= 50


def test_restore_rejects_a_malformed_snapshot():
    device, _, _ = make()
    state = run(device.refresh())
    with pytest.raises(DeviceError, match="snapshot"):
        run(device.restore({"action": "system_off"}, state))


# Sync


def _run_sync(device: ActronDevice, clock: FakeClock, before_stop: Any = None) -> None:
    async def scenario() -> None:
        await device.start_sync()
        await clock.exhausted.wait()
        if before_stop is not None:
            await before_stop()
        await device.stop_sync()

    run(scenario())


def test_push_failure_falls_back_to_polling():
    api = FakeActronApi()
    api.push_default = False
    device, _, clock = make(api, FakeClock(budget_seconds=150))
    _run_sync(device, clock)
    # One poll at start, then one per 30 s fallback tick.
    assert api.polls == 1 + 5
    assert api.start_push_calls[0] == [SERIAL]


def test_push_retry_backs_off_up_to_300_seconds():
    api = FakeActronApi()
    api.push_default = False
    device, _, clock = make(api, FakeClock(budget_seconds=3000))
    attempts: list[float] = []
    original = api.start_push

    async def recording_start_push(*args: Any, **kwargs: Any) -> bool:
        attempts.append(clock.elapsed())
        return await original(*args, **kwargs)

    api.start_push = recording_start_push
    _run_sync(device, clock)
    gaps = [b - a for a, b in zip(attempts, attempts[1:], strict=False)]
    assert gaps[:4] == [30, 60, 120, 240]
    assert set(gaps[4:]) == {300}


def test_reconcile_poll_runs_while_push_works():
    api = FakeActronApi()
    device, _, clock = make(api, FakeClock(budget_seconds=900))
    _run_sync(device, clock)
    assert len(api.start_push_calls) == 1
    # The start poll, then one reconcile poll every 300 s.
    assert api.polls == 1 + 3


def test_push_update_refreshes_state_and_calls_on_change():
    changes: list[DeviceState] = []
    api = FakeActronApi()
    device, _, clock = make(api, FakeClock(budget_seconds=10), on_change=changes.append)

    async def owner_switches_off() -> None:
        api.owner_sets("UserAirconSettings.isOn", False)
        await api.push()
        await api.push()

    _run_sync(device, clock, owner_switches_off)
    assert [c.is_on for c in changes] == [True, False]
    assert device.state().is_on is False


def test_connection_drop_falls_back_to_polling_and_restarts_push():
    api = FakeActronApi()
    device, _, clock = make(api, FakeClock(budget_seconds=40))

    async def scenario() -> None:
        await device.start_sync()
        await api.emit_connection(RealtimeConnectionState.DISCONNECTED)
        await clock.exhausted.wait()
        await device.stop_sync()

    run(scenario())
    # The 30 s tick polls instead of waiting for the reconcile, and push is stopped and retried.
    assert api.polls == 2
    assert api.stop_push_calls >= 1
    assert len(api.start_push_calls) == 2


def test_stop_sync_unsubscribes_and_stops_push():
    api = FakeActronApi()
    device, _, clock = make(api, FakeClock(budget_seconds=10))
    _run_sync(device, clock)
    assert api.subscriber_count == 0
    assert api.stop_push_calls == 1


def test_start_sync_raises_when_the_first_poll_fails():
    api = FakeActronApi()
    api.poll_error = ActronAirAPIError("down")
    device, _, _ = make(api)
    with pytest.raises(DeviceError):
        run(device.start_sync())
    assert api.subscriber_count == 0


def test_poll_errors_in_the_loop_do_not_stop_sync():
    api = FakeActronApi()
    api.push_default = False
    device, _, clock = make(api, FakeClock(budget_seconds=100))

    async def scenario() -> None:
        await device.start_sync()
        api.poll_error = ActronAirAPIError("down")
        await clock.exhausted.wait()
        await device.stop_sync()

    run(scenario())
    assert api.polls == 1 + 3


def test_apply_confirms_by_push_without_polling():
    api = FakeActronApi()
    api.push_on_command = True
    device, _, clock = make(api, FakeClock(budget_seconds=10))

    async def scenario() -> None:
        await device.start_sync()
        polls = api.polls
        await device.apply("system_off", {})
        assert api.polls == polls
        await device.stop_sync()

    run(scenario())
    assert device.state().is_on is False


def test_apply_polls_when_push_is_up_but_silent():
    api = FakeActronApi()
    device, _, clock = make(api, FakeClock(budget_seconds=10))

    async def scenario() -> None:
        await device.start_sync()
        await clock.exhausted.wait()  # the sync loop is parked; lift the budget for the confirm steps
        clock.budget_seconds = None
        polls = api.polls
        await device.apply("system_off", {})
        assert api.polls == polls + 1
        await device.stop_sync()

    run(scenario())
    assert 10 <= clock.elapsed() < 15


# Setpoint limits (fix round 1)

LIMITS = {"setCool_Min": 18, "setCool_Max": 26, "setHeat_Min": 12, "setHeat_Max": 24}


def test_state_carries_setpoint_limits_for_the_current_mode():
    device, _, _ = make(FakeActronApi(raw_state(limits=LIMITS)))
    state = run(device.refresh())
    assert (state.setpoint_min_c, state.setpoint_max_c) == (18.0, 26.0)
    assert (state.to_payload()["setpoint_min_c"], state.to_payload()["setpoint_max_c"]) == (18.0, 26.0)
    heat = run(make(FakeActronApi(raw_state(mode="HEAT", limits=LIMITS)))[0].refresh())
    assert (heat.setpoint_min_c, heat.setpoint_max_c) == (12.0, 24.0)


def test_out_of_range_temperature_is_clamped_sent_and_confirmed():
    device, api, clock = make(FakeActronApi(raw_state(limits=LIMITS)))
    run(device.refresh())
    snapshot = device.snapshot_for("set_temperature", {"temperature_c": 30})
    assert snapshot["applied"] == {"setpoint_c": 26.0}
    run(device.apply("set_temperature", {"temperature_c": 30}))
    assert api.commands[-1]["UserAirconSettings.TemperatureSetpoint_Cool_oC"] == 26.0
    assert device.state().cool_setpoint_c == 26.0
    assert device.matches_action("set_temperature", {"temperature_c": 30}, device.state())
    assert clock.elapsed() < 45


def test_heat_mode_clamps_to_heat_limits():
    device, api, _ = make(FakeActronApi(raw_state(mode="HEAT", limits=LIMITS)))
    run(device.refresh())
    run(device.apply("set_temperature", {"temperature_c": 10}))
    assert api.commands[-1]["UserAirconSettings.TemperatureSetpoint_Heat_oC"] == 12.0


def test_auto_mode_is_not_clamped_like_the_library():
    device, api, _ = make(FakeActronApi(raw_state(mode="AUTO", cool_c=23.0, heat_c=21.0, limits=LIMITS)))
    run(device.refresh())
    assert device.snapshot_for("set_temperature", {"temperature_c": 28})["applied"] == {"setpoint_c": 28.0}
    run(device.apply("set_temperature", {"temperature_c": 28}))
    assert api.commands[-1]["UserAirconSettings.TemperatureSetpoint_Cool_oC"] == 28.0
    assert api.commands[-1]["UserAirconSettings.TemperatureSetpoint_Heat_oC"] == 26.0


def test_capability_temperature_range_comes_from_limits():
    caps = build_capabilities(status_from(raw_state(limits=LIMITS)))
    temp = next(a for a in caps["actions"] if a["id"] == "set_temperature")["params"][0]
    assert (temp["min"], temp["max"]) == (18.0, 26.0)


# Owner changes the mode while the actor holds the unit off (ruling 20)


def test_restore_after_system_off_keeps_the_mode_the_owner_chose():
    device, api, _ = make(FakeActronApi(raw_state(is_on=True, mode="COOL")))
    run(device.refresh())
    snapshot = _act(device, "system_off", {})
    api.owner_sets("UserAirconSettings.Mode", "HEAT")
    current = run(device.refresh())
    result = run(device.restore(snapshot, current))
    assert result == RestoreResult(restored=["power"], skipped=["mode"])
    assert api.commands[-1]["UserAirconSettings.isOn"] is True
    assert api.commands[-1]["UserAirconSettings.Mode"] == "HEAT"
    assert (device.state().is_on, device.state().mode) == (True, "HEAT")


def test_restore_after_system_off_skips_mode_and_power_when_owner_changed_both():
    device, api, _ = make()
    run(device.refresh())
    snapshot = _act(device, "system_off", {})
    api.owner_sets("UserAirconSettings.Mode", "FAN")
    api.owner_sets("UserAirconSettings.isOn", True)
    current = run(device.refresh())
    sent = len(api.commands)
    result = run(device.restore(snapshot, current))
    assert result == RestoreResult(restored=[], skipped=["mode", "power"])
    assert len(api.commands) == sent


# Confirmation needs a state seen after the command was sent


def test_apply_does_not_confirm_on_a_stale_state_that_matches():
    device, api, clock = make(FakeActronApi(raw_state(is_on=False)))
    run(device.refresh())  # the cached state says off
    api.owner_sets("UserAirconSettings.isOn", True)  # the unit is really on
    api.respond_to_commands = False
    with pytest.raises(DeviceError, match="not confirmed"):
        run(device.apply("system_off", {}))
    assert device.state().is_on is True


def test_restore_does_not_confirm_on_a_stale_state_that_matches():
    device, api, _ = make()
    cached = run(device.refresh())  # the cached state says on
    snapshot = device.snapshot_for("system_off", {})
    api.owner_sets("UserAirconSettings.isOn", False)  # the unit is really off
    api.respond_to_commands = False
    off = dataclasses.replace(cached, is_on=False)
    with pytest.raises(DeviceError, match="not confirmed"):
        run(device.restore(snapshot, off))


def test_apply_with_nothing_to_send_confirms_at_once():
    device, api, clock = make()
    run(device.refresh())
    api.poll_error = ActronAirAPIError("down")
    run(device.apply("set_zones", {"zones": ["Kids"], "enabled": False}))
    assert api.commands == []
    assert clock.elapsed() == 0


def test_change_handler_failure_logs_the_type_name_only(caplog):
    def handler(_state: DeviceState) -> None:
        raise RuntimeError("response body with a secret")

    device, _, _ = make(on_change=handler)
    caplog.set_level(logging.DEBUG)
    run(device.refresh())
    assert "device change handler failed: RuntimeError" in caplog.text
    assert "secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_zone_without_a_reported_temperature_has_no_reading():
    raw = raw_state()
    del raw["RemoteZoneInfo"][2]["LiveTemp_oC"]
    device, _, _ = make(FakeActronApi(raw))
    state = run(device.refresh())
    assert state.zone_temps_c == {"Family": 24.0, "Master": 23.5}
    assert "zone_temp_c@Kids" not in state.readings()
    assert state.enabled_zones["Kids"] is False


# Zone setpoints (set_zone_temperature)

STUDY_ZONES: list[tuple[str, bool, float]] = [("Study", True, 21.0), ("Lounge", True, 23.0)]


def study_unit(mode: str = "COOL", cool_c: float = 22.0, variance_c: float = 2.0) -> FakeActronApi:
    """Main setpoint 22.0 with a zone variance of 2.0, so a zone may be set from 20.0 to 24.0."""
    raw = raw_state(mode=mode, cool_c=cool_c, heat_c=cool_c, zones=STUDY_ZONES)
    raw["UserAirconSettings"]["ZoneTemperatureSetpointVariance_oC"] = variance_c
    return FakeActronApi(raw)


def test_state_carries_zone_setpoints_and_ranges():
    device, _, _ = make(study_unit())
    state = run(device.refresh())
    assert state.zone_setpoints_c == {"Study": 22.0, "Lounge": 22.0}
    assert state.zone_ranges_c == {"Study": (20.0, 24.0), "Lounge": (20.0, 24.0)}
    payload = json.loads(json.dumps(state.to_payload()))
    assert payload["zone_setpoints_c"] == {"Study": 22.0, "Lounge": 22.0}
    assert payload["zone_ranges_c"] == {"Study": [20.0, 24.0], "Lounge": [20.0, 24.0]}


def test_zone_temperature_inside_range_sends_command():
    device, api, clock = make(study_unit())
    run(device.refresh())
    run(device.apply("set_zone_temperature", {"zone": "Study", "temperature_c": 23.5}))
    assert api.commands == [{"type": "set-settings", "RemoteZoneInfo[0].TemperatureSetpoint_Cool_oC": 23.5}]
    assert device.state().zone_setpoints_c["Study"] == 23.5
    assert clock.elapsed() < 45


def test_zone_temperature_at_min_and_max_is_accepted():
    device, api, _ = make(study_unit())
    run(device.refresh())
    run(device.apply("set_zone_temperature", {"zone": "Study", "temperature_c": 20.0}))
    run(device.apply("set_zone_temperature", {"zone": "Lounge", "temperature_c": 24.0}))
    assert api.commands == [
        {"type": "set-settings", "RemoteZoneInfo[0].TemperatureSetpoint_Cool_oC": 20.0},
        {"type": "set-settings", "RemoteZoneInfo[1].TemperatureSetpoint_Cool_oC": 24.0},
    ]
    assert device.state().zone_setpoints_c == {"Study": 20.0, "Lounge": 24.0}


def test_zone_temperature_range_check_allows_float_error_at_the_edge():
    device, api, _ = make(study_unit(cool_c=22.9, variance_c=1.2))  # 22.9 + 1.2 is 24.099999999999998
    run(device.refresh())
    run(device.apply("set_zone_temperature", {"zone": "Study", "temperature_c": 24.1}))
    assert len(api.commands) == 1


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (28.0, "Study 28.0°C is outside 20.0–24.0°C (the unit is set to 22.0°C)"),
        (24.1, "Study 24.1°C is outside 20.0–24.0°C (the unit is set to 22.0°C)"),
        (19.9, "Study 19.9°C is outside 20.0–24.0°C (the unit is set to 22.0°C)"),
    ],
)
def test_zone_temperature_just_outside_range_fails_and_sends_nothing(value, message):
    device, api, _ = make(study_unit())
    run(device.refresh())
    with pytest.raises(DeviceError) as excinfo:
        run(device.apply("set_zone_temperature", {"zone": "Study", "temperature_c": value}))
    assert str(excinfo.value) == message
    assert api.commands == []


@pytest.mark.parametrize("mode", ["FAN", "OFF"])
def test_zone_temperature_in_fan_and_off_mode_fails_with_library_text(mode):
    device, api, _ = make(study_unit(mode=mode))
    run(device.refresh())
    with pytest.raises(DeviceError) as excinfo:
        run(device.apply("set_zone_temperature", {"zone": "Study", "temperature_c": 22.0}))
    assert str(excinfo.value) == f"set_zone_temperature failed: Cannot set temperature in {mode} mode"
    assert api.commands == []


def test_unknown_zone_fails():
    device, api, _ = make(study_unit())
    run(device.refresh())
    with pytest.raises(DeviceError, match="unknown zone: Garage"):
        run(device.apply("set_zone_temperature", {"zone": "Garage", "temperature_c": 22.0}))
    with pytest.raises(DeviceError, match="unknown zone: Garage"):
        device.snapshot_for("set_zone_temperature", {"zone": "Garage", "temperature_c": 22.0})
    assert api.commands == []


def test_capabilities_declare_set_zone_temperature():
    caps = build_capabilities(status_from(raw_state(zones=STUDY_ZONES, limits=LIMITS)))
    ids = [a["id"] for a in caps["actions"]]
    assert ids.index("set_zone_temperature") == ids.index("set_zones") + 1
    action = caps["actions"][ids.index("set_zone_temperature")]
    assert (action["label"], action["restorable"]) == ("Set zone temperature", True)
    zone, temp = action["params"]
    assert (zone["name"], zone["type"]) == ("zone", "choice")
    assert [o["value"] for o in zone["options"]] == ["Study", "Lounge"]
    assert (temp["name"], temp["type"], temp["min"], temp["max"], temp["unit"]) == (
        "temperature_c",
        "number",
        18.0,
        26.0,
        "C",
    )


def test_zone_temperature_matches_action_and_snapshot():
    device, _, _ = make(study_unit())
    state = run(device.refresh())
    assert device.matches_action("set_zone_temperature", {"zone": "Study", "temperature_c": 22.04}, state)
    assert not device.matches_action("set_zone_temperature", {"zone": "Study", "temperature_c": 22.1}, state)
    assert not device.matches_action("set_zone_temperature", {"zone": "Garage", "temperature_c": 22.0}, state)
    snapshot = device.snapshot_for("set_zone_temperature", {"zone": "Study", "temperature_c": 23})
    assert snapshot == {
        "action": "set_zone_temperature",
        "applied": {"zone": "Study", "setpoint_c": 23.0},
        "saved": {"zone": "Study", "cool_setpoint_c": 22.0, "heat_setpoint_c": 0.0},
    }
    json.dumps(snapshot)


def test_restore_zone_temperature_puts_the_saved_setpoint_back():
    device, api, _ = make(study_unit())
    run(device.refresh())
    snapshot = _act(device, "set_zone_temperature", {"zone": "Study", "temperature_c": 23.5})
    result = run(device.restore(snapshot, device.state()))
    assert result == RestoreResult(restored=["zone_temp:Study"], skipped=[])
    assert api.commands[-1] == {"type": "set-settings", "RemoteZoneInfo[0].TemperatureSetpoint_Cool_oC": 22.0}
    assert device.state().zone_setpoints_c["Study"] == 22.0


def test_restore_zone_temperature_uses_the_heat_setpoint_in_heat_mode():
    api = study_unit(mode="HEAT")
    api.device["RemoteZoneInfo"][0]["TemperatureSetpoint_Heat_oC"] = 21.0
    device, _, _ = make(api)
    run(device.refresh())
    snapshot = _act(device, "set_zone_temperature", {"zone": "Study", "temperature_c": 23.0})
    assert snapshot["saved"] == {"zone": "Study", "cool_setpoint_c": 22.0, "heat_setpoint_c": 21.0}
    run(device.restore(snapshot, device.state()))
    assert api.commands[-1] == {"type": "set-settings", "RemoteZoneInfo[0].TemperatureSetpoint_Heat_oC": 21.0}


def test_restore_zone_temperature_skips_a_zone_the_owner_changed():
    device, api, _ = make(study_unit())
    run(device.refresh())
    snapshot = _act(device, "set_zone_temperature", {"zone": "Study", "temperature_c": 23.5})
    api.device["RemoteZoneInfo"][0]["TemperatureSetpoint_Cool_oC"] = 21.0  # the owner, at the wall controller
    current = run(device.refresh())
    sent = len(api.commands)
    result = run(device.restore(snapshot, current))
    assert result == RestoreResult(restored=[], skipped=["zone_temp:Study"])
    assert len(api.commands) == sent


def test_restore_zone_temperature_outside_the_range_fails_with_the_range_message():
    device, api, _ = make(study_unit())
    run(device.refresh())
    snapshot = _act(device, "set_zone_temperature", {"zone": "Study", "temperature_c": 24.0})
    api.owner_sets("UserAirconSettings.TemperatureSetpoint_Cool_oC", 26.0)  # the range is now 24.0 to 28.0
    current = run(device.refresh())
    sent = len(api.commands)
    with pytest.raises(DeviceError) as excinfo:
        run(device.restore(snapshot, current))
    assert str(excinfo.value) == "Study 22.0°C is outside 24.0–28.0°C (the unit is set to 26.0°C)"
    assert len(api.commands) == sent


def test_restore_message_words_a_zone_temperature_field():
    result = RestoreResult(restored=["zone_temp:Study"], skipped=["zone:Lounge"])
    assert restore_message(result) == (
        "restored zone Study temperature; left alone because the owner changed them: zone Lounge"
    )


# Several actions: one snapshot, one restore


def _act_all(device: ActronDevice, actions: list[ActionSpec]) -> dict:
    snapshot = device.snapshot_for_all(actions)
    for action in actions:
        run(device.apply(action.id, action.params))
    return snapshot


def test_two_actions_on_one_field_restore_the_value_from_before_the_run():
    device, _, _ = make()
    run(device.refresh())
    temps = [ActionSpec("set_temperature", {"temperature_c": 25}), ActionSpec("set_temperature", {"temperature_c": 26})]
    snapshot = _act_all(device, temps)
    # The unit shows 26, the value the run applied last, so the owner has not changed it.
    result = run(device.restore(snapshot, device.state()))
    assert result == RestoreResult(restored=["setpoint"], skipped=[])
    assert device.state().cool_setpoint_c == 23.0


def test_restore_puts_main_setpoint_back_before_zone_setpoints():
    api = study_unit()
    api.device["RemoteZoneInfo"][0]["TemperatureSetpoint_Cool_oC"] = 21.0
    device, _, _ = make(api)
    run(device.refresh())
    actions = [
        ActionSpec("set_zone_temperature", {"zone": "Study", "temperature_c": 24.0}),
        ActionSpec("set_temperature", {"temperature_c": 26}),
    ]
    snapshot = _act_all(device, actions)
    sent = len(api.commands)
    # Zone 21.0 is outside 24.0–28.0 until the main setpoint is back at 22.0.
    result = run(device.restore(snapshot, device.state()))
    assert result == RestoreResult(restored=["setpoint", "zone_temp:Study"], skipped=[])
    assert api.commands[sent:] == [
        {"type": "set-settings", "UserAirconSettings.TemperatureSetpoint_Cool_oC": 22.0},
        {"type": "set-settings", "RemoteZoneInfo[0].TemperatureSetpoint_Cool_oC": 21.0},
    ]
    assert device.state().zone_setpoints_c["Study"] == 21.0


def test_restore_leaves_a_field_the_owner_changed():
    device, api, _ = make()
    run(device.refresh())
    actions = [ActionSpec("set_temperature", {"temperature_c": 26}), ActionSpec("set_fan_mode", {"fan_mode": "LOW"})]
    snapshot = _act_all(device, actions)
    api.owner_sets("UserAirconSettings.FanMode", "HIGH+CONT")
    current = run(device.refresh())
    result = run(device.restore(snapshot, current))
    assert result == RestoreResult(restored=["setpoint"], skipped=["fan_mode"])
    assert device.state().cool_setpoint_c == 23.0
    assert device.state().fan_mode == "HIGH+CONT"


def test_restore_puts_the_setpoint_back_for_the_mode_the_power_restore_leaves():
    device, _, _ = make()
    run(device.refresh())
    actions = [ActionSpec("set_mode", {"mode": "HEAT"}), ActionSpec("set_temperature", {"temperature_c": 25})]
    snapshot = _act_all(device, actions)
    result = run(device.restore(snapshot, device.state()))
    assert result == RestoreResult(restored=["power", "setpoint"], skipped=[])
    # Back in COOL, the cool setpoint is the one in use; the saved heat value must not land on it.
    state = device.state()
    assert (state.mode, state.cool_setpoint_c) == ("COOL", 23.0)


def _setpoint_then_zone(main_saved: float, zone_saved: float) -> dict:
    """A list snapshot whose restore sends the main setpoint, then the Study setpoint, in one batch."""
    return {
        "actions": [
            {
                "action": "set_temperature",
                "applied": {"setpoint_c": 26.0},
                "saved": {"cool_setpoint_c": main_saved, "heat_setpoint_c": main_saved},
            },
            {
                "action": "set_zone_temperature",
                "applied": {"zone": "Study", "setpoint_c": 27.0},
                "saved": {"zone": "Study", "cool_setpoint_c": zone_saved, "heat_setpoint_c": zone_saved},
            },
        ]
    }


def _study_at_26_and_27() -> tuple[ActronDevice, FakeActronApi]:
    api = study_unit(cool_c=26.0)  # zone range 24.0 to 28.0
    api.device["RemoteZoneInfo"][0]["TemperatureSetpoint_Cool_oC"] = 27.0
    device, _, _ = make(api)
    run(device.refresh())
    return device, api


def test_zone_range_is_read_after_an_earlier_command_in_the_same_batch_moves_it():
    device, api = _study_at_26_and_27()
    # 29.0 is outside the cached 24.0–28.0, inside 27.0–30.0 once the main setpoint is 29.0.
    run(device.restore(_setpoint_then_zone(29.0, 29.0), device.state()))
    assert api.commands == [
        {"type": "set-settings", "UserAirconSettings.TemperatureSetpoint_Cool_oC": 29.0},
        {"type": "set-settings", "RemoteZoneInfo[0].TemperatureSetpoint_Cool_oC": 29.0},
    ]
    assert device.state().zone_setpoints_c["Study"] == 29.0


def test_zone_range_after_an_earlier_command_refuses_a_value_the_cached_range_allowed():
    device, api = _study_at_26_and_27()
    # 25.0 is inside the cached 24.0–28.0, outside 20.0–24.0 once the main setpoint is 22.0.
    with pytest.raises(DeviceError) as excinfo:
        run(device.restore(_setpoint_then_zone(22.0, 25.0), device.state()))
    assert str(excinfo.value) == "Study 25.0°C is outside 20.0–24.0°C (the unit is set to 22.0°C)"
    assert api.commands == [{"type": "set-settings", "UserAirconSettings.TemperatureSetpoint_Cool_oC": 22.0}]
