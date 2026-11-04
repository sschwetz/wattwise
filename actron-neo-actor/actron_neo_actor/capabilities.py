"""The capabilities declaration sent to Core API (`PUT /actor/instances/{id}/capabilities`).

The shape follows core-api/core_api/actor_schemas.py: `Capabilities`,
`ActionDecl`, `ParamDecl`, `ReadingDecl` and `Option`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from actron_neo_api import ActronAirStatus

from actron_neo_actor.device_state import zone_titles

# The library has no list of fan speeds; these are the Actron Neo values
# without the "+CONT" suffix, which set_fan_mode keeps as it was.
FAN_MODES = ("AUTO", "LOW", "MED", "HIGH")

TEMPERATURE_MIN_C = 10
TEMPERATURE_MAX_C = 30


def _options(values: Iterable[str], label: Callable[[str], str] = str.title) -> list[dict]:
    return [{"value": v, "label": label(v)} for v in values]


def _as_is(value: str) -> str:
    return value


def _fan_label(value: str) -> str:
    return "Medium" if value == "MED" else value.title()


def _mode_param(modes: list[str]) -> dict:
    return {"name": "mode", "label": "Mode", "type": "choice", "options": _options(modes)}


def _actions(modes: list[str], zones: list[str]) -> list[dict]:
    temperature = {
        "name": "temperature_c",
        "label": "Temperature",
        "type": "number",
        "min": TEMPERATURE_MIN_C,
        "max": TEMPERATURE_MAX_C,
        "unit": "C",
    }
    fan = {"name": "fan_mode", "label": "Fan speed", "type": "choice", "options": _options(FAN_MODES, _fan_label)}
    zone_list = {"name": "zones", "label": "Zones", "type": "multi_choice", "options": _options(zones, _as_is)}
    enabled = {"name": "enabled", "label": "Zones on", "type": "bool"}
    return [
        {"id": "system_off", "label": "Turn off", "restorable": True, "params": []},
        {"id": "system_on", "label": "Turn on", "restorable": True, "params": [_mode_param(modes)]},
        {"id": "set_mode", "label": "Set mode", "restorable": True, "params": [_mode_param(modes)]},
        {"id": "set_temperature", "label": "Set temperature", "restorable": True, "params": [temperature]},
        {"id": "set_fan_mode", "label": "Set fan speed", "restorable": True, "params": [fan]},
        {"id": "set_zones", "label": "Switch zones", "restorable": True, "params": [zone_list, enabled]},
    ]


def _readings(zones: list[str]) -> list[dict]:
    return [
        {"id": "indoor_temp_c", "label": "Indoor temperature", "unit": "C"},
        {"id": "outdoor_temp_c", "label": "Outdoor temperature", "unit": "C"},
        {"id": "humidity_pct", "label": "Indoor humidity", "unit": "%"},
        {"id": "is_on", "label": "Unit on (1) or off (0)"},
        {"id": "zone_temp_c", "label": "Zone temperature", "unit": "C", "target_options": _options(zones, _as_is)},
    ]


def build_capabilities(status: ActronAirStatus) -> dict:
    """Modes come from the unit's mode support flags and zones from its zone titles."""
    modes = status.user_aircon_settings.supported_modes
    zones = zone_titles(status)
    return {"version": 1, "actions": _actions(modes, zones), "readings": _readings(zones)}
