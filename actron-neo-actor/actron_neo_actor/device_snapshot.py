"""What each action sets, what it saves first, and how to put it back.

Snapshot shapes (Core API stores them as opaque JSON on the action run). A run
of several actions saves the list form, one entry per action in schedule order;
runs from before schedules held a list saved a single entry. Restore accepts both.

    {"actions": [<entry>, ...]}     # list form, every entry read before any command is sent

    {                               # an entry, or a whole single-entry snapshot
        "action": "<action id>",
        "applied": {...},   # what the action set, to tell the actor's change from the owner's
        "saved": {...},     # the values before the action, which restore puts back
    }

When two entries cover the same field, restore puts back the earlier entry's
`saved` value (the value before the run) and compares the unit with the later
entry's `applied` value (what the run left). Main unit fields go back first, in
the order power, setpoint, fan_mode, then zones on/off and zone setpoints: a
zone's old setpoint was valid for the old main setpoint. The main and zone
setpoints go back in the mode the run applied them in: before power and mode
when the unit is on in COOL, HEAT or AUTO and still shows them in its current
mode, else after them (see _plan_mode_setpoint). The commands that go first are
sent and confirmed as their own batch, main setpoint before zone setpoints.

By action:

    system_off           applied {"is_on": false}
    system_on, set_mode  applied {"is_on": true, "mode": "<mode>"}
        all three        saved   {"is_on": <bool>, "mode": "<mode>"}
    set_temperature      applied {"setpoint_c": <float>}
                         saved   {"cool_setpoint_c": <float>, "heat_setpoint_c": <float>}
    set_fan_mode         applied {"fan_mode": "<speed without +CONT>"}
                         saved   {"fan_mode": "<fan mode as reported, e.g. AUTO+CONT>"}
    set_zones            applied {"zones": {"<title>": <bool>}}
                         saved   {"zones": {"<title>": <bool>}}
        set_zones lists only the named zones whose state the action changes.
    set_zone_temperature applied {"zone": "<title>", "setpoint_c": <float>}
                         saved   {"zone": "<title>", "cool_setpoint_c": <float>, "heat_setpoint_c": <float>}

Restore reports fields as "power" (is_on with mode), "mode", "setpoint",
"fan_mode", "zone:<title>" and "zone_temp:<title>". A field is skipped when its current value
differs from what the action applied, because the owner has changed it since.
After system_off, which sets no mode, "mode" is skipped when the owner chose a
different mode while the unit was off; the power restore then turns the unit
on in the owner's mode, not the saved one.

The set_temperature `setpoint_c` is the clamped value the unit ends up at
(see device_state.clamp_setpoint), not the value requested. The library
clamps a zone setpoint too, but set_zone_temperature refuses a value outside
the zone's range instead (see check_zone_range), so its `setpoint_c` is the
value requested. The range is read when the command is sent, because an
earlier command in the same batch can move it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from actron_neo_actor.device_state import HEAT, DeviceState, active_setpoint, base_fan_mode, clamp_setpoint

SETPOINT_TOLERANCE_C = 0.05
# Room for float error at the range edges: 22.9 + 1.2 is 24.099999999999998.
RANGE_EPSILON_C = 1e-6
POWER_ACTIONS = frozenset({"system_off", "system_on", "set_mode"})
# The library refuses a setpoint in FAN and OFF mode.
SETPOINT_MODES = frozenset({"COOL", "HEAT", "AUTO"})
ACTIONS = POWER_ACTIONS | {"set_temperature", "set_fan_mode", "set_zones", "set_zone_temperature"}
# Restore order for the fields of a list-form snapshot. Zone setpoints ("zone_temp:<title>") go last.
_FIELD_ORDER = {"power": 0, "setpoint": 1, "fan_mode": 2, "zones": 3}

Command = tuple[str, dict]


class DeviceError(Exception):
    """A command or refresh failed. The message never holds a token or raw status."""


class DeviceAuthError(DeviceError):
    """The Actron cloud rejected the sign-in; the instance needs a new one."""


@dataclass(frozen=True)
class RestoreResult:
    restored: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class RestorePlan:
    # Sent and confirmed before `commands`, while the unit is still in the mode the run left.
    early: list[Command] = field(default_factory=list)
    commands: list[Command] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def check_action(action_id: str) -> None:
    if action_id not in ACTIONS:
        raise DeviceError(f"unknown action: {action_id}")


def check_zones(names: list[str], state: DeviceState) -> None:
    unknown = [name for name in names if name not in state.enabled_zones]
    if unknown:
        raise DeviceError(f"unknown zone: {', '.join(unknown)}")


def _check_zone_known(zone: str, state: DeviceState) -> None:
    if zone not in state.zone_ranges_c:
        raise DeviceError(f"unknown zone: {zone}")


def check_zone_range(zone: str, value: float, zone_range: tuple[float, float], unit_setpoint: float) -> None:
    """Refuse a zone setpoint outside `zone_range`, the (min, max) the unit accepts now.
    The library's `zone.set_temperature` would clamp it and send it anyway."""
    low, high = zone_range
    if low - RANGE_EPSILON_C <= value <= high + RANGE_EPSILON_C:
        return
    raise DeviceError(
        f"{zone} {value:.1f}°C is outside {low:.1f}–{high:.1f}°C (the unit is set to {unit_setpoint:.1f}°C)"
    )


