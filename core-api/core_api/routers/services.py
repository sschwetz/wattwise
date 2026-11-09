from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Header, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core_api.deps import get_current_user, get_db_session
from core_api.errors import AppError
from core_api.models.identity import User
from core_api.service_status import COMMIT_PATTERN, allowed_services_for_key, list_statuses, record_report

router = APIRouter(prefix="/services", tags=["services"])


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    commit: str = Field(pattern=COMMIT_PATTERN.pattern)


class ServiceStatusOut(BaseModel):
    service: str
    commit: str | None
    last_reported_at: AwareDatetime | None
    reporting: bool


@router.post("/report", status_code=status.HTTP_204_NO_CONTENT)
def report_service(
    payload: ReportIn,
    x_collector_key: str | None = Header(None),
    x_actor_key: str | None = Header(None),
    db: Session = Depends(get_db_session),
) -> None:
    presented = x_collector_key or x_actor_key
    allowed = allowed_services_for_key(presented) if presented else frozenset()
    if not allowed:
        raise AppError(401, "invalid_service_key", "Invalid service key")
    if payload.service not in allowed:
        raise AppError(403, "service_not_allowed", "This key may not report for that service")
    record_report(db, payload.service, payload.commit, datetime.datetime.now(datetime.UTC))


@router.get("", response_model=list[ServiceStatusOut])
def list_services(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> list[ServiceStatusOut]:
    rows = list_statuses(db, datetime.datetime.now(datetime.UTC))
    return [ServiceStatusOut(**vars(row)) for row in rows]
