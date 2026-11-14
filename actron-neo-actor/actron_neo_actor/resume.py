"""Finish runs that a stopped actor left pending or running.

A worker runs actions and restores inside its tick, so any it finds still open
at the start of a tick were left by an earlier process or a tick that failed
part way. Test runs run in the background, so the worker passes their ids in
`in_flight` and those are left alone.
"""

from __future__ import annotations

import datetime

from actron_neo_actor.actions import run_created
from actron_neo_actor.executor import Env, apply_with_retries, settled, shows_action, update
from actron_neo_actor.models import ActionSpec, RetrySpec, Run
from actron_neo_actor.restores import execute_restore, take
from actron_neo_actor.run_steps import RunResult, StepResult, finish_run, from_attempt
from actron_neo_actor.snapshots import snapshot_matches
from actron_neo_actor.test_runs import finish_interrupted
from actron_neo_actor.view import HOLDING, OPEN, TickView

TOO_LATE = "never started: the give-up time passed before the actor could run it"
SCHEDULE_GONE = "the schedule was removed before the run started"
CONFIRMED = "confirmed after a restart"
REAPPLIED = "applied after a restart"
NOT_CONFIRMED = "the actor restarted before the unit confirmed"
NOTHING_TO_RESTORE = "nothing left to restore"
SCHEDULE_CHANGED = "the schedule changed before the actor restarted"


async def resume(env: Env, view: TickView, in_flight: set[int]) -> None:
    left_open = {r.id: r for r in view.unfinished + view.recent if r.outcome in OPEN and r.id not in in_flight}
    runs = sorted(left_open.values(), key=lambda r: r.kind != "action")  # actions first: restores need them settled
    for run in runs:
        if run.kind == "action":
            await _resume_action(env, view, run)
        elif run.kind == "restore":
            await _resume_restore(env, view, run)
        elif run.outcome == "running":
            await finish_interrupted(env, view, run)


async def _resume_action(env: Env, view: TickView, run: Run) -> None:
    if run.outcome == "pending":
        await _start_pending(env, view, run)
        return
    done = await _carry_on(env, view, run)
    if done is not None:
        view.note(done)


async def _start_pending(env: Env, view: TickView, run: Run) -> None:
    """A pending row is a trigger Core API saved but the actor never started, for example when the
    create call timed out. It runs as soon as it is found, unless the schedule's give-up time since
    the trigger has passed. That ends `failed`, not `skipped`, because the action did not happen."""
    schedule = view.schedules.get(run.schedule_id)
    if schedule is None:
        done = await update(env, run, "skipped", SCHEDULE_GONE)
    elif env.clock() > run.trigger_at + datetime.timedelta(minutes=schedule.retry.give_up_minutes):
        done = await update(env, run, "failed", TOO_LATE)
    else:
        await run_created(env, view, schedule, run)
        return
    if done is not None:
        view.note(done)


async def _carry_on(env: Env, view: TickView, run: Run) -> Run | None:
    """A claimed run: each action the unit shows counts as done, and each one it does not is applied
    again within the give-up time from the claim. Steps are recorded only when a run finishes, so the
    schedule's actions are the list to check.

    When the actions were edited after the claim, nothing is sent: the run ends `partial` with no
    steps, so the restore puts back the whole snapshot, field by field, and no override ends it."""
    schedule = view.schedules.get(run.schedule_id)
    if schedule is None or run.snapshot is None:
        return await update(env, run, "failed", NOT_CONFIRMED)
    if not snapshot_matches(run.snapshot, schedule.actions):
        return await update(env, run, "partial", SCHEDULE_CHANGED)
    started = run.started_at or env.clock()
    steps = [await _carry_on_step(env, i, action, schedule.retry, started) for i, action in enumerate(schedule.actions)]
    ok_message = REAPPLIED if any(s.message == REAPPLIED for s in steps) else CONFIRMED
    return await finish_run(env, run, RunResult(steps), ok_message)


async def _carry_on_step(
    env: Env, index: int, action: ActionSpec, retry: RetrySpec, started: datetime.datetime
) -> StepResult:
    if await shows_action(env, action):
        return StepResult(index, action, True, CONFIRMED, 0)
    if env.clock() >= started + datetime.timedelta(minutes=retry.give_up_minutes):
        confirmed = await settled(env, action)
        return StepResult(index, action, confirmed, CONFIRMED if confirmed else NOT_CONFIRMED, 0)
    attempt = await apply_with_retries(env, action, retry, started)
    return from_attempt(index, action, attempt, REAPPLIED)


async def _resume_restore(env: Env, view: TickView, run: Run) -> None:
    parent, schedule = view.unfinished_run(run.parent_run_id), view.schedules.get(run.schedule_id)
    if parent is None or parent.outcome not in HOLDING or schedule is None:
        done = await update(env, run, "skipped", NOTHING_TO_RESTORE)
    elif await take(env, view, parent):
        done = await execute_restore(env, run, parent, schedule.retry)
    else:
        return
    if done is not None:
        view.note(done)
