from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_api.commit import COMMIT_PATTERN as COMMIT_PATTERN
from core_api.config import get_settings
from core_api.models.service_status import ServiceStatus
from core_api.security import keys_match
from core_api.time_utils import as_utc

SERVICES: tuple[str, ...] = (
    "core-api",
    "webapp",
    "envoy-collector",
    "weatherlink-local-collector",
    "amber-pricing-collector",
    "local-pricing-collector",
    "actron-neo-actor",
)
REPORT_INTERVAL_SECONDS = 60
# A service counts as reporting while its last report is at most three intervals old.
STALE_AFTER = datetime.timedelta(seconds=3 * REPORT_INTERVAL_SECONDS)


@dataclass(frozen=True)
class ServiceStatusRow:
    service: str
    commit: str | None
    last_reported_at: datetime.datetime | None
    reporting: bool


def _key_services() -> list[tuple[str, frozenset[str]]]:
    settings = get_settings()
    return [
        (settings.envoy_collector_key, frozenset({"envoy-collector"})),
        (settings.weather_collector_key, frozenset({"weatherlink-local-collector"})),
        (settings.pricing_collector_key, frozenset({"amber-pricing-collector", "local-pricing-collector"})),
        (settings.actor_key, frozenset({"actron-neo-actor"})),
    ]


def allowed_services_for_key(presented: str) -> frozenset[str]:
    """Services the presented key may report for; empty when it matches no key.
    Checks every key, with no early exit."""
    allowed: frozenset[str] = frozenset()
    for key, services in _key_services():
        if keys_match(presented, key):
            allowed = allowed | services
    return allowed


def _update_report(db: Session, service: str, commit: str, now: datetime.datetime) -> bool:
    result = db.execute(
        update(ServiceStatus).where(ServiceStatus.service == service).values(commit=commit, reported_at=now)
    )
    return result.rowcount > 0  # type: ignore[attr-defined]


def record_report(db: Session, service: str, commit: str, now: datetime.datetime) -> None:
    """Upsert without a read: UPDATE, INSERT when no row matched, and UPDATE again
    if another writer inserted the row first."""
    if not _update_report(db, service, commit, now):
        try:
            db.add(ServiceStatus(service=service, commit=commit, reported_at=now))
            db.flush()
        except IntegrityError:
            db.rollback()
            _update_report(db, service, commit, now)
    db.commit()


def list_statuses(db: Session, now: datetime.datetime) -> list[ServiceStatusRow]:
    stored = {row.service: row for row in db.query(ServiceStatus).all()}
    result: list[ServiceStatusRow] = []
    for service in SERVICES:
        row = stored.get(service)
        if row is None:
            result.append(ServiceStatusRow(service, None, None, False))
            continue
        reported_at = as_utc(row.reported_at)
        result.append(ServiceStatusRow(service, row.commit, reported_at, now - reported_at <= STALE_AFTER))
    return result
