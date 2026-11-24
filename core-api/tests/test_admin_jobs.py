from __future__ import annotations

import datetime
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from core_api import scheduler as scheduler_module
from core_api.db import make_engine, make_session_factory
from core_api.job_registry import JOBS
from core_api.main import create_app
from core_api.models.identity import User
from core_api.models.job_schedules import JobSchedule
from tests.test_billing import auth

JOBS_URL = "/api/v1/admin/jobs"


@pytest.fixture(autouse=True)
def _clear_last_runs():
    scheduler_module.LAST_RUNS.clear()
    yield
    scheduler_module.LAST_RUNS.clear()


@pytest.fixture(autouse=True)
def _enable_core_api_loggers(app_and_db_url, monkeypatch):
    for name, candidate in logging.root.manager.loggerDict.items():
        if name.startswith("core_api") and isinstance(candidate, logging.Logger):
            monkeypatch.setattr(candidate, "disabled", False)


@pytest.fixture
def session(app_and_db_url):
    _, db_url = app_and_db_url
    db = make_session_factory(make_engine(db_url))()
    yield db
    db.close()


@pytest.fixture
def admin(session) -> int:
    user = User(email="jobs-admin@example.com", password_hash="x", is_admin=True)
    session.add(user)
    session.commit()
    return user.id


@pytest.fixture
def live(app_and_db_url, monkeypatch):
    """A client inside the app lifespan, with the scheduler running."""
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    app, _ = app_and_db_url
    with TestClient(app) as client:
        yield client


def _rows(session) -> list[JobSchedule]:
    session.expire_all()
    return list(session.scalars(select(JobSchedule)))


def _interval(client, job_id: str) -> datetime.timedelta:
    return client.app.state.scheduler.get_job(job_id).trigger.interval


def test_get_lists_all_registry_jobs_with_defaults(live, admin):
    got = live.get(JOBS_URL, headers=auth(admin))
    assert got.status_code == 200
    body = got.json()
    assert body["scheduler_running"] is True
    assert body["timezone"] == str(live.app.state.scheduler.timezone)
    assert {j["id"] for j in body["jobs"]} == set(JOBS)
    assert len(body["jobs"]) == 6
    for job in body["jobs"]:
        assert job["is_default"] is True
        assert job["timing"] == job["default"]
        assert job["next_run"] is not None
        assert job["last_run"] is None


def test_get_reports_last_run(live, admin):
    stamp = datetime.datetime(2026, 1, 2, 3, 4, tzinfo=datetime.UTC)
    scheduler_module.LAST_RUNS["actor_watchdog"] = stamp
    jobs = {j["id"]: j for j in live.get(JOBS_URL, headers=auth(admin)).json()["jobs"]}
    assert jobs["actor_watchdog"]["last_run"].startswith("2026-01-02T03:04")


def test_put_stores_row_and_reschedules(live, admin, session):
    got = live.put(f"{JOBS_URL}/demand_interval", json={"kind": "interval", "interval_minutes": 10},
                   headers=auth(admin))
    assert got.status_code == 200
    body = got.json()
    assert body["is_default"] is False
    assert body["timing"]["interval_minutes"] == 10
    assert body["default"]["interval_minutes"] == 5
    assert _interval(live, "demand_interval") == datetime.timedelta(minutes=10)
    [row] = _rows(session)
    assert (row.job_id, row.kind, row.interval_minutes) == ("demand_interval", "interval", 10)


