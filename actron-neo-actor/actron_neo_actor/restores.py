"""Unfinished action runs: notice when the owner takes over, and put the unit back when the restore is due.

`runs_lock` is held while an action run is marked overridden and while a
restore takes the run off the unfinished list, so a push-triggered override
check never marks a run that a restore is already putting back.
"""

from __future__ import annotations

import dataclasses
import datetime

from actron_neo_actor.actions import finish
from actron_neo_actor.device import CONFIRM_SECONDS, RestoreResult
from actron_neo_actor.executor import Attempt, Env, create, current_state_restore, record, update, with_retries
from actron_neo_actor.models import RetrySpec, Run, Schedule
from actron_neo_actor.schedule_eval import early_restore_due, restore_gate_passes, restore_time
from actron_neo_actor.unit_checks import owner_changed, restore_message
from actron_neo_actor.view import TickView, sensed

CONFIRM_WINDOW = datetime.timedelta(seconds=CONFIRM_SECONDS)
OWNER_CHANGED = "owner changed the unit"
OWNER_OVERRIDE = "owner override"


async def watch_for_override(env: Env, view: TickView) -> None:
    for run in list(view.unfinished):
        await _check_override(env, view, run)


async def _check_override(env: Env, view: TickView, run: Run) -> None:
    schedule, state = view.schedules.get(run.schedule_id), env.device.state()
    if run.outcome != "ok" or run.finished_at is None or schedule is None or state is None:
        return
    if env.clock() - run.finished_at < CONFIRM_WINDOW:
        return
    if not owner_changed(env.device, [schedule.action], run.snapshot, state):
        return
    async with env.runs_lock:
        if view.unfinished_run(run.id) is None:
            return
        changed = await update(env, run, "overridden", OWNER_CHANGED)
        # A 409 means the run finished some other way; either way it no longer holds the unit.
        view.note(changed or dataclasses.replace(run, outcome="overridden"))


async def run_restores(env: Env, view: TickView) -> None:
    for run in list(view.unfinished):
        schedule = view.schedules.get(run.schedule_id)
        if run.outcome != "ok" or schedule is None:
            continue
        at = _restore_due_at(env, view, schedule, run)
        if at is not None and await take(env, view, run):
            await _restore(env, view, schedule, run, at)


def _restore_due_at(env: Env, view: TickView, schedule: Schedule, run: Run) -> datetime.datetime | None:
    """The restore's trigger_at when it is due and its gate passes, else None (look again next tick).

    Disabling the schedule, the instance or the site brings the restore forward to now."""
    now = env.clock()
    readings, stale = sensed(env.device.state(), now)
    scheduled = restore_time(schedule, run, view.context)
    stopping = view.restore_only or not schedule.enabled
    if scheduled is not None and scheduled <= now:
        at = scheduled
    elif stopping or early_restore_due(schedule, readings, stale, view.context):
        at = now.replace(microsecond=0)
    else:
        return None
    return at if restore_gate_passes(schedule.restore.only_if, readings, stale, view.context) else None


async def take(env: Env, view: TickView, run: Run) -> bool:
    """Take the run off the unfinished list so no override check marks it while it is put back."""
    async with env.runs_lock:
        if view.unfinished_run(run.id) is None:
            return False
        view.unfinished = [r for r in view.unfinished if r.id != run.id]
        return True


async def _restore(env: Env, view: TickView, schedule: Schedule, run: Run, at: datetime.datetime) -> None:
    restore = await create(env, schedule.id, "restore", at, run.id)
    if restore is None:
        return
    view.note(restore)
    finished = await execute_restore(env, restore, run.snapshot, schedule.retry)
    if finished is not None:
        view.note(finished)


async def execute_restore(env: Env, restore: Run, snapshot: dict | None, retry: RetrySpec) -> Run | None:
    """Claim the restore row if it is still pending, put the snapshot back with retries and finish it."""
    if restore.outcome == "pending":
        claimed = await update(env, restore, "running")
        if claimed is None:
            return None
        restore = claimed
    attempt: Attempt[RestoreResult] = await with_retries(env, retry, current_state_restore(env, snapshot))
    message = restore_message(attempt.value) if attempt.ok and attempt.value is not None else None
    return await finish(env, restore, attempt, message)


async def record_override_skips(env: Env, view: TickView) -> None:
    """Give each overridden action its `skipped: owner override` restore row once the restore time comes."""
    now = env.clock()
    for run in view.recent:
        schedule = view.schedules.get(run.schedule_id)
        if run.kind != "action" or run.outcome != "overridden" or view.has_restore(run.id):
            continue
        if schedule is None or not schedule.restore.enabled:
            continue
        at = restore_time(schedule, run, view.context)
        if at is not None and at > now:
            continue
        row = await record(env, schedule.id, "restore", at or now.replace(microsecond=0), OWNER_OVERRIDE, run.id)
        if row is not None:
            view.note(row)
