"""Action runs: record missed triggers, then create, check and run each due one."""

from __future__ import annotations

import datetime
from collections.abc import Callable

from actron_neo_actor.device import DeviceError
from actron_neo_actor.executor import Attempt, Cutoff, Env, apply_with_retries, create, record, update
from actron_neo_actor.models import ActionSpec, RetrySpec, Run, Schedule
from actron_neo_actor.schedule_eval import due_triggers, evaluate_conditions, missed_triggers
from actron_neo_actor.view import TickView, sensed

OTHER_ACTIVE = "another schedule is active"


async def run_schedule(env: Env, view: TickView, schedule: Schedule, was_met: bool | None) -> None:
    """`was_met` is whether a price or temperature trigger's level was passed at the last tick."""
    existing = view.action_trigger_ats(schedule.id)
    for missed in missed_triggers(schedule, view.context, view.now, existing):
        run = await record(env, schedule.id, "action", missed.trigger_at, "missed")
        if run is not None:
            view.note(run)
    readings, stale = sensed(env.device.state(), env.clock())
    for due in due_triggers(schedule, view.context, view.now, existing, was_met, readings, stale):
        await _run_trigger(env, view, schedule, due.trigger_at)


async def _run_trigger(env: Env, view: TickView, schedule: Schedule, at: datetime.datetime) -> None:
    run = await create(env, schedule.id, "action", at)
    if run is None:
        return
    view.note(run)
    reason = _skip_reason(env, view, schedule, run)
    if reason is not None:
        finished = await update(env, run, "skipped", reason)
    else:
        finished = await _apply(env, run, schedule)
    if finished is not None:
        view.note(finished)


def _skip_reason(env: Env, view: TickView, schedule: Schedule, run: Run) -> str | None:
    if any(other.id != run.id for other in view.unfinished):
        return OTHER_ACTIVE
    readings, stale = sensed(env.device.state(), env.clock())
    result = evaluate_conditions(schedule.conditions, readings, stale, view.context)
    return None if result.passed else result.reason


async def _apply(env: Env, run: Run, schedule: Schedule) -> Run | None:
    applied = await claim_and_apply(env, run, schedule.action, schedule.retry)
    if applied is None:
        return None
    claimed, attempt = applied
    return await finish(env, claimed, attempt)


async def claim_and_apply(
    env: Env,
    run: Run,
    action: ActionSpec,
    retry: RetrySpec,
    before_apply: Callable[[], None] | None = None,
    latest_start: Cutoff | None = None,
) -> tuple[Run, Attempt] | None:
    """Save the snapshot with the claim, so it is in Core API before the unit changes, then apply with retries.
    `before_apply` runs just before the first command. None when another process claimed the run first."""
    try:
        snapshot = env.device.snapshot_for(action.id, action.params)
    except DeviceError as err:
        return run, Attempt(0, error=str(err))
    claimed = await update(env, run, "running", snapshot=snapshot)
    if claimed is None:
        return None
    if before_apply is not None:
        before_apply()
    return claimed, await apply_with_retries(env, action, retry, latest_start=latest_start)


async def finish(env: Env, run: Run, attempt: Attempt, ok_message: str | None = None) -> Run | None:
    if attempt.ok:
        return await update(env, run, "ok", ok_message, attempts=attempt.attempts)
    return await update(env, run, "failed", attempt.failure(), attempts=attempt.attempts)
