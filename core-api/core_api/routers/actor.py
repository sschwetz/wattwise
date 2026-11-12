from __future__ import annotations

import datetime
from typing import cast

from fastapi import APIRouter, Depends, Header, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, computed_field
from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_api.actor_capabilities import Capabilities
from core_api.actor_context import ActorContext, build_context
from core_api.actor_email import site_local_time, site_owner
from core_api.actor_runs import has_unfinished_action_run, unfinished_action_runs
from core_api.actor_schemas import AwareUtc, RunCreate, RunOut, RunUpdate, StoredConditions, StoredRestore
from core_api.deps import get_actor_instance, get_db_session, get_email_sender, require_actor_key
from core_api.email_sender import EmailSender, try_send_email
from core_api.errors import AppError
from core_api.models.actors import ActorInstance, ActorRun, ActorSchedule, RunKind, RunOutcome
from core_api.models.identity import Site
from core_api.time_utils import as_utc

router = APIRouter(prefix="/actor", tags=["actor"])

# A run with one of these outcomes is finished. The one change still allowed is
# an ok action run becoming overridden (see _is_override).
_TERMINAL = {RunOutcome.OK, RunOutcome.PARTIAL, RunOutcome.SKIPPED, RunOutcome.FAILED, RunOutcome.OVERRIDDEN}


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
    conditions: StoredConditions
    actions: list[dict]
    restore: StoredRestore
    retry: dict
    notify_email: bool
    created_at: AwareUtc

    @computed_field  # type: ignore[prop-decorator]
    @property
    def action(self) -> dict:
        """The first action, kept for one release for actors that read a single action."""
        return self.actions[0]


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


def actor_instances_for_provider(db: Session, provider: str) -> list[tuple[ActorInstance, Site, bool]]:
    """Instances of `provider` the actor should drive, each with its site and a restore_only flag.
    A disabled instance or site is listed only while an action run still needs
    its restore, and then restore_only is true."""
    rows = db.execute(
        select(ActorInstance, Site)
        .join(Site, Site.id == ActorInstance.site_id)
        .where(ActorInstance.provider == provider)
        .order_by(ActorInstance.id)
    ).all()
    result: list[tuple[ActorInstance, Site, bool]] = []
    for instance, site in rows:
        if instance.enabled and site.enabled:
            result.append((instance, site, False))
        elif has_unfinished_action_run(db, instance.id):
            result.append((instance, site, True))
    return result


@router.get("/instances", response_model=list[ActorInstanceOut])
def list_actor_instances(
    provider: str,
    x_actor_key: str = Header(...),
    x_actor_provider: str | None = Header(None),
    db: Session = Depends(get_db_session),
) -> list[ActorInstanceOut]:
    """The provider header must name the provider asked for, as on the per-instance routes."""
    require_actor_key(x_actor_key)
    if x_actor_provider != provider:
        raise AppError(401, "invalid_actor_key", "Invalid actor key for this instance")
    return [
        ActorInstanceOut(
            instance_id=instance.id,
            site_id=instance.site_id,
            name=instance.name,
            timezone=site.timezone,
            restore_only=restore_only,
            config=dict(instance.config or {}),
        )
        for instance, site, restore_only in actor_instances_for_provider(db, provider)
    ]


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
    if since is not None:
        stmt = stmt.where(ActorRun.trigger_at >= as_utc(since))
    else:
        stmt = stmt.where(ActorRun.kind == RunKind.TEST, ActorRun.outcome == RunOutcome.PENDING)
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
    if run.outcome in _TERMINAL and not _is_override(run, payload.outcome):
        raise AppError(409, "run_finished", "This run has already finished")
    if payload.outcome == RunOutcome.PENDING:
        raise AppError(422, "invalid_outcome", "A run cannot return to pending")
    _transition_run(db, run, payload, datetime.datetime.now(datetime.UTC))
    if payload.outcome in (RunOutcome.FAILED, RunOutcome.PARTIAL):
        _email_failure(db, run, email_sender)
    return run


def _is_override(run: ActorRun, new: RunOutcome) -> bool:
    """The one change allowed on a finished run: the owner changed the unit after an ok action run."""
    return run.kind == RunKind.ACTION and run.outcome == RunOutcome.OK and new == RunOutcome.OVERRIDDEN


def _check_restore_parent(db: Session, payload: RunCreate) -> None:
    if payload.kind != RunKind.RESTORE:
        if payload.parent_run_id is not None:
            raise AppError(422, "invalid_parent", "Only a restore run takes parent_run_id")
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


def _step_values(payload: RunUpdate) -> list[dict]:
    return [step.model_dump() for step in payload.steps or []]


def _transition_run(db: Session, run: ActorRun, payload: RunUpdate, now: datetime.datetime) -> None:
    """Apply the update in one conditional UPDATE so a concurrent caller cannot
    claim twice or rewrite a finished run. Zero matched rows means it lost."""
    overriding = _is_override(run, payload.outcome)
    # An override keeps the snapshot, attempts and steps of the action it overrides.
    fields = ("message",) if overriding else ("message", "attempts", "snapshot", "steps")
    values: dict = {"outcome": payload.outcome}
    values.update({f: getattr(payload, f) for f in fields if f in payload.model_fields_set})
    if values.get("steps") is not None:
        values["steps"] = _step_values(payload)
    allowed: tuple[RunOutcome, ...]
    if payload.outcome == RunOutcome.RUNNING:
        values["started_at"] = now
        allowed, lost = (RunOutcome.PENDING,), AppError(409, "run_not_pending", "This run is no longer pending")
    elif overriding:
        # finished_at stays as the action set it.
        allowed, lost = (RunOutcome.OK,), AppError(409, "run_finished", "This run has already finished")
    else:
        values["finished_at"] = now
        allowed = (RunOutcome.PENDING, RunOutcome.RUNNING)
        lost = AppError(409, "run_finished", "This run has already finished")
    result = cast(
        CursorResult,
        db.execute(
            update(ActorRun)
            .where(ActorRun.id == run.id, ActorRun.outcome.in_(allowed))
            .values(**values)
            .execution_options(synchronize_session=False)
        ),
    )
    if result.rowcount != 1:
        db.rollback()
        raise lost
    db.commit()
    db.refresh(run)


def _email_failure(db: Session, run: ActorRun, email_sender: EmailSender) -> None:
    schedule = db.get(ActorSchedule, run.schedule_id)
    if schedule is None or not schedule.notify_email:
        return
    instance = db.get(ActorInstance, schedule.instance_id)
    site = db.get(Site, instance.site_id) if instance else None
    owner = site_owner(db, site) if site else None
    if site is None or owner is None:
        return
    when = site_local_time(run.finished_at or run.trigger_at, site)
    verb = "partly failed" if run.outcome == RunOutcome.PARTIAL else "failed"
    body = (
        f'The schedule "{schedule.name}" {verb} at {when} for the site "{site.name}".\n\n'
        f"What went wrong: {run.message or 'no details were reported'}\n"
    )
    try_send_email(email_sender, owner.email, f'Schedule failed: {schedule.name}', body, "actor run failure")
