"""The current actor failures at a site, for the Dashboard tile and the watchdog job.

A failure is current while its cause holds: a failed or partial action run until a later
run of the same schedule ends ok, skipped or overridden, and a failed restore
until a later run ends ok or overridden (a skip leaves the unit as it was); a held-off unit until
its restore finishes; a quiet actor until it reports again; a lost sign-in
until the actor clears the instance's auth_state."""

from __future__ import annotations

import datetime
import zoneinfo
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session, aliased

from core_api.actor_context import window_occurrences
from core_api.actor_runs import unfinished_action_runs
from core_api.actor_schemas import RestoreSpec, RetrySpec
from core_api.alerts import ALERT_CLEARED_TTL
from core_api.models.actors import ActorInstance, ActorRun, ActorSchedule, RunKind, RunOutcome
from core_api.models.alerts import (
    SEVERITY_FAILURE,
    SEVERITY_NOTICE,
    SEVERITY_PARTIAL,
    SUBJECT_INSTANCE,
    SUBJECT_SITE,
    SUBJECT_SOURCE,
    Alert,
)
from core_api.models.identity import Site
from core_api.models.sources import Source
from core_api.time_utils import as_utc, site_tz

FailureKind = Literal["run_failed", "still_off", "actor_silent", "needs_sign_in", "alert"]

SILENT_AFTER = datetime.timedelta(minutes=5)
# The latest a restore can fall: a window end with no matching occurrence, and a
# conditions-only restore whose condition never holds, both restore a day after the trigger.
RESTORE_CAP = datetime.timedelta(hours=24)
_CLEARS_ANY = (RunOutcome.OK, RunOutcome.OVERRIDDEN)
_FAILED_OUTCOMES = (RunOutcome.FAILED, RunOutcome.PARTIAL)


@dataclass(frozen=True)
class ActorFailure:
    kind: FailureKind
    instance_id: int | None
    schedule_id: int | None
    schedule_name: str | None
    message: str
    since: datetime.datetime
    # What an "alert" is about: the source or actor name, or "Site". None for the other kinds.
    subject: str | None = None
    severity: str = SEVERITY_FAILURE
    alert_id: int | None = None
    cleared_at: datetime.datetime | None = None
    sticky: bool = False


@dataclass(frozen=True)
class HeldOffFailure:
    """A still_off or actor_silent failure with the action run it is about."""

    run: ActorRun
    schedule: ActorSchedule
    failure: ActorFailure


def current_failures(db: Session, site_id: int, now: datetime.datetime) -> list[ActorFailure]:
    """Every current failure at the site.

    Unresolved first by severity, newest first, then recently cleared alerts. Disabled
    instances and schedules are included while the condition holds."""
    site = db.get(Site, site_id)
    if site is None:
        return []
    now = as_utc(now)
    found: list[ActorFailure] = _alert_failures(db, site_id, now)
    for instance in _site_instances(db, site_id):
        found.extend(_sign_in_failures(instance, now))
        found.extend(_failed_runs(db, instance))
        found.extend(held.failure for held in held_off_failures(db, site, instance, now))
    return sorted(found, key=_sort_key)


_SEVERITY_RANK = {SEVERITY_FAILURE: 0, SEVERITY_PARTIAL: 1, SEVERITY_NOTICE: 2}


def _sort_key(f: ActorFailure) -> tuple[bool, int, float, int, int]:
    """Unresolved before cleared, then failure, partial, notice, then newest first."""
    return (
        f.cleared_at is not None,
        _SEVERITY_RANK.get(f.severity, len(_SEVERITY_RANK)),
        -f.since.timestamp(),
        f.instance_id or 0,
        f.schedule_id or 0,
    )


def held_off_failures(db: Session, site: Site, instance: ActorInstance, now: datetime.datetime) -> list[HeldOffFailure]:
    """still_off and actor_silent failures for the instance's unfinished action runs."""
    now = as_utc(now)
    found: list[HeldOffFailure] = []
    for run in unfinished_action_runs(db, instance.id):
        schedule = db.get(ActorSchedule, run.schedule_id)
        if schedule is None:
            continue
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


def _alert_failures(db: Session, site_id: int, now: datetime.datetime) -> list[ActorFailure]:
    """The site's alerts. Those of a disabled source or actor instance are left out, since nothing is left to clear them."""
    stmt = (
        select(Alert)
        .where(Alert.site_id == site_id, Alert.cleared_at.is_(None) | (Alert.cleared_at > now - ALERT_CLEARED_TTL))
        .order_by(Alert.id)
    )
    alerts = list(db.scalars(stmt))
    if not alerts:
        return []
    sources = {
        row.id: row
        for row in db.execute(select(Source.id, Source.name, Source.provider, Source.enabled).where(Source.site_id == site_id))
    }
    instances = {
        row.id: row
        for row in db.execute(
            select(ActorInstance.id, ActorInstance.name, ActorInstance.provider, ActorInstance.enabled).where(
                ActorInstance.site_id == site_id
            )
        )
    }
    found: list[ActorFailure] = []
    for alert in alerts:
        subject = _alert_subject(alert, sources, instances)
        if subject is not None:
            found.append(_alert_failure(alert, subject))
    return found


def _alert_failure(alert: Alert, subject: str) -> ActorFailure:
    cleared_at = None if alert.cleared_at is None else as_utc(alert.cleared_at)
    return ActorFailure(
        "alert", None, None, None, alert.message, as_utc(alert.since), subject,
        severity=alert.severity, alert_id=alert.id, cleared_at=cleared_at, sticky=alert.sticky,
    )


