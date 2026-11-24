from __future__ import annotations

import datetime

import pytest

from core_api.db import make_engine, make_session_factory
from core_api.errors import AppError
from core_api.job_registry import JOBS, Timing, default_timing, validate_timing
from core_api.models.job_schedules import JobSchedule


@pytest.fixture
def session(app_and_db_url):
    _, db_url = app_and_db_url
    db = make_session_factory(make_engine(db_url))()
    yield db
    db.close()


def test_registry_defaults_match_current_schedule():
    assert JOBS["demand_interval"].default_minutes == 5
    assert JOBS["actor_watchdog"].default_minutes == 1
    assert JOBS["service_status"].default_minutes == 1
    for job_id, (hour, minute) in {
        "demand_charge": (0, 10),
        "retention_rollup": (0, 20),
        "mail_outbox_cleanup": (0, 30),
    }.items():
        assert (JOBS[job_id].default_hour, JOBS[job_id].default_minute) == (hour, minute)
    assert "expire_admin_password_file" not in JOBS


@pytest.mark.parametrize(
    ("job_id", "timing"),
    [
        ("demand_interval", Timing("interval", 0, None, None)),
        ("demand_interval", Timing("interval", -5, None, None)),
        ("demand_interval", Timing("interval", True, None, None)),
        ("demand_interval", Timing("interval", 1.5, None, None)),
        ("demand_interval", Timing("interval", None, None, None)),
        ("demand_charge", Timing("daily", None, 24, 0)),
        ("demand_charge", Timing("daily", None, 0, 60)),
        ("demand_charge", Timing("daily", None, True, 0)),
        ("demand_charge", Timing("daily", None, 1.0, 0)),
        ("demand_charge", Timing("daily", None, None, None)),
        ("demand_charge", Timing("interval", 5, None, None)),
        ("demand_interval", Timing("daily", None, 1, 0)),
        ("demand_interval", Timing("cron", 5, None, None)),
    ],
)
def test_validate_timing_rejects(job_id, timing):
    with pytest.raises(AppError) as err:
        validate_timing(JOBS[job_id], timing)
    assert err.value.status_code == 422
    assert err.value.code == "invalid_job_schedule"


@pytest.mark.parametrize("spec", list(JOBS.values()), ids=list(JOBS))
def test_validate_timing_accepts_default(spec):
    validate_timing(spec, default_timing(spec))


def test_job_schedule_row_round_trips(session):
    now = datetime.datetime(2026, 11, 24, 1, 2, tzinfo=datetime.UTC)
    session.add(JobSchedule(job_id="demand_charge", kind="daily", hour=3, minute=15, updated_at=now))
    session.commit()
    row = session.get(JobSchedule, "demand_charge")
    assert (row.kind, row.interval_minutes, row.hour, row.minute) == ("daily", None, 3, 15)
