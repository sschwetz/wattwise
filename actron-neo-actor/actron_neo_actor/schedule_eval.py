"""Pure schedule evaluation: which triggers are due or missed, when a restore
falls, and whether conditions pass. None of the functions read the clock or
do I/O; callers pass `now` where time matters."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from actron_neo_actor.models import Condition, Context, Run, Schedule, TimeRef, Trigger, WindowOccurrence

GRACE = timedelta(minutes=10)
LOOKBACK = timedelta(hours=24)
# The latest a restore falls: a window end with no matching occurrence, and a
# conditions-only restore whose condition never holds, both restore a day after the trigger.
RESTORE_CAP = timedelta(hours=24)
PRICE_STEP_SECONDS = 300


@dataclass(frozen=True)
class DueTrigger:
    trigger_at: datetime
    label: str
    # Set when a limit on the schedule holds this trigger back: why, for the skipped run that records it.
    blocked: str | None = None


@dataclass(frozen=True)
class ConditionResult:
    passed: bool
    reason: str | None = None
    # A failure for want of data (sun times) rather than because the condition is false.
    unavailable: bool = False


# --- local clock helpers -------------------------------------------------


def _parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _gap_end(naive: datetime, tz: ZoneInfo) -> datetime:
    """The instant the clock jumps over `naive`: where the offset changes."""
    hi = naive.replace(tzinfo=tz).astimezone(UTC)
    lo = hi - timedelta(hours=3)
    before = lo.astimezone(tz).utcoffset()
    while hi - lo > timedelta(seconds=1):
        mid = lo + (hi - lo) / 2
        if mid.astimezone(tz).utcoffset() == before:
            lo = mid
        else:
            hi = mid
    return hi.replace(microsecond=0)


def _local_instants(day: date, clock: time, tz: ZoneInfo) -> list[datetime]:
    """UTC instants, earliest first, at which the site clock reads `clock` on
    `day`. An ambiguous time gives two; a time in a DST gap gives the end of the gap."""
    naive = datetime.combine(day, clock)
    found: list[datetime] = []
    for fold in (0, 1):
        utc = naive.replace(tzinfo=tz, fold=fold).astimezone(UTC)
        if utc.astimezone(tz).replace(tzinfo=None) == naive and utc not in found:
            found.append(utc)
    return sorted(found) or [_gap_end(naive, tz)]


def _local_instant(day: date, clock: time, tz: ZoneInfo) -> datetime:
    """The first instant from `_local_instants`, used by the recurring `time` trigger."""
    return _local_instants(day, clock, tz)[0]


def _time_trigger_instants(
    days: list[int], clock: time, tz: ZoneInfo, start: datetime, end: datetime
) -> list[datetime]:
    first = start.astimezone(tz).date() - timedelta(days=1)
    last = end.astimezone(tz).date() + timedelta(days=1)
    out: list[datetime] = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        if day.weekday() in days:
            out.append(_local_instant(day, clock, tz))
    return out


# --- time references ----------------------------------------------------


def ref_instant(ref: TimeRef, day: date, context: Context) -> datetime | None:
    """The instant `ref` falls on local date `day`, or None when the sun gives no time that day or the context has none."""
    if ref.kind == "clock":
        return _local_instant(day, _parse_hhmm(ref.time or ""), ZoneInfo(context.timezone))
    entry = next((d for d in context.sun if d.date == day), None)
    event = None if entry is None else (entry.sunrise if ref.event == "sunrise" else entry.sunset)
    return None if event is None else event + timedelta(minutes=ref.offset_minutes)


def next_after(ref: TimeRef, after: datetime, context: Context) -> datetime | None:
    """The first occurrence of `ref` after `after`, or None when none is known."""
    first = after.astimezone(ZoneInfo(context.timezone)).date()
    found = (ref_instant(ref, first + timedelta(days=n), context) for n in range(-1, 3))
    return min((at for at in found if at is not None and at > after), default=None)


def _ref_label(ref: TimeRef) -> str:
    if ref.kind == "clock":
        return ref.time or ""
    return f"{ref.event} {ref.offset_minutes:+d} min" if ref.offset_minutes else str(ref.event)


# --- triggers ------------------------------------------------------------


def _window_candidates(schedule: Schedule, context: Context) -> list[DueTrigger]:
    trigger = schedule.trigger
    use_start = trigger.type == "window_start"
    return [
        DueTrigger(w.start if use_start else w.end, w.label)
        for w in context.windows
        if w.window_id == trigger.window_id
    ]


def _time_candidates(schedule: Schedule, context: Context, now: datetime) -> list[DueTrigger]:
    trigger = schedule.trigger
    if trigger.time is None or trigger.days_of_week is None:
        return []
    instants = _time_trigger_instants(
        trigger.days_of_week, _parse_hhmm(trigger.time), ZoneInfo(context.timezone), now - LOOKBACK, now
    )
    return [DueTrigger(at, trigger.time) for at in instants]


def _sun_candidates(schedule: Schedule, context: Context, now: datetime) -> list[DueTrigger]:
    trigger = schedule.trigger
    if trigger.event is None or trigger.days_of_week is None:
        return []
    ref = TimeRef("sun", event=trigger.event, offset_minutes=trigger.offset_minutes)
    tz = ZoneInfo(context.timezone)
    first = (now - LOOKBACK).astimezone(tz).date() - timedelta(days=1)
    last = now.astimezone(tz).date() + timedelta(days=1)
    found = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        at = ref_instant(ref, day, context) if day.weekday() in trigger.days_of_week else None
        if at is not None:
            found.append(DueTrigger(at, _ref_label(ref)))
    return found


def _candidates(schedule: Schedule, context: Context, now: datetime, existing: set[datetime]) -> list[DueTrigger]:
    """Non-price triggers inside [max(created_at, now - 24h), now] not yet recorded."""
    if not schedule.enabled:
        return []
    kind = schedule.trigger.type
    if kind in ("window_start", "window_end"):
        found = _window_candidates(schedule, context)
    elif kind == "time":
        found = _time_candidates(schedule, context, now)
    elif kind == "sun":
        found = _sun_candidates(schedule, context, now)
    else:
        return []
    floor = max(schedule.created_at, now - LOOKBACK)
    kept = [d for d in found if floor <= d.trigger_at <= now and d.trigger_at not in existing]
    return sorted(kept, key=lambda d: d.trigger_at)


LEVEL_TRIGGERS = ("price_above", "temperature")


def _level_label(trigger: Trigger) -> str:
    if trigger.type == "price_above":
        return f"price above {trigger.c_per_kwh:g}"
    return f"{trigger.reading} {_WORDS.get(trigger.op or '', trigger.op)} {trigger.value:g}"


def _level_value(
    schedule: Schedule, context: Context, readings: Mapping[str, float], stale: set[str]
) -> float | None:
    """The price or temperature a level trigger watches, or None when it is unknown or the trigger has no level.
    A temperature uses the device reading first, then the site's."""
    trigger = schedule.trigger
    if trigger.type == "price_above":
        price = context.price_c_per_kwh
        return price if price is not None and math.isfinite(price) else None
    if trigger.type == "temperature" and trigger.reading:
        return _lookup(trigger.reading, readings, stale, context)
    return None


