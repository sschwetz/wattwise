"""The Actron Neo unit: a synced copy of its state, and commands that wait for the state to confirm them.

Sync: `start_sync` asks the library for push updates. While push is down (start
failed, or the transport reported a disconnect) the actor polls every
`poll_fallback_seconds` and retries push after 30, 60, 120, 240, then every 300
seconds. While push works it still polls every `reconcile_seconds` to catch
missed messages.

Nothing from the library's raw status is logged or kept; see device_state.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from actron_neo_api import ActronAirAPI, ActronAirAPIError, ActronAirAuthError, ActronAirStatus
from actron_neo_api.const import AC_MODE_OFF
from actron_neo_api.rt import RealtimeConnectionEvent, RealtimeConnectionState

from actron_neo_actor.capabilities import build_capabilities
from actron_neo_actor.config import Settings
from actron_neo_actor.device_snapshot import (
    Command,
    DeviceAuthError,
    DeviceError,
    RestoreResult,
    check_action,
    check_zones,
    plan_restore,
)
from actron_neo_actor.device_snapshot import matches_action as _matches_action
from actron_neo_actor.device_snapshot import snapshot_for as _snapshot_for
from actron_neo_actor.device_state import DeviceState, clamp_setpoint, state_from_status, titled_zones

__all__ = ["ActronDevice", "Device", "DeviceAuthError", "DeviceError", "DeviceState", "RestoreResult"]

_LOG = logging.getLogger(__name__)

CONFIRM_SECONDS = 45
CONFIRM_STEP_SECONDS = 3
# With push up but silent this long after a command, poll instead of waiting for it.
PUSH_QUIET_SECONDS = 10
PUSH_RETRY_MAX_SECONDS = 300

_PUSH_STOPPED = frozenset({RealtimeConnectionState.DISCONNECTED, RealtimeConnectionState.ERROR})


class Device(Protocol):
    def state(self) -> DeviceState | None: ...
    async def refresh(self) -> DeviceState: ...
    def capabilities(self) -> dict: ...
    def snapshot_for(self, action_id: str, params: dict) -> dict: ...
    async def apply(self, action_id: str, params: dict) -> None: ...
    async def restore(self, snapshot: dict, current: DeviceState) -> RestoreResult: ...
    def matches_action(self, action_id: str, params: dict, state: DeviceState) -> bool: ...
    async def start_sync(self) -> None: ...
    async def stop_sync(self) -> None: ...


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@dataclass
class _Push:
    started: bool = False  # start_push returned True and stop_push has not run since
    connected: bool = False
    stopped: bool = False  # the transport reported it gave up; stop and start again
    retry_at: datetime.datetime | None = None
    delay_seconds: float = 0
    last_message_at: datetime.datetime | None = None

    @property
    def healthy(self) -> bool:
        return self.started and self.connected and not self.stopped


async def _switch_zones(status: ActronAirStatus, params: dict, state: DeviceState) -> bool:
    """Command only the zones that differ. False when every zone is already as asked."""
    names, enabled = list(params["zones"]), bool(params["enabled"])
    check_zones(names, state)
    index = {title: i for i, title in titled_zones(status)}
    differ = [name for name in names if state.enabled_zones[name] != enabled]
    for name in differ:
        await status.remote_zone_info[index[name]].enable(enabled)
    return bool(differ)


async def _dispatch(status: ActronAirStatus, action_id: str, params: dict, state: DeviceState) -> bool:
    """Send the command. False when nothing needed sending."""
    if action_id == "set_zones":
        return await _switch_zones(status, params, state)
    settings = status.user_aircon_settings
    if action_id == "system_off":
        await settings.set_system_mode(AC_MODE_OFF)
    elif action_id in ("system_on", "set_mode"):
        await settings.set_system_mode(str(params["mode"]))
    elif action_id == "set_temperature":
        await settings.set_temperature(clamp_setpoint(state, float(params["temperature_c"])))
    else:
        await settings.set_fan_mode(str(params["fan_mode"]))
    return True


class ActronDevice:
    def __init__(
        self,
        api: ActronAirAPI,
        serial: str,
        settings: Settings,
        *,
        on_change: Callable[[DeviceState], None] | None = None,
        clock: Callable[[], datetime.datetime] = _utcnow,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api = api
        self._serial = serial.strip().lower()
        self._settings = settings
        self._on_change = on_change
        self._clock = clock
        self._sleep = sleep
        self._state: DeviceState | None = None
        # Counts the states stored, so a confirm can tell a state seen after a send from the cached one.
        self._stores = 0
        self._push = _Push(delay_seconds=settings.poll_fallback_seconds)
        self._last_poll_at = clock()
        self._task: asyncio.Task[None] | None = None
        self._unsubscribers: list[Callable[[], None]] = []

    # Reading state

    def state(self) -> DeviceState | None:
        return self._state

    def capabilities(self) -> dict:
        return build_capabilities(self._status())

    def matches_action(self, action_id: str, params: dict, state: DeviceState) -> bool:
        return _matches_action(action_id, params, state)

    def snapshot_for(self, action_id: str, params: dict) -> dict:
        return _snapshot_for(action_id, params, self._require_state())

    async def refresh(self) -> DeviceState:
        """Poll the cloud once and return the new state. Raises DeviceError."""
        try:
            results = await self._api.update_status(self._serial)
        except ActronAirAuthError as err:
            raise DeviceAuthError("status refresh: sign-in rejected") from err
        except (ActronAirAPIError, OSError) as err:
            raise DeviceError(f"status refresh failed: {type(err).__name__}") from err
        finally:
            self._last_poll_at = self._clock()
        status = results.get(self._serial) or self._api.state_manager.get_status(self._serial)
        if status is None:
            raise DeviceError("status refresh returned no status for this unit")
        return self._store(status)

    # Commands

    async def apply(self, action_id: str, params: dict) -> None:
        """Send the action and wait up to 45 seconds for the synced state to show it."""
        check_action(action_id)
        await self._run([(action_id, params)])

    async def restore(self, snapshot: dict, current: DeviceState) -> RestoreResult:
        """Put back what `snapshot_for` saved, leaving alone each field the owner has changed since."""
        plan = plan_restore(snapshot, current)
        if plan.commands:
            await self._run(plan.commands)
        return RestoreResult(restored=plan.restored, skipped=plan.skipped)

    async def _run(self, commands: list[Command]) -> None:
        """Send the commands, then wait for a state seen after sending that shows them all.
        A cached state from before the send never confirms. When nothing needed sending
        the unit is already as asked."""
        sent_at, stores_before = self._clock(), self._stores
        sent = [await self._send(action_id, params) for action_id, params in commands]
        if any(sent):
            await self._confirm(commands, sent_at, stores_before)

    async def _send(self, action_id: str, params: dict) -> bool:
        status, state = self._status(), self._require_state()
        try:
            return await _dispatch(status, action_id, params, state)
        except ActronAirAuthError as err:
            raise DeviceAuthError(f"{action_id}: sign-in rejected") from err
        except (ActronAirAPIError, OSError) as err:
            raise DeviceError(f"{action_id} failed: {type(err).__name__}") from err
        except KeyError as err:
            raise DeviceError(f"{action_id}: missing parameter {err}") from err
        except ValueError as err:
            # The library's own ValueErrors say what was wrong ("Cannot set temperature in FAN mode");
            # subclasses such as pydantic's ValidationError can quote input values, so only the type is kept.
            detail = str(err) if type(err) is ValueError else type(err).__name__
            raise DeviceError(f"{action_id} failed: {detail}") from err

    async def _confirm(self, commands: list[Command], sent_at: datetime.datetime, stores_before: int) -> None:
        deadline = sent_at + datetime.timedelta(seconds=CONFIRM_SECONDS)
        while not self._confirmed(commands, sent_at, stores_before):
            if self._clock() >= deadline:
                raise DeviceError("not confirmed")
            await self._sleep(CONFIRM_STEP_SECONDS)
            if self._push_quiet_since(sent_at):
                await self._poll_quietly()

    def _confirmed(self, commands: list[Command], sent_at: datetime.datetime, stores_before: int) -> bool:
        """True once a state stored after the send started, at or after `sent_at`, shows every command."""
        state = self._state
        if state is None or self._stores == stores_before or state.updated_at < sent_at:
            return False
        return all(_matches_action(a, p, state) for a, p in commands)

    def _push_quiet_since(self, sent_at: datetime.datetime) -> bool:
        if not self._push.healthy:
            return True
        last = max(sent_at, self._push.last_message_at or sent_at)
        return (self._clock() - last).total_seconds() >= PUSH_QUIET_SECONDS

    # Sync

    async def start_sync(self) -> None:
        """Poll once (raising DeviceError on failure), start push, then keep the state synced in the background."""
        await self.refresh()
        self._unsubscribers = [
            self._api.subscribe_system_updates(self._serial, self._on_push),
            self._api.subscribe_connection_state(self._on_connection),
        ]
        await self._try_push()
        self._task = asyncio.create_task(self._sync_loop())

    async def stop_sync(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers = []
        if self._push.started:
            await self._stop_push()

    async def _sync_loop(self) -> None:
        while True:
            await self._sleep(self._settings.poll_fallback_seconds)
            try:
                await self._sync_tick()
            except Exception as err:  # one bad tick must not end sync
                _LOG.error("device sync step failed: %s", type(err).__name__)

    async def _sync_tick(self) -> None:
        if self._push.started and self._push.stopped:
            await self._stop_push()
            self._push.retry_at = self._clock()
        if self._push.healthy:
            if (self._clock() - self._last_poll_at).total_seconds() >= self._settings.reconcile_seconds:
                await self._poll_quietly()
            return
        await self._poll_quietly()
        if not self._push.started and (self._push.retry_at is None or self._clock() >= self._push.retry_at):
            await self._try_push()

    async def _try_push(self) -> None:
        try:
            started = await self._api.start_push([self._serial])
        except ActronAirAuthError:
            _LOG.warning("push not started: sign-in rejected")
            started = False
        except (ActronAirAPIError, OSError) as err:
            _LOG.warning("push not started: %s", type(err).__name__)
            started = False
        if started:
            self._push = _Push(started=True, connected=True, delay_seconds=self._settings.poll_fallback_seconds)
            return
        self._push.retry_at = self._clock() + datetime.timedelta(seconds=self._push.delay_seconds)
        self._push.delay_seconds = min(self._push.delay_seconds * 2, PUSH_RETRY_MAX_SECONDS)

    async def _stop_push(self) -> None:
        try:
            await self._api.stop_push()
        except Exception as err:  # the transport is being discarded either way
            _LOG.warning("stopping push failed: %s", type(err).__name__)
        self._push.started = self._push.connected = self._push.stopped = False

    async def _poll_quietly(self) -> None:
        try:
            await self.refresh()
        except DeviceError as err:
            _LOG.warning("%s", err)

    def _on_push(self, status: ActronAirStatus) -> None:
        self._push.last_message_at = self._clock()
        self._store(status)

    def _on_connection(self, event: RealtimeConnectionEvent) -> None:
        if not self._push.started:
            return
        self._push.connected = event.state == RealtimeConnectionState.CONNECTED
        if event.state in _PUSH_STOPPED:
            self._push.stopped = True

    # Helpers

    def _store(self, status: ActronAirStatus) -> DeviceState:
        new = state_from_status(status, self._clock())
        changed = new != self._state
        self._state = new
        self._stores += 1
        if changed and self._on_change is not None:
            try:
                self._on_change(new)
            except Exception as err:  # its text or traceback could quote a response body
                _LOG.error("device change handler failed: %s", type(err).__name__)
        return new

    def _status(self) -> ActronAirStatus:
        status = self._api.state_manager.get_status(self._serial)
        if status is None:
            raise DeviceError("no status for this unit yet")
        return status

    def _require_state(self) -> DeviceState:
        if self._state is None:
            raise DeviceError("no device state yet")
        return self._state
