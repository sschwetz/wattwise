from __future__ import annotations

import datetime
import zoneinfo
from dataclasses import dataclass

SCHEDULED_TIME = datetime.time(23, 59)
CARRY_OVER_UNTIL = datetime.time(2, 0)


@dataclass(frozen=True)
class Triggers:
    """Why a source needs work this tick. `catch_up` fills what is missing
    (a new source, a restart, a new local day). `scheduled_for` is the local
    date whose prices the daily push must rewrite, or None."""

    catch_up: bool
    scheduled_for: datetime.date | None = None

    @property
    def scheduled(self) -> bool:
        return self.scheduled_for is not None

    @property
    def any(self) -> bool:
        return self.catch_up or self.scheduled


@dataclass(frozen=True)
class SourceState:
    """What the parent remembers about a source: the local date on which its
    catch-up last succeeded and the target date its scheduled push last
    succeeded for. It lives in memory only,
    so a restart starts with none and does a fresh catch-up."""

    caught_up_date: datetime.date | None = None
    scheduled_date: datetime.date | None = None


def _scheduled_target(local: datetime.datetime) -> datetime.date | None:
    """The date the daily push is owed for: tomorrow from 23:59, and today
    until 02:00 so a failed push is retried after midnight."""
    clock = local.time().replace(second=0, microsecond=0)
    if clock >= SCHEDULED_TIME:
        return local.date() + datetime.timedelta(days=1)
    if clock < CARRY_OVER_UNTIL:
        return local.date()
    return None


def due(state: SourceState | None, now: datetime.datetime, tz: zoneinfo.ZoneInfo) -> Triggers:
    local = now.astimezone(tz)
    state = state or SourceState()
    target = _scheduled_target(local)
    return Triggers(
        catch_up=state.caught_up_date != local.date(),
        scheduled_for=target if target != state.scheduled_date else None,
    )


def after_success(
    state: SourceState | None, triggers: Triggers, now: datetime.datetime, tz: zoneinfo.ZoneInfo
) -> SourceState:
    day = now.astimezone(tz).date()
    state = state or SourceState()
    return SourceState(
        caught_up_date=day if triggers.catch_up else state.caught_up_date,
        scheduled_date=triggers.scheduled_for or state.scheduled_date,
    )
