from __future__ import annotations

import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.actor_runs import has_unfinished_action_run
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
)
from core_api.deps import get_db_session, require_site_access, require_site_owner
from core_api.errors import AppError
from core_api.models.actors import ActorInstance, ActorRun, ActorSchedule, RunKind, RunOutcome
from core_api.models.identity import Site

router = APIRouter(prefix="/sites/{site_id}/actors", tags=["actors"])

_SCHEDULE_FIELDS = ("name", "enabled", "trigger", "conditions", "action", "restore", "retry", "notify_email")


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


def _refuse_if_held_off(db: Session, instance_id: int) -> None:
    if has_unfinished_action_run(db, instance_id):
        raise AppError(409, "unit_held_off", "The unit is held off by a run that has not been restored yet")


# Instances


@router.get("/instances", response_model=list[ActorInstanceView])
def list_instances(site: Site = Depends(require_site_access), db: Session = Depends(get_db_session)):
    stmt = select(ActorInstance).where(ActorInstance.site_id == site.id).order_by(ActorInstance.id)
    return list(db.scalars(stmt))


@router.post("/instances", response_model=ActorInstanceView, status_code=status.HTTP_201_CREATED)
def create_instance(
    payload: ActorInstanceCreate, site: Site = Depends(require_site_owner), db: Session = Depends(get_db_session)
):
    instance = ActorInstance(site_id=site.id, provider=payload.provider, name=payload.name, enabled=True, config={})
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance


@router.patch("/instances/{instance_id}", response_model=ActorInstanceView)
def patch_instance(
    instance_id: int,
    payload: ActorInstancePatch,
    site: Site = Depends(require_site_owner),
    db: Session = Depends(get_db_session),
):
    instance = _instance_of_site(db, site, instance_id)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(instance, field, value)
    db.commit()
    db.refresh(instance)
    return instance


@router.delete("/instances/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_instance(
    instance_id: int, site: Site = Depends(require_site_owner), db: Session = Depends(get_db_session)
) -> Response:
    instance = _instance_of_site(db, site, instance_id)
    _refuse_if_held_off(db, instance.id)
    schedule_ids = list(db.scalars(select(ActorSchedule.id).where(ActorSchedule.instance_id == instance.id)))
    for run in db.scalars(select(ActorRun).where(ActorRun.schedule_id.in_(schedule_ids))):
        db.delete(run)
    for schedule in db.scalars(select(ActorSchedule).where(ActorSchedule.instance_id == instance.id)):
        db.delete(schedule)
    db.delete(instance)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    payload: ScheduleCreate, site: Site = Depends(require_site_owner), db: Session = Depends(get_db_session)
):
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
    validate_schedule(db, site.id, db.get(ActorInstance, schedule.instance_id), parsed)
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
    _refuse_if_held_off(db, schedule.instance_id)
    for run in db.scalars(select(ActorRun).where(ActorRun.schedule_id == schedule.id)):
        db.delete(run)
    db.delete(schedule)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/schedules/{schedule_id}/test", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def test_schedule(
    schedule_id: int, site: Site = Depends(require_site_owner), db: Session = Depends(get_db_session)
):
    """Queue a test run for the actor to pick up. Refused with 409 when the actor is disabled
    (it would never poll) or has no declared capabilities."""
    schedule = _schedule_of_site(db, site, schedule_id)
    instance = db.get(ActorInstance, schedule.instance_id)
    if not instance.enabled:
        raise AppError(409, "instance_disabled", "Enable the actor before testing a schedule")
    if not instance.capabilities:
        raise AppError(409, "no_capabilities", "This actor has not declared its capabilities yet")
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
    return [RunListItem(**RunOut.model_validate(run).model_dump(), schedule_name=name) for run, name in db.execute(stmt)]


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
