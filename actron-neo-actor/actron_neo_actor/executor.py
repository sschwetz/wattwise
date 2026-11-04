"""Run rows and device commands with retries.

A 409 from Core API means another tick or process already has the run, so the
helpers return None and the caller moves on. Failure messages are short and
plain: a DeviceError's own message (device.py keeps those free of tokens and
raw status) or an exception type name, never a library exception's text.
"""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from actron_neo_actor.async_core import AsyncCore
from actron_neo_actor.core_api_client import CoreApiConflict
from actron_neo_actor.device import Device, DeviceAuthError, DeviceError, RestoreResult
from actron_neo_actor.models import RetrySpec, Run

Clock = Callable[[], datetime.datetime]
Sleep = Callable[[float], Awaitable[None]]


@dataclass
class Env:
    """What run execution needs for one instance. `auth_lost` is set when the Actron cloud rejects the sign-in."""

    instance_id: int
    core: AsyncCore
    device: Device
    clock: Clock
    sleep: Sleep
    runs_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    auth_lost: bool = False


@dataclass(frozen=True)
class Attempt[T]:
    attempts: int
    value: T | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def failure(self) -> str:
        if self.attempts == 0:  # nothing was sent: the run could not start
            return str(self.error)
        plural = "" if self.attempts == 1 else "s"
        return f"failed after {self.attempts} attempt{plural}: {self.error}"


async def create(env: Env, schedule_id: int, kind: str, at: datetime.datetime, parent: int | None = None) -> Run | None:
    try:
        return await env.core.create_run(env.instance_id, schedule_id, kind, at, parent)
    except CoreApiConflict:
        return None


async def update(
    env: Env,
    run: Run,
    outcome: str,
    message: str | None = None,
    *,
    attempts: int | None = None,
    snapshot: dict | None = None,
) -> Run | None:
    try:
        return await env.core.update_run(env.instance_id, run.id, outcome, message, attempts, snapshot)
    except CoreApiConflict:
        return None


async def record(
    env: Env, schedule_id: int, kind: str, at: datetime.datetime, message: str, parent: int | None = None
) -> Run | None:
    """A row that is finished `skipped` as soon as it exists."""
    run = await create(env, schedule_id, kind, at, parent)
    return None if run is None else await update(env, run, "skipped", message)


async def with_retries[T](env: Env, retry: RetrySpec, op: Callable[[], Awaitable[T]]) -> Attempt[T]:
    """Try `op` up to `retry.max_attempts` times, `interval_seconds` apart, giving up once the next
    try would start more than `give_up_minutes` after the first. A rejected sign-in ends at once."""
    deadline = env.clock() + datetime.timedelta(minutes=retry.give_up_minutes)
    attempts = 0
    while True:
        attempts += 1
        try:
            return Attempt(attempts, value=await op())
        except DeviceAuthError as err:
            env.auth_lost = True
            return Attempt(attempts, error=str(err))
        except DeviceError as err:
            error = str(err)
        except Exception as err:  # a bug or library surprise; its text may quote raw values
            error = type(err).__name__
        next_try = env.clock() + datetime.timedelta(seconds=retry.interval_seconds)
        if attempts >= max(retry.max_attempts, 1) or next_try > deadline:
            return Attempt(attempts, error=error)
        await env.sleep(retry.interval_seconds)


def current_state_restore(env: Env, snapshot: dict | None) -> Callable[[], Awaitable[RestoreResult]]:
    """An op that restores `snapshot` against the state synced at the moment it runs."""

    async def op() -> RestoreResult:
        state = env.device.state()
        if snapshot is None:
            raise DeviceError("no snapshot to restore")
        if state is None:
            raise DeviceError("no device state yet")
        return await env.device.restore(snapshot, state)

    return op
