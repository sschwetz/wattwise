from __future__ import annotations

import datetime
import zoneinfo


def as_utc(value: datetime.datetime) -> datetime.datetime:
    """Normalize a possibly-naive datetime to aware UTC. SQLite's DateTime
    column silently drops tzinfo on write, so a value read back naive was
    always stored as UTC — treat it as such here (consolidated from
    routers/sources.py's original _as_utc once a second caller needed the
    identical logic)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def local_midnight(day: datetime.date, tz: zoneinfo.ZoneInfo) -> datetime.datetime:
    """The tz-aware instant for local midnight on `day` in `tz` (consolidated
    from jobs/billing_periods.py's original _local_midnight once a second
    caller needed the identical logic)."""
    return datetime.datetime(day.year, day.month, day.day, tzinfo=tz)


def site_tz(site_timezone: str) -> zoneinfo.ZoneInfo:
    """A site's configured timezone as a ZoneInfo (consolidated once a third
    caller needed the identical zoneinfo.ZoneInfo(site_timezone) construction)."""
    return zoneinfo.ZoneInfo(site_timezone)
