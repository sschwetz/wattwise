from __future__ import annotations

from fastapi.testclient import TestClient

from core_api.main import create_app


def test_scheduler_starts_and_registers_all_three_jobs(app_and_db_url, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    app, db_url = app_and_db_url
    app = create_app(database_url=db_url)
    with TestClient(app) as client:
        scheduler = client.app.state.scheduler
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert job_ids == {"demand_interval", "demand_charge", "retention_rollup"}
        assert scheduler.running is True


def test_scheduler_disabled_via_env_var(app_and_db_url, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    app, db_url = app_and_db_url
    app = create_app(database_url=db_url)
    with TestClient(app) as client:
        assert getattr(client.app.state, "scheduler", None) is None
