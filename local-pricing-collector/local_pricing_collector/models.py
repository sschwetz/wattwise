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
