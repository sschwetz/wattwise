import asyncio
import dataclasses
import datetime
import threading
from collections.abc import Coroutine
from typing import Any

from actron_neo_api import ActronAirAuthError

from actron_neo_actor.core_api_client import CoreApiConflict
from actron_neo_actor.models import ActionSpec, Condition, RestoreSpec, RetrySpec, Trigger
from tests.fakes import START, FakeActronApi, raw_state
from tests.worker_support import (
    SETTINGS,
    WINDOW_END,
    WINDOW_START,
    GatedApi,
    Harness,
    harness,
    power_commands,
    restore_spec,
    schedule,
    with_sleep,
)

MIN = datetime.timedelta(minutes=1)
SEC = datetime.timedelta(seconds=1)


def run(coro: Coroutine[Any, Any, None]) -> None:
    asyncio.run(coro)


def only(runs: list) -> Any:
    assert len(runs) == 1, runs
    return runs[0]


# Actions


def test_window_start_turns_unit_off_and_records_ok():
    async def scenario() -> None:
        h = harness()
        await h.tick()
        action = only(h.core.of("action"))
        assert (action.outcome, action.trigger_at, action.attempts) == ("ok", WINDOW_START, 1)
        assert action.snapshot == {"action": "system_off", "applied": {"is_on": False},
                                   "saved": {"is_on": True, "mode": "COOL"}}
        # The snapshot went to Core API with the claim, before the command.
        assert h.core.updates[0] == (action.id, "running", action.snapshot)
        assert power_commands(h.api) == ["OFF"]
        assert h.api.device["UserAirconSettings"]["isOn"] is False

    run(scenario())


def test_unit_already_on_is_turned_off():
    async def scenario() -> None:
        h = harness(api=FakeActronApi(raw_state(is_on=True, mode="HEAT")))
        await h.tick()
        assert only(h.core.of("action")).outcome == "ok"
        await h.tick_at(WINDOW_END)
        restore = only(h.core.of("restore"))
        assert (restore.outcome, restore.trigger_at, restore.message) == ("ok", WINDOW_END, "restored power")
        assert power_commands(h.api) == ["OFF", "HEAT"]

    run(scenario())


def test_unit_already_off_at_start_gets_off_restore():
    async def scenario() -> None:
        h = harness(api=FakeActronApi(raw_state(is_on=False)))
        await h.tick()
        action = only(h.core.of("action"))
        assert action.outcome == "ok"
        assert action.snapshot["saved"]["is_on"] is False
        await h.tick_at(WINDOW_END)
        restore = only(h.core.of("restore"))
        assert (restore.outcome, restore.message) == ("ok", "restored power")
        # The action's OFF only: the restore puts back "off", which the unit already shows.
        assert power_commands(h.api) == ["OFF"]
        assert h.api.device["UserAirconSettings"]["isOn"] is False

    run(scenario())


def test_duplicate_trigger_409_is_not_run_twice():
    async def scenario() -> None:
        h = harness()
        h.core.errors["create_run"] = CoreApiConflict("Failed to create run: 409", "duplicate_run")
        await h.tick()
        assert h.core.updates == []
        assert h.api.commands == []
        del h.core.errors["create_run"]
        await h.tick()
        await h.tick_at(START + 30 * SEC)
        assert only(h.core.of("action")).outcome == "ok"
        assert power_commands(h.api) == ["OFF"]

    run(scenario())


def test_retries_then_gives_up_and_marks_failed():
    async def scenario() -> None:
        h = harness([schedule(retry=RetrySpec(3, 10, 5))])
        h.api.respond_to_commands = False
        await h.tick()
        action = only(h.core.of("action"))
        assert (action.outcome, action.attempts) == ("failed", 3)
        assert action.message == "failed after 3 attempts: not confirmed"
        assert power_commands(h.api) == ["OFF", "OFF", "OFF"]

    run(scenario())


