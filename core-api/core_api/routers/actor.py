from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Header, status
from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_api.actor_capabilities import Capabilities
from core_api.actor_context import ActorContext, build_context
from core_api.actor_runs import has_unfinished_action_run, unfinished_action_runs
from core_api.actor_schemas import RunCreate, RunOut, RunUpdate
from core_api.deps import get_actor_instance, get_db_session, get_email_sender, require_actor_key
from core_api.email_sender import EmailSender, try_send_email
from core_api.errors import AppError
from core_api.models.actors import ActorInstance, ActorRun, ActorSchedule, RunKind, RunOutcome
from core_api.models.identity import Site, User
from core_api.time_utils import as_utc, site_tz

router = APIRouter(prefix="/actor", tags=["actor"])


class ActorInstanceOut(BaseModel):
    instance_id: int
    site_id: int
    name: str | None
    timezone: str
    restore_only: bool
    config: dict


class DiscoveredActorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: dict | None = None
    auth_state: dict | None = None


class ReadingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: dict


class ActorScheduleOut(BaseModel):
    id: int
    name: str
    enabled: bool
    trigger: dict
    conditions: list
    action: dict
    restore: dict
    retry: dict
    notify_email: bool


class WindowOccurrenceOut(BaseModel):
    window_id: int
    label: str
    start: AwareDatetime
    end: AwareDatetime


class ActorContextOut(BaseModel):
    now: AwareDatetime
    timezone: str
    outdoor_temp_c: float | None
    battery_soc_pct: float | None
    price_c_per_kwh: float | None
    windows: list[WindowOccurrenceOut]


def actor_instances_for_provider(db: Session, provider: str) -> list[tuple[ActorInstance, bool]]:
    """Instances of `provider` the actor should drive, each with a restore_only flag.
    A disabled instance or site is listed only while an action run still needs
    its restore, and then restore_only is true."""
    rows = db.execute(
        select(ActorInstance, Site)
        .join(Site, Site.id == ActorInstance.site_id)
        .where(ActorInstance.provider == provider)
        .order_by(ActorInstance.id)
    ).all()
    result: list[tuple[ActorInstance, bool]] = []
    for instance, site in rows:
        if instance.enabled and site.enabled:
            result.append((instance, False))
        elif has_unfinished_action_run(db, instance.id):
            result.append((instance, True))
    return result


@router.get("/instances", response_model=list[ActorInstanceOut])
def list_actor_instances(
    provider: str,
    x_actor_key: str = Header(...),
    db: Session = Depends(get_db_session),
) -> list[ActorInstanceOut]:
    require_actor_key(x_actor_key)
    out = []
    for instance, restore_only in actor_instances_for_provider(db, provider):
        site = db.get(Site, instance.site_id)
        out.append(
            ActorInstanceOut(
                instance_id=instance.id,
                site_id=instance.site_id,
                name=instance.name,
                timezone=site.timezone,
                restore_only=restore_only,
                config=dict(instance.config or {}),
            )
        )
    return out


@router.patch("/instances/{instance_id}/discovered-config", status_code=status.HTTP_204_NO_CONTENT)
def report_actor_discovered_config(
    payload: DiscoveredActorConfig,
    instance: ActorInstance = Depends(get_actor_instance),
    db: Session = Depends(get_db_session),
) -> None:
    if payload.config is not None:
        instance.config = {**(instance.config or {}), **payload.config}
    if "auth_state" in payload.model_fields_set:
        instance.auth_state = payload.auth_state
    db.commit()


@router.put("/instances/{instance_id}/capabilities", status_code=status.HTTP_204_NO_CONTENT)
def put_actor_capabilities(
    payload: Capabilities,
    instance: ActorInstance = Depends(get_actor_instance),
    db: Session = Depends(get_db_session),
) -> None:
    instance.capabilities = payload.model_dump(mode="json")
    instance.capabilities_updated_at = datetime.datetime.now(datetime.UTC)
    db.commit()


