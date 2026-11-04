import asyncio
import datetime
import logging
from collections.abc import Coroutine
from typing import Any

import pytest
from actron_neo_api.models.system import ActronAirSystemInfo

from actron_neo_actor import main as main_module
from actron_neo_actor.async_core import AsyncCore
from actron_neo_actor.connector import ActronConnector
from actron_neo_actor.core_api_client import CoreApiError
from actron_neo_actor.models import Instance, Trigger
from actron_neo_actor.runner import Runner, run_forever
from tests.fake_core import FakeCore
from tests.fakes import SERIAL, START, FakeActronApi, FakeClock, raw_state
from tests.worker_support import (
    SETTINGS,
    WINDOW_END,
    WINDOW_START,
    FakeFactory,
    context,
    instance,
    power_commands,
    schedule,
)

SECRET = "REFRESH-zq81-very-secret"


def run(coro: Coroutine[Any, Any, None]) -> None:
    asyncio.run(coro)


class Site:
    """A FakeCore with one FakeFactory (and FakeActronApi) per instance, run by a Runner."""

    def __init__(self, *instances: Instance) -> None:
        self.clock = FakeClock()
        self.core = FakeCore(self.clock.now)
        self.factories: dict[int, FakeFactory] = {}
        for inst in instances:
            api = FakeActronApi()
            api.push_on_command = True
            self.factories[inst.instance_id] = FakeFactory(api, self.clock)
            self.core.schedules[inst.instance_id] = [schedule(inst.instance_id, instance_id=inst.instance_id)]
            self.core.contexts[inst.instance_id] = context()
        self.core.instances = list(instances)
        self.runner = Runner(AsyncCore(self.core), SETTINGS, lambda i: self.factories[i.instance_id],  # type: ignore[arg-type,return-value]
                             self.clock.now, self.clock.sleep)

    async def round(self, at: datetime.datetime | None = None) -> None:
        if at is not None:
            self.clock.current = at
        await self.runner.round()
        await self.runner.settle()


def test_one_failing_instance_does_not_stop_another(caplog):
    async def scenario() -> None:
        site = Site(instance(1), instance(2))
        site.core.instance_errors[1] = CoreApiError("Failed to fetch schedules: 500 body with details")
        await site.round()
        assert site.core.of("action", 1) == []
        assert site.core.of("action", 2)[0].outcome == "ok"
        assert power_commands(site.factories[2].api) == ["OFF"]
        del site.core.instance_errors[1]
        await site.round(START + datetime.timedelta(seconds=30))
        assert site.core.of("action", 1)[0].outcome == "ok"

    caplog.set_level(logging.INFO)
    run(scenario())
    assert "Tick failed (instance 1): CoreApiError" in caplog.text
    assert "body with details" not in caplog.text


def test_a_tick_still_running_is_not_started_again():
    async def scenario() -> None:
        site = Site(instance(1))
        factory = site.factories[1]
        gate, calls = asyncio.Event(), []

        async def slow_sign_in(inst: Instance) -> bool:
            calls.append(inst)
            await gate.wait()
            return True

        factory.sign_in = slow_sign_in  # type: ignore[method-assign]
        await site.runner.round()
        await asyncio.sleep(0)
        await site.runner.round()
        await asyncio.sleep(0)
        assert len(calls) == 1
        gate.set()
        await site.runner.settle()
        assert site.core.of("action", 1)[0].outcome == "ok"

    run(scenario())


def test_restore_only_instance_restores_then_stops():
    async def scenario() -> None:
        site = Site(instance(1, restore_only=True))
        later = Trigger("time", time="14:10", days_of_week=list(range(7)))
        site.core.schedules[1].append(schedule(2, trigger=later))
        factory = site.factories[1]
        factory.api.device = raw_state(is_on=False)
        snapshot = {"action": "system_off", "applied": {"is_on": False}, "saved": {"is_on": True, "mode": "COOL"}}
        action = site.core.add_run(schedule_id=1, kind="action", trigger_at=WINDOW_START, outcome="ok",
                                   finished_at=START, snapshot=snapshot)
        test = site.core.add_run(schedule_id=1, kind="test", trigger_at=START)
        # Schedule 2 is due at 14:10, but a restore-only instance starts nothing new.
        await site.round(START + datetime.timedelta(minutes=10))
        assert site.core.of("action") == [action]
        assert site.core.runs[test.id].outcome == "pending"
        assert site.core.capabilities == []
        # The window runs to 16:00; the instance was disabled, so the unit goes back now.
        restore = site.core.of("restore")[0]
        assert (restore.outcome, restore.parent_run_id) == ("ok", action.id)
        assert power_commands(factory.api) == ["COOL"]
        site.core.instances = []
        await site.round(START + datetime.timedelta(minutes=11))
        assert factory.closed and factory.api.stop_push_calls == 1

    run(scenario())


