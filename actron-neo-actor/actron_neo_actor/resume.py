"""Finish runs that a stopped actor left pending or running.

A worker runs actions and restores inside its tick, so any it finds still open
at the start of a tick were left by an earlier process or a tick that failed
part way. Test runs run in the background, so the worker passes their ids in
`in_flight` and those are left alone.
"""

from __future__ import annotations

import datetime

from actron_neo_actor.executor import Env, apply_with_retries, settled, shows_action, update
from actron_neo_actor.models import Run
from actron_neo_actor.restores import execute_restore, take
from actron_neo_actor.test_runs import finish_interrupted
from actron_neo_actor.view import OPEN, TickView

NOT_STARTED = "the actor restarted before the run"
CONFIRMED = "confirmed after a restart"
REAPPLIED = "applied after a restart"
NOT_CONFIRMED = "the actor restarted before the unit confirmed"
NOTHING_TO_RESTORE = "nothing left to restore"


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
        done = await update(env, run, "skipped", NOT_STARTED)
    else:
        done = await _carry_on(env, view, run)
    if done is not None:
        view.note(done)


async def _carry_on(env: Env, view: TickView, run: Run) -> Run | None:
    """A claimed action: done if the unit shows it, else applied again within the give-up time from its claim."""
    schedule = view.schedules.get(run.schedule_id)
    if schedule is None or run.snapshot is None:
        return await update(env, run, "failed", NOT_CONFIRMED)
    action, retry = schedule.action, schedule.retry
    if await shows_action(env, action):
        return await update(env, run, "ok", CONFIRMED)
    started = run.started_at or env.clock()
    if env.clock() >= started + datetime.timedelta(minutes=retry.give_up_minutes):
        confirmed = await settled(env, action)
        return await update(env, run, "ok" if confirmed else "failed", CONFIRMED if confirmed else NOT_CONFIRMED)
    attempt = await apply_with_retries(env, action, retry, started)
    if attempt.ok:
        return await update(env, run, "ok", REAPPLIED, attempts=attempt.attempts)
    return await update(env, run, "failed", attempt.failure(), attempts=attempt.attempts)


async def _resume_restore(env: Env, view: TickView, run: Run) -> None:
    parent, schedule = view.unfinished_run(run.parent_run_id), view.schedules.get(run.schedule_id)
    if parent is None or parent.outcome != "ok" or schedule is None:
        done = await update(env, run, "skipped", NOTHING_TO_RESTORE)
    elif await take(env, view, parent):
        done = await execute_restore(env, run, parent.snapshot, schedule.retry)
    else:
        return
    if done is not None:
        view.note(done)
