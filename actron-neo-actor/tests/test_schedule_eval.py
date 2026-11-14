from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from actron_neo_actor.models import (
    ActionSpec,
    Condition,
    Context,
    RestoreSpec,
    RetrySpec,
    Run,
    Schedule,
    Trigger,
    WindowOccurrence,
)
from actron_neo_actor.schedule_eval import (
    ConditionResult,
    DueTrigger,
    due_triggers,
    early_restore_due,
    evaluate_conditions,
    missed_triggers,
    restore_gate_passes,
    restore_time,
    week_number,
)

SYDNEY = "Australia/Sydney"


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def restore(when="window_end", enabled=True, minutes=None, time=None, only_if=(), early_if=()) -> RestoreSpec:
    return RestoreSpec(enabled, when, minutes, time, list(only_if), list(early_if))


def make_schedule(trigger: Trigger, *, created_at=None, enabled=True, rest=None) -> Schedule:
    return Schedule(
        id=1,
        instance_id=1,
        name="s",
        enabled=enabled,
        notify_email=False,
        created_at=created_at or utc(2026, 1, 1),
        trigger=trigger,
        conditions=[],
        actions=[ActionSpec("turn_on")],
        restore=rest or restore(),
        retry=RetrySpec(3, 60, 30),
    )


def make_context(now, windows=(), price=None, tz=SYDNEY, outdoor=None, soc=None) -> Context:
    return Context(now, tz, outdoor, soc, price, list(windows))


def run_at(trigger_at: datetime) -> Run:
    return Run(1, 1, "action", trigger_at, None, None, "ok", None, 1, None, None)


WINDOW = WindowOccurrence(7, "Solar soak", utc(2026, 11, 5, 1, 0), utc(2026, 11, 5, 5, 0))
START = Trigger("window_start", window_id=7)
NOW = utc(2026, 11, 5, 1, 5)


def test_window_start_due_inside_grace():
    got = due_triggers(make_schedule(START), make_context(NOW, [WINDOW]), NOW, set(), True)
    assert got == [DueTrigger(utc(2026, 11, 5, 1, 0), "Solar soak")]


def test_window_start_missed_after_grace():
    now = utc(2026, 11, 5, 1, 30)
    ctx = make_context(now, [WINDOW])
    assert due_triggers(make_schedule(START), ctx, now, set(), True) == []
    assert missed_triggers(make_schedule(START), ctx, now, set()) == [DueTrigger(WINDOW.start, "Solar soak")]


def test_ten_minute_boundary():
    sched = make_schedule(START)
    on_edge = WINDOW.start + timedelta(minutes=10)
    ctx = make_context(on_edge, [WINDOW])
    assert len(due_triggers(sched, ctx, on_edge, set(), True)) == 1
    assert missed_triggers(sched, ctx, on_edge, set()) == []
    over = on_edge + timedelta(seconds=1)
    assert due_triggers(sched, ctx, over, set(), True) == []
    assert len(missed_triggers(sched, ctx, over, set())) == 1


def test_existing_trigger_not_due_twice():
    ctx = make_context(NOW, [WINDOW])
    assert due_triggers(make_schedule(START), ctx, NOW, {WINDOW.start}, True) == []
    later = utc(2026, 11, 5, 2, 0)
    assert missed_triggers(make_schedule(START), make_context(later, [WINDOW]), later, {WINDOW.start}) == []


def test_window_end_trigger_uses_end():
    now = utc(2026, 11, 5, 5, 2)
    sched = make_schedule(Trigger("window_end", window_id=7))
    got = due_triggers(sched, make_context(now, [WINDOW]), now, set(), True)
    assert got == [DueTrigger(WINDOW.end, "Solar soak")]


def test_other_window_ignored_and_future_window_not_due():
    other = WindowOccurrence(8, "x", WINDOW.start, WINDOW.end)
    early = utc(2026, 11, 5, 0, 55)
    assert due_triggers(make_schedule(START), make_context(NOW, [other]), NOW, set(), True) == []
    assert due_triggers(make_schedule(START), make_context(early, [WINDOW]), early, set(), True) == []


