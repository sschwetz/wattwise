"""What one tick knows about an instance: its schedules, the site context and its
runs, read from Core API at the start of the tick and kept current as the tick
creates and finishes runs."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from actron_neo_actor.device import DeviceState
from actron_neo_actor.models import Context, Run, Schedule

STALE_AFTER = datetime.timedelta(minutes=5)
OPEN_ACTION = ("pending", "running", "ok")
CLOSING_RESTORE = ("ok", "skipped", "failed")
OPEN = ("pending", "running")


def sensed(state: DeviceState | None, now: datetime.datetime) -> tuple[dict[str, float], set[str]]:
    """Device readings for conditions, and which of them are stale. With no state yet every reading is missing."""
    if state is None:
        return {}, set()
    readings = state.readings()
    stale = set(readings) if now - state.updated_at > STALE_AFTER else set()
    return readings, stale


@dataclass
class TickView:
    now: datetime.datetime
    schedules: dict[int, Schedule]
    context: Context
    unfinished: list[Run]  # action runs that still owe a restore, as Core API counts them
    recent: list[Run]  # every run with trigger_at in the last 24 hours
    restore_only: bool = False  # the instance or its site is disabled; only restores remain

    def action_trigger_ats(self, schedule_id: int) -> set[datetime.datetime]:
        return {r.trigger_at for r in self.recent if r.schedule_id == schedule_id and r.kind == "action"}

    def has_restore(self, action_run_id: int) -> bool:
        return any(r.kind == "restore" and r.parent_run_id == action_run_id for r in self.recent)

    def unfinished_run(self, run_id: int | None) -> Run | None:
        return next((r for r in self.unfinished if r.id == run_id), None)

    def note(self, run: Run) -> None:
        """Take in a run this tick created or changed."""
        self.recent = [r for r in self.recent if r.id != run.id] + [run]
        self.unfinished = [r for r in self.unfinished if r.id != run.id]
        schedule = self.schedules.get(run.schedule_id)
        restores = schedule is not None and schedule.restore.enabled
        if run.kind == "action" and run.outcome in OPEN_ACTION and restores:
            self.unfinished.append(run)
        if run.kind == "restore" and run.outcome in CLOSING_RESTORE:
            self.unfinished = [r for r in self.unfinished if r.id != run.parent_run_id]