def _passes(trigger: Trigger, value: float) -> bool | None:
    """Whether `value` is past the trigger's level; None when the trigger has no level."""
    if trigger.type == "price_above":
        return None if trigger.c_per_kwh is None else value > trigger.c_per_kwh
    if trigger.op and trigger.value is not None:
        return _holds(Condition(trigger.reading or "", trigger.op, trigger.value), value)
    return None


def level_met(
    schedule: Schedule, context: Context, readings: Mapping[str, float], stale: set[str]
) -> bool | None:
    """Whether a price or temperature trigger's level is passed now; None when the value is unknown
    or the trigger is not a level trigger."""
    value = _level_value(schedule, context, readings, stale)
    return None if value is None else _passes(schedule.trigger, value)


def rearm_due(schedule: Schedule, context: Context, readings: Mapping[str, float], stale: set[str]) -> bool:
    """True when a level trigger that has fired may fire again. With a `rearm` value the reading must be
    back at or past it; without one it must no longer pass the level. An unknown reading never re-arms."""
    trigger = schedule.trigger
    value = _level_value(schedule, context, readings, stale)
    if trigger.type not in LEVEL_TRIGGERS or value is None:
        return False
    if trigger.rearm is None:
        return _passes(trigger, value) is False
    return value <= trigger.rearm if trigger.type == "price_above" or trigger.op == "gt" else value >= trigger.rearm


