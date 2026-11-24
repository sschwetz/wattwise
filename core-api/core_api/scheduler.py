from __future__ import annotations

import datetime
import pathlib
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from core_api.admin_user import expire_password_file
from core_api.email_sender import EmailSender
from core_api.job_registry import JOBS, Timing, default_timing
from core_api.jobs.actor_watchdog_job import run_actor_watchdog_job
from core_api.jobs.demand_charge_job import run_demand_charge_job
from core_api.jobs.demand_interval_job import run_demand_interval_job
from core_api.jobs.mail_outbox_cleanup_job import run_mail_outbox_cleanup_job
from core_api.jobs.retention_rollup_job import run_retention_rollup_job
from core_api.jobs.service_status_job import run_service_status_job
from core_api.models.job_schedules import JobSchedule

# When each job last finished, success or failure. Cleared only on restart.
LAST_RUNS: dict[str, datetime.datetime] = {}


def _run_with_session(
    job_id: str, session_factory: sessionmaker[Session], job_fn: Callable[[Session, datetime.datetime], None]
) -> None:
    db = session_factory()
    try:
        job_fn(db, datetime.datetime.now(datetime.UTC))
    finally:
        db.close()
        LAST_RUNS[job_id] = datetime.datetime.now(datetime.UTC)


def trigger_for(timing: Timing) -> IntervalTrigger | CronTrigger:
    if timing.kind == "interval":
        return IntervalTrigger(minutes=timing.interval_minutes)
    return CronTrigger(hour=timing.hour, minute=timing.minute)


def load_timings(db: Session) -> dict[str, Timing]:
    """The effective timing per job id: the stored override if there is one, else the default."""
    timings = {job_id: default_timing(spec) for job_id, spec in JOBS.items()}
    for row in db.scalars(select(JobSchedule)):
        if row.job_id in timings:
            timings[row.job_id] = Timing(row.kind, row.interval_minutes, row.hour, row.minute)
    return timings


def apply_timing(scheduler: BackgroundScheduler, job_id: str, timing: Timing) -> None:
    scheduler.reschedule_job(job_id, trigger=trigger_for(timing))


def _service_status_options() -> dict[str, object]:
    # Also runs once at start so the About dialog is not empty for a minute.
    return {"next_run_time": datetime.datetime.now(datetime.UTC), "misfire_grace_time": 30}


def _watchdog(email_sender: EmailSender) -> Callable[[Session, datetime.datetime], None]:
    return lambda db, now: run_actor_watchdog_job(db, now, email_sender)


def build_scheduler(
    session_factory: sessionmaker[Session], password_file: pathlib.Path, email_sender: EmailSender
) -> BackgroundScheduler:
    """One in-process scheduler for all the periodic jobs -- no new
    service is justified for lightweight periodic tasks at this scale
    (spec: Purpose). Every job runs one at a time and coalesces missed runs."""
    scheduler = BackgroundScheduler()
    job_fns: dict[str, Callable[[Session, datetime.datetime], None]] = {
        "demand_interval": run_demand_interval_job,
        "demand_charge": run_demand_charge_job,
        "retention_rollup": run_retention_rollup_job,
        "actor_watchdog": _watchdog(email_sender),
        "mail_outbox_cleanup": run_mail_outbox_cleanup_job,
        "service_status": run_service_status_job,
    }
    with session_factory() as db:
        timings = load_timings(db)
    for job_id, job_fn in job_fns.items():
        scheduler.add_job(
            _run_with_session,
            args=[job_id, session_factory, job_fn],
            trigger=trigger_for(timings[job_id]),
            id=job_id,
            max_instances=1,
            coalesce=True,
            **(_service_status_options() if job_id == "service_status" else {}),
        )
    scheduler.add_job(
        expire_password_file,
        args=[password_file],
        trigger=CronTrigger(hour=3, minute=17),
        id="expire_admin_password_file",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
