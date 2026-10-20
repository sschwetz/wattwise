from __future__ import annotations

import datetime


def naive_utc(value: datetime.datetime) -> datetime.datetime:
    """SQLite hands datetimes back naive (always UTC); callers pass aware ones."""
    if value.tzinfo is None:
        return value
    return value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
