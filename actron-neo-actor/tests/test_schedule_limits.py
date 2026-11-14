"""Sun and clock time references, the sun trigger, the time-of-day row, re-arm and the limits on how often a schedule fires."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from actron_neo_actor.models import (
    ActionSpec,
    Condition,
    Context,
    Limits,
    RestoreSpec,
    RetrySpec,
    Run,
    Schedule,
    SunDay,
    TimeRef,
    Trigger,
)
from actron_neo_actor.schedule_eval import (
    blocked_until,
    due_triggers,
    evaluate_conditions,
    missed_triggers,
    next_after,
    rearm_due,
    ref_instant,
    restore_gate_passes,
)

SYDNEY = "Australia/Sydney"
TZ = ZoneInfo(SYDNEY)


def local(day: int, hour: int, minute: int = 0, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TZ).astimezone(UTC)


def sun_days(first: int = 12, count: int = 5) -> list[SunDay]:
    """Sunrise at 05:30 and sunset at 19:30 local, for `count` days from 12 November."""
    return [SunDay(date(2026, 11, d), local(d, 5, 30), local(d, 19, 30)) for d in range(first, first + count)]


def ctx(now: datetime, sun=None, **kw) -> Context:
    return Context(now, SYDNEY, kw.get("outdoor"), None, kw.get("price"), [], sun_days() if sun is None else sun)


def clock(text: str) -> TimeRef:
    return TimeRef("clock", time=text)


def sun(event: str, offset: int = 0) -> TimeRef:
    return TimeRef("sun", event=event, offset_minutes=offset)


def schedule(trigger: Trigger, *, limits: Limits | None = None, conditions=()) -> Schedule:
    return Schedule(
        id=1, instance_id=1, name="s", enabled=True, notify_email=False, created_at=datetime(2026, 1, 1, tzinfo=UTC),
        trigger=trigger, conditions=list(conditions), actions=[ActionSpec("system_on")],
        restore=RestoreSpec(True, "after_minutes", 60, None, [], []), retry=RetrySpec(3, 60, 30),
        limits=limits or Limits(),
    )


def run(id_: int, kind: str, trigger_at: datetime, finished_at: datetime | None, outcome: str = "ok", schedule_id: int = 1) -> Run:
    return Run(id_, schedule_id, kind, trigger_at, None, finished_at, outcome, None, 1, None, None)


# Time references


def test_a_clock_time_is_an_instant_on_the_local_date():
    assert ref_instant(clock("18:00"), date(2026, 11, 14), ctx(local(14, 12))) == local(14, 18)


def test_a_sun_time_is_the_event_plus_its_offset():
    context = ctx(local(14, 12))
    assert ref_instant(sun("sunset", -30), date(2026, 11, 14), context) == local(14, 19)
    assert ref_instant(sun("sunrise", 15), date(2026, 11, 14), context) == local(14, 5, 45)


def test_a_sun_time_is_none_without_sun_data_for_that_day():
    assert ref_instant(sun("sunset"), date(2026, 11, 14), ctx(local(14, 12), sun=[])) is None
    assert ref_instant(sun("sunset"), date(2026, 12, 25), ctx(local(14, 12))) is None
    polar = [SunDay(date(2026, 11, 14), None, None)]
    assert ref_instant(sun("sunrise"), date(2026, 11, 14), ctx(local(14, 12), sun=polar)) is None


@pytest.mark.parametrize(
    ("ref", "after", "expected"),
    [
        (clock("06:00"), local(14, 10), local(15, 6)),
        (clock("06:00"), local(14, 5), local(14, 6)),
        (clock("06:00"), local(14, 6), local(15, 6)),
        (sun("sunrise"), local(14, 10), local(15, 5, 30)),
        (sun("sunrise"), local(14, 3), local(14, 5, 30)),
        (sun("sunset", -30), local(14, 18, 59), local(14, 19)),
    ],
)
def test_next_after_is_the_first_occurrence_after_an_instant(ref, after, expected):
    assert next_after(ref, after, ctx(local(14, 12))) == expected


def test_next_after_is_none_when_the_sun_never_gives_a_time():
    assert next_after(sun("sunrise"), local(14, 10), ctx(local(14, 12), sun=[])) is None


# Sun trigger


def test_a_sun_trigger_is_due_at_the_event_within_the_grace_period():
    sched = schedule(Trigger("sun", event="sunset", offset_minutes=-30, days_of_week=list(range(7))))
    now = local(14, 19, 4)
    got = due_triggers(sched, ctx(now), now, set(), True)
    assert [(d.trigger_at, d.label) for d in got] == [(local(14, 19), "sunset -30 min")]
    assert due_triggers(sched, ctx(now), now, {local(14, 19)}, True) == []


def test_a_sun_trigger_runs_only_on_its_days():
    now = local(14, 19, 32)  # 14 November 2026 is a Saturday, day 5
    on_fridays = schedule(Trigger("sun", event="sunset", days_of_week=[4]))
    assert due_triggers(on_fridays, ctx(now), now, set(), True) == []
    on_saturdays = schedule(Trigger("sun", event="sunset", days_of_week=[5]))
    assert len(due_triggers(on_saturdays, ctx(now), now, set(), True)) == 1


def test_a_sun_trigger_ten_minutes_late_is_missed_and_one_without_sun_data_is_neither():
    sched = schedule(Trigger("sun", event="sunrise", days_of_week=list(range(7))))
    now = local(14, 6, 30)
    assert due_triggers(sched, ctx(now), now, set(), True) == []
    assert [m.trigger_at for m in missed_triggers(sched, ctx(now), now, set())] == [local(14, 5, 30)]
    empty = ctx(now, sun=[])
    assert due_triggers(sched, empty, now, set(), True) == []
    assert missed_triggers(sched, empty, now, set()) == []


# Time-of-day row


def row(start: TimeRef, end: TimeRef, enabled: bool = True) -> Condition:
    return Condition(kind="time_of_day", start=start, end=end, enabled=enabled)


@pytest.mark.parametrize(
    ("start", "end", "at", "passes"),
    [
        (clock("18:00"), clock("23:00"), local(14, 19), True),
        (clock("18:00"), clock("23:00"), local(14, 17), False),
        (clock("18:00"), clock("23:00"), local(14, 23), False),
        (clock("22:00"), clock("06:00"), local(14, 23, 30), True),
        (clock("22:00"), clock("06:00"), local(14, 3), True),
        (clock("22:00"), clock("06:00"), local(14, 12), False),
        (sun("sunset", -30), clock("23:00"), local(14, 19, 10), True),
        (sun("sunset", -30), clock("23:00"), local(14, 18, 50), False),
        (sun("sunset"), sun("sunrise"), local(14, 21), True),
        (sun("sunset"), sun("sunrise"), local(14, 4), True),
        (sun("sunset"), sun("sunrise"), local(14, 12), False),
    ],
)
def test_time_of_day_row_passes_between_its_times(start, end, at, passes):
    assert evaluate_conditions([row(start, end)], {}, set(), ctx(at)).passed is passes


def test_a_failed_time_of_day_row_says_why():
    result = evaluate_conditions([row(clock("18:00"), clock("23:00"))], {}, set(), ctx(local(14, 17)))
    assert result.reason == "time 17:00 is outside 18:00 to 23:00"


def test_a_switched_off_time_of_day_row_is_ignored():
    assert evaluate_conditions([row(clock("18:00"), clock("23:00"), enabled=False)], {}, set(), ctx(local(14, 12))).passed


def test_a_sun_row_without_sun_data_fails_a_condition_but_never_holds_a_restore():
    conditions = [row(sun("sunset"), clock("23:00"))]
    context = ctx(local(14, 20), sun=[])
    assert evaluate_conditions(conditions, {}, set(), context).passed is False
    assert restore_gate_passes(conditions, {}, set(), context) is True


# Level triggers: armed and re-arm


def temp(op="gt", value=30.0, rearm=None) -> Trigger:
    return Trigger("temperature", reading="outdoor_temp_c", op=op, value=value, rearm=rearm)


def test_a_level_trigger_fires_only_while_armed():
    sched = schedule(temp())
    now = datetime(2026, 11, 14, 3, 12, 40, tzinfo=UTC)
    context = ctx(now, outdoor=31.0)
    assert [d.trigger_at for d in due_triggers(sched, context, now, set(), True)] == [datetime(2026, 11, 14, 3, 10, tzinfo=UTC)]
    assert due_triggers(sched, context, now, set(), False) == []
    assert due_triggers(sched, ctx(now, outdoor=29.0), now, set(), True) == []


@pytest.mark.parametrize(
    ("trigger", "outdoor", "expected"),
    [
        (temp(), 29.0, True),
        (temp(), 30.0, True),
        (temp(), 31.0, False),
        (temp(rearm=28.0), 29.0, False),
        (temp(rearm=28.0), 28.0, True),
        (temp(rearm=28.0), 27.0, True),
        (temp(op="lt", value=10.0), 11.0, True),
        (temp(op="lt", value=10.0, rearm=12.0), 11.0, False),
        (temp(op="lt", value=10.0, rearm=12.0), 12.0, True),
        (temp(), None, False),
    ],
)
def test_rearm_due_when_the_reading_is_back_past_the_rearm_value(trigger, outdoor, expected):
    now = datetime(2026, 11, 14, 3, 12, tzinfo=UTC)
    assert rearm_due(schedule(trigger), ctx(now, outdoor=outdoor), {}, set()) is expected


def test_a_stale_device_reading_never_rearms():
    sched = schedule(Trigger("temperature", reading="indoor_temp_c", op="gt", value=28.0, rearm=26.0))
    now = datetime(2026, 11, 14, 3, 12, tzinfo=UTC)
    assert rearm_due(sched, ctx(now), {"indoor_temp_c": 25.0}, set()) is True
    assert rearm_due(sched, ctx(now), {"indoor_temp_c": 25.0}, {"indoor_temp_c"}) is False


def test_price_rearm_compares_the_current_price():
    sched = schedule(Trigger("price_above", c_per_kwh=40.0, rearm=35.0))
    now = datetime(2026, 11, 14, 3, 12, tzinfo=UTC)
    assert rearm_due(sched, ctx(now, price=36.0), {}, set()) is False
    assert rearm_due(sched, ctx(now, price=35.0), {}, set()) is True


def test_rearm_is_never_due_for_a_trigger_with_no_level():
    now = datetime(2026, 11, 14, 3, 12, tzinfo=UTC)
    assert rearm_due(schedule(Trigger("window_start", window_id=1)), ctx(now), {}, set()) is False


# Limits


def test_no_limits_never_block():
    assert blocked_until(schedule(temp()), ctx(local(14, 12)), [run(1, "action", local(14, 10), local(14, 10))]) is None


def test_the_minimum_gap_counts_from_the_last_restore():
    sched = schedule(temp(), limits=Limits(min_gap_minutes=30))
    runs = [run(1, "action", local(14, 10), local(14, 10, 1)), run(2, "restore", local(14, 11), local(14, 11, 2))]
    assert blocked_until(sched, ctx(local(14, 12)), runs) == local(14, 11, 32)


def test_the_minimum_gap_counts_from_the_last_run_when_nothing_is_restored():
    sched = schedule(temp(), limits=Limits(min_gap_minutes=30))
    assert blocked_until(sched, ctx(local(14, 12)), [run(1, "action", local(14, 10), local(14, 10, 1))]) == local(14, 10, 31)


def test_skipped_missed_test_and_other_schedules_runs_do_not_count():
    sched = schedule(temp(), limits=Limits(min_gap_minutes=30, run_again_after=clock("06:00")))
    runs = [
        run(1, "action", local(14, 10), local(14, 10), "skipped"),
        run(2, "test", local(14, 10), local(14, 10)),
        run(3, "action", local(14, 10), local(14, 10), schedule_id=2),
    ]
    assert blocked_until(sched, ctx(local(14, 12)), runs) is None


def test_run_again_after_waits_for_the_next_chosen_time_after_the_last_run():
    sched = schedule(temp(), limits=Limits(run_again_after=sun("sunrise")))
    runs = [run(1, "action", local(14, 14), local(14, 14, 1))]
    assert blocked_until(sched, ctx(local(14, 15)), runs) == local(15, 5, 30)


def test_the_later_of_the_gap_and_the_lockout_wins():
    sched = schedule(temp(), limits=Limits(min_gap_minutes=1440, run_again_after=clock("06:00")))
    runs = [run(1, "action", local(14, 14), local(14, 14, 1))]
    assert blocked_until(sched, ctx(local(14, 15)), runs) == local(15, 14, 1)


def test_an_unknown_lockout_time_does_not_block():
    sched = schedule(temp(), limits=Limits(run_again_after=sun("sunrise")))
    runs = [run(1, "action", local(14, 14), local(14, 14, 1))]
    assert blocked_until(sched, ctx(local(14, 15), sun=[]), runs) is None


def test_a_blocked_level_trigger_is_not_due_and_stays_silent():
    sched = schedule(temp(), limits=Limits(min_gap_minutes=30))
    now = datetime(2026, 11, 14, 3, 12, 40, tzinfo=UTC)
    runs = [run(1, "restore", now - timedelta(minutes=20), now - timedelta(minutes=19))]
    assert due_triggers(sched, ctx(now, outdoor=31.0), now, set(), True, runs=runs) == []
    later = now + timedelta(minutes=15)
    assert len(due_triggers(sched, ctx(later, outdoor=31.0), later, set(), True, runs=runs)) == 1


def test_a_blocked_time_trigger_is_returned_with_the_reason():
    sched = schedule(
        Trigger("sun", event="sunset", days_of_week=list(range(7))), limits=Limits(run_again_after=sun("sunrise"))
    )
    now = local(14, 19, 32)
    runs = [run(1, "action", local(14, 15), local(14, 15, 1))]
    [due] = due_triggers(sched, ctx(now), now, set(), True, runs=runs)
    assert due.trigger_at == local(14, 19, 30)
    assert due.blocked == "waiting until Sun 05:30"
