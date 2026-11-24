"""Timing data for the scheduled jobs an admin can edit.

This module holds data only. It must not import from core_api.jobs, which
imports the scheduler and would create a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeGuard

from core_api.errors import AppError

MAX_HOUR = 23
MAX_MINUTE = 59


@dataclass(frozen=True)
class JobSpec:
    id: str
    label: str
    kind: Literal["interval", "daily"]
    default_minutes: int | None = None
    default_hour: int | None = None
    default_minute: int | None = None
    min_minutes: int | None = None
    max_minutes: int | None = None


@dataclass(frozen=True)
class Timing:
    kind: str
    interval_minutes: int | None
    hour: int | None
    minute: int | None


MAX_INTERVAL_MINUTES = 1440


def _interval(job_id: str, label: str, minutes: int, max_minutes: int = MAX_INTERVAL_MINUTES) -> JobSpec:
    return JobSpec(job_id, label, "interval", default_minutes=minutes, min_minutes=1, max_minutes=max_minutes)


def _daily(job_id: str, label: str, hour: int, minute: int) -> JobSpec:
    return JobSpec(job_id, label, "daily", default_hour=hour, default_minute=minute)


JOBS: dict[str, JobSpec] = {
    spec.id: spec
    for spec in (
        _interval("demand_interval", "Demand interval calculation", 5),
        _daily("demand_charge", "Daily demand charge", 0, 10),
        _daily("retention_rollup", "Retention rollup", 0, 20),
        _interval("actor_watchdog", "Actor watchdog", 1),
        _daily("mail_outbox_cleanup", "Mail outbox cleanup", 0, 30),
        # The About dialog marks a service stale after 3 x 60 s (STALE_AFTER in service_status.py).
        _interval("service_status", "Service status report", 1, max_minutes=2),
    )
}


def default_timing(spec: JobSpec) -> Timing:
    """The timing the job runs with when no override row exists."""
    if spec.kind == "interval":
        return Timing("interval", spec.default_minutes, None, None)
    return Timing("daily", None, spec.default_hour, spec.default_minute)


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _fail(message: str) -> AppError:
    return AppError(422, "invalid_job_schedule", message)


def _validate_interval(spec: JobSpec, timing: Timing) -> None:
    minutes = timing.interval_minutes
    if not _is_int(minutes):
        raise _fail("interval_minutes must be a whole number of minutes")
    floor = spec.min_minutes or 1
    if minutes < floor:
        raise _fail(f"interval_minutes must be at least {floor}")
    if spec.max_minutes is not None and minutes > spec.max_minutes:
        raise _fail(f"interval_minutes must be at most {spec.max_minutes}")


def _validate_daily(timing: Timing) -> None:
    for name, value, limit in (("hour", timing.hour, MAX_HOUR), ("minute", timing.minute, MAX_MINUTE)):
        if not _is_int(value):
            raise _fail(f"{name} must be a whole number")
        if not 0 <= value <= limit:
            raise _fail(f"{name} must be between 0 and {limit}")


def validate_timing(spec: JobSpec, timing: Timing) -> None:
    """Raise AppError(422, "invalid_job_schedule") when the timing does not fit the job."""
    if timing.kind != spec.kind:
        raise _fail(f"{spec.id} runs on a {spec.kind} schedule, not {timing.kind}")
    if spec.kind == "interval":
        _validate_interval(spec, timing)
    else:
        _validate_daily(timing)
