"""Discovery loop and worker registry.

Every `tick_seconds` the runner lists the instances, stops the workers of
instances that are gone, and starts a tick for each listed instance whose last
tick has finished, so one worker never runs two ticks at once and a slow
instance never holds up the others. A failing tick is logged with the instance
id and the exception type name only.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
from collections.abc import Callable

from actron_neo_actor.alerts import InstanceAlerts
from actron_neo_actor.async_core import AsyncCore
from actron_neo_actor.config import Settings
from actron_neo_actor.connector import ActronConnector
from actron_neo_actor.core_api_client import CoreApiClient
from actron_neo_actor.executor import Clock, Sleep
from actron_neo_actor.models import Instance
from actron_neo_actor.worker import DeviceFactory, InstanceWorker

_LOG = logging.getLogger(__name__)

TICK_ALERT = "tick"


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class Runner:
    def __init__(
        self,
        core: AsyncCore,
        settings: Settings,
        factory_for: Callable[[Instance], DeviceFactory],
        clock: Clock = _utcnow,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._core = core
        self._settings = settings
        self._factory_for = factory_for
        self._clock = clock
        self._sleep = sleep
        self._alerts = InstanceAlerts(core)
        self._workers: dict[int, InstanceWorker] = {}
        self._ticks: dict[int, asyncio.Task[None]] = {}

    async def round(self) -> None:
        try:
            instances = await self._core.list_instances()
        except Exception as err:
            _LOG.error("Discovery failed: %s", type(err).__name__)
            return
        await self._drop_missing({i.instance_id for i in instances})
        for instance in instances:
            self._start_tick(instance)

    def _start_tick(self, instance: Instance) -> None:
        worker = self._workers.get(instance.instance_id)
        if worker is None:
            factory = self._factory_for(instance)
            worker = InstanceWorker(instance, factory, self._core, self._settings, self._clock, self._sleep)
            self._workers[instance.instance_id] = worker
        running = self._ticks.get(instance.instance_id)
        if running is None or running.done():
            self._ticks[instance.instance_id] = asyncio.create_task(self._guarded_tick(worker, instance))
        worker.pulse()

    async def _guarded_tick(self, worker: InstanceWorker, instance: Instance) -> None:
        try:
            await worker.tick(instance)
        except Exception as err:
            _LOG.error("Tick failed (instance %s): %s", instance.instance_id, type(err).__name__)
            message = "WattWise cannot reach the air conditioner through Actron. Schedules may not run."
            await self._alerts.failure(instance.instance_id, TICK_ALERT, message)
        else:
            await self._alerts.ok(instance.instance_id, TICK_ALERT)

    async def _drop_missing(self, keep: set[int]) -> None:
        for instance_id in [i for i in self._workers if i not in keep]:
            await self._drop(instance_id)

    async def _drop(self, instance_id: int) -> None:
        tick = self._ticks.pop(instance_id, None)
        if tick is not None:
            tick.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tick
        worker = self._workers.pop(instance_id)
        try:
            await worker.stop()
        except Exception as err:
            _LOG.error("Stopping a worker failed (instance %s): %s", instance_id, type(err).__name__)

    async def settle(self) -> None:
        """Wait for the ticks started so far and the work they set off."""
        await asyncio.gather(*self._ticks.values())
        for worker in list(self._workers.values()):
            await worker.idle()

    async def stop(self) -> None:
        for instance_id in list(self._workers):
            await self._drop(instance_id)


def production_runner(settings: Settings) -> Runner:
    core = AsyncCore(CoreApiClient(settings.core_api_url, settings.actor_key))
    return Runner(core, settings, lambda _instance: ActronConnector(core, settings))


async def run_forever(settings: Settings, runner: Runner | None = None, sleep: Sleep = asyncio.sleep) -> None:
    runner = runner or production_runner(settings)
    try:
        while True:
            await runner.round()
            await sleep(settings.tick_seconds)
    finally:
        await runner.stop()