def _near(current: float | None, target: float) -> bool:
    return current is not None and abs(current - target) <= SETPOINT_TOLERANCE_C


def matches_action(action_id: str, params: dict, state: DeviceState) -> bool:
    """True when the synced state shows what the action sets."""
    check_action(action_id)
    if action_id == "system_off":
        return not state.is_on
    if action_id == "system_on":
        return state.is_on and state.mode == params["mode"]
    if action_id == "set_mode":
        return state.mode == params["mode"]
    if action_id == "set_temperature":
        return _near(active_setpoint(state), clamp_setpoint(state, float(params["temperature_c"])))
    if action_id == "set_zone_temperature":
        return _near(state.zone_setpoints_c.get(params["zone"]), float(params["temperature_c"]))
    if action_id == "set_fan_mode":
        return base_fan_mode(state.fan_mode) == params["fan_mode"]
    return all(state.enabled_zones.get(zone) == params["enabled"] for zone in params["zones"])


def _applied(action_id: str, params: dict, state: DeviceState) -> dict:
    if action_id == "system_off":
        return {"is_on": False}
    if action_id in POWER_ACTIONS:
        return {"is_on": True, "mode": params["mode"]}
    if action_id == "set_temperature":
        return {"setpoint_c": clamp_setpoint(state, float(params["temperature_c"]))}
    if action_id == "set_fan_mode":
        return {"fan_mode": params["fan_mode"]}
    if action_id == "set_zone_temperature":
        # No range check here: an earlier action in the same run can move the range.
        _check_zone_known(params["zone"], state)
        return {"zone": params["zone"], "setpoint_c": float(params["temperature_c"])}
    check_zones(list(params["zones"]), state)
    enabled = bool(params["enabled"])
    return {"zones": {zone: enabled for zone in params["zones"] if state.enabled_zones[zone] != enabled}}


def _saved(action_id: str, applied: dict, state: DeviceState) -> dict:
    if action_id in POWER_ACTIONS:
        return {"is_on": state.is_on, "mode": state.mode}
    if action_id == "set_temperature":
        return {"cool_setpoint_c": state.cool_setpoint_c, "heat_setpoint_c": state.heat_setpoint_c}
    if action_id == "set_fan_mode":
        return {"fan_mode": state.fan_mode}
    if action_id == "set_zone_temperature":
        cool, heat = state.zone_cool_heat_c[applied["zone"]]
        return {"zone": applied["zone"], "cool_setpoint_c": cool, "heat_setpoint_c": heat}
    return {"zones": {zone: state.enabled_zones[zone] for zone in applied["zones"]}}


