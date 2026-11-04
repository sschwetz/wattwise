"""The current actor failures at a site, for the Dashboard tile and the watchdog job.

A failure is current while its cause holds: a failed run until a later run
of the same schedule ends ok, skipped or overridden; a held-off unit until
its restore finishes; a quiet actor until it reports again; a lost sign-in
until the actor clears the instance's auth_state."""

from __future__ import annotations

import datetime
import zoneinfo
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from core_api.actor_context import window_occurrences
from core_api.actor_runs import unfinished_action_runs
from core_api.actor_schemas import RestoreSpec, RetrySpec
from core_api.models.actors import ActorInstance, ActorRun, ActorSchedule, RunKind, RunOutcome
from core_api.models.identity import Site
from core_api.time_utils import as_utc, site_tz

FailureKind = Literal["run_failed", "still_off", "actor_silent", "needs_sign_in"]

SILENT_AFTER = datetime.timedelta(minutes=5)
WINDOW_END_FALLBACK = datetime.timedelta(hours=24)
_CLEARING_OUTCOMES = (RunOutcome.OK, RunOutcome.SKIPPED, RunOutcome.OVERRIDDEN)


@dataclass(frozen=True)
class ActorFailure:
    kind: FailureKind
    instance_id: int
    schedule_id: int | None
    schedule_name: str | None
    message: str
    since: datetime.datetime


@dataclass(frozen=True)
class HeldOffFailure:
    """A still_off or actor_silent failure with the action run it is about."""

    run: ActorRun
    schedule: ActorSchedule
    failure: ActorFailure


def current_failures(db: Session, site_id: int, now: datetime.datetime) -> list[ActorFailure]:
    """Every current failure at the site, oldest first. Disabled instances and
    schedules are included while the condition holds."""
    site = db.get(Site, site_id)
    if site is None:
        return []
    now = as_utc(now)
    found: list[ActorFailure] = []
    for instance in _site_instances(db, site_id):
        found.extend(_sign_in_failures(instance, now))
        found.extend(_failed_runs(db, instance))
        found.extend(held.failure for held in held_off_failures(db, site, instance, now))
    return sorted(found, key=lambda f: (f.since, f.instance_id, f.schedule_id or 0))


def held_off_failures(db: Session, site: Site, instance: ActorInstance, now: datetime.datetime) -> list[HeldOffFailure]:
    """still_off and actor_silent failures for the instance's unfinished action runs."""
    now = as_utc(now)
    found: list[HeldOffFailure] = []
    for run in unfinished_action_runs(db, instance.id):
        schedule = db.get(ActorSchedule, run.schedule_id)
        silent_since = _silent_since(instance, run, now)
        if silent_since is not None:
            found.append(HeldOffFailure(run, schedule, _held_off(instance, schedule, "actor_silent", silent_since)))
        restore_at = restore_time(db, site, schedule, run)
        if restore_at is not None and now > restore_at + _give_up(schedule):
            found.append(HeldOffFailure(run, schedule, _held_off(instance, schedule, "still_off", restore_at)))
    return found


def _site_instances(db: Session, site_id: int) -> list[ActorInstance]:
    stmt = select(ActorInstance).where(ActorInstance.site_id == site_id).order_by(ActorInstance.id)
    return list(db.scalars(stmt))


def _sign_in_failures(instance: ActorInstance, now: datetime.datetime) -> list[ActorFailure]:
    if (instance.auth_state or {}).get("needs_sign_in") is not True:
        return []
    # auth_state carries no timestamp, so the failure is dated now.
    return [ActorFailure("needs_sign_in", instance.id, None, None, "The device account needs signing in again", now)]