def test_command_that_lands_after_the_retries_counts_as_ok():
    async def scenario() -> None:
        h = harness([schedule(retry=RetrySpec(1, 10, 5))])
        h.api.respond_to_commands = False
        settles: list[float] = []

        async def sleep(seconds: float) -> None:
            settles.append(seconds)
            h.api.owner_sets("UserAirconSettings.isOn", False)  # the OFF lands late
            await h.clock.sleep(seconds)

        h = with_sleep(h, sleep)
        await h.tick()
        action = only(h.core.of("action"))
        assert (action.outcome, action.attempts) == ("ok", 1)
        assert settles[0] == 15  # the settle wait; a readings push may sleep after it
        h.api.respond_to_commands = True
        await h.tick_at(WINDOW_END)
        assert only(h.core.of("restore")).outcome == "ok"

    run(scenario())


def test_give_up_minutes_stops_retries_early():
    async def scenario() -> None:
        h = harness([schedule(retry=RetrySpec(10, 60, 2))])
        h.api.respond_to_commands = False
        await h.tick()
        action = only(h.core.of("action"))
        # 45 s to fail, 60 s wait, 45 s to fail: a third try would start after the 2 minute limit.
        assert (action.outcome, action.attempts) == ("failed", 2)

    run(scenario())


def test_condition_failure_records_skipped_with_reason():
    async def scenario() -> None:
        h = harness([schedule(conditions=[Condition("outdoor_temp_c", "gt", 30)])])
        await h.tick()
        action = only(h.core.of("action"))
        assert (action.outcome, action.message) == ("skipped", "outdoor_temp_c 18.6 is not above 30.0")
        assert h.api.commands == []

    run(scenario())


def test_stale_reading_skips_with_reading_unavailable():
    async def scenario() -> None:
        h = harness([schedule(conditions=[Condition("indoor_temp_c", "gt", 20)])])
        h.core.schedules[1] = []
        await h.tick()  # syncs the device at 14:00
        h.core.schedules[1] = [schedule(conditions=[Condition("indoor_temp_c", "gt", 20)])]
        await h.tick_at(START + 6 * MIN)
        action = only(h.core.of("action"))
        assert (action.outcome, action.message) == ("skipped", "reading unavailable: indoor_temp_c")

    run(scenario())


def test_missed_trigger_recorded_skipped():
    async def scenario() -> None:
        h = harness()
        await h.tick_at(START + 15 * MIN)
        action = only(h.core.of("action"))
        assert (action.outcome, action.message, action.trigger_at) == ("skipped", "missed", WINDOW_START)
        assert h.api.commands == []

    run(scenario())


def test_second_schedule_skipped_while_first_is_active():
    async def scenario() -> None:
        later = Trigger("time", time="14:30", days_of_week=list(range(7)))
        h = harness([schedule(1), schedule(2, trigger=later, action=ActionSpec("set_mode", {"mode": "FAN"}))])
        await h.tick()
        await h.tick_at(START + 30 * MIN)
        second = only(h.core.of("action", 2))
        assert (second.outcome, second.message) == ("skipped", "another schedule is active")
        assert power_commands(h.api) == ["OFF"]

    run(scenario())


def test_device_auth_error_fails_run_and_drops_device():
    async def scenario() -> None:
        h = harness()
        h.api.command_error = ActronAirAuthError("401 from cloud")
        await h.tick()
        action = only(h.core.of("action"))
        assert (action.outcome, action.attempts) == ("failed", 1)
        assert action.message == "failed after 1 attempt: system_off: sign-in rejected"
        assert h.factory.signed_out_calls == 1
        assert h.api.stop_push_calls == 1
        h.api.command_error = None
        await h.tick_at(START + 30 * SEC)
        assert len(h.factory.devices) == 2

    run(scenario())


# Override


def test_owner_turns_unit_on_marks_overridden_and_skips_restore():
    async def scenario() -> None:
        h = harness()
        await h.tick()
        action = only(h.core.of("action"))
        h.clock.current = START + 20 * SEC
        h.api.owner_sets("UserAirconSettings.isOn", True)
        await h.api.push()
        await h.worker.idle()
        assert h.core.runs[action.id].outcome == "ok"  # inside the 45 second confirm window
        await h.tick_at(START + 50 * SEC)
        overridden = h.core.runs[action.id]
        assert (overridden.outcome, overridden.message) == ("overridden", "owner changed the unit")
        assert overridden.finished_at == action.finished_at
        await h.tick_at(WINDOW_END)
        await h.tick_at(WINDOW_END + 30 * SEC)
        restore = only(h.core.of("restore"))
        assert (restore.outcome, restore.message, restore.parent_run_id) == ("skipped", "owner override", action.id)
        assert restore.trigger_at == WINDOW_END
        assert power_commands(h.api) == ["OFF"]
        assert h.api.device["UserAirconSettings"]["isOn"] is True

    run(scenario())


