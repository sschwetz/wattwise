"""Test runs from the web app's "Test now": apply the schedule's action whatever its trigger
and conditions say, hold it for `test_hold_seconds`, then restore."""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import Awaitable, Callable

from actron_neo_actor.actions import OTHER_ACTIVE, claim_and_apply, finish
from actron_neo_actor.device import CONFIRM_SECONDS, RestoreResult
from actron_neo_actor.executor import Attempt, Clock, Cutoff, Env, current_state_restore, update, with_retries
from actron_neo_actor.models import Run, Schedule
from actron_neo_actor.unit_checks import restore_message
from actron_neo_actor.view import TickView

INTERRUPTED = "the actor restarted during the test"
RESTORE_OFF = "applied; restore is off for this schedule"
EXPIRED = "expired"
# A test run that waited longer than this (the actor was down, or the site or
# instance was disabled) is not run: the owner is no longer watching for it.
TEST_EXPIRY = datetime.timedelta(minutes=10)
# After a stop, the restore gets this long from the stop: a try starts only if its confirm window
# can end inside it.
STOP_RESTORE_SECONDS = 60


class TestControl:
    """How the worker stops a test run.

    Before the first command is sent, `request_stop` returns False and the worker
    cancels the run, which resume picks up later. Once the apply has started it
    returns True and the run finishes on its own: an apply in progress may confirm,
    the hold ends at once, and no try of the apply or restore starts later than
    STOP_RESTORE_SECONDS minus the confirm window after the stop.
    """

    __test__ = False  # not a pytest class

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._stop = asyncio.Event()
        self._stopped_at: datetime.datetime | None = None
        self.committed = False

    def commit(self) -> None:
        self.committed = True

    @property
    def stopping(self) -> bool:
        return self._stopped_at is not None

    def request_stop(self) -> bool:
        if not self.committed:
            return False
        if self._stopped_at is None:
            self._stopped_at = self._clock()
            self._stop.set()
        return True

    def latest_start(self) -> datetime.datetime | None:
        if self._stopped_at is None:
            return None
        return self._stopped_at + datetime.timedelta(seconds=STOP_RESTORE_SECONDS - CONFIRM_SECONDS)

    async def hold(self, sleep: Callable[[float], Awaitable[None]], seconds: float) -> bool:
        """Wait out the hold. True when a stop cut it short."""
        sleeper = asyncio.ensure_future(sleep(seconds))
        stopper = asyncio.ensure_future(self._stop.wait())
        try:
            await asyncio.wait({sleeper, stopper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sleeper, stopper):
                task.cancel()
            await asyncio.gather(sleeper, stopper, return_exceptions=True)
        return self.stopping


async def triage(env: Env, view: TickView, run: Run) -> Schedule | None:
    """The schedule to test, or None after finishing the run because it cannot go ahead.

    While an action holds the unit, a test would save the actor's own change as
    the state to put back, so it is skipped like an overlapping schedule."""
    schedule = view.schedules.get(run.schedule_id)
    if run.outcome == "pending" and env.clock() - run.trigger_at > TEST_EXPIRY:
        await update(env, run, "skipped", EXPIRED)
    elif view.unfinished:
        await update(env, run, "skipped", OTHER_ACTIVE)
    elif schedule is None:
        await update(env, run, "failed", "schedule is not active")
    else:
        return schedule
    return None


async def run_test(env: Env, run: Run, schedule: Schedule, hold_seconds: int, control: TestControl) -> None:
    applied = await claim_and_apply(env, run, schedule.action, schedule.retry, control.commit, control.latest_start)
    if applied is None:
        return
    claimed, attempt = applied
    # After a stop the restore runs even when the apply failed, in case the command lands late.
    if not schedule.restore.enabled or not (attempt.ok or control.stopping):
        await finish(env, claimed, attempt, RESTORE_OFF)
        return
    cut = await control.hold(env.sleep, hold_seconds) if attempt.ok else False
    restored = await _restore(env, schedule, claimed.snapshot, control.latest_start)
    outcome = "ok" if attempt.ok and restored.ok else "failed"
    message = _test_message(attempt, restored, cut, control.stopping)
    await update(env, claimed, outcome, message, attempts=attempt.attempts + restored.attempts)


def _test_message(attempt: Attempt, restored: Attempt[RestoreResult], cut: bool, stopping: bool) -> str:
    if not attempt.ok:
        lead = f"{attempt.failure()}; the actor stopped"
        return _outcome_message(f"{lead}, then", f"{lead}, and the restore", restored)
    message = _outcome_message("applied, then", "applied, but the restore", restored)
    if restored.ok and cut:
        return f"{message} early because the actor stopped"
    if not restored.ok and stopping:
        return f"{message} (the actor stopped during the test)"
    return message


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
    env: Env, schedule: Schedule, snapshot: dict | None, latest_start: Cutoff | None = None
) -> Attempt[RestoreResult]:
    op = current_state_restore(env, snapshot)
    return await with_retries(env, schedule.retry, op, latest_start=latest_start)


def _outcome_message(done: str, failed: str, restored: Attempt[RestoreResult]) -> str:
    if restored.ok and restored.value is not None:
        return f"{done} {restore_message(restored.value)}"
    return f"{failed} {restored.failure()}"
