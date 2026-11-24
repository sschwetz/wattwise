"""Admin side of job schedules: list, save and reset a job's timing.

save_timing and reset_timing commit, because the live trigger changes only
after the row is stored. Neither logs; the router does."""

from __future__ import annotations

import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from core_api.admin_jobs_schemas import JobListOut, JobOut, TimingIn, TimingOut
from core_api.errors import AppError
from core_api.job_registry import JOBS, JobSpec, Timing, default_timing, validate_timing
from core_api.models.job_schedules import JobSchedule
from core_api.scheduler import LAST_RUNS, apply_timing, load_timings


def get_spec_or_404(job_id: str) -> JobSpec:
    spec = JOBS.get(job_id)
    if spec is None:
        raise AppError(404, "not_found", f"Job {job_id} not found")
    return spec


def _timing_out(timing: Timing) -> TimingOut:
    return TimingOut(interval_minutes=timing.interval_minutes, hour=timing.hour, minute=timing.minute)


def _next_run(scheduler: BackgroundScheduler | None, job_id: str) -> datetime.datetime | None:
    job = scheduler.get_job(job_id) if scheduler is not None else None
    return getattr(job, "next_run_time", None)


def _job_out(spec: JobSpec, timing: Timing, scheduler: BackgroundScheduler | None) -> JobOut:
    default = default_timing(spec)
    return JobOut(
        id=spec.id,
        label=spec.label,
        kind=spec.kind,
        timing=_timing_out(timing),
        default=_timing_out(default),
        is_default=timing == default,
        min_minutes=spec.min_minutes,
        max_minutes=spec.max_minutes,
        next_run=_next_run(scheduler, spec.id),
        last_run=LAST_RUNS.get(spec.id),
    )


def list_jobs(db: Session, scheduler: BackgroundScheduler | None) -> JobListOut:
    timings = load_timings(db)
    return JobListOut(
        timezone=str(scheduler.timezone) if scheduler is not None else "UTC",
        scheduler_running=scheduler is not None and scheduler.running,
        jobs=[_job_out(spec, timings[job_id], scheduler) for job_id, spec in JOBS.items()],
    )


def _finish(scheduler: BackgroundScheduler | None, spec: JobSpec, timing: Timing) -> JobOut:
    """Commit has already happened. Point the live trigger at the stored timing."""
    if scheduler is not None:
        apply_timing(scheduler, spec.id, timing)
    return _job_out(spec, timing, scheduler)


def _upsert_row(db: Session, job_id: str, timing: Timing) -> None:
    row = db.get(JobSchedule, job_id) or JobSchedule(job_id=job_id)
    row.kind = timing.kind
    row.interval_minutes = timing.interval_minutes
    row.hour = timing.hour
    row.minute = timing.minute
    row.updated_at = datetime.datetime.now(datetime.UTC)
    db.add(row)
    db.commit()


def _delete_row(db: Session, job_id: str) -> None:
    row = db.get(JobSchedule, job_id)
    if row is not None:
        db.delete(row)
        db.commit()


def save_timing(db: Session, scheduler: BackgroundScheduler | None, job_id: str, payload: TimingIn) -> JobOut:
    spec = get_spec_or_404(job_id)
    timing = Timing(payload.kind, payload.interval_minutes, payload.hour, payload.minute)
    validate_timing(spec, timing)
    if timing == default_timing(spec):
        _delete_row(db, job_id)  # the table never holds a copy of a default
    else:
        _upsert_row(db, job_id, timing)
    return _finish(scheduler, spec, timing)


def reset_timing(db: Session, scheduler: BackgroundScheduler | None, job_id: str) -> JobOut:
    spec = get_spec_or_404(job_id)
    _delete_row(db, job_id)
    return _finish(scheduler, spec, default_timing(spec))