def test_window_crossing_midnight_uses_exact_start():
    # 23:00-02:00 Sydney (AEDT, +11) on 5 Nov -> 12:00Z to 15:00Z.
    win = WindowOccurrence(3, "Night", utc(2026, 11, 5, 12, 0), utc(2026, 11, 5, 15, 0))
    ctx = make_context(utc(2026, 11, 5, 15, 3), [win])
    end = Trigger("window_end", window_id=3)
    got = due_triggers(make_schedule(end), ctx, ctx.now, set(), True)
    assert got == [DueTrigger(utc(2026, 11, 5, 15, 0), "Night")]


def test_created_at_floor_hides_earlier_windows():
    now = utc(2026, 11, 5, 1, 30)
    ctx = make_context(now, [WINDOW])
    sched = make_schedule(START, created_at=utc(2026, 11, 5, 1, 1))
    assert missed_triggers(sched, ctx, now, set()) == []
    sched = make_schedule(START, created_at=utc(2026, 11, 5, 1, 0))
    assert len(missed_triggers(sched, ctx, now, set())) == 1


def test_older_than_24h_is_not_reported():
    now = utc(2026, 11, 6, 1, 1)
    assert missed_triggers(make_schedule(START), make_context(now, [WINDOW]), now, set()) == []


def test_disabled_schedule_yields_nothing():
    sched = make_schedule(START, enabled=False)
    assert due_triggers(sched, make_context(NOW, [WINDOW]), NOW, set(), True) == []
    later = utc(2026, 11, 5, 3, 0)
    assert missed_triggers(sched, make_context(later, [WINDOW]), later, set()) == []


def test_time_trigger_site_local():
    # 12:00 Sydney on 5 Nov 2026 (AEDT) is 01:00Z. 2026-11-05 is a Thursday (3).
    sched = make_schedule(Trigger("time", time="12:00", days_of_week=[3]))
    now = utc(2026, 11, 5, 1, 4)
    got = due_triggers(sched, make_context(now), now, set(), True)
    assert got == [DueTrigger(utc(2026, 11, 5, 1, 0), "12:00")]
    other_day = make_schedule(Trigger("time", time="12:00", days_of_week=[0, 1]))
    assert due_triggers(other_day, make_context(now), now, set(), True) == []


def test_time_trigger_yesterday_is_missed():
    sched = make_schedule(Trigger("time", time="12:00", days_of_week=list(range(7))))
    now = utc(2026, 11, 5, 9, 0)
    got = missed_triggers(sched, make_context(now), now, set())
    assert got == [DueTrigger(utc(2026, 11, 5, 1, 0), "12:00")]


def test_time_trigger_in_dst_gap_moves_to_end_of_gap():
    # Sydney springs forward 2026-10-04 02:00 -> 03:00 (Sunday, weekday 6).
    sched = make_schedule(Trigger("time", time="02:30", days_of_week=[6]))
    now = utc(2026, 10, 3, 16, 5)  # 03:05 AEDT
    got = due_triggers(sched, make_context(now), now, set(), True)
    assert got == [DueTrigger(utc(2026, 10, 3, 16, 0), "02:30")]


def test_time_trigger_ambiguous_takes_first_occurrence():
    # Sydney falls back 2026-04-05 03:00 AEDT -> 02:00 AEST; 02:30 happens twice.
    sched = make_schedule(Trigger("time", time="02:30", days_of_week=[6]), created_at=utc(2026, 1, 1))
    now = utc(2026, 4, 4, 15, 35)
    got = due_triggers(sched, make_context(now), now, set(), True)
    assert got == [DueTrigger(utc(2026, 4, 4, 15, 30), "02:30")]


