from __future__ import annotations

import math
from collections.abc import Iterable

from core_api.actor_schemas import (
    ActionSpec,
    Capabilities,
    ConditionSpec,
    ParamDecl,
)
from core_api.errors import AppError

SITE_READING_IDS = frozenset({"outdoor_temp_c", "battery_soc_pct", "price_c_per_kwh"})


def _invalid(message: str) -> AppError:
    return AppError(422, "invalid_schedule", message)


def _check_number(param: ParamDecl, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise _invalid(f"Parameter '{param.name}' must be a finite number, got {value!r}")
    if param.min is not None and value < param.min:
        raise _invalid(f"Parameter '{param.name}' is {value}, below the minimum of {param.min}")
    if param.max is not None and value > param.max:
        raise _invalid(f"Parameter '{param.name}' is {value}, above the maximum of {param.max}")


def _check_choice(param: ParamDecl, value: object) -> None:
    allowed = {o.value for o in param.options}
    if not isinstance(value, str) or value not in allowed:
        raise _invalid(f"Parameter '{param.name}' is {value!r}, which is not one of {sorted(allowed)}")


def _check_multi_choice(param: ParamDecl, value: object) -> None:
    if not isinstance(value, list):
        raise _invalid(f"Parameter '{param.name}' must be a list of options, got {value!r}")
    allowed = {o.value for o in param.options}
    for item in value:
        if item not in allowed:
            raise _invalid(f"Parameter '{param.name}' has {item!r}, which is not one of {sorted(allowed)}")


def _check_param(param: ParamDecl, value: object) -> None:
    if param.type == "number":
        _check_number(param, value)
    elif param.type == "choice":
        _check_choice(param, value)
    elif param.type == "multi_choice":
        _check_multi_choice(param, value)
    elif not isinstance(value, bool):
        raise _invalid(f"Parameter '{param.name}' must be true or false, got {value!r}")


def validate_action_against(capabilities: Capabilities, action: ActionSpec) -> None:
    """Reject an action the actor did not declare, or one with params the declaration does not allow."""
    decl = next((a for a in capabilities.actions if a.id == action.id), None)
    if decl is None:
        raise _invalid(f"Action '{action.id}' is not supported by this actor")
    declared = {p.name: p for p in decl.params}
    for name in action.params:
        if name not in declared:
            raise _invalid(f"Action '{action.id}' has no parameter '{name}'")
    for name, param in declared.items():
        if name not in action.params:
            raise _invalid(f"Action '{action.id}' is missing parameter '{name}'")
        _check_param(param, action.params[name])


def _check_condition(capabilities: Capabilities, site_reading_ids: set[str], cond: ConditionSpec) -> None:
    device = next((r for r in capabilities.readings if r.id == cond.reading), None)
    if device is None:
        if cond.reading not in site_reading_ids:
            raise _invalid(f"Reading '{cond.reading}' is not available for this site or actor")
        if cond.target is not None:
            raise _invalid(f"Reading '{cond.reading}' does not take a target, got '{cond.target}'")
        return
    if cond.target is None:
        return
    options = device.target_options
    if options is None:
        raise _invalid(f"Reading '{cond.reading}' does not take a target, got '{cond.target}'")
    if cond.target not in {o.value for o in options}:
        raise _invalid(f"Target '{cond.target}' is not a declared target for reading '{cond.reading}'")


def validate_conditions_against(
    capabilities: Capabilities, site_reading_ids: set[str], conditions: Iterable[ConditionSpec]
) -> None:
    """Each enabled condition reads a device reading first, then a site reading.
    A switched-off condition is not checked, so it may name a reading the device no longer declares."""
    for cond in conditions:
        if cond.enabled:
            _check_condition(capabilities, site_reading_ids, cond)