def test_owner_change_is_seen_from_a_push_without_a_tick():
    async def scenario() -> None:
        h = harness()
        await h.tick()
        action = only(h.core.of("action"))
        h.clock.current = START + 2 * MIN
        h.api.owner_sets("UserAirconSettings.isOn", True)
        await h.api.push()
        await h.worker.idle()
        assert h.core.runs[action.id].outcome == "overridden"

    run(scenario())


def test_partial_zone_change_keeps_the_rest_of_the_run():
    async def scenario() -> None:
        zones_off = ActionSpec("set_zones", {"zones": ["Family", "Master"], "enabled": False})
        h = harness([schedule(action=zones_off)])
        await h.tick()
        action = only(h.core.of("action"))
        h.clock.current = START + 2 * MIN
        h.api.owner_sets("UserAirconSettings.EnabledZones", [True, False, False])
        await h.api.push()
        await h.worker.idle()
        assert h.core.runs[action.id].outcome == "ok"
        await h.tick_at(WINDOW_END)
        restore = only(h.core.of("restore"))
        assert restore.outcome == "ok"
        assert restore.message == "restored zone Master; left alone because the owner changed them: zone Family"
        assert h.api.device["UserAirconSettings"]["EnabledZones"] == [True, True, False]

    run(scenario())


# Restore


def test_restart_resumes_unfinished_run_and_restores_at_window_end():
    async def scenario() -> None:
        h = harness(api=FakeActronApi(raw_state(is_on=False)))
        snapshot = {"action": "system_off", "applied": {"is_on": False}, "saved": {"is_on": True, "mode": "COOL"}}
        action = h.core.add_run(schedule_id=1, kind="action", trigger_at=WINDOW_START, outcome="ok",
                                started_at=START, finished_at=START + MIN, attempts=1, snapshot=snapshot)
        await h.tick_at(START + 30 * MIN)
        assert h.core.of("restore") == [] and h.api.commands == []
        assert len(h.core.of("action")) == 1
        await h.tick_at(WINDOW_END)
        restore = only(h.core.of("restore"))
        assert (restore.outcome, restore.trigger_at, restore.parent_run_id) == ("ok", WINDOW_END, action.id)
        assert power_commands(h.api) == ["COOL"]

    run(scenario())


def test_restart_finishes_runs_left_open():
    async def scenario() -> None:
        h = harness([schedule(1), schedule(2)], api=FakeActronApi(raw_state(is_on=False)))
        snapshot = {"action": "system_off", "applied": {"is_on": False}, "saved": {"is_on": True, "mode": "COOL"}}
        claimed = h.core.add_run(schedule_id=1, kind="action", trigger_at=WINDOW_START, outcome="running",
                                 started_at=START, snapshot=snapshot)
        created = h.core.add_run(schedule_id=2, kind="action", trigger_at=WINDOW_START)
        await h.tick_at(START + 30 * MIN)
        assert (h.core.runs[claimed.id].outcome, h.core.runs[claimed.id].message) == ("ok", "confirmed after a restart")
        assert (h.core.runs[created.id].outcome, h.core.runs[created.id].message) == (
            "skipped", "the actor restarted before the run")
        await h.tick_at(WINDOW_END)
        assert only(h.core.of("restore", 1)).outcome == "ok"
        assert power_commands(h.api) == ["COOL"]

    run(scenario())


def test_restart_retries_a_claimed_action_the_unit_does_not_show():
    async def scenario() -> None:
        h = harness()
        snapshot = {"action": "system_off", "applied": {"is_on": False}, "saved": {"is_on": True, "mode": "COOL"}}
        claimed = h.core.add_run(schedule_id=1, kind="action", trigger_at=WINDOW_START, outcome="running",
                                 started_at=START, snapshot=snapshot)
        await h.tick_at(START + 2 * MIN)
        done = h.core.runs[claimed.id]
        assert (done.outcome, done.message, done.attempts) == ("ok", "applied after a restart", 1)
        assert power_commands(h.api) == ["OFF"]
        await h.tick_at(WINDOW_END)
        assert only(h.core.of("restore")).outcome == "ok"

    run(scenario())