def test_price_above_fires_once_per_arming():
    sched = make_schedule(Trigger("price_above", c_per_kwh=30.0))
    now = utc(2026, 11, 5, 3, 12, 40)
    ctx = make_context(now, price=45.0)
    got = due_triggers(sched, ctx, now, set(), True)
    assert got == [DueTrigger(utc(2026, 11, 5, 3, 10), got[0].label)]
    assert due_triggers(sched, ctx, now, set(), False) == []
    assert due_triggers(sched, ctx, now, {utc(2026, 11, 5, 3, 10)}, True) == []
    assert due_triggers(sched, make_context(now, price=30.0), now, set(), True) == []
    assert due_triggers(sched, make_context(now, price=None), now, set(), True) == []
    assert missed_triggers(sched, ctx, now, set()) == []


def test_temperature_trigger_fires_once_per_arming():
    sched = make_schedule(Trigger("temperature", reading="indoor_temp_c", op="gt", value=28.0))
    now = utc(2026, 11, 5, 3, 12, 40)
    ctx = make_context(now)
    hot, cool = {"indoor_temp_c": 29.5}, {"indoor_temp_c": 27.0}
    got = due_triggers(sched, ctx, now, set(), True, hot, set())
    assert got == [DueTrigger(utc(2026, 11, 5, 3, 10), "indoor_temp_c above 28")]
    assert due_triggers(sched, ctx, now, set(), False, hot, set()) == []
    assert due_triggers(sched, ctx, now, set(), True, cool, set()) == []
    assert due_triggers(sched, ctx, now, set(), True, hot, {"indoor_temp_c"}) == []
    assert due_triggers(sched, ctx, now, set(), True) == []


def test_temperature_trigger_falls_below_and_uses_the_site_outdoor_reading():
    sched = make_schedule(Trigger("temperature", reading="outdoor_temp_c", op="lt", value=10.0))
    now = utc(2026, 11, 5, 3, 12, 40)
    got = due_triggers(sched, make_context(now, outdoor=8.0), now, set(), True)
    assert [d.label for d in got] == ["outdoor_temp_c below 10"]
    assert due_triggers(sched, make_context(now, outdoor=12.0), now, set(), True) == []


@pytest.mark.parametrize(
    ("rest", "expected"),
    [
        (restore("window_end"), utc(2026, 11, 5, 5, 0)),
        (restore("after_minutes", minutes=90), utc(2026, 11, 5, 2, 30)),
        (restore("at_time", time="18:00"), utc(2026, 11, 5, 7, 0)),
        (restore("at_time", time="09:00"), utc(2026, 11, 5, 22, 0)),
        (restore("conditions_only"), utc(2026, 11, 6, 1, 0)),
        (restore("after_minutes", enabled=False, minutes=90), None),
    ],
)
def test_restore_time_per_mode(rest, expected):
    sched = make_schedule(START, rest=rest)
    assert restore_time(sched, run_at(WINDOW.start), make_context(NOW, [WINDOW])) == expected


def test_restore_time_window_end_falls_back_to_24h():
    sched = make_schedule(START)
    trig = utc(2026, 11, 4, 1, 0)
    assert restore_time(sched, run_at(trig), make_context(NOW, [WINDOW])) == utc(2026, 11, 5, 1, 0)


def test_restore_at_time_equal_to_trigger_is_same_instant():
    sched = make_schedule(START, rest=restore("at_time", time="12:00"))
    assert restore_time(sched, run_at(WINDOW.start), make_context(NOW, [WINDOW])) == WINDOW.start


def test_restore_at_time_dst_gap():
    sched = make_schedule(START, rest=restore("at_time", time="02:30"))
    trig = utc(2026, 10, 3, 10, 0)  # 20:00 Sydney on 3 Oct
    got = restore_time(sched, run_at(trig), make_context(NOW))
    assert got == utc(2026, 10, 3, 16, 0)


