from __future__ import annotations

import datetime

from pydantic import BaseModel, StrictInt


class TimingIn(BaseModel):
    """Strict ints, so 1.5 and true fail at the schema. Range checks live in validate_timing."""

    kind: str
    interval_minutes: StrictInt | None = None
    hour: StrictInt | None = None
    minute: StrictInt | None = None


class TimingOut(BaseModel):
    interval_minutes: int | None
    hour: int | None
    minute: int | None


class JobOut(BaseModel):
    id: str
    label: str
    kind: str
    timing: TimingOut
    default: TimingOut
    is_default: bool
    min_minutes: int | None
    next_run: datetime.datetime | None
    last_run: datetime.datetime | None


class JobListOut(BaseModel):
    timezone: str
    scheduler_running: bool
    jobs: list[JobOut]