def test_restart_past_give_up_checks_once_then_fails():
    async def scenario() -> None:
        h = harness([schedule(retry=RetrySpec(3, 10, 5))])
        snapshot = {"action": "system_off", "applied": {"is_on": False}, "saved": {"is_on": True, "mode": "COOL"}}
        claimed = h.core.add_run(schedule_id=1, kind="action", trigger_at=WINDOW_START, outcome="running",
                                 started_at=START, snapshot=snapshot)
        await h.tick_at(START + 6 * MIN)
        done = h.core.runs[claimed.id]
        assert (done.outcome, done.message) == ("failed", "the actor restarted before the unit confirmed")
        assert h.api.commands == []

    run(scenario())


def test_restart_finds_an_open_restore_older_than_a_day():
    async def scenario() -> None:
        h = harness(api=FakeActronApi(raw_state(is_on=False)))
        snapshot = {"action": "system_off", "applied": {"is_on": False}, "saved": {"is_on": True, "mode": "COOL"}}
        old = START - datetime.timedelta(hours=54)
        action = h.core.add_run(schedule_id=1, kind="action", trigger_at=old, outcome="ok",
                                started_at=old, finished_at=old + MIN, snapshot=snapshot)
        # No window occurrence starts at `old`, so the restore falls 24 hours later: 30 hours ago.
        restore = h.core.add_run(schedule_id=1, kind="restore", trigger_at=old + datetime.timedelta(hours=24),
                                 outcome="running", parent_run_id=action.id, started_at=old)
        await h.tick_at(START + 30 * MIN)
        assert (h.core.runs[restore.id].outcome, h.core.runs[restore.id].message) == ("ok", "restored power")
        assert power_commands(h.api) == ["COOL"]

    run(scenario())


def test_restart_finishes_a_restore_left_open():
    async def scenario() -> None:
        h = harness(api=FakeActronApi(raw_state(is_on=False)))
        snapshot = {"action": "system_off", "applied": {"is_on": False}, "saved": {"is_on": True, "mode": "COOL"}}
        action = h.core.add_run(schedule_id=1, kind="action", trigger_at=WINDOW_START, outcome="ok",
                                started_at=START, finished_at=START + MIN, snapshot=snapshot)
        restore = h.core.add_run(schedule_id=1, kind="restore", trigger_at=WINDOW_END, outcome="running",
                                 parent_run_id=action.id, started_at=WINDOW_END)
        await h.tick_at(WINDOW_END + 5 * MIN)
        assert (h.core.runs[restore.id].outcome, h.core.runs[restore.id].message) == ("ok", "restored power")
        assert only(h.core.of("restore")).id == restore.id
        assert power_commands(h.api) == ["COOL"]

    run(scenario())


def test_early_restore_when_indoor_above_threshold():
    async def scenario() -> None:
        early = restore_spec(early_if=[Condition("indoor_temp_c", "gt", 26)])
        h = harness([schedule(restore=early)])
        await h.tick()
        await h.tick_at(START + 5 * MIN)
        assert h.core.of("restore") == []
        now = START + 10 * MIN + datetime.timedelta(microseconds=250_000)
        h.clock.current = now
        h.api.owner_sets("MasterInfo.LiveTemp_oC", 27.0)
        await h.api.push()
        await h.tick_at(now)
        restore = only(h.core.of("restore"))
        assert (restore.outcome, restore.trigger_at) == ("ok", now.replace(microsecond=0))
        assert power_commands(h.api) == ["OFF", "COOL"]

    run(scenario())


def test_conditions_only_restore_with_a_missing_reading_restores_at_the_day_cap():
    async def scenario() -> None:
        never = restore_spec(when="conditions_only", early_if=[Condition("mystery_c", "lt", 20)])
        h = harness([schedule(restore=never)])
        await h.tick()
        await h.tick_at(START + datetime.timedelta(hours=23, minutes=59))
        assert h.core.of("restore") == []
        assert h.api.device["UserAirconSettings"]["isOn"] is False
        await h.tick_at(START + datetime.timedelta(hours=24))
        restore = only(h.core.of("restore"))
        assert (restore.outcome, restore.trigger_at) == ("ok", START + datetime.timedelta(hours=24))
        assert h.api.device["UserAirconSettings"]["isOn"] is True

    run(scenario())


