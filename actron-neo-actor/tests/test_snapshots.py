import dataclasses

from actron_neo_api import ActronAirStatus

from actron_neo_actor.config import Settings
from actron_neo_actor.device import ActronDevice, DeviceState
from actron_neo_actor.device_snapshot import snapshot_for
from actron_neo_actor.device_state import state_from_status
from actron_neo_actor.models import ActionSpec
from actron_neo_actor.snapshots import restorable, snapshot_for_all, snapshot_matches
from actron_neo_actor.unit_checks import owner_changed
from tests.fakes import SERIAL, START, FakeActronApi, raw_state

SETTINGS = Settings(core_api_url="http://core", actor_key="k", poll_fallback_seconds=30, reconcile_seconds=300)


def state_of(**kwargs) -> DeviceState:
    status = ActronAirStatus.model_validate({"isOnline": True, "lastKnownState": raw_state(**kwargs)})
    return state_from_status(status, START)


def step(index: int, outcome: str) -> dict:
    return {"index": index, "action_id": "x", "params": {}, "outcome": outcome, "message": None, "attempts": 1}


TEMP_26 = ActionSpec("set_temperature", {"temperature_c": 26})
TEMP_20 = ActionSpec("set_temperature", {"temperature_c": 20})
FAN_LOW = ActionSpec("set_fan_mode", {"fan_mode": "LOW"})


def test_snapshot_lists_one_entry_per_action():
    before = state_of()
    snapshot = snapshot_for_all([TEMP_26, FAN_LOW, TEMP_20], before)
    assert snapshot == {
        "actions": [
            snapshot_for("set_temperature", {"temperature_c": 26}, before),
            snapshot_for("set_fan_mode", {"fan_mode": "LOW"}, before),
            snapshot_for("set_temperature", {"temperature_c": 20}, before),
        ]
    }
    # Every entry is read from the state before the run, so the second setpoint saves 23.0 too.
    assert snapshot["actions"][2]["saved"] == {"cool_setpoint_c": 23.0, "heat_setpoint_c": 21.0}


def test_restorable_drops_failed_steps():
    snapshot = snapshot_for_all([TEMP_26, FAN_LOW, TEMP_20], state_of())
    steps = [step(0, "failed"), step(1, "ok"), step(2, "ok")]
    assert restorable(snapshot, steps) == {"actions": snapshot["actions"][1:]}
    assert restorable(snapshot, [step(0, "failed"), step(1, "failed"), step(2, "failed")]) is None
    assert restorable(snapshot, None) == snapshot
    assert restorable(None, steps) is None


def test_restorable_passes_legacy_snapshot_through():
    legacy = snapshot_for("set_temperature", {"temperature_c": 26}, state_of())
    assert restorable(legacy, [step(0, "ok")]) == legacy
    assert restorable(legacy, None) == legacy


def test_owner_changed_needs_every_ok_action_gone():
    device = ActronDevice(FakeActronApi(), SERIAL, SETTINGS)  # type: ignore[arg-type]
    actions = [TEMP_26, FAN_LOW]
    snapshot = snapshot_for_all(actions, state_of())
    as_left = state_of(cool_c=26.0, fan_mode="LOW+CONT")
    one_back = state_of(cool_c=24.0, fan_mode="LOW+CONT")
    both_back = state_of(cool_c=24.0, fan_mode="HIGH+CONT")
    assert not owner_changed(device, actions, snapshot, as_left)
    assert not owner_changed(device, actions, snapshot, one_back)
    assert owner_changed(device, actions, snapshot, both_back)
    # The fan action failed, so only the setpoint counts.
    steps = [step(0, "ok"), step(1, "failed")]
    ok, kept = [TEMP_26], restorable(snapshot, steps)
    assert owner_changed(device, ok, kept, one_back)
    assert not owner_changed(device, ok, kept, state_of(cool_c=26.0, fan_mode="HIGH+CONT"))


def test_owner_changed_keeps_the_single_action_rules_for_a_legacy_snapshot():
    device = ActronDevice(FakeActronApi(), SERIAL, SETTINGS)  # type: ignore[arg-type]
    zones_off = ActionSpec("set_zones", {"zones": ["Family", "Master"], "enabled": False})
    legacy = snapshot_for(zones_off.id, zones_off.params, state_of())
    one_back = [("Family ", True, 24.0), ("Master", False, 23.5), ("Kids", False, 22.0)]
    both_back = [("Family ", True, 24.0), ("Master", True, 23.5), ("Kids", False, 22.0)]
    assert not owner_changed(device, [zones_off], legacy, state_of(zones=one_back))
    assert owner_changed(device, [zones_off], legacy, state_of(zones=both_back))
    assert owner_changed(device, [TEMP_26], None, state_of(cool_c=24.0))


def test_snapshot_matches_the_actions_it_was_taken_for():
    kids_on = ActionSpec("set_zones", {"zones": ["Kids", "Master"], "enabled": True})
    study = ActionSpec("set_zone_temperature", {"zones": ["Master"], "temperature_c": 22.0})
    actions = [FAN_LOW, kids_on, study, ActionSpec("system_on", {"mode": "HEAT"})]
    snapshot = snapshot_for_all(actions, state_of())
    assert snapshot_matches(snapshot, actions)
    assert snapshot_matches(snapshot, [FAN_LOW, kids_on, dataclasses.replace(study, params={**study.params,
                                                                                             "temperature_c": 23.0}),
                                       actions[3]])  # a new value for the same field
    assert not snapshot_matches(snapshot, actions[:3])
    assert not snapshot_matches(snapshot, [ActionSpec("set_mode", {"mode": "HEAT"}), *actions[1:]])
    assert not snapshot_matches(snapshot, [FAN_LOW, dataclasses.replace(kids_on, params={"zones": ["Master"],
                                                                                         "enabled": True}),
                                           *actions[2:]])
    assert not snapshot_matches(snapshot, [*actions[:2], ActionSpec("set_zone_temperature", {"zone": "Kids",
                                                                                            "temperature_c": 22.0}),
                                           actions[3]])
    assert not snapshot_matches(snapshot, [ActionSpec("set_fan_mode", {"fan_mode": "HIGH"}), *actions[1:]])
    assert not snapshot_matches(snapshot, [*actions[:3], ActionSpec("system_on", {"mode": "COOL"})])


def test_snapshot_matches_a_legacy_entry_and_refuses_a_malformed_one():
    legacy = snapshot_for("system_off", {}, state_of())
    assert snapshot_matches(legacy, [ActionSpec("system_off")])
    assert not snapshot_matches(legacy, [ActionSpec("system_off"), FAN_LOW])
    assert not snapshot_matches({"actions": [{"applied": None}]}, [FAN_LOW])
