from __future__ import annotations

from fastapi.testclient import TestClient

from core_api import scheduler as scheduler_module
from core_api.main import create_app


def test_scheduler_starts_and_registers_all_jobs(app_and_db_url, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    app, db_url = app_and_db_url
    app = create_app(database_url=db_url)
    with TestClient(app) as client:
        scheduler = client.app.state.scheduler
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert job_ids == {
            "demand_interval", "demand_charge", "retention_rollup", "expire_admin_password_file", "actor_watchdog",
            "service_status",
        }
        assert scheduler.running is True


def test_scheduler_disabled_via_env_var(app_and_db_url, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    app, db_url = app_and_db_url
    app = create_app(database_url=db_url)
    with TestClient(app) as client:
        assert getattr(client.app.state, "scheduler", None) is None


def test_watchdog_job_uses_the_app_email_sender(app_and_db_url, fake_email_sender, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    seen = []
    monkeypatch.setattr(scheduler_module, "run_actor_watchdog_job", lambda db, now, sender: seen.append(sender))
    app, _ = app_and_db_url
    with TestClient(app) as client:
        job = client.app.state.scheduler.get_job("actor_watchdog")
        job.func(*job.args)
    assert seen == [fake_email_sender]
    assert client.app.state.email_sender is fake_email_sender
