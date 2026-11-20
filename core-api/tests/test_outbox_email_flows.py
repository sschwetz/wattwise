"""Each flow that sends email, run through the real app on a file-backed
migrated database. With an active provider the mail lands in the outbox and
the fallback sender gets nothing; with none it reaches the fallback sender.

The outbox sender commits through its own session, so a caller still holding
an uncommitted write when it sends would block that commit on SQLite's
single write lock. These tests would then see no outbox row."""

from __future__ import annotations

import datetime

import pyotp
import pytest
from sqlalchemy import select

from core_api.db import make_engine, make_session_factory
from core_api.models import ActorSchedule, RunOutcome
from core_api.models.identity import Site, User
from core_api.models.mail import EmailOutbox, MailProvider, OutboxStatus
from core_api.scheduler import build_scheduler
from tests.conftest import ACTOR_KEY, UTC
from tests.test_account import make_enrolled_user
from tests.test_actor_failures import make_instance, make_run, make_schedule
from tests.test_admin_users import USERS_URL, _user
from tests.test_invites import invite, make_site
from tests.test_sites import auth, make_user

ACTOR_HEADERS = {"X-Actor-Key": ACTOR_KEY, "X-Actor-Provider": "actron_neo"}


def _session(db_url: str):
    return make_session_factory(make_engine(db_url))()


def _set_provider(db_url: str, active: bool) -> None:
    with _session(db_url) as db:
        db.add(MailProvider(provider="smtp2go", label="SMTP2GO", fields=[], settings={}, is_active=active))
        db.commit()


def _outbox(db_url: str) -> list[tuple[str, OutboxStatus]]:
    with _session(db_url) as db:
        return [(r.to_address, r.status) for r in db.scalars(select(EmailOutbox).order_by(EmailOutbox.id))]


def _notifying_schedule(db) -> ActorSchedule:
    """An owner, a site, an actor instance and a schedule with notify_email on."""
    owner = User(email="owner@example.com", password_hash="x")
    db.add(owner)
    db.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="Australia/Sydney")
    db.add(site)
    db.commit()
    return make_schedule(db, make_instance(db, site), notify=True)


def password_reset(client, db_url, tmp_path) -> str:
    make_user(db_url, "reset@example.com")
    response = client.post("/api/v1/auth/password-reset/request", json={"email": "reset@example.com"})
    assert response.status_code == 200, response.text
    return "reset@example.com"


def admin_password_reset(client, db_url, tmp_path) -> str:
    admin = _user(db_url, "admin@example.com", is_admin=True)
    target = _user(db_url, "target@example.com", email_confirmed=True)
    response = client.post(f"{USERS_URL}/{target}/password-reset", headers=auth(admin))
    assert response.status_code == 204, response.text
    return "target@example.com"


def registration(client, db_url, tmp_path) -> str:
    response = client.post("/api/v1/auth/register", json={"email": "new-user@example.com"})
    assert response.status_code == 200, response.text
    return "new-user@example.com"


def site_invite(client, db_url, tmp_path) -> str:
    owner_id = make_user(db_url, "inviter@example.com")
    response = invite(client, owner_id, make_site(client, owner_id), "invitee@example.com")
    assert response.status_code == 201, response.text
    return "invitee@example.com"


def email_change(client, db_url, tmp_path) -> str:
    user_id, secret = make_enrolled_user(db_url)
    body = {"new_email": "changed@example.com", "current_password": "correct-password",
            "totp_code": pyotp.TOTP(secret).now()}
    response = client.post("/api/v1/users/me/email/change/request", json=body, headers=auth(user_id))
    assert response.status_code == 200, response.text
    return "changed@example.com"


def actor_run_failure(client, db_url, tmp_path) -> str:
    with _session(db_url) as db:
        schedule = _notifying_schedule(db)
        instance_id, schedule_id = schedule.instance_id, schedule.id
    url = f"/api/v1/actor/instances/{instance_id}/runs"
    body = {"schedule_id": schedule_id, "kind": "action", "trigger_at": datetime.datetime.now(UTC).isoformat()}
    run = client.post(url, json=body, headers=ACTOR_HEADERS)
    assert run.status_code == 201, run.text
    failed = client.patch(f"{url}/{run.json()['id']}", json={"outcome": "failed", "message": "x"}, headers=ACTOR_HEADERS)
    assert failed.status_code == 200, failed.text
    return "owner@example.com"


def actor_watchdog(client, db_url, tmp_path) -> str:
    with _session(db_url) as db:
        make_run(db, _notifying_schedule(db), datetime.datetime.now(UTC) - datetime.timedelta(hours=3), RunOutcome.FAILED)
    app = client.app
    scheduler = build_scheduler(app.state.session_factory, tmp_path / "password.txt", app.state.email_sender)
    job = scheduler.get_job("actor_watchdog")
    job.func(*job.args)
    return "owner@example.com"


FLOWS = [password_reset, admin_password_reset, registration, site_invite, email_change, actor_run_failure, actor_watchdog]


@pytest.mark.parametrize("flow", FLOWS, ids=lambda f: f.__name__)
def test_flow_queues_one_pending_message_when_a_provider_is_active(
    flow, client, app_and_db_url, fake_email_sender, tmp_path
):
    _, db_url = app_and_db_url
    _set_provider(db_url, active=True)
    to = flow(client, db_url, tmp_path)
    assert _outbox(db_url) == [(to, OutboxStatus.PENDING)]
    assert fake_email_sender.sent == []


@pytest.mark.parametrize("flow", FLOWS, ids=lambda f: f.__name__)
def test_flow_sends_through_the_fallback_when_no_provider_is_active(
    flow, client, app_and_db_url, fake_email_sender, tmp_path
):
    _, db_url = app_and_db_url
    _set_provider(db_url, active=False)
    to = flow(client, db_url, tmp_path)
    assert [sent_to for sent_to, _subject, _body in fake_email_sender.sent] == [to]
    assert _outbox(db_url) == []