def test_dropped_instance_is_stopped_and_new_one_started():
    async def scenario() -> None:
        site = Site(instance(1))
        await site.round()
        site.core.instances = [instance(1, restore_only=True)]
        await site.round(WINDOW_END)
        assert site.core.of("restore", 1)[0].outcome == "ok"
        site.core.instances = []
        await site.round(WINDOW_END + datetime.timedelta(seconds=30))
        assert site.factories[1].closed
        assert site.factories[1].api.subscriber_count == 0

    run(scenario())


def test_discovery_failure_is_logged_and_the_next_round_carries_on(caplog):
    async def scenario() -> None:
        site = Site(instance(1))
        site.core.errors["list_instances"] = CoreApiError("Failed to reach Core API: ConnectError")
        await site.round()
        assert site.core.runs == {}
        del site.core.errors["list_instances"]
        await site.round()
        assert site.core.of("action", 1)[0].outcome == "ok"

    caplog.set_level(logging.INFO)
    run(scenario())
    assert "Discovery failed: CoreApiError" in caplog.text


def test_run_forever_ticks_then_stops_workers_when_cancelled():
    async def scenario() -> None:
        site = Site(instance(1))
        sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            await site.runner.settle()
            if len(sleeps) == 2:
                raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await run_forever(SETTINGS, site.runner, sleep)
        assert sleeps == [SETTINGS.tick_seconds, SETTINGS.tick_seconds]
        assert site.core.of("action", 1)[0].outcome == "ok"
        assert site.factories[1].closed

    run(scenario())


# The production connector


class AccountApi(FakeActronApi):
    async def get_ac_systems(self) -> list[ActronAirSystemInfo]:
        return [ActronAirSystemInfo(serial=SERIAL)]

    async def close(self) -> None:
        pass


def test_connector_close_cancels_a_pending_sign_in():
    async def scenario() -> None:
        core = FakeCore(FakeClock().now)
        api = AccountApi()
        connector = ActronConnector(AsyncCore(core), SETTINGS, api=api)  # type: ignore[arg-type]
        assert await connector.sign_in(instance(1, token="")) is False
        state = connector._state
        assert state is not None and state.task is not None and not state.task.done()
        await connector.close()
        await asyncio.sleep(0)
        assert state.task.cancelled()

    run(scenario())


def test_no_secret_from_config_or_tokens_in_logs(caplog):
    async def scenario() -> None:
        clock = FakeClock()
        core = FakeCore(clock.now)
        apis: list[AccountApi] = []

        def connector(_inst: Instance) -> ActronConnector:
            api = AccountApi()
            api.push_on_command = True
            apis.append(api)
            return ActronConnector(AsyncCore(core), SETTINGS, api=api, clock=clock.now)  # type: ignore[arg-type]

        core.instances = [instance(1, token=SECRET)]
        core.schedules[1] = [schedule()]
        core.contexts[1] = context()
        core.instance_errors[1] = CoreApiError(f"Failed to fetch schedules: 500 {SECRET}")
        runner = Runner(AsyncCore(core), SETTINGS, connector, clock.now, clock.sleep)  # type: ignore[arg-type]
        await runner.round()
        await runner.settle()
        del core.instance_errors[1]
        apis[0].command_error = RuntimeError(f"cloud said {SECRET}")
        await runner.round()
        await runner.settle()
        assert core.of("action")[0].outcome == "failed"
        assert SECRET not in (core.of("action")[0].message or "")
        await runner.stop()

    caplog.set_level(logging.DEBUG)
    run(scenario())
    assert "Tick failed (instance 1): CoreApiError" in caplog.text
    assert SECRET not in caplog.text


def test_main_quiets_the_library_logger(monkeypatch):
    seen: dict[str, Any] = {}

    async def fake_run_forever(settings: Any) -> None:
        seen["level"] = logging.getLogger("actron_neo_api").getEffectiveLevel()
        seen["settings"] = settings

    monkeypatch.setattr(main_module, "run_forever", fake_run_forever)
    monkeypatch.setattr(main_module, "get_settings", lambda: SETTINGS)
    logging.getLogger("actron_neo_api").setLevel(logging.NOTSET)
    main_module.main()
    assert seen == {"level": logging.ERROR, "settings": SETTINGS}