def _alert_subject(alert: Alert, sources: dict, instances: dict) -> str | None:
    if alert.subject_kind == SUBJECT_SITE:
        return "Site"
    if alert.subject_kind == SUBJECT_SOURCE:
        row = sources.get(alert.subject_id)
    elif alert.subject_kind == SUBJECT_INSTANCE:
        row = instances.get(alert.subject_id)
    else:
        return None
    return row.name or row.provider if row is not None and row.enabled else None


def _sign_in_failures(instance: ActorInstance, now: datetime.datetime) -> list[ActorFailure]:
    if (instance.auth_state or {}).get("needs_sign_in") is not True:
        return []
    # auth_state carries no timestamp, so the failure is dated now.
    return [ActorFailure("needs_sign_in", instance.id, None, None, "The device account needs signing in again", now)]


def _failed_runs(db: Session, instance: ActorInstance) -> list[ActorFailure]:
    """The latest failed action or restore run of each schedule that no later run of that
    schedule cleared. A skip clears a failed action but not a failed restore."""
    later = aliased(ActorRun)
    cleared = exists().where(
        later.schedule_id == ActorRun.schedule_id,
        later.trigger_at > ActorRun.trigger_at,
        or_(
            later.outcome.in_(_CLEARS_ANY),
            and_(ActorRun.kind == RunKind.ACTION, later.outcome == RunOutcome.SKIPPED),
        ),
    )
    stmt = (
        select(ActorRun, ActorSchedule)
        .join(ActorSchedule, ActorSchedule.id == ActorRun.schedule_id)
        .where(
            ActorSchedule.instance_id == instance.id,
            ActorRun.kind.in_((RunKind.ACTION, RunKind.RESTORE)),
            ActorRun.outcome.in_(_FAILED_OUTCOMES),
            ~cleared,
        )
        .order_by(ActorRun.trigger_at, ActorRun.id)
    )
    latest = {schedule.id: (run, schedule) for run, schedule in db.execute(stmt).all()}
    return [_run_failed(instance, run, schedule) for run, schedule in latest.values()]


def _run_failed(instance: ActorInstance, run: ActorRun, schedule: ActorSchedule) -> ActorFailure:
    if run.kind == RunKind.RESTORE:
        what = "The restore failed"
    else:
        what = "The action partly failed" if run.outcome == RunOutcome.PARTIAL else "The action failed"
    message = f"{what}: {run.message}" if run.message else what
    since = as_utc(run.finished_at or run.trigger_at)
    severity = SEVERITY_PARTIAL if run.outcome == RunOutcome.PARTIAL else SEVERITY_FAILURE
    return ActorFailure("run_failed", instance.id, schedule.id, schedule.name, message, since, severity=severity)


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
    """When the action run should have been restored, or None when restore is
    disabled or the spec is invalid. A conditions-only restore falls due at the day cap."""
    try:
        restore = RestoreSpec.model_validate(schedule.restore or {})
    except ValidationError:
        return None
    trigger_at = as_utc(run.trigger_at)
    if not restore.enabled:
        return None
    if restore.when == "conditions_only":
        return trigger_at + RESTORE_CAP
    if restore.when == "after_minutes" and restore.minutes is not None:
        return trigger_at + datetime.timedelta(minutes=restore.minutes)
    if restore.when == "at_time" and restore.time is not None:
        return next_local_time(trigger_at, restore.time, site_tz(site.timezone))
    return _window_end(db, site, schedule.trigger or {}, trigger_at)


def _window_end(db: Session, site: Site, trigger: dict, trigger_at: datetime.datetime) -> datetime.datetime:
    """The end of the trigger window's occurrence that started at trigger_at, else trigger_at plus 24 hours."""
    window_id = trigger.get("window_id")
    for occurrence in window_occurrences(db, site, trigger_at, horizon=datetime.timedelta(minutes=1)):
        if occurrence.window_id == window_id and occurrence.start == trigger_at:
            return occurrence.end
    return trigger_at + RESTORE_CAP


def next_local_time(after: datetime.datetime, hhmm: str, tz: zoneinfo.ZoneInfo) -> datetime.datetime:
    """The first instant at or after `after` whose wall clock in `tz` reads HH:MM, as the
    actor's schedule_eval works it out: a repeated time counts on both passes, and a time
    in a DST gap falls at the end of the gap."""
    wall = datetime.time.fromisoformat(hhmm)
    day = after.astimezone(tz).date()
    while True:
        for instant in _local_instants(datetime.datetime.combine(day, wall), tz):
            if instant >= after:
                return instant
        day += datetime.timedelta(days=1)


def _local_instants(naive: datetime.datetime, tz: zoneinfo.ZoneInfo) -> list[datetime.datetime]:
    found: list[datetime.datetime] = []
    for fold in (0, 1):
        instant = naive.replace(tzinfo=tz, fold=fold).astimezone(datetime.UTC)
        if instant.astimezone(tz).replace(tzinfo=None) == naive and instant not in found:
            found.append(instant)
    return sorted(found) or [_gap_end(naive, tz)]


def _gap_end(naive: datetime.datetime, tz: zoneinfo.ZoneInfo) -> datetime.datetime:
    """The instant the clock jumps over `naive`: where the offset changes."""
    hi = naive.replace(tzinfo=tz).astimezone(datetime.UTC)
    lo = hi - datetime.timedelta(hours=3)
    before = lo.astimezone(tz).utcoffset()
    while hi - lo > datetime.timedelta(seconds=1):
        mid = lo + (hi - lo) / 2
        if mid.astimezone(tz).utcoffset() == before:
            lo = mid
        else:
            hi = mid
    return hi.replace(microsecond=0)
