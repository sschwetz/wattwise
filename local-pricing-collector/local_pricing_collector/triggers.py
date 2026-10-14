from __future__ import annotations

import datetime
import zoneinfo
from dataclasses import dataclass

SCHEDULED_TIME = datetime.time(23, 59)


@dataclass(frozen=True)
class Triggers:
    """Why a source needs work this tick. `catch_up` fills what is missing
    (a new source, a restart, a new local day). `scheduled` is the daily push
    for the next local day."""

    catch_up: bool
    scheduled: bool

    @property
    def any(self) -> bool:
        return self.catch_up or self.scheduled


@dataclass(frozen=True)
class SourceState:
    """What the parent remembers about a source: the local dates on which its
    catch-up and its scheduled push last succeeded. It lives in memory only,
    so a restart starts with none and does a fresh catch-up."""

    caught_up_date: datetime.date | None = None
    scheduled_date: datetime.date | None = None


def due(state: SourceState | None, now: datetime.datetime, tz: zoneinfo.ZoneInfo) -> Triggers:
    local = now.astimezone(tz)
    state = state or SourceState()
    clock = local.time().replace(second=0, microsecond=0)
    return Triggers(
        catch_up=state.caught_up_date != local.date(),
        scheduled=clock >= SCHEDULED_TIME and state.scheduled_date != local.date(),
    )


def after_success(
    state: SourceState | None, triggers: Triggers, now: datetime.datetime, tz: zoneinfo.ZoneInfo
) -> SourceState:
    day = now.astimezone(tz).date()
    state = state or SourceState()
    return SourceState(
        caught_up_date=day if triggers.catch_up else state.caught_up_date,
        scheduled_date=day if triggers.scheduled else state.scheduled_date,
    )