def test_restore_gate_false_waits_then_restores_when_it_passes():
    async def scenario() -> None:
        gate = restore_spec(only_if=[Condition("outdoor_temp_c", "lt", 25)])
        h = harness([schedule(restore=gate)])
        await h.tick()
        h.clock.current = WINDOW_END
        h.api.owner_sets("MasterInfo.LiveOutdoorTemp_oC", 30.0)
        await h.api.push()
        await h.tick_at(WINDOW_END)
        await h.tick_at(WINDOW_END + MIN)
        assert h.core.of("restore") == []
        assert h.api.device["UserAirconSettings"]["isOn"] is False
        h.clock.current = WINDOW_END + 2 * MIN
        h.api.owner_sets("MasterInfo.LiveOutdoorTemp_oC", 20.0)
        await h.api.push()
        await h.tick_at(WINDOW_END + 2 * MIN)
        restore = only(h.core.of("restore"))
        assert (restore.outcome, restore.trigger_at) == ("ok", WINDOW_END)
        assert h.api.device["UserAirconSettings"]["isOn"] is True

    run(scenario())


def test_disabling_the_schedule_restores_at_once():
    async def scenario() -> None:
        h = harness()
        await h.tick()
        h.core.schedules[1] = [dataclasses.replace(schedule(), enabled=False)]
        now = START + 10 * MIN + datetime.timedelta(microseconds=5)
        await h.tick_at(now)
        restore = only(h.core.of("restore"))
        assert (restore.outcome, restore.trigger_at) == ("ok", now.replace(microsecond=0))
        assert power_commands(h.api) == ["OFF", "COOL"]

    run(scenario())


def test_restore_disabled_schedule_never_restores():
    async def scenario() -> None:
        h = harness([schedule(restore=RestoreSpec(False, "window_end", None, None, [], []))])
        await h.tick()
        await h.tick_at(WINDOW_END)
        await h.tick_at(WINDOW_END + MIN)
        assert only(h.core.of("action")).outcome == "ok"
        assert h.core.of("restore") == []
        assert power_commands(h.api) == ["OFF"]

    run(scenario())


def test_restore_retries_and_fails():
    async def scenario() -> None:
        h = harness([schedule(retry=RetrySpec(2, 10, 5))])
        await h.tick()
        h.api.respond_to_commands = False
        await h.tick_at(WINDOW_END)
        restore = only(h.core.of("restore"))
        assert (restore.outcome, restore.attempts) == ("failed", 2)
        assert restore.message == "failed after 2 attempts: not confirmed"

    run(scenario())


# Test runs


def test_test_run_applies_then_restores_after_the_hold():
    async def scenario() -> None:
        h = harness()
        h.core.contexts[1] = dataclasses.replace(h.core.contexts[1], windows=[])
        test = h.core.add_run(schedule_id=1, kind="test", trigger_at=START)
        await h.tick()
        done = h.core.runs[test.id]
        assert (done.outcome, done.message) == ("ok", "applied, then restored power")
        assert done.snapshot is not None and done.snapshot["saved"]["is_on"] is True
        assert power_commands(h.api) == ["OFF", "COOL"]
        assert 30 in h.clock.sleeps

    run(scenario())


def test_test_run_older_than_10_minutes_is_skipped_as_expired():
    async def scenario() -> None:
        h = harness()
        h.core.contexts[1] = dataclasses.replace(h.core.contexts[1], windows=[])
        old = h.core.add_run(schedule_id=1, kind="test", trigger_at=START - 11 * MIN)
        await h.tick()
        done = h.core.runs[old.id]
        assert (done.outcome, done.message) == ("skipped", "expired")
        assert power_commands(h.api) == []

    run(scenario())


def test_test_run_10_minutes_old_still_runs():
    async def scenario() -> None:
        h = harness()
        h.core.contexts[1] = dataclasses.replace(h.core.contexts[1], windows=[])
        test = h.core.add_run(schedule_id=1, kind="test", trigger_at=START - 10 * MIN)
        await h.tick()
        assert h.core.runs[test.id].outcome == "ok"

    run(scenario())