def _level_due(schedule: Schedule, now: datetime, existing: set[datetime], blocked: datetime | None) -> list[DueTrigger]:
    """One trigger per firing, stamped to the 5-minute step. A limit that still holds leaves the trigger armed
    and unrecorded, so it fires when the limit ends if the level is still passed."""
    step = int(now.timestamp()) // PRICE_STEP_SECONDS * PRICE_STEP_SECONDS
    at = datetime.fromtimestamp(step, UTC)
    if at in existing or (blocked is not None and now < blocked):
        return []
    return [DueTrigger(at, _level_label(schedule.trigger))]


def _block_reason(blocked: datetime | None, at: datetime, timezone: str) -> str | None:
    if blocked is None or at >= blocked:
        return None
    return f"waiting until {blocked.astimezone(ZoneInfo(timezone)):%a %H:%M}"


def due_triggers(
    schedule: Schedule,
    context: Context,
    now: datetime,
    existing_trigger_ats: set[datetime],
    armed: bool,
    readings: Mapping[str, float] | None = None,
    stale: set[str] | None = None,
    runs: Sequence[Run] = (),
) -> list[DueTrigger]:
    """Triggers at most 10 minutes old that have no run yet. A level trigger fires only while `armed`;
    `readings` and `stale` are the device's, for temperature triggers. `runs` are the instance's recent runs,
    for the schedule's limits. A time or window trigger a limit holds back is returned with `blocked` set."""
    if not schedule.enabled:
        return []
    blocked = blocked_until(schedule, context, runs)
    if schedule.trigger.type in LEVEL_TRIGGERS:
        met = level_met(schedule, context, readings or {}, stale or set())
        return _level_due(schedule, now, existing_trigger_ats, blocked) if met is True and armed else []
    found = _candidates(schedule, context, now, existing_trigger_ats)
    return [
        DueTrigger(d.trigger_at, d.label, _block_reason(blocked, d.trigger_at, context.timezone))
        for d in found
        if now - d.trigger_at <= GRACE
    ]


def missed_triggers(
    schedule: Schedule, context: Context, now: datetime, existing_trigger_ats: set[datetime]
) -> list[DueTrigger]:
    """Triggers more than 10 minutes old that have no run yet. Price crossings never qualify."""
    found = _candidates(schedule, context, now, existing_trigger_ats)
    return [d for d in found if now - d.trigger_at > GRACE]


# --- limits --------------------------------------------------------------


def blocked_until(schedule: Schedule, context: Context, runs: Sequence[Run]) -> datetime | None:
    """The instant before which the schedule may not trigger, or None when no limit holds.
    The minimum gap runs from the end of the schedule's last restore or run, and "run again after"
    from the start of its last run. Skipped runs and test runs do not count."""
    limits = schedule.limits
    mine = [r for r in runs if r.schedule_id == schedule.id and r.kind in ("action", "restore") and r.outcome != "skipped"]
    ends: list[datetime] = []
    if mine and limits.min_gap_minutes > 0:
        ends.append(max(r.finished_at or r.trigger_at for r in mine) + timedelta(minutes=limits.min_gap_minutes))
    actions = [r.trigger_at for r in mine if r.kind == "action"]
    if actions and limits.run_again_after is not None:
        waits = next_after(limits.run_again_after, max(actions), context)
        if waits is not None:
            ends.append(waits)
    return max(ends, default=None)


