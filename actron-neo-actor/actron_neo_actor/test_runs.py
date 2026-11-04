"""Test runs from the web app's "Test now": apply the schedule's action whatever its trigger
and conditions say, hold it for `test_hold_seconds`, then restore."""

from __future__ import annotations

from actron_neo_actor.actions import OTHER_ACTIVE, claim_and_apply, finish
from actron_neo_actor.device import RestoreResult
from actron_neo_actor.executor import Attempt, Env, current_state_restore, update, with_retries
from actron_neo_actor.models import Run, Schedule
from actron_neo_actor.unit_checks import restore_message
from actron_neo_actor.view import TickView

INTERRUPTED = "the actor restarted during the test"


async def triage(env: Env, view: TickView, run: Run) -> Schedule | None:
    """The schedule to test, or None after finishing the run because it cannot go ahead.

    While an action holds the unit, a test would save the actor's own change as
    the state to put back, so it is skipped like an overlapping schedule."""
    schedule = view.schedules.get(run.schedule_id)
    if view.unfinished:
        await update(env, run, "skipped", OTHER_ACTIVE)
    elif schedule is None:
        await update(env, run, "failed", "schedule is not active")
    else:
        return schedule
    return None


async def run_test(env: Env, run: Run, schedule: Schedule, hold_seconds: int) -> None:
    applied = await claim_and_apply(env, run, schedule.action, schedule.retry)
    if applied is None:
        return
    claimed, attempt = applied
    if not attempt.ok:
        await finish(env, claimed, attempt)
        return
    if not schedule.restore.enabled:
        await finish(env, claimed, attempt, "applied; restore is off for this schedule")
        return
    await env.sleep(hold_seconds)
    restored = await _restore(env, schedule, claimed.snapshot)
    outcome = "ok" if restored.ok else "failed"
    message = _outcome_message("applied, then", "applied, but the restore", restored)
    await update(env, claimed, outcome, message, attempts=attempt.attempts + restored.attempts)


async def finish_interrupted(env: Env, view: TickView, run: Run) -> None:
    """A test left running by a stopped actor may have left the unit changed: put it back."""
    schedule = view.schedules.get(run.schedule_id)
    if schedule is None or not schedule.restore.enabled or run.snapshot is None:
        await update(env, run, "failed", INTERRUPTED)
        return
    restored = await _restore(env, schedule, run.snapshot)
    message = _outcome_message(f"{INTERRUPTED}, then", f"{INTERRUPTED}, and the restore", restored)
    await update(env, run, "failed", message, attempts=restored.attempts)


async def _restore(env: Env, schedule: Schedule, snapshot: dict | None) -> Attempt[RestoreResult]:
    return await with_retries(env, schedule.retry, current_state_restore(env, snapshot))


def _outcome_message(done: str, failed: str, restored: Attempt[RestoreResult]) -> str:
    if restored.ok and restored.value is not None:
        return f"{done} {restore_message(restored.value)}"
    return f"{failed} {restored.failure()}"
