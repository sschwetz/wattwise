from __future__ import annotations

import datetime
import re
import secrets
from dataclasses import dataclass

from sqlalchemy.orm import Session

from core_api.config import get_settings
from core_api.models.service_status import ServiceStatus
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
STALE_AFTER = datetime.timedelta(seconds=180)
COMMIT_PATTERN: re.Pattern[str] = re.compile(r"^([0-9a-f]{7,40}|unknown|dev)$")


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
    Compares UTF-8 bytes because compare_digest raises TypeError on non-ASCII str."""
    presented_bytes = presented.encode("utf-8")
    allowed: frozenset[str] = frozenset()
    for key, services in _key_services():
        if secrets.compare_digest(presented_bytes, key.encode("utf-8")):
            allowed = allowed | services
    return allowed


def record_report(db: Session, service: str, commit: str, now: datetime.datetime) -> None:
    row = db.get(ServiceStatus, service)
    if row is None:
        db.add(ServiceStatus(service=service, commit=commit, reported_at=now))
    else:
        row.commit = commit
        row.reported_at = now
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
