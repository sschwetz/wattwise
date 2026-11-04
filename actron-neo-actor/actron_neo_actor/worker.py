"""One actor instance: its device, its tick, and the work a device change sets off.

A tick runs, in order: sign in and sync the device, declare capabilities, read
schedules, context and runs, finish runs left open, start a pending test run,
run due action triggers, watch unfinished actions for owner changes, restore
those that are due, and record skipped restores for overridden actions. A
`restore_only` instance does only the steps that put the unit back.

Logs carry the instance id and exception type names only. Exception messages
can quote Core API bodies, and the instance config holds the refresh token.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Protocol

from actron_neo_actor.actions import price_above, run_schedule
from actron_neo_actor.async_core import AsyncCore
from actron_neo_actor.config import Settings
from actron_neo_actor.device import Device, DeviceAuthError, DeviceState
from actron_neo_actor.executor import Clock, Env, Sleep
from actron_neo_actor.models import Instance
from actron_neo_actor.restores import record_override_skips, run_restores, watch_for_override
from actron_neo_actor.resume import resume
from actron_neo_actor.schedule_eval import LOOKBACK
from actron_neo_actor.test_runs import TestControl, run_test, triage
from actron_neo_actor.view import STALE_AFTER, TickView

_LOG = logging.getLogger(__name__)

CHANGE_GAP_SECONDS = 2
# How long stop waits for a test run to restore and finish before it cancels it: the 60 second
# restore allowance plus a margin.
STOP_WAIT_SECONDS = 65


class DeviceFactory(Protocol):
    """Signs an instance in and builds its device. One per instance; it keeps the sign-in state between ticks."""

    async def sign_in(self, instance: Instance) -> bool: ...
    async def create(self, on_change: Callable[[DeviceState], None]) -> Device: ...
    def signed_out(self) -> None: ...
    async def close(self) -> None: ...


class Coalesced:
    """Runs `job` in the background when poked, one run at a time. A poke during a run makes it run once more."""

    def __init__(self, job: Callable[[], Awaitable[None]], on_error: Callable[[Exception], None]) -> None:
        self._job = job
        self._on_error = on_error
        self._again = False
        self._task: asyncio.Task[None] | None = None

    def poke(self) -> None:
        self._again = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while self._again:
            self._again = False
            try:
                await self._job()
            except Exception as err:
                self._on_error(err)

    async def wait(self) -> None:
        if self._task is not None:
            await self._task

    async def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


class InstanceWorker:
    def __init__(
        self,
        instance: Instance,
        device_factory: DeviceFactory,
        core: AsyncCore,
        settings: Settings,
        clock: Clock,
        sleep: Sleep,
    ) -> None:
        self._instance = instance
        self._factory = device_factory
        self._core = core
        self._settings = settings
        self._clock = clock
        self._sleep = sleep
        self._env: Env | None = None
        self._view: TickView | None = None
        self._tick_lock = asyncio.Lock()
        self._runs_lock = asyncio.Lock()
        self._price_above: dict[int, bool | None] = {}
        self._capabilities_sent: str | None = None
        self._latest: DeviceState | None = None
        self._readings_sent_at: datetime.datetime | None = None
        self._readings = Coalesced(self._send_readings, self._log_error("readings"))
        self._override_watch = Coalesced(self._watch, self._log_error("override check"))
        self._test: tuple[int, asyncio.Task[None], TestControl] | None = None

    @property
    def instance_id(self) -> int:
        return self._instance.instance_id

    # Tick

    async def tick(self, instance: Instance | None = None) -> None:
        """One pass over the instance. `instance` is the copy just read from discovery."""
        async with self._tick_lock:
            if instance is not None:
                self._instance = instance
            try:
                env = await self._connect()
                if env is not None:
                    await self._steps(env)
            except DeviceAuthError:
                _LOG.warning("Actron sign-in rejected (instance %s)", self.instance_id)
                await self._sign_out()
                return
            if self._env is not None and self._env.auth_lost:
                await self._sign_out()

    async def _steps(self, env: Env) -> None:
        restore_only = self._instance.restore_only
        if not restore_only:
            await self._declare(env)
        view = self._view = await self._read()
        await resume(env, view, self._tests_in_flight())
        if not restore_only:
            await self._start_test(env, view)
            if self._test is None:
                await self._run_due(env, view)
        await watch_for_override(env, view)
        await run_restores(env, view)
        await record_override_skips(env, view)
        self.pulse()

    async def _connect(self) -> Env | None:
        if not await self._factory.sign_in(self._instance):
            await self._drop_device()
            return None
        if self._env is None:
            device = await self._factory.create(self._on_change)
            await device.start_sync()
            self._env = Env(self.instance_id, self._core, device, self._clock, self._sleep, self._runs_lock)
        return self._env

    async def _declare(self, env: Env) -> None:
        capabilities = env.device.capabilities()
        dumped = json.dumps(capabilities, sort_keys=True)
        if dumped != self._capabilities_sent:
            await self._core.put_capabilities(self.instance_id, capabilities)
            self._capabilities_sent = dumped

    async def _read(self) -> TickView:
        """Recent runs reach back to the oldest unfinished action, so its open restore row is always seen."""
        now, instance_id = self._clock(), self.instance_id
        schedules = await self._core.get_schedules(instance_id)
        context = await self._core.get_context(instance_id)
        unfinished = await self._core.list_unfinished_runs(instance_id)
        since = min([now - LOOKBACK, *(r.trigger_at for r in unfinished)])
        recent = await self._core.list_recent_runs(instance_id, since)
        schedules_by_id = {s.id: s for s in schedules}
        return TickView(now, schedules_by_id, context, unfinished, recent, self._instance.restore_only)

    async def _run_due(self, env: Env, view: TickView) -> None:
        for schedule in view.schedules.values():
            await run_schedule(env, view, schedule, self._price_above.get(schedule.id))
            self._price_above[schedule.id] = price_above(schedule, view.context)

    # Test runs

    def _tests_in_flight(self) -> set[int]:
        if self._test is not None and self._test[1].done():
            self._test = None
        return set() if self._test is None else {self._test[0]}

    async def _start_test(self, env: Env, view: TickView) -> None:
        if self._tests_in_flight():
            return
        for run in await self._core.list_pending_test_runs(self.instance_id):
            schedule = await triage(env, view, run)
            if schedule is not None:
                control = TestControl(self._clock)
                job = run_test(env, run, schedule, self._settings.test_hold_seconds, control)
                self._test = (run.id, asyncio.create_task(self._guarded("test run", job)), control)
                return

    async def _guarded(self, what: str, job: Coroutine[Any, Any, None]) -> None:
        try:
            await job
        except Exception as err:
            self._log_error(what)(err)

    # Device changes and readings

    def _on_change(self, state: DeviceState) -> None:
        self._latest = state
        self._readings.poke()
        self._override_watch.poke()

    def pulse(self) -> None:
        """Send the readings if the last send is `heartbeat_seconds` old and the device state is fresh.

        The heartbeat keeps the instance's last_seen_at current, so it stops once the device
        state is older than STALE_AFTER or the device is dropped. Core API then shows the actor
        as not reporting."""
        latest, sent_at, now = self._latest, self._readings_sent_at, self._clock()
        heartbeat = datetime.timedelta(seconds=self._settings.heartbeat_seconds)
        if latest is None or now - latest.updated_at > STALE_AFTER:
            return
        if sent_at is None or now - sent_at >= heartbeat:
            self._readings.poke()

    async def _send_readings(self) -> None:
        if self._readings_sent_at is not None:
            wait = CHANGE_GAP_SECONDS - (self._clock() - self._readings_sent_at).total_seconds()
            if wait > 0:
                await self._sleep(wait)
        if self._latest is not None:
            await self._core.put_readings(self.instance_id, self._latest.to_payload())
            self._readings_sent_at = self._clock()

    async def _watch(self) -> None:
        if self._env is not None and self._view is not None:
            await watch_for_override(self._env, self._view)

    # Lifecycle

    async def idle(self) -> None:
        """Wait for background work started so far: readings, override checks and a test run."""
        await self._readings.wait()
        await self._override_watch.wait()
        if self._test is not None:
            await self._test[1]
        await self._readings.wait()  # the test run's commands change the state, which sends readings again

    async def stop(self) -> None:
        """Stop background work and sync, and cancel a pending sign-in. Call it when the instance is dropped.

        A test run whose apply has started is asked to finish (see test_runs.TestControl) and gets
        STOP_WAIT_SECONDS before it is cancelled; one that has not sent a command yet is cancelled."""
        if self._test is not None:
            await self._stop_test(*self._test[1:])
            self._test = None
        await self._readings.cancel()
        await self._override_watch.cancel()
        await self._drop_device()
        await self._factory.close()

    async def _stop_test(self, task: asyncio.Task[None], control: TestControl) -> None:
        if control.request_stop():
            timer = asyncio.ensure_future(self._sleep(STOP_WAIT_SECONDS))
            try:
                await asyncio.wait({task, timer}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                timer.cancel()
                await asyncio.gather(timer, return_exceptions=True)
            if not task.done():
                _LOG.warning("Test run did not finish before the stop (instance %s)", self.instance_id)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _sign_out(self) -> None:
        self._factory.signed_out()
        await self._drop_device()

    async def _drop_device(self) -> None:
        env, self._env, self._view, self._latest = self._env, None, None, None
        if env is not None:
            await env.device.stop_sync()

    def _log_error(self, what: str) -> Callable[[Exception], None]:
        def log(err: Exception) -> None:
            _LOG.error("%s failed (instance %s): %s", what, self.instance_id, type(err).__name__)

        return log
