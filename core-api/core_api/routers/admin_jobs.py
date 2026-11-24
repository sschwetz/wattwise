from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from core_api import admin_jobs, admin_users
from core_api.admin_jobs_schemas import JobListOut, JobOut, TimingIn
from core_api.deps import get_db_session, require_admin
from core_api.models.identity import User

router = APIRouter(prefix="/admin/jobs", tags=["admin"], dependencies=[Depends(require_admin)])


def _scheduler(request: Request) -> BackgroundScheduler | None:
    """None when the scheduler is disabled or the app runs without its lifespan."""
    return getattr(request.app.state, "scheduler", None)


@router.get("", response_model=JobListOut)
def list_jobs(request: Request, db: Session = Depends(get_db_session)) -> JobListOut:
    return admin_jobs.list_jobs(db, _scheduler(request))


@router.put("/{job_id}", response_model=JobOut)
def save_job_schedule(
    job_id: str,
    payload: TimingIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
) -> JobOut:
    out = admin_jobs.save_timing(db, _scheduler(request), job_id, payload)
    admin_users.log_admin_action(admin, None, "save_job_schedule")
    return out


@router.delete("/{job_id}", response_model=JobOut)
def reset_job_schedule(
    job_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
) -> JobOut:
    out = admin_jobs.reset_timing(db, _scheduler(request), job_id)
    admin_users.log_admin_action(admin, None, "reset_job_schedule")
    return out
