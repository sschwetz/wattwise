from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.actor_capabilities import (
    SITE_READING_IDS,
    validate_action_against,
    validate_conditions_against,
)
from core_api.actor_schemas import Capabilities, ScheduleCreate
from core_api.errors import AppError
from core_api.models.actors import ActorInstance
from core_api.models.billing import DemandWindow, SiteTariff


def parse_schedule(data: dict) -> ScheduleCreate:
    """Parse a full schedule dict, turning pydantic errors into a 422 invalid_schedule."""
    try:
        return ScheduleCreate.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise AppError(422, "invalid_schedule", f"{where}: {first['msg']}") from exc


def _check_window_belongs_to_site(db: Session, site_id: int, schedule: ScheduleCreate) -> None:
    window_id = getattr(schedule.trigger, "window_id", None)
    if window_id is None:
        return
    found = db.scalar(
        select(DemandWindow.id)
        .join(SiteTariff, SiteTariff.id == DemandWindow.site_tariff_id)
        .where(DemandWindow.id == window_id, SiteTariff.site_id == site_id)
    )
    if found is None:
        raise AppError(422, "invalid_schedule", f"Window {window_id} is not a demand window of this site")


def validate_schedule(db: Session, site_id: int, instance: ActorInstance, schedule: ScheduleCreate) -> None:
    """Check the action, every condition and the trigger window against the instance and site."""
    if not instance.capabilities:
        raise AppError(422, "no_capabilities", "This actor has not declared its capabilities yet")
    capabilities = Capabilities.model_validate(instance.capabilities)
    validate_action_against(capabilities, schedule.action)
    conditions = [*schedule.conditions, *schedule.restore.only_if, *schedule.restore.early_if]
    validate_conditions_against(capabilities, set(SITE_READING_IDS), conditions)
    _check_window_belongs_to_site(db, site_id, schedule)
