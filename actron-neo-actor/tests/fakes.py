"""Test doubles for the Actron library and for time.

`FakeActronApi` has the same method signatures as `actron_neo_api.ActronAirAPI`
for the parts the actor uses. It keeps a raw `lastKnownState` dict as the unit's
own state and builds real `ActronAirStatus` models from it, so the library's
setters (`set_system_mode`, `enable`, ...) run unchanged and reach the fake
through `send_command`, as they reach the cloud API in production.
"""

from __future__ import annotations

import asyncio
import copy
import datetime
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from actron_neo_api import ActronAirAuthError, ActronAirStatus
from actron_neo_api.models.auth import ActronAirDeviceCode, ActronAirToken
from actron_neo_api.rt import RealtimeConnectionEvent, RealtimeConnectionState, RealtimeEventKind, RealtimeTransportType
from actron_neo_api.state import StateManager

SERIAL = "abc123"
START = datetime.datetime(2026, 11, 5, 4, 0, tzinfo=datetime.UTC)

DEFAULT_ZONES: list[tuple[str, bool, float]] = [("Family ", True, 24.0), ("Master", True, 23.5), ("Kids", False, 22.0)]


def raw_state(
    *,
    is_on: bool = True,
    mode: str = "COOL",
    fan_mode: str = "AUTO+CONT",
    cool_c: float = 23.0,
    heat_c: float = 21.0,
    zones: list[tuple[str, bool, float]] | None = None,
    indoor_c: float = 24.1,
    outdoor_c: float = 18.6,
    humidity_pct: float = 55.0,
    limits: dict[str, float] | None = None,
) -> dict[str, Any]:
    """A `lastKnownState` dict shaped like the Actron cloud's. Zones are (title, enabled, temperature).

    `limits` becomes NV_Limits.UserSetpoint_oC, for example {"setCool_Min": 18, "setCool_Max": 26}.
    """
    zone_list = DEFAULT_ZONES if zones is None else zones
    raw: dict[str, Any] = {
        "AirconSystem": {"MasterSerial": SERIAL, "OutdoorUnit": {"AmbTemp": 0.0}},
        "UserAirconSettings": {
            "isOn": is_on,
            "Mode": mode,
            "FanMode": fan_mode,
            "TemperatureSetpoint_Cool_oC": cool_c,
            "TemperatureSetpoint_Heat_oC": heat_c,
            "EnabledZones": [enabled for _, enabled, _ in zone_list],
            "ModeSupport": {"Cool": True, "Heat": True, "Fan": True, "Auto": True, "Dry": False},
        },
        "MasterInfo": {"LiveTemp_oC": indoor_c, "LiveOutdoorTemp_oC": outdoor_c, "LiveHumidity_pc": humidity_pct},
        "LiveAircon": {"SystemOn": is_on, "OutdoorUnit": {"AmbTemp": 0.0}},
        "RemoteZoneInfo": [
            {
                "NV_Title": title,
                "NV_Exists": True,
                "CanOperate": True,
                "LiveTemp_oC": temp,
                "LiveHumidity_pc": 50.0,
                "TemperatureSetpoint_Cool_oC": cool_c,
            }
            for title, _, temp in zone_list
        ],
    }
    if limits is not None:
        raw["NV_Limits"] = {"UserSetpoint_oC": limits}
    return raw


def _set_path(state: dict[str, Any], dotted: str, value: Any) -> None:
    """Set a value at a command key. A parent may index a list: "RemoteZoneInfo[0].TemperatureSetpoint_Cool_oC"."""
    *parents, leaf = dotted.split(".")
    node = state
    for key in parents:
        name, _, index = key.partition("[")
        node = node.setdefault(name, {})
        if index:
            node = node[int(index.rstrip("]"))]
    node[leaf] = value


