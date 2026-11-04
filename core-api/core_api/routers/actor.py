from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Header, status
from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.actor_capabilities import Capabilities
from core_api.actor_context import ActorContext, build_context
from core_api.actor_runs import has_unfinished_action_run, unfinished_action_runs
from core_api.deps import get_actor_instance, get_db_session, require_actor_key
from core_api.errors import AppError
from core_api.models.actors import ActorInstance, ActorSchedule
from core_api.models.identity import Site
from core_api.time_utils import as_utc

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
