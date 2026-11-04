"""Builders for worker and runner tests: one site with a 14:00 to 16:00 peak window,
a FakeCore, and a real ActronDevice over FakeActronApi sharing one FakeClock."""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from actron_neo_actor.async_core import AsyncCore
from actron_neo_actor.config import Settings
from actron_neo_actor.device import ActronDevice, Device, DeviceState
from actron_neo_actor.models import (
    ActionSpec,
    Condition,
    Context,
    Instance,
    RestoreSpec,
    RetrySpec,
    Schedule,
    Trigger,
    WindowOccurrence,
)
from actron_neo_actor.worker import InstanceWorker
from tests.fake_core import FakeCore
from tests.fakes import SERIAL, START, FakeActronApi, FakeClock

# The device's background sync sleeps this long; the fake sleep blocks on it so
# sync never runs on its own and moves the shared clock.
NO_SYNC_SECONDS = 1_000_000
SETTINGS = Settings(
    core_api_url="http://core",
    actor_key="k",
    tick_seconds=30,
    poll_fallback_seconds=NO_SYNC_SECONDS,
    reconcile_seconds=NO_SYNC_SECONDS,
    heartbeat_seconds=60,
    test_hold_seconds=30,
)
TZ = "Australia/Brisbane"
WINDOW_ID = 7
WINDOW_START = START  # 14:00 in Brisbane
WINDOW_END = START + datetime.timedelta(hours=2)
CREATED = START - datetime.timedelta(hours=1)


def instance(instance_id: int = 1, *, restore_only: bool = False, token: str = "REFRESH-TOKEN") -> Instance:
    return Instance(instance_id, 10, "House", TZ, restore_only, {"refresh_token": token})


def restore_spec(
    enabled: bool = True,
    when: str = "window_end",
    only_if: list[Condition] | None = None,
    early_if: list[Condition] | None = None,
) -> RestoreSpec:
    return RestoreSpec(enabled, when, None, None, only_if or [], early_if or [])


def schedule(
    schedule_id: int = 1,
    *,
    instance_id: int = 1,
    trigger: Trigger | None = None,
    action: ActionSpec | None = None,
    conditions: list[Condition] | None = None,
    restore: RestoreSpec | None = None,
    retry: RetrySpec | None = None,
) -> Schedule:
    return Schedule(
        id=schedule_id,
        instance_id=instance_id,
        name=f"Schedule {schedule_id}",
        enabled=True,
        notify_email=False,
        created_at=CREATED,
        trigger=trigger or Trigger("window_start", window_id=WINDOW_ID),
        conditions=conditions or [],
        action=action or ActionSpec("system_off"),
        restore=restore or restore_spec(),
        retry=retry or RetrySpec(3, 10, 5),
    )


def context() -> Context:
    window = WindowOccurrence(WINDOW_ID, "Peak", WINDOW_START, WINDOW_END)
    return Context(START, TZ, 20.0, 50.0, 30.0, [window])


def device_sleep(clock: FakeClock) -> Callable[[float], Any]:
    async def sleep(seconds: float) -> None:
        if seconds >= NO_SYNC_SECONDS:
            await asyncio.Event().wait()
        await clock.sleep(seconds)

    return sleep


@dataclass
class FakeFactory:
    """Stands in for ActronConnector: signs in when `signed_in` is set and builds a real ActronDevice."""

    api: FakeActronApi
    clock: FakeClock
    signed_in: bool = True
    create_error: Exception | None = None
    signed_out_calls: int = 0
    closed: bool = False
    devices: list[ActronDevice] = field(default_factory=list)

    async def sign_in(self, instance: Instance) -> bool:
        return self.signed_in

    async def create(self, on_change: Callable[[DeviceState], None]) -> Device:
        if self.create_error is not None:
            raise self.create_error
        device = ActronDevice(
            self.api, SERIAL, SETTINGS, on_change=on_change, clock=self.clock.now, sleep=device_sleep(self.clock)
        )  # type: ignore[arg-type]
        self.devices.append(device)
        return device

    def signed_out(self) -> None:
        self.signed_out_calls += 1

    async def close(self) -> None:
        self.closed = True


@dataclass
class Harness:
    clock: FakeClock
    api: FakeActronApi
    core: FakeCore
    factory: FakeFactory
    worker: InstanceWorker
    instance: Instance

    async def tick(self) -> None:
        await self.worker.tick(self.instance)
        await self.worker.idle()

    async def tick_at(self, at: datetime.datetime) -> None:
        self.clock.current = at
        await self.tick()


def harness(
    schedules: list[Schedule] | None = None,
    *,
    api: FakeActronApi | None = None,
    inst: Instance | None = None,
    settings: Settings = SETTINGS,
) -> Harness:
    clock = FakeClock()
    api = api or FakeActronApi()
    api.push_on_command = True
    core = FakeCore(clock.now)
    inst = inst or instance()
    core.instances = [inst]
    core.schedules[inst.instance_id] = schedules if schedules is not None else [schedule()]
    core.contexts[inst.instance_id] = context()
    factory = FakeFactory(api, clock)
    worker = InstanceWorker(inst, factory, AsyncCore(core), settings, clock.now, clock.sleep)  # type: ignore[arg-type]
    return Harness(clock, api, core, factory, worker, inst)


def power_commands(api: FakeActronApi) -> list[Any]:
    """The modes sent with set_system_mode, in order ("OFF", "COOL", ...)."""
    out = []
    for command in api.commands:
        if "UserAirconSettings.isOn" in command:
            out.append(command.get("UserAirconSettings.Mode", "OFF") if command["UserAirconSettings.isOn"] else "OFF")
    return out