def test_restore_at_time_ambiguous_first_occurrence():
    sched = make_schedule(START, rest=restore("at_time", time="02:30"))
    trig = utc(2026, 4, 4, 10, 0)
    assert restore_time(sched, run_at(trig), make_context(NOW)) == utc(2026, 4, 4, 15, 30)


HOT = Condition("outdoor_temp_c", "gt", 35.0)


def test_condition_passes_and_empty_passes():
    ctx = make_context(NOW, outdoor=40.0)
    assert evaluate_conditions([HOT], {}, set(), ctx).passed
    assert evaluate_conditions([], {}, set(), ctx).passed


def test_condition_fails_with_reason():
    res = evaluate_conditions([HOT], {}, set(), make_context(NOW, outdoor=22.0))
    assert (res.passed, res.reason) == (False, "outdoor_temp_c 22.0 is not above 35.0")
    cold = Condition("battery_soc_pct", "lt", 20.0)
    res = evaluate_conditions([cold], {}, set(), make_context(NOW, soc=55.0))
    assert res.reason == "battery_soc_pct 55.0 is not below 20.0"


def test_device_reading_beats_context():
    ctx = make_context(NOW, outdoor=40.0)
    res = evaluate_conditions([HOT], {"outdoor_temp_c": 20.0}, set(), ctx)
    assert not res.passed


def test_stale_reading_fails_action_condition():
    res = evaluate_conditions([HOT], {"outdoor_temp_c": 40.0}, {"outdoor_temp_c"}, make_context(NOW, outdoor=40.0))
    assert (res.passed, res.reason) == (False, "reading unavailable: outdoor_temp_c")


def test_missing_and_nonfinite_fail_action_condition():
    res = evaluate_conditions([HOT], {}, set(), make_context(NOW))
    assert res.reason == "reading unavailable: outdoor_temp_c"
    res = evaluate_conditions([HOT], {"outdoor_temp_c": float("nan")}, set(), make_context(NOW))
    assert res.reason == "reading unavailable: outdoor_temp_c"
    res = evaluate_conditions([HOT], {}, set(), make_context(NOW, outdoor=float("inf")))
    assert not res.passed


def test_zone_target_condition_uses_target_reading():
    cond = Condition("zone_temp_c", "gt", 26.0, target="Study")
    readings = {"zone_temp_c": 18.0, "zone_temp_c@Study": 28.0}
    assert evaluate_conditions([cond], readings, set(), make_context(NOW)).passed
    res = evaluate_conditions([cond], {"zone_temp_c": 30.0}, set(), make_context(NOW))
    assert res.reason == "reading unavailable: zone_temp_c@Study"
    res = evaluate_conditions([cond], readings, {"zone_temp_c@Study"}, make_context(NOW))
    assert res.reason == "reading unavailable: zone_temp_c@Study"


def test_missing_reading_does_not_block_restore():
    ctx = make_context(NOW)
    assert restore_gate_passes([HOT], {}, set(), ctx)
    assert restore_gate_passes([HOT], {"outdoor_temp_c": 50.0}, {"outdoor_temp_c"}, ctx)
    assert restore_gate_passes([HOT], {"outdoor_temp_c": float("nan")}, set(), ctx)
    assert not restore_gate_passes([HOT], {"outdoor_temp_c": 20.0}, set(), ctx)
    assert restore_gate_passes([], {}, set(), ctx)


def test_early_restore_when_indoor_above():
    cond = Condition("indoor_temp_c", "gt", 25.0)
    sched = make_schedule(START, rest=restore(early_if=[cond]))
    ctx = make_context(NOW)
    assert early_restore_due(sched, {"indoor_temp_c": 26.0}, set(), ctx)
    assert not early_restore_due(sched, {"indoor_temp_c": 24.0}, set(), ctx)
    assert not early_restore_due(sched, {}, set(), ctx)
    assert not early_restore_due(sched, {"indoor_temp_c": 26.0}, {"indoor_temp_c"}, ctx)
    assert not early_restore_due(make_schedule(START), {"indoor_temp_c": 26.0}, set(), ctx)