class FakeOAuth:
    """The part of `ActronAirOAuth2DeviceCodeAuth` the sign-in flow uses.

    `refresh_error` is raised from `refresh_access_token`; `rotate_to` replaces the
    refresh token on a successful refresh, as the library does when the server rotates it.
    """

    def __init__(self) -> None:
        self.refresh_token: str | None = None
        self.refresh_error: Exception | None = None
        self.rotate_to: str | None = None
        self.refreshes = 0

    async def refresh_access_token(self) -> tuple[str, float]:
        self.refreshes += 1
        if self.refresh_error is not None:
            raise self.refresh_error
        if self.rotate_to is not None:
            self.refresh_token = self.rotate_to
        return "access-token-fake", 0.0


class FakeActronApi:
    """Records commands and serves status from `self.device`, the unit's raw state.

    Knobs:
    - `respond_to_commands`: False leaves the unit unchanged, as if it ignored the command.
    - `command_error` / `poll_error`: raised from `send_command` / `update_status`.
    - `push_results`: return values for successive `start_push` calls; when empty, `push_default`.
    - `push_on_command`: deliver the new status to subscribers after each applied command.
    - Sign-in: `request_device_code` returns codes numbered DEVICE-1, USER-1, ... and
      `poll_for_token` blocks until the test calls `approve`, `time_out` or `fail_poll`.
    """

    def __init__(self, device: dict[str, Any] | None = None, serial: str = SERIAL) -> None:
        self.serial = serial
        self.device = raw_state() if device is None else device
        self.state_manager = StateManager()
        self.state_manager.set_api(self)  # type: ignore[arg-type]
        self.commands: list[dict[str, Any]] = []
        self.respond_to_commands = True
        self.command_error: Exception | None = None
        self.poll_error: Exception | None = None
        self.push_results: list[bool] = []
        self.push_default = True
        self.push_on_command = False
        self.start_push_calls: list[list[str] | None] = []
        self.stop_push_calls = 0
        self.polls = 0
        self.oauth2_auth = FakeOAuth()
        self.code_error: Exception | None = None
        self.code_gate: asyncio.Event | None = None
        self.codes_issued = 0
        self.expires_in = 600
        self.code_interval = 5
        self.poll_calls: list[tuple[str, int, int]] = []
        self._polls: list[asyncio.Future[ActronAirToken | None]] = []
        self._update_callbacks: dict[str, list[Callable[[ActronAirStatus], Awaitable[None] | None]]] = {}
        self._connection_callbacks: list[Callable[[RealtimeConnectionEvent], Awaitable[None] | None]] = []

    # Library surface

    async def update_status(self, serial_number: str | None = None) -> dict[str, ActronAirStatus | None]:
        self.polls += 1
        if self.poll_error is not None:
            raise self.poll_error
        status = self.state_manager.process_status_update(self.serial, self._build_status())
        return {serial_number or self.serial: status}

    async def send_command(self, serial_number: str, command: dict[str, Any]) -> None:
        self.commands.append(dict(command["command"]))
        if self.command_error is not None:
            raise self.command_error
        if not self.respond_to_commands:
            return
        for key, value in command["command"].items():
            if key != "type":
                _set_path(self.device, key, value)
        if self.push_on_command:
            await self.push()

    async def start_push(
        self,
        serial_numbers: list[str] | None = None,
        *,
        connection_details: Any = None,
        client_id: str | None = None,
    ) -> bool:
        self.start_push_calls.append(serial_numbers)
        return self.push_results.pop(0) if self.push_results else self.push_default

    async def stop_push(self) -> None:
        self.stop_push_calls += 1

    def subscribe_system_updates(
        self, serial_number: str, callback: Callable[[ActronAirStatus], Awaitable[None] | None]
    ) -> Callable[[], None]:
        callbacks = self._update_callbacks.setdefault(serial_number.strip().lower(), [])
        callbacks.append(callback)
        return lambda: callbacks.remove(callback) if callback in callbacks else None

    def subscribe_connection_state(
        self, callback: Callable[[RealtimeConnectionEvent], Awaitable[None] | None]
    ) -> Callable[[], None]:
        self._connection_callbacks.append(callback)
        return lambda: self._connection_callbacks.remove(callback) if callback in self._connection_callbacks else None

    @property
    def refresh_token_value(self) -> str | None:
        return self.oauth2_auth.refresh_token

    async def request_device_code(self) -> ActronAirDeviceCode:
        if self.code_gate is not None:
            await self.code_gate.wait()
        if self.code_error is not None:
            raise self.code_error
        self.codes_issued += 1
        n = self.codes_issued
        return ActronAirDeviceCode(
            device_code=f"DEVICE-{n}",
            user_code=f"USER-{n}",
            verification_uri="https://example.test/verify",
            verification_uri_complete=f"https://example.test/verify?user_code=USER-{n}",
            expires_in=self.expires_in,
            interval=self.code_interval,
        )

    async def poll_for_token(self, device_code: str, interval: int = 5, timeout: int = 600) -> ActronAirToken | None:
        self.poll_calls.append((device_code, interval, timeout))
        future: asyncio.Future[ActronAirToken | None] = asyncio.get_running_loop().create_future()
        self._polls.append(future)
        return await future

    # Test drivers

    def approve(self, refresh_token: str = "REFRESH-NEW", access_token: str = "ACCESS-NEW") -> None:
        """The owner approves the latest device code."""
        self._polls[-1].set_result(ActronAirToken(access_token=access_token, refresh_token=refresh_token))  # type: ignore[call-arg]

    def time_out(self) -> None:
        self._polls[-1].set_result(None)

    def fail_poll(self, error: Exception | None = None) -> None:
        self._polls[-1].set_exception(error or ActronAirAuthError("Device code has expired"))

    def owner_sets(self, dotted: str, value: Any) -> None:
        """A change made at the wall controller, for example ("UserAirconSettings.isOn", True)."""
        _set_path(self.device, dotted, value)

    async def push(self) -> None:
        """Deliver the unit's current state to push subscribers, as the library's realtime handler does."""
        status = self.state_manager.process_status_update(self.serial, self._build_status())
        for callback in list(self._update_callbacks.get(self.serial, [])):
            result = callback(status)
            if inspect.isawaitable(result):
                await result

    async def emit_connection(self, state: RealtimeConnectionState) -> None:
        event = RealtimeConnectionEvent(
            transport=RealtimeTransportType.MQTT, kind=RealtimeEventKind.CONNECTION, state=state
        )
        for callback in list(self._connection_callbacks):
            result = callback(event)
            if inspect.isawaitable(result):
                await result

    @property
    def subscriber_count(self) -> int:
        return sum(len(c) for c in self._update_callbacks.values()) + len(self._connection_callbacks)

    def _build_status(self) -> ActronAirStatus:
        return ActronAirStatus.model_validate({"isOnline": True, "lastKnownState": copy.deepcopy(self.device)})


class FakeClock:
    """An aware UTC clock that `sleep` advances instantly.

    With `budget_seconds` set, a sleep that would pass the budget sets
    `exhausted` and then blocks until cancelled, so a test can let a background
    loop run for a fixed span of fake time and then stop it.
    """

    def __init__(self, start: datetime.datetime = START, budget_seconds: float | None = None) -> None:
        self.current = start
        self.start = start
        self.budget_seconds = budget_seconds
        self.exhausted = asyncio.Event()
        self.sleeps: list[float] = []

    def now(self) -> datetime.datetime:
        return self.current

    def elapsed(self) -> float:
        return (self.current - self.start).total_seconds()

    async def sleep(self, seconds: float) -> None:
        if self.budget_seconds is not None and self.elapsed() + seconds > self.budget_seconds:
            self.exhausted.set()
            await asyncio.Event().wait()
        self.sleeps.append(seconds)
        self.current += datetime.timedelta(seconds=seconds)
        await asyncio.sleep(0)