def test_test_run_waits_while_an_action_holds_the_unit():
    async def scenario() -> None:
        h = harness()
        await h.tick()
        test = h.core.add_run(schedule_id=1, kind="test", trigger_at=START + MIN)
        await h.tick_at(START + MIN)
        done = h.core.runs[test.id]
        assert (done.outcome, done.message) == ("skipped", "another schedule is active")
        assert power_commands(h.api) == ["OFF"]

    run(scenario())


STOP_WAIT = 65  # how long stop waits for a test run before it cancels it


def stop_sleep(h: Harness, *, block_hold: bool, holding: asyncio.Event | None = None,
               stop_wait: asyncio.Event | None = None) -> Any:
    """A worker sleep for stop tests. The hold blocks when `block_hold` (setting `holding`); stop's
    last-resort wait blocks forever, or ends once `stop_wait` is set."""

    async def sleep(seconds: float) -> None:
        if seconds == SETTINGS.test_hold_seconds and block_hold:
            if holding is not None:
                holding.set()
            await asyncio.Event().wait()
        if seconds == STOP_WAIT:
            await (stop_wait or asyncio.Event()).wait()
            return
        await h.clock.sleep(seconds)

    return sleep


async def start_test(h: Harness) -> int:
    h.core.contexts[1] = dataclasses.replace(h.core.contexts[1], windows=[])
    test = h.core.add_run(schedule_id=1, kind="test", trigger_at=START)
    await h.worker.tick(h.instance)
    return test.id


async def stop_soon(h: Harness) -> asyncio.Task[None]:
    """Start stopping the worker and let stop signal the test run before the test goes on."""
    stopper = asyncio.create_task(h.worker.stop())
    for _ in range(3):
        await asyncio.sleep(0)
    return stopper


def test_stop_during_a_test_hold_restores_first():
    async def scenario() -> None:
        holding = asyncio.Event()
        h = harness()
        h = with_sleep(h, stop_sleep(h, block_hold=True, holding=holding))
        test_id = await start_test(h)
        await asyncio.wait_for(holding.wait(), timeout=5)
        assert power_commands(h.api) == ["OFF"]
        await asyncio.wait_for(h.worker.stop(), timeout=5)
        done = h.core.runs[test_id]
        assert (done.outcome, done.message) == ("ok", "applied, then restored power early because the actor stopped")
        assert power_commands(h.api) == ["OFF", "COOL"]
        assert h.factory.closed

    run(scenario())


def test_stop_during_a_test_hold_gives_the_restore_60_seconds():
    async def scenario() -> None:
        holding = asyncio.Event()
        h = harness([schedule(retry=RetrySpec(10, 5, 30))])
        h = with_sleep(h, stop_sleep(h, block_hold=True, holding=holding))
        test_id = await start_test(h)
        await asyncio.wait_for(holding.wait(), timeout=5)
        h.api.respond_to_commands = False
        await asyncio.wait_for(h.worker.stop(), timeout=5)
        done = h.core.runs[test_id]
        assert done.outcome == "failed"
        assert done.message == ("applied, but the restore failed after 1 attempt: not confirmed"
                                " (the actor stopped during the test)")
        assert h.clock.elapsed() <= 60
        assert h.factory.closed

    run(scenario())


def test_stop_during_the_restore_after_the_hold_lets_it_finish():
    async def scenario() -> None:
        h = harness(api=GatedApi(gate_on=True))
        api: GatedApi = h.api  # type: ignore[assignment]
        h = with_sleep(h, stop_sleep(h, block_hold=False))
        test_id = await start_test(h)
        await asyncio.wait_for(api.reached.wait(), timeout=5)  # the restore's COOL is being sent
        stopper = await stop_soon(h)
        api.release.set()
        await asyncio.wait_for(stopper, timeout=5)
        done = h.core.runs[test_id]
        assert (done.outcome, done.message) == ("ok", "applied, then restored power")
        assert power_commands(h.api) == ["OFF", "COOL"]

    run(scenario())