@router.get("/instances/{instance_id}/schedules", response_model=list[ActorScheduleOut])
def list_actor_schedules(
    instance: ActorInstance = Depends(get_actor_instance),
    db: Session = Depends(get_db_session),
) -> list[ActorScheduleOut]:
    schedules = db.scalars(
        select(ActorSchedule).where(ActorSchedule.instance_id == instance.id).order_by(ActorSchedule.id)
    ).all()
    unfinished = {
        run.schedule_id for run in unfinished_action_runs(db, instance.id)
    }
    return [
        ActorScheduleOut.model_validate(s, from_attributes=True)
        for s in schedules
        if s.enabled or s.id in unfinished
    ]


@router.get("/instances/{instance_id}/context", response_model=ActorContextOut)
def get_actor_context(
    instance: ActorInstance = Depends(get_actor_instance),
    db: Session = Depends(get_db_session),
) -> ActorContextOut:
    site = db.get(Site, instance.site_id)
    if site is None:
        raise AppError(404, "not_found", "Site not found")
    context = build_context(db, site, datetime.datetime.now(datetime.UTC))
    return _context_out(context)


@router.put("/instances/{instance_id}/readings", status_code=status.HTTP_204_NO_CONTENT)
def put_actor_readings(
    payload: ReadingsIn,
    instance: ActorInstance = Depends(get_actor_instance),
    db: Session = Depends(get_db_session),
) -> None:
    instance.latest_readings = payload.state
    instance.last_seen_at = datetime.datetime.now(datetime.UTC)
    db.commit()


def _context_out(context: ActorContext) -> ActorContextOut:
    return ActorContextOut(
        now=as_utc(context.now),
        timezone=context.timezone,
        outdoor_temp_c=context.outdoor_temp_c,
        battery_soc_pct=context.battery_soc_pct,
        price_c_per_kwh=context.price_c_per_kwh,
        windows=[
            WindowOccurrenceOut(
                window_id=w.window_id, label=w.label, start=as_utc(w.start), end=as_utc(w.end)
            )
            for w in context.windows
        ],
    )


_TERMINAL = {RunOutcome.OK, RunOutcome.SKIPPED, RunOutcome.FAILED, RunOutcome.OVERRIDDEN}


@router.get("/instances/{instance_id}/runs", response_model=list[RunOut])
def list_actor_runs(
    unfinished: bool = False,
    pending_test: bool = False,
    since: AwareDatetime | None = None,
    instance: ActorInstance = Depends(get_actor_instance),
    db: Session = Depends(get_db_session),
) -> list[ActorRun]:
    """Exactly one of unfinished, pending_test or since must be given."""
    if int(unfinished) + int(pending_test) + int(since is not None) != 1:
        raise AppError(422, "invalid_filter", "Give exactly one of unfinished, pending_test or since")
    if unfinished:
        return unfinished_action_runs(db, instance.id)
    stmt = select(ActorRun).join(ActorSchedule, ActorSchedule.id == ActorRun.schedule_id)
    stmt = stmt.where(ActorSchedule.instance_id == instance.id)
    if pending_test:
        stmt = stmt.where(ActorRun.kind == RunKind.TEST, ActorRun.outcome == RunOutcome.PENDING)
    else:
        stmt = stmt.where(ActorRun.trigger_at >= as_utc(since))
    return list(db.scalars(stmt.order_by(ActorRun.trigger_at, ActorRun.id)))