# --- restore -------------------------------------------------------------


def _window_end_restore(schedule: Schedule, trigger_at: datetime, windows: list[WindowOccurrence]) -> datetime:
    for w in windows:
        if w.window_id == schedule.trigger.window_id and w.start == trigger_at:
            return w.end
    return trigger_at + RESTORE_CAP


def _at_time_restore(trigger_at: datetime, clock: time, tz: ZoneInfo) -> datetime:
    day = trigger_at.astimezone(tz).date()
    for offset in range(3):
        for candidate in _local_instants(day + timedelta(days=offset), clock, tz):
            if candidate >= trigger_at:
                return candidate
    raise ValueError("no restore instant found")  # unreachable: one of three days is at or after trigger_at


def restore_time(schedule: Schedule, action_run: Run, context: Context) -> datetime | None:
    """When the unit is put back, or None when restore is off. A conditions-only restore
    falls due at the day cap; an early restore can come before it."""
    spec = schedule.restore
    trigger_at = action_run.trigger_at
    if not spec.enabled:
        return None
    if spec.when == "conditions_only":
        return trigger_at + RESTORE_CAP
    if spec.when == "window_end":
        return _window_end_restore(schedule, trigger_at, context.windows)
    if spec.when == "after_minutes" and spec.minutes is not None:
        return trigger_at + timedelta(minutes=spec.minutes)
    if spec.when == "at_time" and spec.time is not None:
        return _at_time_restore(trigger_at, _parse_hhmm(spec.time), ZoneInfo(context.timezone))
    return None


# --- conditions ----------------------------------------------------------

_CONTEXT_READINGS = ("outdoor_temp_c", "battery_soc_pct", "price_c_per_kwh")


def _key(cond: Condition) -> str:
    return f"{cond.reading}@{cond.target}" if cond.target else cond.reading


def _lookup(key: str, readings: Mapping[str, float], stale: set[str], context: Context) -> float | None:
    """A fresh, finite value for `key`, or None. Device readings win over the site context."""
    if key in stale:
        return None
    value = readings.get(key)
    if value is None and "@" not in key and key in _CONTEXT_READINGS:
        value = getattr(context, key)
    if value is None or not math.isfinite(value):
        return None
    return float(value)


_WORDS = {"gt": "above", "lt": "below"}


def _holds(cond: Condition, value: float) -> bool | None:
    """The comparison result, or None for an op other than gt/lt."""
    if cond.op == "gt":
        return value > cond.value
    if cond.op == "lt":
        return value < cond.value
    return None


def _failure(cond: Condition, key: str, value: float) -> str:
    return f"{key} {value:.1f} is not {_WORDS[cond.op]} {cond.value:.1f}"


def _switched_on(conditions: list[Condition]) -> list[Condition]:
    return [c for c in conditions if c.enabled]


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
EPOCH = date(1970, 1, 1)


def week_number(day: date) -> int:
    """Weeks run Monday to Sunday; week 0 holds 1970-01-01 (a Thursday). Even weeks are 'even', odd are 'odd'."""
    return ((day - EPOCH).days + 3) // 7


def _membership(noun: str, current: int, cond: Condition, names: tuple[str, ...], first: int) -> ConditionResult:
    listed = ", ".join(names[v - first] for v in cond.values if 0 <= v - first < len(names))
    here = names[current - first]
    inside = current in cond.values
    if inside == (cond.op == "in"):
        return ConditionResult(True)
    return ConditionResult(False, f"{noun} {here} is {'in' if inside else 'not in'} {listed}")


def _week_result(cond: Condition, local: datetime) -> ConditionResult:
    actual = "odd" if week_number(local.date()) % 2 else "even"
    if actual == cond.parity:
        return ConditionResult(True)
    return ConditionResult(False, f"week is {actual}, not {cond.parity}")


