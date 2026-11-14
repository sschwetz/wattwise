from __future__ import annotations

import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Response, status
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core_api.actor_failures import ActorFailure, site_log
from core_api.actor_runs import unfinished_action_runs
from core_api.actor_schedule_validation import parse_schedule, validate_schedule
from core_api.actor_schemas import (
    ActorInstanceCreate,
    ActorInstancePatch,
    ActorInstanceView,
    RunListItem,
    RunOut,
    ScheduleCreate,
    SchedulePatch,
    ScheduleView,
    instance_view,
)
from core_api.alerts import dismiss_alert
from core_api.deps import get_current_user, get_db_session, require_site_access, require_site_owner
from core_api.errors import AppError
from core_api.models.actors import ActorInstance, ActorRun, ActorSchedule, RunKind, RunOutcome
from core_api.models.identity import Site, User

router = APIRouter(prefix="/sites/{site_id}/actors", tags=["actors"])

_SCHEDULE_FIELDS = ("name", "enabled", "trigger", "conditions", "actions", "restore", "retry", "notify_email")
# Changing one of these while the schedule holds the unit off would change how
# or when the unit is put back, so it is refused until the restore finishes.
_HELD_FIELDS = ("trigger", "actions", "restore")


def _instance_of_site(db: Session, site: Site, instance_id: int) -> ActorInstance:
    instance = db.get(ActorInstance, instance_id)
    if instance is None or instance.site_id != site.id:
        raise AppError(404, "not_found", "Actor not found")
    return instance


def _schedule_of_site(db: Session, site: Site, schedule_id: int) -> ActorSchedule:
    schedule = db.get(ActorSchedule, schedule_id)
    owner = None if schedule is None else db.get(ActorInstance, schedule.instance_id)
    if schedule is None or owner is None or owner.site_id != site.id:
        raise AppError(404, "not_found", "Schedule not found")
    return schedule


def _instance_of(db: Session, schedule: ActorSchedule) -> ActorInstance:
    instance = db.get(ActorInstance, schedule.instance_id)
    if instance is None:
        raise AppError(404, "not_found", "Actor not found")
    return instance


def _refuse_if_held_off(db: Session, instance_id: int, schedule_id: int | None = None) -> None:
    """Refuse while an action run owes a restore: any of the instance's runs, or only the given schedule's."""
    runs = unfinished_action_runs(db, instance_id)
    if schedule_id is not None:
        runs = [r for r in runs if r.schedule_id == schedule_id]
    if runs:
        raise AppError(409, "unit_held_off", "The unit is held off by a run that has not been restored yet")


def _stored_spec(schedule: ActorSchedule) -> dict:
    """The stored trigger, actions and restore as the schema dumps them, so defaults compare equal.
    A stored spec the schema no longer accepts is compared as stored."""
    stored = {f: getattr(schedule, f) for f in _SCHEDULE_FIELDS}
    try:
        parsed = ScheduleCreate.model_validate({**stored, "instance_id": schedule.instance_id})
    except ValidationError:
        return {f: stored[f] for f in _HELD_FIELDS}
    return parsed.model_dump(mode="json", include=set(_HELD_FIELDS))


def _refuse_held_spec_change(db: Session, schedule: ActorSchedule, parsed: ScheduleCreate) -> None:
    new = parsed.model_dump(mode="json", include=set(_HELD_FIELDS))
    if new == _stored_spec(schedule):
        return
    if any(r.schedule_id == schedule.id for r in unfinished_action_runs(db, schedule.instance_id)):
        raise AppError(
            409,
            "unit_held_off",
            "This schedule is holding the unit off. Change its trigger, actions or restore after the unit is put back.",
        )


def _delete_run_history(db: Session, schedule_ids: list[int]) -> None:
    """Deleting a schedule or instance deletes its run history, restore rows (which point at their
    action run through parent_run_id) before the action rows. Disabling keeps history."""
    runs = ActorRun.schedule_id.in_(schedule_ids)
    db.execute(delete(ActorRun).where(runs, ActorRun.parent_run_id.is_not(None)))
    db.execute(delete(ActorRun).where(runs))


