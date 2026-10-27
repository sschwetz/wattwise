from __future__ import annotations

import datetime
import pathlib
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session, sessionmaker

from core_api.admin_user import expire_password_file
from core_api.jobs.demand_charge_job import run_demand_charge_job
from core_api.jobs.demand_interval_job import run_demand_interval_job
from core_api.jobs.retention_rollup_job import run_retention_rollup_job


def _run_with_session(session_factory: sessionmaker[Session], job_fn: Callable[[Session, datetime.datetime], None]) -> None:
    db = session_factory()
    try:
        job_fn(db, datetime.datetime.now(datetime.UTC))
    finally:
        db.close()


def build_scheduler(session_factory: sessionmaker[Session], password_file: pathlib.Path) -> BackgroundScheduler:
    """One in-process scheduler for all the periodic jobs -- no new
    service is justified for lightweight periodic tasks at this scale
    (spec: Purpose)."""
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: _run_with_session(session_factory, run_demand_interval_job),
        trigger=IntervalTrigger(minutes=5),
        id="demand_interval",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        lambda: _run_with_session(session_factory, run_demand_charge_job),
        trigger=CronTrigger(hour=0, minute=10),
        id="demand_charge",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        lambda: _run_with_session(session_factory, run_retention_rollup_job),
        trigger=CronTrigger(hour=0, minute=20),
        id="retention_rollup",
        max_instances=1,
        coalesce=True,
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