def test_restore_at_time_ambiguous_later_pass_still_ahead():
    # Trigger 02:45 AEDT (first pass); 02:30 AEST (second pass) is 16:30Z, still ahead.
    sched = make_schedule(START, rest=restore("at_time", time="02:30"))
    trig = utc(2026, 4, 4, 15, 45)
    assert restore_time(sched, run_at(trig), make_context(NOW)) == utc(2026, 4, 4, 16, 30)


def test_restore_at_time_ambiguous_after_second_pass_goes_next_day():
    sched = make_schedule(START, rest=restore("at_time", time="02:30"))
    trig = utc(2026, 4, 4, 16, 31)
    assert restore_time(sched, run_at(trig), make_context(NOW)) == utc(2026, 4, 5, 16, 30)


def test_unsupported_op_never_raises():
    bad = Condition("outdoor_temp_c", "gte", 35.0)
    ctx = make_context(NOW, outdoor=40.0)
    res = evaluate_conditions([bad], {}, set(), ctx)
    assert (res.passed, res.reason) == (False, "unsupported comparison: gte")
    assert restore_gate_passes([bad], {}, set(), ctx)
    sched = make_schedule(START, rest=restore(early_if=[bad]))
    assert not early_restore_due(sched, {}, set(), ctx)


HOT_OFF = Condition("outdoor_temp_c", "gt", 35.0, enabled=False)


def test_disabled_failing_condition_does_not_fail_the_action():
    ctx = make_context(NOW, outdoor=22.0)
    assert evaluate_conditions([HOT_OFF], {}, set(), ctx).passed
    res = evaluate_conditions([HOT_OFF, HOT], {}, set(), ctx)
    assert res.reason == "outdoor_temp_c 22.0 is not above 35.0"


def test_disabled_missing_reading_does_not_block():
    gone = Condition("zone_temp_c", "gt", 26.0, target="Study", enabled=False)
    ctx = make_context(NOW)
    assert evaluate_conditions([gone], {}, set(), ctx) == ConditionResult(True)
    assert restore_gate_passes([HOT_OFF], {"outdoor_temp_c": 20.0}, set(), ctx)


def test_disabled_rows_do_not_count_toward_early_restore():
    warm = Condition("indoor_temp_c", "gt", 25.0)
    warm_off = Condition("indoor_temp_c", "gt", 25.0, enabled=False)
    cold_off = Condition("indoor_temp_c", "lt", 10.0, enabled=False)
    ctx = make_context(NOW)
    readings = {"indoor_temp_c": 26.0}
    assert early_restore_due(make_schedule(START, rest=restore(early_if=[warm, cold_off])), readings, set(), ctx)
    assert not early_restore_due(make_schedule(START, rest=restore(early_if=[warm_off])), readings, set(), ctx)


def test_legacy_condition_without_flag_is_enabled():
    legacy = Condition.from_json({"reading": "outdoor_temp_c", "op": "gt", "value": 35.0})
    assert legacy.enabled is True
    assert not evaluate_conditions([legacy], {}, set(), make_context(NOW, outdoor=22.0)).passed


# --- calendar and demand window conditions ---------------------------------


def at_sydney(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=ZoneInfo(SYDNEY)).astimezone(UTC)


def calendar_result(cond: Condition, now: datetime, windows=()):
    return evaluate_conditions([cond], {}, set(), make_context(now, windows=windows))


def test_week_number_epoch_anchor():
    assert week_number(date(1970, 1, 1)) == 0
    assert week_number(date(1970, 1, 4)) == 0
    assert week_number(date(1970, 1, 5)) == 1
    assert week_number(date(2026, 11, 9)) == week_number(date(2026, 11, 15)) == 2967
    assert week_number(date(2026, 11, 16)) == 2968