def snapshot_for(action_id: str, params: dict, state: DeviceState) -> dict:
    check_action(action_id)
    try:
        applied = _applied(action_id, params, state)
    except KeyError as err:
        raise DeviceError(f"{action_id}: missing parameter {err}") from err
    return {"action": action_id, "applied": applied, "saved": _saved(action_id, applied, state)}


def _single(plan: RestorePlan, name: str, owner_changed: bool, command: Command, current: DeviceState) -> RestorePlan:
    if owner_changed:
        plan.skipped.append(name)
        return plan
    plan.restored.append(name)
    if not matches_action(*command, current):
        plan.commands.append(command)
    return plan


def _plan_power(applied: dict, saved: dict, current: DeviceState) -> RestorePlan:
    """system_off sets no mode, so a mode that differs from the saved one is the owner's choice: keep it."""
    plan = RestorePlan()
    owner_changed = current.is_on != applied["is_on"] or ("mode" in applied and current.mode != applied["mode"])
    mode = saved["mode"]
    if "mode" not in applied and current.mode != saved["mode"]:
        plan.skipped.append("mode")
        mode = current.mode
    command: Command = ("system_on", {"mode": mode}) if saved["is_on"] else ("system_off", {})
    return _single(plan, "power", owner_changed, command, current)


def _saved_for_mode(saved: dict, current: DeviceState) -> float:
    """The saved setpoint for the unit's mode now, which may differ from its mode at save time."""
    return saved["heat_setpoint_c"] if current.mode.upper() == HEAT else saved["cool_setpoint_c"]


def _after_power(commands: list[Command], current: DeviceState) -> DeviceState:
    """The unit as the power restore will leave it, for planning the setpoints sent after it."""
    for action_id, params in commands:
        if action_id == "system_off":
            return replace(current, is_on=False)
        mode = str(params["mode"])
        heat = mode.upper() == HEAT
        zones = {zone: h if heat else c for zone, (c, h) in current.zone_cool_heat_c.items()}
        return replace(current, is_on=True, mode=mode, zone_setpoints_c=zones)
    return current


def _plan_mode_setpoint(
    name: str,
    applied_c: float,
    read: Callable[[DeviceState], float | None],
    command_for: Callable[[DeviceState], Command],
    states: tuple[DeviceState, DeviceState],
) -> RestorePlan:
    """Put a setpoint back in the mode the run applied it in. `states` is (current, target).

    The applied value was read before the run, so it may belong to the mode the unit
    is in now or to the mode the power restore goes back to (`target`):
    - The unit is on in a mode that takes a setpoint and `read` shows the applied value:
      send `command_for(current)` early, before power and mode go back. A unit that
      is off falls through, since its setpoint may not take.
    - Else `read(target)` shows the applied value: send `command_for(target)` after power.
    - Else the owner has changed it.
    """
    current, target = states
    if current.is_on and current.mode.upper() in SETPOINT_MODES and _near(read(current), applied_c):
        plan = _single(RestorePlan(), name, False, command_for(current), current)
        plan.early, plan.commands = plan.commands, []
        return plan
    owner_changed = not _near(read(target), applied_c)
    return _single(RestorePlan(), name, owner_changed, command_for(target), target)


def _plan_setpoint(applied: dict, saved: dict, current: DeviceState, target: DeviceState) -> RestorePlan:
    def command_for(state: DeviceState) -> Command:
        return ("set_temperature", {"temperature_c": _saved_for_mode(saved, state)})

    return _plan_mode_setpoint("setpoint", applied["setpoint_c"], active_setpoint, command_for, (current, target))


def _plan_zone_setpoint(applied: dict, saved: dict, current: DeviceState, target: DeviceState) -> RestorePlan:
    """The restore command goes through the same range check as the action, read from the live
    status at send time, so a saved value the unit would now clamp fails with the range message."""
    zone = applied["zone"]

    def read(state: DeviceState) -> float | None:
        return state.zone_setpoints_c.get(zone)

    def command_for(state: DeviceState) -> Command:
        return ("set_zone_temperature", {"zone": zone, "temperature_c": _saved_for_mode(saved, state)})

    return _plan_mode_setpoint(f"zone_temp:{zone}", applied["setpoint_c"], read, command_for, (current, target))