@router.post("/instances/{instance_id}/runs", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def create_actor_run(
    payload: RunCreate,
    instance: ActorInstance = Depends(get_actor_instance),
    db: Session = Depends(get_db_session),
) -> ActorRun:
    """The schedule must belong to this instance, else 404."""
    schedule = db.get(ActorSchedule, payload.schedule_id)
    if schedule is None or schedule.instance_id != instance.id:
        raise AppError(404, "not_found", "Schedule not found")
    _check_restore_parent(db, payload)
    run = ActorRun(
        schedule_id=schedule.id,
        kind=payload.kind,
        trigger_at=as_utc(payload.trigger_at),
        outcome=RunOutcome.PENDING,
        parent_run_id=payload.parent_run_id,
        snapshot=payload.snapshot,
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "duplicate_run", "A run for this schedule, kind and trigger time already exists") from None
    return run


@router.patch("/instances/{instance_id}/runs/{run_id}", response_model=RunOut)
def update_actor_run(
    run_id: int,
    payload: RunUpdate,
    instance: ActorInstance = Depends(get_actor_instance),
    db: Session = Depends(get_db_session),
    email_sender: EmailSender = Depends(get_email_sender),
) -> ActorRun:
    run = _instance_run(db, instance, run_id)
    if run.outcome in _TERMINAL:
        raise AppError(409, "run_finished", "This run has already finished")
    now = datetime.datetime.now(datetime.UTC)
    if payload.outcome == RunOutcome.RUNNING:
        _claim_run(db, run, now)
    elif payload.outcome == RunOutcome.PENDING:
        raise AppError(422, "invalid_outcome", "A run cannot return to pending")
    else:
        run.outcome = payload.outcome
        run.finished_at = now
    _apply_details(run, payload)
    db.commit()
    if run.outcome == RunOutcome.FAILED:
        _email_failure(db, run, email_sender)
    return run


def _check_restore_parent(db: Session, payload: RunCreate) -> None:
    if payload.kind != RunKind.RESTORE:
        return
    parent = db.get(ActorRun, payload.parent_run_id) if payload.parent_run_id is not None else None
    if parent is None or parent.kind != RunKind.ACTION or parent.schedule_id != payload.schedule_id:
        raise AppError(422, "invalid_parent", "A restore run needs parent_run_id of an action run on the same schedule")


def _instance_run(db: Session, instance: ActorInstance, run_id: int) -> ActorRun:
    run = db.scalars(
        select(ActorRun)
        .join(ActorSchedule, ActorSchedule.id == ActorRun.schedule_id)
        .where(ActorRun.id == run_id, ActorSchedule.instance_id == instance.id)
    ).one_or_none()
    if run is None:
        raise AppError(404, "not_found", "Run not found")
    return run


def _claim_run(db: Session, run: ActorRun, now: datetime.datetime) -> None:
    """Move pending to running in one conditional UPDATE so two callers cannot both win."""
    result = db.execute(
        update(ActorRun)
        .where(ActorRun.id == run.id, ActorRun.outcome == RunOutcome.PENDING)
        .values(outcome=RunOutcome.RUNNING, started_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise AppError(409, "run_not_pending", "This run is no longer pending")
    db.refresh(run)


def _apply_details(run: ActorRun, payload: RunUpdate) -> None:
    for field in ("message", "attempts", "snapshot"):
        if field in payload.model_fields_set:
            setattr(run, field, getattr(payload, field))


def _email_failure(db: Session, run: ActorRun, email_sender: EmailSender) -> None:
    schedule = db.get(ActorSchedule, run.schedule_id)
    if schedule is None or not schedule.notify_email:
        return
    instance = db.get(ActorInstance, schedule.instance_id)
    site = db.get(Site, instance.site_id) if instance else None
    owner = db.get(User, site.owner_user_id) if site else None
    if owner is None:
        return
    when = as_utc(run.finished_at).astimezone(site_tz(site.timezone)).strftime("%Y-%m-%d %H:%M %Z")
    body = (
        f'The schedule "{schedule.name}" failed at {when} for the site "{site.name}".\n\n'
        f"What went wrong: {run.message or 'no details were reported'}\n"
    )
    try_send_email(email_sender, owner.email, f'Schedule failed: {schedule.name}', body, "actor run failure")