def _window_result(cond: Condition, context: Context) -> ConditionResult:
    now = context.now
    active = [
        w for w in context.windows
        if (cond.window_id is None or w.window_id == cond.window_id) and w.start <= now < w.end
    ]
    if cond.op == "active":
        return ConditionResult(True) if active else ConditionResult(False, "no demand window is active")
    if cond.op == "not_active":
        return ConditionResult(False, f"demand window {active[0].label} is active") if active else ConditionResult(True)
    return ConditionResult(False, f"unsupported comparison: {cond.op}")


def _spans(cond: Condition, day: date, context: Context) -> tuple[datetime, datetime] | None:
    """The span the row covers starting on local date `day`; one that ends by its start runs into the next day."""
    if cond.start is None or cond.end is None:
        return None
    start = ref_instant(cond.start, day, context)
    end = ref_instant(cond.end, day, context)
    if start is not None and end is not None and end <= start:
        end = ref_instant(cond.end, day + timedelta(days=1), context)
    return None if start is None or end is None else (start, end)


def _time_of_day_result(cond: Condition, context: Context) -> ConditionResult:
    """Passes when now is inside the span that started today or yesterday. A span that cannot be placed
    because the sun gives no time counts as unavailable unless another span holds now."""
    if cond.start is None or cond.end is None:
        return ConditionResult(False, "time of day row has no times")
    local = context.now.astimezone(ZoneInfo(context.timezone))
    spans = [_spans(cond, local.date() - timedelta(days=n), context) for n in (1, 0)]
    if any(span is not None and span[0] <= context.now < span[1] for span in spans):
        return ConditionResult(True)
    if any(span is None for span in spans):
        return ConditionResult(False, "sun times unavailable", unavailable=True)
    return ConditionResult(False, f"time {local:%H:%M} is outside {_ref_label(cond.start)} to {_ref_label(cond.end)}")


def _calendar_result(cond: Condition, context: Context) -> ConditionResult:
    """The result of a month, day, week or demand window row."""
    local = context.now.astimezone(ZoneInfo(context.timezone))
    if cond.kind == "months" and cond.op in ("in", "not_in"):
        return _membership("month", local.month, cond, MONTHS, 1)
    if cond.kind == "days" and cond.op in ("in", "not_in"):
        return _membership("day", local.weekday(), cond, DAYS, 0)
    if cond.kind == "week":
        return _week_result(cond, local)
    if cond.kind == "demand_window":
        return _window_result(cond, context)
    if cond.kind == "time_of_day":
        return _time_of_day_result(cond, context)
    return ConditionResult(False, f"unsupported condition: {cond.kind}")


def evaluate_conditions(
    conditions: list[Condition], readings: Mapping[str, float], stale: set[str], context: Context
) -> ConditionResult:
    """All switched-on conditions must pass. A missing or stale reading fails."""
    for cond in _switched_on(conditions):
        if cond.kind != "reading":
            result = _calendar_result(cond, context)
            if not result.passed:
                return result
            continue
        key = _key(cond)
        value = _lookup(key, readings, stale, context)
        if value is None:
            return ConditionResult(False, f"reading unavailable: {key}")
        holds = _holds(cond, value)
        if holds is None:
            return ConditionResult(False, f"unsupported comparison: {cond.op}")
        if not holds:
            return ConditionResult(False, _failure(cond, key, value))
    return ConditionResult(True)


def restore_gate_passes(
    conditions: list[Condition], readings: Mapping[str, float], stale: set[str], context: Context
) -> bool:
    """Like evaluate_conditions, but a missing or stale reading passes so it never keeps the unit off."""
    for cond in _switched_on(conditions):
        if cond.kind != "reading":
            result = _calendar_result(cond, context)
            if not (result.passed or result.unavailable):
                return False
            continue
        value = _lookup(_key(cond), readings, stale, context)
        if value is not None and _holds(cond, value) is False:
            return False
    return True


def early_restore_due(schedule: Schedule, readings: Mapping[str, float], stale: set[str], context: Context) -> bool:
    """True only when early_if has a switched-on condition and every switched-on one is evaluable and passes."""
    conditions = _switched_on(schedule.restore.early_if)
    if not conditions:
        return False
    return evaluate_conditions(conditions, readings, stale, context).passed
