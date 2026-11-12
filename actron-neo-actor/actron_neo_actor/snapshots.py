"""One snapshot for a run of several actions, and the part of it a restore puts back.

Entry i of a list-form snapshot belongs to the action at index i, which the
run's `steps` record as `{"index": i, ..., "outcome": "ok" | "failed"}`.
See device_snapshot.py for the shapes and how restore combines entries.
"""

from __future__ import annotations

from actron_neo_actor.device_snapshot import DeviceError, snapshot_for
from actron_neo_actor.device_state import DeviceState
from actron_neo_actor.models import ActionSpec


def snapshot_for_all(actions: list[ActionSpec], state: DeviceState) -> dict:
    """Every entry is read from `state`, the unit before any command is sent. Raises DeviceError."""
    return {"actions": [snapshot_for(action.id, action.params, state) for action in actions]}


def _ok_indexes(steps: list[dict]) -> set[int]:
    try:
        return {int(step["index"]) for step in steps if step.get("outcome") == "ok"}
    except (KeyError, TypeError, ValueError, AttributeError) as err:
        raise DeviceError(f"malformed steps: {type(err).__name__}") from err


def restorable(snapshot: dict | None, steps: list[dict] | None) -> dict | None:
    """The snapshot with only the entries whose step is `ok`; None when none are left.

    A single-entry snapshot predates steps and passes through, as does any snapshot when `steps` is None."""
    if snapshot is None or steps is None or "actions" not in snapshot:
        return snapshot
    ok = _ok_indexes(steps)
    kept = [entry for i, entry in enumerate(snapshot["actions"]) if i in ok]
    return {"actions": kept} if kept else None


def step_actions(steps: list[dict]) -> list[ActionSpec]:
    """The actions of the `ok` steps in index order, as they ran: they pair with the entries `restorable`
    keeps even after the schedule is edited."""
    try:
        ok = sorted((s for s in steps if s.get("outcome") == "ok"), key=lambda s: int(s["index"]))
        return [ActionSpec(str(s["action_id"]), dict(s.get("params") or {})) for s in ok]
    except (KeyError, TypeError, ValueError, AttributeError) as err:
        raise DeviceError(f"malformed steps: {type(err).__name__}") from err


def ok_actions(actions: list[ActionSpec], steps: list[dict] | None) -> list[ActionSpec]:
    """The actions whose step is `ok`, in order, to pair with the entries `restorable` keeps."""
    if steps is None:
        return list(actions)
    ok = _ok_indexes(steps)
    return [action for i, action in enumerate(actions) if i in ok]
