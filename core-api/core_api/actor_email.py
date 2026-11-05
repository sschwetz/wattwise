"""Shared pieces of the actor emails: who gets them and how times are written."""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from core_api.models.identity import Site, User
from core_api.time_utils import as_utc, site_tz


def site_owner(db: Session, site: Site) -> User | None:
    return db.get(User, site.owner_user_id)


def site_local_time(value: datetime.datetime, site: Site) -> str:
    """`value` on the site's clock, for example "2026-11-05 16:00 AEDT"."""
    return as_utc(value).astimezone(site_tz(site.timezone)).strftime("%Y-%m-%d %H:%M %Z")