def _failed_runs(db: Session, instance: ActorInstance) -> list[ActorFailure]:
    """Failed action and restore runs with no later run of the same schedule that ended ok, skipped or overridden."""
    later = aliased(ActorRun)
    cleared = exists().where(
        later.schedule_id == ActorRun.schedule_id,
        later.trigger_at > ActorRun.trigger_at,
        later.outcome.in_(_CLEARING_OUTCOMES),
    )
    stmt = (
        select(ActorRun, ActorSchedule)
        .join(ActorSchedule, ActorSchedule.id == ActorRun.schedule_id)
        .where(
            ActorSchedule.instance_id == instance.id,
            ActorRun.kind.in_((RunKind.ACTION, RunKind.RESTORE)),
            ActorRun.outcome == RunOutcome.FAILED,
            ~cleared,
        )
        .order_by(ActorRun.trigger_at)
    )
    return [_run_failed(instance, run, schedule) for run, schedule in db.execute(stmt).all()]


def _run_failed(instance: ActorInstance, run: ActorRun, schedule: ActorSchedule) -> ActorFailure:
    what = "The restore failed" if run.kind == RunKind.RESTORE else "The action failed"
    message = f"{what}: {run.message}" if run.message else what
    since = as_utc(run.finished_at or run.trigger_at)
    return ActorFailure("run_failed", instance.id, schedule.id, schedule.name, message, since)


_HELD_OFF_MESSAGES: dict[FailureKind, str] = {
    "still_off": "The unit is still off after its restore time",
    "actor_silent": "The actor has stopped reporting while the unit is held off",
}


def _held_off(
    instance: ActorInstance, schedule: ActorSchedule, kind: FailureKind, since: datetime.datetime
) -> ActorFailure:
    return ActorFailure(kind, instance.id, schedule.id, schedule.name, _HELD_OFF_MESSAGES[kind], since)


def _silent_since(instance: ActorInstance, run: ActorRun, now: datetime.datetime) -> datetime.datetime | None:
    """When the actor went quiet, or None if it reported in the last 5 minutes.
    An actor never seen is dated from the run's trigger."""
    if instance.last_seen_at is None:
        return as_utc(run.trigger_at)
    last_seen = as_utc(instance.last_seen_at)
    return last_seen if now - last_seen > SILENT_AFTER else None


def _give_up(schedule: ActorSchedule) -> datetime.timedelta:
    try:
        minutes = RetrySpec.model_validate(schedule.retry or {}).give_up_minutes
    except ValidationError:
        minutes = RetrySpec().give_up_minutes
    return datetime.timedelta(minutes=minutes)


def restore_time(db: Session, site: Site, schedule: ActorSchedule, run: ActorRun) -> datetime.datetime | None:
    """When the action run should have been restored, or None when the schedule
    has no time-based restore (restore disabled, conditions only, or an invalid spec)."""
    try:
        restore = RestoreSpec.model_validate(schedule.restore or {})
    except ValidationError:
        return None
    trigger_at = as_utc(run.trigger_at)
    if not restore.enabled or restore.when == "conditions_only":
        return None
    if restore.when == "after_minutes":
        return trigger_at + datetime.timedelta(minutes=restore.minutes)
    if restore.when == "at_time":
        return next_local_time(trigger_at, restore.time, site_tz(site.timezone))
    return _window_end(db, site, schedule.trigger or {}, trigger_at)


def _window_end(db: Session, site: Site, trigger: dict, trigger_at: datetime.datetime) -> datetime.datetime:
    """The end of the trigger window's occurrence that started at trigger_at, else trigger_at plus 24 hours."""
    window_id = trigger.get("window_id")
    for occurrence in window_occurrences(db, site, trigger_at, horizon=datetime.timedelta(minutes=1)):
        if occurrence.window_id == window_id and occurrence.start == trigger_at:
            return occurrence.end
    return trigger_at + WINDOW_END_FALLBACK


def next_local_time(after: datetime.datetime, hhmm: str, tz: zoneinfo.ZoneInfo) -> datetime.datetime:
    """The first instant at or after `after` whose wall clock in `tz` reads HH:MM.
    A day where that time falls in a DST gap is skipped."""
    wall = datetime.time.fromisoformat(hhmm)
    day = after.astimezone(tz).date()
    while True:
        local = datetime.datetime.combine(day, wall, tzinfo=tz)
        instant = local.astimezone(datetime.UTC)
        if instant.astimezone(tz).replace(tzinfo=None) == local.replace(tzinfo=None) and instant >= after:
            return instant
        day += datetime.timedelta(days=1)
