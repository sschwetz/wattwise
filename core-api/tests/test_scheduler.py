from __future__ import annotations

import datetime

from apscheduler.triggers.cron import CronTrigger
from fastapi.testclient import TestClient
from sqlalchemy import select

from core_api import scheduler as scheduler_module
from core_api.db import make_engine, make_session_factory
from core_api.email_sender import OutboxEmailSender
from core_api.main import create_app
from core_api.models.mail import EmailOutbox, OutboxStatus


def test_scheduler_starts_and_registers_all_jobs(app_and_db_url, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    app, db_url = app_and_db_url
    app = create_app(database_url=db_url)
    with TestClient(app) as client:
        scheduler = client.app.state.scheduler
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert job_ids == {
            "demand_interval", "demand_charge", "retention_rollup", "expire_admin_password_file", "actor_watchdog",
            "service_status", "mail_outbox_cleanup",
        }
        assert scheduler.running is True
        assert scheduler.get_job("service_status").next_run_time is not None


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
    [sender] = seen
    assert sender is client.app.state.email_sender
    assert isinstance(sender, OutboxEmailSender)
    assert sender.fallback is fake_email_sender


def _finished_row(subject: str, finished_at: datetime.datetime) -> EmailOutbox:
    return EmailOutbox(to_address="a@example.com", subject=subject, body="", status=OutboxStatus.SENT,
                       attempts=1, finished_at=finished_at)


def test_mail_outbox_cleanup_runs_daily_and_purges_old_finished_rows(app_and_db_url, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    app, db_url = app_and_db_url
    now = datetime.datetime.now(datetime.UTC)
    session_factory = make_session_factory(make_engine(db_url))
    with session_factory() as db:
        db.add(_finished_row("old", now - datetime.timedelta(days=31)))
        db.add(_finished_row("recent", now - datetime.timedelta(days=29)))
        db.commit()
    with TestClient(app) as client:
        job = client.app.state.scheduler.get_job("mail_outbox_cleanup")
        assert isinstance(job.trigger, CronTrigger)
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert (fields["hour"], fields["minute"]) == ("0", "30")
        job.func(*job.args)
    with session_factory() as db:
        assert list(db.scalars(select(EmailOutbox.subject))) == ["recent"]
