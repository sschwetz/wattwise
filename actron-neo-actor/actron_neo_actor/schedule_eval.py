"""Pure schedule evaluation: which triggers are due or missed, when a restore
falls, and whether conditions pass. None of the functions read the clock or
do I/O; callers pass `now` where time matters."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from actron_neo_actor.models import Condition, Context, Run, Schedule, Trigger, WindowOccurrence

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


@dataclass(frozen=True)
class ConditionResult:
    passed: bool
    reason: str | None = None


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


def _candidates(schedule: Schedule, context: Context, now: datetime, existing: set[datetime]) -> list[DueTrigger]:
    """Non-price triggers inside [max(created_at, now - 24h), now] not yet recorded."""
    if not schedule.enabled:
        return []
    kind = schedule.trigger.type
    if kind in ("window_start", "window_end"):
        found = _window_candidates(schedule, context)
    elif kind == "time":
        found = _time_candidates(schedule, context, now)
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


def level_met(
    schedule: Schedule, context: Context, readings: Mapping[str, float], stale: set[str]
) -> bool | None:
    """Whether a price or temperature trigger's level is passed now; None when the value is unknown
    or the trigger is not a level trigger. A temperature uses the device reading first, then the site's."""
    trigger = schedule.trigger
    if trigger.type == "price_above":
        price = context.price_c_per_kwh
        if trigger.c_per_kwh is None or price is None or not math.isfinite(price):
            return None
        return price > trigger.c_per_kwh
    if trigger.type == "temperature" and trigger.reading and trigger.op and trigger.value is not None:
        value = _lookup(trigger.reading, readings, stale, context)
        return None if value is None else _holds(Condition(trigger.reading, trigger.op, trigger.value), value)
    return None


def _level_crossing(
    schedule: Schedule, now: datetime, existing: set[datetime], met: bool | None, was_met: bool | None
) -> list[DueTrigger]:
    """One trigger per crossing, stamped to the 5-minute step. A level already passed before the
    actor started counts as a crossing, because `was_met` is unknown then."""
    if met is not True or was_met is True:
        return []
    step = int(now.timestamp()) // PRICE_STEP_SECONDS * PRICE_STEP_SECONDS
    at = datetime.fromtimestamp(step, UTC)
    if at in existing:
        return []
    return [DueTrigger(at, _level_label(schedule.trigger))]


def due_triggers(
    schedule: Schedule,
    context: Context,
    now: datetime,
    existing_trigger_ats: set[datetime],
    was_met: bool | None,
    readings: Mapping[str, float] | None = None,
    stale: set[str] | None = None,
) -> list[DueTrigger]:
    """Triggers at most 10 minutes old that have no run yet. `was_met` is whether a level trigger's
    level was passed at the last look; `readings` and `stale` are the device's, for temperature triggers."""
    if not schedule.enabled:
        return []
    if schedule.trigger.type in LEVEL_TRIGGERS:
        met = level_met(schedule, context, readings or {}, stale or set())
        return _level_crossing(schedule, now, existing_trigger_ats, met, was_met)
    found = _candidates(schedule, context, now, existing_trigger_ats)
    return [d for d in found if now - d.trigger_at <= GRACE]


def missed_triggers(
    schedule: Schedule, context: Context, now: datetime, existing_trigger_ats: set[datetime]
) -> list[DueTrigger]:
    """Triggers more than 10 minutes old that have no run yet. Price crossings never qualify."""
    found = _candidates(schedule, context, now, existing_trigger_ats)
    return [d for d in found if now - d.trigger_at > GRACE]


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


def evaluate_conditions(
    conditions: list[Condition], readings: Mapping[str, float], stale: set[str], context: Context
) -> ConditionResult:
    """All switched-on conditions must pass. A missing or stale reading fails."""
    for cond in _switched_on(conditions):
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