def test_put_daily_job(live, admin, session):
    got = live.put(f"{JOBS_URL}/demand_charge", json={"kind": "daily", "hour": 1, "minute": 5},
                   headers=auth(admin))
    assert got.status_code == 200
    assert got.json()["timing"] == {"interval_minutes": None, "hour": 1, "minute": 5}
    trigger = live.app.state.scheduler.get_job("demand_charge").trigger
    fields = {f.name: str(f) for f in trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("1", "5")
    [row] = _rows(session)
    assert (row.hour, row.minute) == (1, 5)


def test_put_twice_updates_the_same_row(live, admin, session):
    for minutes in (10, 20):
        live.put(f"{JOBS_URL}/demand_interval", json={"kind": "interval", "interval_minutes": minutes},
                 headers=auth(admin))
    [row] = _rows(session)
    assert row.interval_minutes == 20


def test_delete_resets_to_default(live, admin, session):
    live.put(f"{JOBS_URL}/demand_interval", json={"kind": "interval", "interval_minutes": 10}, headers=auth(admin))
    got = live.delete(f"{JOBS_URL}/demand_interval", headers=auth(admin))
    assert got.status_code == 200
    assert got.json()["is_default"] is True
    assert got.json()["timing"]["interval_minutes"] == 5
    assert _rows(session) == []
    assert _interval(live, "demand_interval") == datetime.timedelta(minutes=5)


def test_delete_without_a_row_is_fine(live, admin):
    got = live.delete(f"{JOBS_URL}/demand_interval", headers=auth(admin))
    assert got.status_code == 200 and got.json()["is_default"] is True


INVALID = [
    ("demand_interval", {"kind": "interval", "interval_minutes": 0}),
    ("demand_interval", {"kind": "interval", "interval_minutes": -5}),
    ("demand_interval", {"kind": "interval", "interval_minutes": 1.5}),
    ("demand_interval", {"kind": "interval", "interval_minutes": True}),
    ("demand_interval", {"kind": "interval"}),
    ("demand_charge", {"kind": "daily", "hour": 24, "minute": 0}),
    ("demand_charge", {"kind": "daily", "hour": 0, "minute": 60}),
    ("demand_charge", {"kind": "daily", "hour": -1, "minute": 0}),
    ("demand_charge", {"kind": "daily", "hour": 1}),
    ("demand_charge", {"kind": "interval", "interval_minutes": 5}),
    ("demand_interval", {"kind": "daily", "hour": 1, "minute": 0}),
    ("demand_interval", {"kind": "weekly", "interval_minutes": 5}),
]


@pytest.mark.parametrize(("job_id", "payload"), INVALID)
def test_put_invalid_returns_422_and_changes_nothing(live, admin, session, job_id, payload):
    before = live.app.state.scheduler.get_job(job_id).trigger
    got = live.put(f"{JOBS_URL}/{job_id}", json=payload, headers=auth(admin))
    assert got.status_code == 422
    assert _rows(session) == []
    assert str(live.app.state.scheduler.get_job(job_id).trigger) == str(before)


def test_put_below_min_minutes_is_422(live, admin, session, monkeypatch):
    from dataclasses import replace

    monkeypatch.setitem(JOBS, "demand_interval", replace(JOBS["demand_interval"], min_minutes=5))
    got = live.put(f"{JOBS_URL}/demand_interval", json={"kind": "interval", "interval_minutes": 2},
                   headers=auth(admin))
    assert got.status_code == 422
    assert got.json()["code"] == "invalid_job_schedule"
    assert _rows(session) == []
    assert _interval(live, "demand_interval") == datetime.timedelta(minutes=5)


@pytest.mark.parametrize("job_id", ["nope", "expire_admin_password_file"])
def test_unknown_job_is_404(live, admin, job_id):
    body = {"kind": "interval", "interval_minutes": 5}
    assert live.put(f"{JOBS_URL}/{job_id}", json=body, headers=auth(admin)).status_code == 404
    assert live.delete(f"{JOBS_URL}/{job_id}", headers=auth(admin)).status_code == 404


def test_non_admin_is_403_and_anonymous_is_401(client, session):
    plain = User(email="plain@example.com", password_hash="x")
    session.add(plain)
    session.commit()
    body = {"kind": "interval", "interval_minutes": 5}
    calls = [
        ("GET", JOBS_URL, None),
        ("PUT", f"{JOBS_URL}/demand_interval", body),
        ("DELETE", f"{JOBS_URL}/demand_interval", None),
    ]
    for method, url, payload in calls:
        assert client.request(method, url, json=payload).status_code == 401, (method, url)
        got = client.request(method, url, json=payload, headers=auth(plain.id))
        assert got.status_code == 403, (method, url)
        assert got.json()["code"] == "admin_required"


def test_scheduler_disabled_still_stores_row(app_and_db_url, monkeypatch, admin, session):
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    app, db_url = app_and_db_url
    app = create_app(database_url=db_url)
    with TestClient(app) as client:
        body = client.get(JOBS_URL, headers=auth(admin)).json()
        assert body["scheduler_running"] is False
        assert body["timezone"] == "UTC"
        assert all(job["next_run"] is None for job in body["jobs"])
        got = client.put(f"{JOBS_URL}/demand_interval", json={"kind": "interval", "interval_minutes": 10},
                         headers=auth(admin))
        assert got.status_code == 200 and got.json()["next_run"] is None
        assert client.delete(f"{JOBS_URL}/demand_interval", headers=auth(admin)).status_code == 200
    assert _rows(session) == []


def test_put_without_lifespan_stores_row(client, admin, session):
    got = client.put(f"{JOBS_URL}/demand_interval", json={"kind": "interval", "interval_minutes": 10},
                     headers=auth(admin))
    assert got.status_code == 200
    assert len(_rows(session)) == 1


def test_admin_action_log_has_no_values(live, admin, caplog):
    caplog.set_level(logging.DEBUG, logger="core_api.admin_users")
    live.put(f"{JOBS_URL}/demand_interval", json={"kind": "interval", "interval_minutes": 7351},
             headers=auth(admin))
    live.delete(f"{JOBS_URL}/demand_interval", headers=auth(admin))
    lines = [r.getMessage() for r in caplog.records if r.name == "core_api.admin_users"]
    assert lines == [
        f"admin action: admin_user_id={admin} target=none action=save_job_schedule",
        f"admin action: admin_user_id={admin} target=none action=reset_job_schedule",
    ]
    assert "7351" not in caplog.text
