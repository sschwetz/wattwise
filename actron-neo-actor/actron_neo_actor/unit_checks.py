"""Reading the synced state against what an action set, and wording restore results."""

from __future__ import annotations

from actron_neo_actor.device import Device, DeviceState, RestoreResult
from actron_neo_actor.models import ActionSpec


def owner_changed(device: Device, action: ActionSpec, snapshot: dict | None, state: DeviceState) -> bool:
    """True when the unit no longer shows what the action set, so the owner has taken over.

    A zone action is taken over only when every zone the actor changed is back
    the way the owner wants it. While some are still as the actor left them, the
    restore puts those back and leaves the owner's zones alone, field by field.
    """
    applied = ((snapshot or {}).get("applied") or {}).get("zones") if action.id == "set_zones" else None
    if applied:
        return all(state.enabled_zones.get(zone) != value for zone, value in applied.items())
    return not device.matches_action(action.id, action.params, state)


def _word(field: str) -> str:
    """A restore field in words: `zone:Study` is "zone Study", `zone_temp:Study` is "zone Study temperature"."""
    if field.startswith("zone_temp:"):
        return f"zone {field.removeprefix('zone_temp:')} temperature"
    return field.replace("zone:", "zone ", 1)


def _words(fields: list[str]) -> str:
    return ", ".join(_word(f) for f in fields)


def restore_message(result: RestoreResult) -> str:
    parts = []
    if result.restored:
        parts.append(f"restored {_words(result.restored)}")
    if result.skipped:
        parts.append(f"left alone because the owner changed them: {_words(result.skipped)}")
    return "; ".join(parts) or "nothing to restore"
