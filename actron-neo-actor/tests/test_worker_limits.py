"""A price trigger fires once per arming, re-arms in Core API, and obeys its minimum gap and lockout."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
from collections.abc import Coroutine
from typing import Any

from actron_neo_actor.models import Limits, RestoreSpec, SunDay, TimeRef, Trigger
from tests.fakes import START
from tests.worker_support import harness, schedule

MIN = datetime.timedelta(minutes=1)
NO_RESTORE = RestoreSpec(False, "after_minutes", 60, None, [], [])
RESTORE_AFTER_10 = RestoreSpec(True, "after_minutes", 10, None, [], [])
PRICE = Trigger("price_above", c_per_kwh=40.0, rearm=35.0)


def run(coro: Coroutine[Any, Any, None]) -> None:
    asyncio.run(coro)


def set_price(h: Any, price: float) -> None:
    h.core.contexts[1] = dataclasses.replace(h.core.contexts[1], price_c_per_kwh=price)


def test_a_price_trigger_fires_once_then_waits_for_the_rearm_price():
    async def scenario() -> None:
        h = harness([schedule(trigger=PRICE, restore=NO_RESTORE)])
        set_price(h, 45.0)
        await h.tick()
        assert len(h.core.of("action")) == 1
        assert h.core.armed_calls == [(1, False)]
        # Back under the trigger but not at the re-arm price: still disarmed.
        set_price(h, 38.0)
        await h.tick_at(START + 6 * MIN)
        assert h.core.armed_calls == [(1, False)]
        set_price(h, 45.0)
        await h.tick_at(START + 12 * MIN)
        assert len(h.core.of("action")) == 1
        # At the re-arm price it arms again, and the next rise fires.
        set_price(h, 35.0)
        await h.tick_at(START + 18 * MIN)
        assert h.core.armed_calls == [(1, False), (1, True)]
        set_price(h, 45.0)
        await h.tick_at(START + 24 * MIN)
        assert len(h.core.of("action")) == 2
        assert h.core.armed_calls[-1] == (1, False)

    run(scenario())


def test_a_trigger_blocked_by_the_minimum_gap_stays_armed_and_unrecorded_until_the_gap_ends():
    async def scenario() -> None:
        sched = schedule(trigger=Trigger("price_above", c_per_kwh=40.0), restore=RESTORE_AFTER_10)
        h = harness([dataclasses.replace(sched, limits=Limits(min_gap_minutes=30))])
        set_price(h, 45.0)
        await h.tick()
        await h.tick_at(START + 11 * MIN)
        assert len(h.core.of("restore")) == 1
        # The price dips and rises again inside the gap: no run, no disarm.
        set_price(h, 30.0)
        await h.tick_at(START + 13 * MIN)
        set_price(h, 45.0)
        await h.tick_at(START + 20 * MIN)
        assert len(h.core.of("action")) == 1
        assert h.core.armed_calls[-1] == (1, True)
        # The gap runs 30 minutes from the restore, so it has ended by 45 minutes.
        await h.tick_at(START + 45 * MIN)
        assert len(h.core.of("action")) == 2

    run(scenario())


def test_a_time_trigger_blocked_by_run_again_after_is_recorded_as_skipped():
    async def scenario() -> None:
        trigger = Trigger("sun", event="sunset", days_of_week=list(range(7)))
        sched = dataclasses.replace(
            schedule(trigger=trigger, restore=NO_RESTORE), limits=Limits(run_again_after=TimeRef("clock", time="06:00"))
        )
        h = harness([sched])
        sunset = START + 5 * datetime.timedelta(hours=1)  # 19:00 in Brisbane
        days = [SunDay(datetime.date(2026, 11, d), sunset - datetime.timedelta(days=5 - d), sunset - datetime.timedelta(days=5 - d)) for d in (4, 5, 6, 7)]
        h.core.contexts[1] = dataclasses.replace(h.core.contexts[1], sun=days)
        h.core.add_run(schedule_id=1, kind="action", trigger_at=START, finished_at=START, outcome="ok")
        await h.tick_at(sunset + 1 * MIN)
        held = [r for r in h.core.of("action") if r.trigger_at == sunset]
        assert [(r.outcome, r.message) for r in held] == [("skipped", "waiting until Fri 06:00")]
        assert h.api.commands == []

    run(scenario())