def test_stop_while_the_test_applies_confirms_then_restores():
    async def scenario() -> None:
        h = harness(api=GatedApi(gate_on=False))
        api: GatedApi = h.api  # type: ignore[assignment]
        h = with_sleep(h, stop_sleep(h, block_hold=True))
        test_id = await start_test(h)
        await asyncio.wait_for(api.reached.wait(), timeout=5)  # the test's OFF is being sent
        stopper = await stop_soon(h)
        api.release.set()
        await asyncio.wait_for(stopper, timeout=5)
        done = h.core.runs[test_id]
        assert (done.outcome, done.message) == ("ok", "applied, then restored power early because the actor stopped")
        assert power_commands(h.api) == ["OFF", "COOL"]

    run(scenario())


def test_stop_cancels_a_restore_that_hangs_past_the_cap():
    async def scenario() -> None:
        h = harness(api=GatedApi(gate_on=True))
        api: GatedApi = h.api  # type: ignore[assignment]
        h = with_sleep(h, stop_sleep(h, block_hold=True, stop_wait=api.reached))
        test_id = await start_test(h)
        for _ in range(50):  # until the test is in its hold
            if power_commands(h.api) == ["OFF"]:
                break
            await asyncio.sleep(0.01)
        stopper = asyncio.create_task(h.worker.stop())  # the COOL never gets through
        done, _ = await asyncio.wait({stopper}, timeout=5)
        if stopper not in done:
            stopper.cancel()
        assert stopper in done, "stop waited on the hung restore instead of cancelling it"
        assert h.core.runs[test_id].outcome == "running"  # left for resume: the known limitation
        assert h.factory.closed

    run(scenario())


# Capabilities, heartbeat, threads


def test_capabilities_sent_once_then_on_change():
    async def scenario() -> None:
        h = harness([])
        await h.tick()
        await h.tick_at(START + 30 * SEC)
        assert len(h.core.capabilities) == 1
        h.api.device["RemoteZoneInfo"][2]["NV_Title"] = "Study"
        await h.api.push()
        await h.tick_at(START + MIN)
        assert len(h.core.capabilities) == 2

    run(scenario())


def test_heartbeat_and_change_push_to_core_api():
    async def scenario() -> None:
        h = harness([])
        await h.tick()
        assert len(h.core.readings) == 1
        assert h.core.readings[0][1]["is_on"] is True
        h.api.owner_sets("UserAirconSettings.isOn", False)
        await h.api.push()
        await h.worker.idle()
        assert len(h.core.readings) == 2 and h.core.readings[-1][1]["is_on"] is False
        sent_at = h.clock.now()
        assert sent_at - START >= 2 * SEC  # the second push waited out the 2 second limit
        await h.tick_at(sent_at + 30 * SEC)
        assert len(h.core.readings) == 2
        h.clock.current = sent_at + 61 * SEC
        h.worker.pulse()
        await h.worker.idle()
        assert len(h.core.readings) == 3

    run(scenario())


def test_no_heartbeat_once_the_device_state_is_older_than_5_minutes():
    async def scenario() -> None:
        h = harness([])
        await h.tick()
        assert len(h.core.readings) == 1
        h.clock.current = START + 4 * MIN
        h.worker.pulse()
        await h.worker.idle()
        assert len(h.core.readings) == 2
        h.clock.current = START + 6 * MIN
        h.worker.pulse()
        await h.worker.idle()
        assert len(h.core.readings) == 2

    run(scenario())


def test_no_heartbeat_after_sign_out():
    async def scenario() -> None:
        h = harness([])
        await h.tick()
        assert len(h.core.readings) == 1
        h.factory.signed_in = False
        await h.tick_at(START + 61 * SEC)
        h.worker.pulse()
        await h.worker.idle()
        assert len(h.core.readings) == 1

    run(scenario())


def test_core_api_calls_run_off_the_event_loop_thread():
    async def scenario() -> None:
        h = harness()
        await h.tick()
        await h.tick_at(WINDOW_END)
        assert h.core.threads
        assert threading.get_ident() not in h.core.threads

    run(scenario())


def test_not_signed_in_does_nothing_with_the_unit():
    async def scenario() -> None:
        h = harness()
        h.factory.signed_in = False
        await h.tick()
        assert h.core.runs == {} and h.factory.devices == []

    run(scenario())
