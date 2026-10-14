from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalSource:
    """A local pricing source as discovery returns it: the id and the config
    (timezone and rate windows) core-api resolved for it."""

    source_id: int
    config: dict


@dataclass(frozen=True)
class Coverage:
    """The earliest interval start and latest interval end stored for one
    channel of a source. Both are aware UTC datetimes."""

    first: datetime.datetime
    last: datetime.datetime


@dataclass(frozen=True)
class RepriceMarker:
    """core-api's note that prices from `start` may have changed. `version`
    identifies the note, so a clear for an old version leaves a newer one."""

    start: datetime.datetime
    version: int


def parse_reprice(config: dict) -> RepriceMarker | None:
    """The reprice marker in a source's config, or None. Raises KeyError,
    TypeError or ValueError on a malformed marker."""
    raw = config.get("reprice")
    if raw is None:
        return None
    start = datetime.datetime.fromisoformat(raw["from"])
    if start.tzinfo is None:
        start = start.replace(tzinfo=datetime.timezone.utc)
    return RepriceMarker(start=start.astimezone(datetime.timezone.utc), version=int(raw["version"]))
