"""Test runs from the web app's "Test now": apply the schedule's action whatever its trigger
and conditions say, hold it for `test_hold_seconds`, then restore."""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import Awaitable, Callable

from actron_neo_actor.actions import OTHER_ACTIVE, claim_and_apply, finish
from actron_neo_actor.device import CONFIRM_SECONDS, RestoreResult
from actron_neo_actor.executor import Attempt, Env, current_state_restore, update, with_retries
from actron_neo_actor.models import Run, Schedule
from actron_neo_actor.unit_checks import restore_message
from actron_neo_actor.view import TickView

INTERRUPTED = "the actor restarted during the test"
# When the worker stops during the hold, the restore gets this long in all: a try starts only if
# its confirm window can end inside it.
STOP_RESTORE_SECONDS = 60


class Hold:
    """The wait between a test run's action and its restore. `cut` ends it early when the worker stops."""

    def __init__(self) -> None:
        self._cut = asyncio.Event()
        self.active = False

    async def wait(self, sleep: Callable[[float], Awaitable[None]], seconds: float) -> bool:
        """True when the hold was cut short."""
        self.active = True
        sleeper = asyncio.ensure_future(sleep(seconds))
        cutter = asyncio.ensure_future(self._cut.wait())
        try:
            await asyncio.wait({sleeper, cutter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.active = False
            for task in (sleeper, cutter):
                task.cancel()
            await asyncio.gather(sleeper, cutter, return_exceptions=True)
        return self._cut.is_set()

    def cut(self) -> bool:
        """End the hold now. False when the test is not in its hold."""
        if not self.active:
            return False
        self._cut.set()
        return True


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


async def run_test(env: Env, run: Run, schedule: Schedule, hold_seconds: int, hold: Hold) -> None:
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
    stopped = await hold.wait(env.sleep, hold_seconds)
    latest = env.clock() + datetime.timedelta(seconds=STOP_RESTORE_SECONDS - CONFIRM_SECONDS) if stopped else None
    restored = await _restore(env, schedule, claimed.snapshot, latest)
    message = _outcome_message("applied, then", "applied, but the restore", restored)
    if stopped:
        message += " early because the actor stopped" if restored.ok else " (the actor stopped during the hold)"
    outcome = "ok" if restored.ok else "failed"
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


async def _restore(
    env: Env, schedule: Schedule, snapshot: dict | None, latest_start: datetime.datetime | None = None
) -> Attempt[RestoreResult]:
    op = current_state_restore(env, snapshot)
    return await with_retries(env, schedule.retry, op, latest_start=latest_start)


def _outcome_message(done: str, failed: str, restored: Attempt[RestoreResult]) -> str:
    if restored.ok and restored.value is not None:
        return f"{done} {restore_message(restored.value)}"
    return f"{failed} {restored.failure()}"
