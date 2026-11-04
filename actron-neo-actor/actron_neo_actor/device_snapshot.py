"""What each action sets, what it saves first, and how to put it back.

Snapshot shape (Core API stores it as opaque JSON on the action run):

    {
        "action": "<action id>",
        "applied": {...},   # what the action set, to tell the actor's change from the owner's
        "saved": {...},     # the values before the action, which restore puts back
    }

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

Restore reports fields as "power" (is_on with mode), "setpoint", "fan_mode"
and "zone:<title>". A field is skipped when its current value differs from
what the action applied, because the owner has changed it since.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from actron_neo_actor.device_state import DeviceState, active_setpoint, base_fan_mode

SETPOINT_TOLERANCE_C = 0.05
POWER_ACTIONS = frozenset({"system_off", "system_on", "set_mode"})
ACTIONS = POWER_ACTIONS | {"set_temperature", "set_fan_mode", "set_zones"}

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
        return abs(active_setpoint(state) - float(params["temperature_c"])) <= SETPOINT_TOLERANCE_C
    if action_id == "set_fan_mode":
        return base_fan_mode(state.fan_mode) == params["fan_mode"]
    return all(state.enabled_zones.get(zone) == params["enabled"] for zone in params["zones"])


def _applied(action_id: str, params: dict, state: DeviceState) -> dict:
    if action_id == "system_off":
        return {"is_on": False}
    if action_id in POWER_ACTIONS:
        return {"is_on": True, "mode": params["mode"]}
    if action_id == "set_temperature":
        return {"setpoint_c": float(params["temperature_c"])}
    if action_id == "set_fan_mode":
        return {"fan_mode": params["fan_mode"]}
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
    owner_changed = current.is_on != applied["is_on"] or ("mode" in applied and current.mode != applied["mode"])
    command: Command = ("system_on", {"mode": saved["mode"]}) if saved["is_on"] else ("system_off", {})
    return _single(RestorePlan(), "power", owner_changed, command, current)


def _plan_setpoint(applied: dict, saved: dict, current: DeviceState) -> RestorePlan:
    owner_changed = abs(active_setpoint(current) - applied["setpoint_c"]) > SETPOINT_TOLERANCE_C
    target = saved["heat_setpoint_c"] if current.mode.upper() == "HEAT" else saved["cool_setpoint_c"]
    return _single(RestorePlan(), "setpoint", owner_changed, ("set_temperature", {"temperature_c": target}), current)


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


def plan_restore(snapshot: dict, current: DeviceState) -> RestorePlan:
    try:
        action_id, applied, saved = snapshot["action"], snapshot["applied"], snapshot["saved"]
        if action_id in POWER_ACTIONS:
            return _plan_power(applied, saved, current)
        if action_id == "set_temperature":
            return _plan_setpoint(applied, saved, current)
        if action_id == "set_fan_mode":
            return _plan_fan(applied, saved, current)
        if action_id == "set_zones":
            return _plan_zones(applied, saved, current)
    except (KeyError, TypeError, AttributeError) as err:
        raise DeviceError(f"malformed snapshot: {type(err).__name__} {err}") from err
    raise DeviceError(f"malformed snapshot: unknown action {action_id!r}")