def _plan_fan(applied: dict, saved: dict, current: DeviceState) -> RestorePlan:
    owner_changed = base_fan_mode(current.fan_mode) != applied["fan_mode"]
    command: Command = ("set_fan_mode", {"fan_mode": base_fan_mode(saved["fan_mode"])})
    return _single(RestorePlan(), "fan_mode", owner_changed, command, current)


def _plan_zones(applied: dict, saved: dict, current: DeviceState) -> RestorePlan:
    plan = RestorePlan()
    to_set: dict[bool, list[str]] = {True: [], False: []}
    for zone, before in saved["zones"].items():
        now = current.enabled_zones.get(zone)
        if now != applied["zones"].get(zone):
            plan.skipped.append(f"zone:{zone}")
            continue
        plan.restored.append(f"zone:{zone}")
        if now != before:
            to_set[bool(before)].append(zone)
    plan.commands = [("set_zones", {"zones": zones, "enabled": on}) for on, zones in to_set.items() if zones]
    return plan


def entries(snapshot: dict) -> list[dict]:
    """The entries of a list-form snapshot, or a single-entry snapshot as a list of one."""
    return list(snapshot["actions"]) if "actions" in snapshot else [snapshot]


def _field(entry: dict) -> str:
    action_id = entry["action"]
    if action_id in POWER_ACTIONS:
        return "power"
    if action_id == "set_zone_temperature":
        return f"zone_temp:{entry['applied']['zone']}"
    names = {"set_temperature": "setpoint", "set_fan_mode": "fan_mode", "set_zones": "zones"}
    if action_id in names:
        return names[action_id]
    raise DeviceError(f"malformed snapshot: unknown action {action_id!r}")


def _combine(earlier: dict, later: dict) -> dict:
    """Two entries on one field: applied values from the later entry win, saved values from the earlier."""
    if later["action"] == "set_zones":
        applied = {"zones": {**earlier["applied"]["zones"], **later["applied"]["zones"]}}
        saved = {"zones": {**later["saved"]["zones"], **earlier["saved"]["zones"]}}
    else:
        applied, saved = {**earlier["applied"], **later["applied"]}, earlier["saved"]
    return {"action": later["action"], "applied": applied, "saved": saved}


def merge_entries(found: list[dict]) -> list[tuple[str, dict]]:
    """One entry per field, in restore order. Zone setpoints keep their schedule order."""
    merged: dict[str, dict] = {}
    for entry in found:
        name = _field(entry)
        merged[name] = _combine(merged[name], entry) if name in merged else entry
    return sorted(merged.items(), key=lambda item: _FIELD_ORDER.get(item[0], len(_FIELD_ORDER)))


def _plan_entry(entry: dict, current: DeviceState, target: DeviceState) -> RestorePlan:
    action_id, applied, saved = entry["action"], entry["applied"], entry["saved"]
    if action_id in POWER_ACTIONS:
        return _plan_power(applied, saved, current)
    if action_id == "set_temperature":
        return _plan_setpoint(applied, saved, current, target)
    if action_id == "set_fan_mode":
        return _plan_fan(applied, saved, current)
    if action_id == "set_zones":
        return _plan_zones(applied, saved, current)
    return _plan_zone_setpoint(applied, saved, current, target)


def plan_restore(snapshot: dict, current: DeviceState) -> RestorePlan:
    """Plan the restore of either snapshot shape; see the module docstring for the rules."""
    try:
        plan, target = RestorePlan(), current
        for name, entry in merge_entries(entries(snapshot)):
            part = _plan_entry(entry, current, target)
            plan.early += part.early
            plan.commands += part.commands
            plan.restored += part.restored
            plan.skipped += part.skipped
            if name == "power":
                target = _after_power(part.commands, current)
        return plan
    except (KeyError, TypeError, AttributeError) as err:
        raise DeviceError(f"malformed snapshot: {type(err).__name__} {err}") from err