def test_week_row_parity_and_reason():
    saturday = at_sydney(2026, 11, 14)
    assert calendar_result(Condition(kind="week", parity="odd"), saturday).passed
    res = calendar_result(Condition(kind="week", parity="even"), saturday)
    assert (res.passed, res.reason) == (False, "week is odd, not even")


def test_week_row_uses_site_timezone():
    sunday_utc_monday_sydney = utc(2026, 11, 15, 14)
    assert not calendar_result(Condition(kind="week", parity="odd"), sunday_utc_monday_sydney).passed
    assert calendar_result(Condition(kind="week", parity="even"), sunday_utc_monday_sydney).passed


def test_week_alternates_across_year_end():
    assert calendar_result(Condition(kind="week", parity="even"), at_sydney(2027, 1, 3)).passed
    assert calendar_result(Condition(kind="week", parity="odd"), at_sydney(2027, 1, 4)).passed


def test_months_row():
    summer = Condition(kind="months", op="in", values=(12, 1, 2))
    res = calendar_result(summer, at_sydney(2026, 7, 10))
    assert (res.passed, res.reason) == (False, "month Jul is not in Dec, Jan, Feb")
    assert calendar_result(summer, at_sydney(2027, 1, 10)).passed
    res = calendar_result(Condition(kind="months", op="not_in", values=(7, 8)), at_sydney(2026, 7, 10))
    assert res.reason == "month Jul is in Jul, Aug"


def test_days_row():
    weekdays = Condition(kind="days", op="in", values=(0, 1, 2, 3, 4))
    res = calendar_result(weekdays, at_sydney(2026, 11, 14))
    assert (res.passed, res.reason) == (False, "day Sat is not in Mon, Tue, Wed, Thu, Fri")
    res = calendar_result(Condition(kind="days", op="not_in", values=(5, 6)), at_sydney(2026, 11, 14))
    assert res.reason == "day Sat is in Sat, Sun"
    assert calendar_result(weekdays, at_sydney(2026, 11, 13)).passed


def test_demand_window_row():
    peak = WindowOccurrence(7, "Peak", utc(2026, 11, 5, 1), utc(2026, 11, 5, 3))
    inside = utc(2026, 11, 5, 2)
    idle = Condition(kind="demand_window", op="not_active")
    res = calendar_result(idle, inside, [peak])
    assert (res.passed, res.reason) == (False, "demand window Peak is active")
    assert calendar_result(Condition(kind="demand_window", op="active"), inside, [peak]).passed
    other = Condition(kind="demand_window", op="active", window_id=8)
    assert calendar_result(other, inside, [peak]).reason == "no demand window is active"
    assert calendar_result(Condition(kind="demand_window", op="not_active", window_id=8), inside, [peak]).passed
    assert calendar_result(idle, inside).passed
    assert not calendar_result(idle, utc(2026, 11, 5, 1), [peak]).passed
    assert calendar_result(idle, utc(2026, 11, 5, 3), [peak]).passed


def test_calendar_rows_ignore_stale_and_missing_readings():
    ctx = make_context(at_sydney(2026, 11, 14))
    assert evaluate_conditions([Condition(kind="week", parity="odd")], {}, {"outdoor_temp_c"}, ctx).passed


def test_restore_gate_evaluates_calendar_rows():
    ctx = make_context(at_sydney(2026, 11, 14))
    assert not restore_gate_passes([Condition(kind="week", parity="even")], {}, set(), ctx)
    assert restore_gate_passes([Condition(kind="week", parity="odd")], {}, set(), ctx)


def test_switched_off_calendar_row_is_ignored():
    off = Condition(kind="week", parity="even", enabled=False)
    assert calendar_result(off, at_sydney(2026, 11, 14)).passed


def test_condition_from_json_old_and_new():
    old = Condition.from_json({"reading": "outdoor_temp_c", "op": "gt", "value": 30.0})
    assert old.kind == "reading"
    new = Condition.from_json({"kind": "months", "op": "in", "values": [12, 1], "enabled": True})
    assert (new.kind, new.values) == ("months", (12, 1))