# Instances


@router.get("/instances", response_model=list[ActorInstanceView])
def list_instances(
    site: Site = Depends(require_site_access),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    stmt = select(ActorInstance).where(ActorInstance.site_id == site.id).order_by(ActorInstance.id)
    return [instance_view(i, site, user) for i in db.scalars(stmt)]


@router.post("/instances", response_model=ActorInstanceView, status_code=status.HTTP_201_CREATED)
def create_instance(
    payload: ActorInstanceCreate,
    site: Site = Depends(require_site_owner),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    instance = ActorInstance(site_id=site.id, provider=payload.provider, name=payload.name, enabled=True, config={})
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance_view(instance, site, user)


@router.patch("/instances/{instance_id}", response_model=ActorInstanceView)
def patch_instance(
    instance_id: int,
    payload: ActorInstancePatch,
    site: Site = Depends(require_site_owner),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    instance = _instance_of_site(db, site, instance_id)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(instance, field, value)
    db.commit()
    db.refresh(instance)
    return instance_view(instance, site, user)


@router.delete("/instances/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_instance(
    instance_id: int, site: Site = Depends(require_site_owner), db: Session = Depends(get_db_session)
) -> Response:
    instance = _instance_of_site(db, site, instance_id)
    _refuse_if_held_off(db, instance.id)
    schedule_ids = list(db.scalars(select(ActorSchedule.id).where(ActorSchedule.instance_id == instance.id)))
    _delete_run_history(db, schedule_ids)
    db.execute(delete(ActorSchedule).where(ActorSchedule.instance_id == instance.id))
    db.delete(instance)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/instances/{instance_id}/sign-in/reset", response_model=ActorInstanceView)
def reset_sign_in(
    instance_id: int,
    site: Site = Depends(require_site_owner),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """Drop the stored refresh token so the actor starts a new device-code sign-in."""
    instance = _instance_of_site(db, site, instance_id)
    # With the sign-in lost the actor can neither restore nor finish the run, so a
    # new sign-in is the only way out. It restores from the stored snapshot once approved.
    if (instance.auth_state or {}).get("needs_sign_in") is not True:
        _refuse_if_held_off(db, instance.id)
    instance.config = {k: v for k, v in instance.config.items() if k != "refresh_token"}
    instance.auth_state = None
    db.commit()
    db.refresh(instance)
    return instance_view(instance, site, user)


# Schedules


@router.get("/schedules", response_model=list[ScheduleView])
def list_schedules(site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)):
    stmt = (
        select(ActorSchedule)
        .join(ActorInstance, ActorInstance.id == ActorSchedule.instance_id)
        .where(ActorInstance.site_id == site.id)
        .order_by(ActorSchedule.id)
    )
    return list(db.scalars(stmt))


@router.post("/schedules", response_model=ScheduleView, status_code=status.HTTP_201_CREATED)
def create_schedule(
    body: dict = Body(...), site: Site = Depends(require_site_owner), db: Session = Depends(get_db_session)
):
    # Parsed here, not by FastAPI, so a bad list length is an invalid_schedule like every other bad schedule.
    payload = parse_schedule(body)
    instance = _instance_of_site(db, site, payload.instance_id)
    validate_schedule(db, site.id, instance, payload)
    schedule = ActorSchedule(
        instance_id=instance.id,
        **payload.model_dump(mode="json", include=set(_SCHEDULE_FIELDS)),
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


@router.patch("/schedules/{schedule_id}", response_model=ScheduleView)
def patch_schedule(
    schedule_id: int,
    payload: SchedulePatch,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
):
    schedule = _schedule_of_site(db, site, schedule_id)
    merged = {f: getattr(schedule, f) for f in _SCHEDULE_FIELDS}
    merged.update(payload.model_dump(exclude_unset=True))
    parsed = parse_schedule({**merged, "instance_id": schedule.instance_id})
    validate_schedule(db, site.id, _instance_of(db, schedule), parsed)
    _refuse_held_spec_change(db, schedule, parsed)
    for field, value in parsed.model_dump(mode="json", include=set(_SCHEDULE_FIELDS)).items():
        setattr(schedule, field, value)
    db.commit()
    db.refresh(schedule)
    return schedule


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: int, site: Site = Depends(require_site_owner), db: Session = Depends(get_db_session)
) -> Response:
    schedule = _schedule_of_site(db, site, schedule_id)
    _refuse_if_held_off(db, schedule.instance_id, schedule.id)
    _delete_run_history(db, [schedule.id])
    db.delete(schedule)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _refuse_test(db: Session, site: Site, schedule: ActorSchedule) -> None:
    """The actor does not poll a disabled site or instance, has nothing to act on without
    capabilities, and runs one test of a schedule at a time."""
    instance = _instance_of(db, schedule)
    if not site.enabled:
        raise AppError(409, "site_disabled", "Enable the site before testing a schedule")
    if not instance.enabled:
        raise AppError(409, "instance_disabled", "Enable the actor before testing a schedule")
    if not schedule.enabled:
        raise AppError(409, "schedule_disabled", "Enable the schedule before testing it")
    if not instance.capabilities:
        raise AppError(409, "no_capabilities", "This actor has not declared its capabilities yet")
    in_progress = db.scalar(
        select(ActorRun.id).where(
            ActorRun.schedule_id == schedule.id,
            ActorRun.kind == RunKind.TEST,
            ActorRun.outcome.in_((RunOutcome.PENDING, RunOutcome.RUNNING)),
        ).limit(1)
    )
    if in_progress is not None:
        raise AppError(409, "test_in_progress", "A test of this schedule is already waiting or running")


@router.post("/schedules/{schedule_id}/test", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def test_schedule(
    schedule_id: int, site: Site = Depends(require_site_owner), db: Session = Depends(get_db_session)
):
    """Queue a test run for the actor to pick up. Refused with 409 when the actor would not run it."""
    schedule = _schedule_of_site(db, site, schedule_id)
    _refuse_test(db, site, schedule)
    run = ActorRun(
        schedule_id=schedule.id,
        kind=RunKind.TEST,
        trigger_at=datetime.datetime.now(datetime.UTC),
        outcome=RunOutcome.PENDING,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# Runs

_Limit = Annotated[int, Query(ge=1, le=200)]


@router.get("/runs", response_model=list[RunListItem])
def list_site_runs(
    limit: _Limit = 50, site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)
):
    stmt = (
        select(ActorRun, ActorSchedule.name)
        .join(ActorSchedule, ActorSchedule.id == ActorRun.schedule_id)
        .join(ActorInstance, ActorInstance.id == ActorSchedule.instance_id)
        .where(ActorInstance.site_id == site.id)
        .order_by(ActorRun.trigger_at.desc(), ActorRun.id.desc())
        .limit(limit)
    )
    return [
        RunListItem(**RunOut.model_validate(run).model_dump(), schedule_name=name) for run, name in db.execute(stmt)
    ]


@router.get("/schedules/{schedule_id}/runs", response_model=list[RunOut])
def list_schedule_runs(
    schedule_id: int,
    limit: _Limit = 50,
    site: Site = Depends(require_site_access),
    db: Session = Depends(get_db_session),
):
    schedule = _schedule_of_site(db, site, schedule_id)
    stmt = (
        select(ActorRun)
        .where(ActorRun.schedule_id == schedule.id)
        .order_by(ActorRun.trigger_at.desc(), ActorRun.id.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


# Failures


@router.get("/failures", response_model=list[ActorFailure])
def list_failures(site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)):
    return site_log(db, site.id, datetime.datetime.now(datetime.UTC))


@router.delete("/alerts/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_failure_alert(alert_id: int, site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)):
    dismiss_alert(db, site.id, alert_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
