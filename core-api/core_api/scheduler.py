from __future__ import annotations

import datetime
import pathlib
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session, sessionmaker

from core_api.admin_user import expire_password_file
from core_api.email_sender import EmailSender
from core_api.jobs.actor_watchdog_job import run_actor_watchdog_job
from core_api.jobs.demand_charge_job import run_demand_charge_job
from core_api.jobs.demand_interval_job import run_demand_interval_job
from core_api.jobs.retention_rollup_job import run_retention_rollup_job
from core_api.jobs.service_status_job import run_service_status_job
from core_api.service_status import REPORT_INTERVAL_SECONDS


def _run_with_session(session_factory: sessionmaker[Session], job_fn: Callable[[Session, datetime.datetime], None]) -> None:
    db = session_factory()
    try:
        job_fn(db, datetime.datetime.now(datetime.UTC))
    finally:
        db.close()


def _watchdog(email_sender: EmailSender) -> Callable[[Session, datetime.datetime], None]:
    return lambda db, now: run_actor_watchdog_job(db, now, email_sender)


def build_scheduler(
    session_factory: sessionmaker[Session], password_file: pathlib.Path, email_sender: EmailSender
) -> BackgroundScheduler:
    """One in-process scheduler for all the periodic jobs -- no new
    service is justified for lightweight periodic tasks at this scale
    (spec: Purpose). Every job runs one at a time and coalesces missed runs."""
    scheduler = BackgroundScheduler()
    session_jobs = [
        ("demand_interval", run_demand_interval_job, IntervalTrigger(minutes=5)),
        ("demand_charge", run_demand_charge_job, CronTrigger(hour=0, minute=10)),
        ("retention_rollup", run_retention_rollup_job, CronTrigger(hour=0, minute=20)),
        ("actor_watchdog", _watchdog(email_sender), IntervalTrigger(minutes=1)),
        ("service_status", run_service_status_job, IntervalTrigger(seconds=REPORT_INTERVAL_SECONDS)),
    ]
    # service_status also runs once at start so the About dialog is not empty for a minute.
    run_at_start = {"service_status"}
    for job_id, job_fn, trigger in session_jobs:
        extra = {"next_run_time": datetime.datetime.now(datetime.UTC)} if job_id in run_at_start else {}
        scheduler.add_job(
            _run_with_session,
            args=[session_factory, job_fn],
            trigger=trigger,
            id=job_id,
            max_instances=1,
            coalesce=True,
            **extra,
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
