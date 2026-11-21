from __future__ import annotations

import datetime
import logging
import re

import pytest
import sqlalchemy as sa
from fastapi.routing import iter_route_contexts

from core_api import outbox
from core_api.db import make_engine, make_session_factory
from core_api.models.identity import User
from core_api.models.mail import EmailOutbox, MailProvider, OutboxStatus
from tests.conftest import MAIL_ACTOR_KEY
from tests.test_billing import auth

MAIL_URL = "/api/v1/admin/mail"
ACTOR_URL = "/api/v1/actor/mail/providers"
ACTOR_HEADERS = {"X-Actor-Key": MAIL_ACTOR_KEY, "X-Actor-Provider": "mail"}
SECRET = "dummy-secret-value-zq81"
FIELDS = [
    {"name": "host", "label": "Host", "secret": False, "required": True},
    {"name": "api_key", "label": "API key", "secret": True, "required": True},
    {"name": "note", "label": "Note", "secret": False, "required": False},
]


@pytest.fixture
def session(app_and_db_url):
    _, db_url = app_and_db_url
    db = make_session_factory(make_engine(db_url))()
    yield db
    db.close()


@pytest.fixture(autouse=True)
def _enable_core_api_loggers(app_and_db_url, monkeypatch):
    """Alembic's fileConfig in app_and_db_url disables loggers that already
    exist; the no-leak log checks mean nothing unless they are live."""
    for name, candidate in logging.root.manager.loggerDict.items():
        if name.startswith("core_api") and isinstance(candidate, logging.Logger):
            monkeypatch.setattr(candidate, "disabled", False)


@pytest.fixture
def admin(session) -> int:
    user = User(email="admin@example.com", password_hash="x", is_admin=True)
    session.add(user)
    session.commit()
    return user.id


def _declare(client, *providers: str) -> None:
    body = [{"provider": p, "label": p.upper(), "fields": FIELDS} for p in providers]
    assert client.put(ACTOR_URL, json=body, headers=ACTOR_HEADERS).status_code == 204


def _save(client, admin, provider="alpha", **settings):
    values = {"host": "mail.example.com", "api_key": SECRET} | settings
    return client.put(f"{MAIL_URL}/providers/{provider}", json={"settings": values}, headers=auth(admin))


def _stored(session, provider: str) -> dict | None:
    session.expire_all()
    return session.get(MailProvider, provider).settings


def test_list_masks_secrets_and_reports_is_set(client, admin):
    _declare(client, "alpha")
    _save(client, admin)
    got = client.get(f"{MAIL_URL}/providers", headers=auth(admin))
    assert got.status_code == 200
    assert SECRET not in got.text
    [alpha] = got.json()
    assert alpha["available"] is True and alpha["is_active"] is False
    by_name = {f["name"]: f for f in alpha["fields"]}
    assert by_name["host"]["value"] == "mail.example.com" and by_name["host"]["is_set"] is True
    assert by_name["api_key"]["value"] is None and by_name["api_key"]["is_set"] is True
    assert by_name["note"]["value"] is None and by_name["note"]["is_set"] is False


def test_one_actor_put_stamps_every_row_with_one_timestamp(client, session):
    _declare(client, "alpha", "beta", "gamma")
    stamps = {r.declared_at for r in session.scalars(sa.select(MailProvider))}
    assert len(stamps) == 1


def test_providers_from_an_older_declaration_are_unavailable(client, admin):
    _declare(client, "alpha", "beta")
    _declare(client, "beta")
    got = {p["provider"]: p for p in client.get(f"{MAIL_URL}/providers", headers=auth(admin)).json()}
    assert got["beta"]["available"] is True
    assert got["alpha"]["available"] is False


def test_save_never_returns_or_logs_the_secret(client, admin, caplog):
    _declare(client, "alpha")
    caplog.set_level(logging.DEBUG, logger="core_api.admin_users")
    responses = [
        _save(client, admin),
        client.post(f"{MAIL_URL}/providers/alpha/activate", headers=auth(admin)),
        client.get(f"{MAIL_URL}/providers", headers=auth(admin)),
        client.post(f"{MAIL_URL}/providers/alpha/deactivate", headers=auth(admin)),
        _save(client, admin, "alpha", bogus="x"),
    ]
    for got in responses:
        assert SECRET not in got.text
    assert SECRET not in caplog.text


def test_blank_or_missing_secret_keeps_the_stored_value(client, admin, session):
    _declare(client, "alpha")
    _save(client, admin)
    got = client.put(
        f"{MAIL_URL}/providers/alpha",
        json={"settings": {"api_key": "", "host": "new.example.com"}},
        headers=auth(admin),
    )
    assert got.status_code == 200
    assert _stored(session, "alpha") == {"host": "new.example.com", "api_key": SECRET}
    client.put(f"{MAIL_URL}/providers/alpha", json={"settings": {"note": "hi"}}, headers=auth(admin))
    assert _stored(session, "alpha")["api_key"] == SECRET


def test_blank_plain_value_is_removed(client, admin, session):
    _declare(client, "alpha")
    _save(client, admin, note="hello")
    assert _stored(session, "alpha")["note"] == "hello"
    _save(client, admin, note="")
    assert "note" not in _stored(session, "alpha")


def test_save_sets_updated_at(client, admin, session):
    _declare(client, "alpha")
    before = session.get(MailProvider, "alpha").updated_at
    _save(client, admin)
    session.expire_all()
    assert session.get(MailProvider, "alpha").updated_at > before


def test_unknown_field_is_422(client, admin):
    _declare(client, "alpha")
    got = _save(client, admin, bogus="x")
    assert got.status_code == 422
    assert got.json()["code"] == "unknown_field"


def test_overlong_value_is_422(client, admin):
    _declare(client, "alpha")
    assert _save(client, admin, note="x" * 501).status_code == 422
    assert _save(client, admin, note="x" * 500).status_code == 200


def test_missing_required_field_is_422_and_names_the_label(client, admin, session):
    _declare(client, "alpha")
    got = client.put(f"{MAIL_URL}/providers/alpha", json={"settings": {"host": "h"}}, headers=auth(admin))
    assert got.status_code == 422
    assert got.json()["code"] == "field_required"
    assert "API key" in got.json()["message"]
    assert _stored(session, "alpha") is None


def test_unknown_provider_is_404_on_every_provider_route(client, admin):
    for method, suffix in [("PUT", ""), ("POST", "/activate"), ("POST", "/deactivate")]:
        got = client.request(
            method,
            f"{MAIL_URL}/providers/nope{suffix}",
            json={"settings": {}} if method == "PUT" else None,
            headers=auth(admin),
        )
        assert got.status_code == 404, (method, suffix)
        assert got.json()["code"] == "not_found"


def test_activating_one_provider_deactivates_the_other(client, admin, session):
    _declare(client, "alpha", "beta")
    _save(client, admin, "alpha")
    _save(client, admin, "beta")
    assert client.post(f"{MAIL_URL}/providers/alpha/activate", headers=auth(admin)).json()["is_active"] is True
    got = client.post(f"{MAIL_URL}/providers/beta/activate", headers=auth(admin))
    assert got.status_code == 200 and got.json()["is_active"] is True
    session.expire_all()
    assert {r.provider for r in session.scalars(sa.select(MailProvider).where(MailProvider.is_active))} == {"beta"}


def test_activate_with_no_saved_settings_is_409(client, admin):
    _declare(client, "alpha")
    got = client.post(f"{MAIL_URL}/providers/alpha/activate", headers=auth(admin))
    assert got.status_code == 409
    assert got.json()["code"] == "settings_incomplete"


def test_activate_with_a_required_field_missing_is_409(client, admin, session):
    _declare(client, "alpha")
    row = session.get(MailProvider, "alpha")
    row.settings = {"host": "h"}
    session.commit()
    got = client.post(f"{MAIL_URL}/providers/alpha/activate", headers=auth(admin))
    assert got.status_code == 409
    assert got.json()["code"] == "settings_incomplete"


def test_activate_an_unavailable_provider_is_409(client, admin):
    _declare(client, "alpha", "beta")
    _save(client, admin, "alpha")
    _declare(client, "beta")
    got = client.post(f"{MAIL_URL}/providers/alpha/activate", headers=auth(admin))
    assert got.status_code == 409
    assert got.json()["code"] == "provider_unavailable"


def test_deactivate_is_idempotent(client, admin):
    _declare(client, "alpha")
    _save(client, admin)
    client.post(f"{MAIL_URL}/providers/alpha/activate", headers=auth(admin))
    for _ in range(2):
        got = client.post(f"{MAIL_URL}/providers/alpha/deactivate", headers=auth(admin))
        assert got.status_code == 200 and got.json()["is_active"] is False


def _activate_alpha(client, admin) -> None:
    _declare(client, "alpha")
    _save(client, admin)
    assert client.post(f"{MAIL_URL}/providers/alpha/activate", headers=auth(admin)).status_code == 200


def test_test_without_an_active_provider_is_409(client, admin, session):
    got = client.post(f"{MAIL_URL}/test", headers=auth(admin))
    assert got.status_code == 409
    assert got.json()["code"] == "no_active_provider"
    assert session.scalar(sa.select(sa.func.count()).select_from(EmailOutbox)) == 0


def test_test_queues_one_email_to_the_admin(client, admin, session):
    _activate_alpha(client, admin)
    got = client.post(f"{MAIL_URL}/test", headers=auth(admin))
    assert got.status_code == 202
    assert got.json() == {"queued": True}
    [row] = session.scalars(sa.select(EmailOutbox)).all()
    assert row.to_address == "admin@example.com"
    assert row.subject == "WattWise test email"
    assert SECRET not in row.body
    assert len(row.body.splitlines()) == 2


def test_the_sixth_test_in_ten_minutes_is_429(client, admin, session):
    _activate_alpha(client, admin)
    for _ in range(5):
        assert client.post(f"{MAIL_URL}/test", headers=auth(admin)).status_code == 202
    got = client.post(f"{MAIL_URL}/test", headers=auth(admin))
    assert got.status_code == 429
    assert got.json()["code"] == "too_many_tests"
    assert session.scalar(sa.select(sa.func.count()).select_from(EmailOutbox)) == 5


def test_old_tests_do_not_count_against_the_limit(client, admin, session):
    _activate_alpha(client, admin)
    old = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=11)
    for _ in range(5):
        session.add(
            EmailOutbox(to_address="admin@example.com", subject="WattWise test email", body="x", created_at=old)
        )
    session.commit()
    assert client.post(f"{MAIL_URL}/test", headers=auth(admin)).status_code == 202


def _outbox(session, status: OutboxStatus, *, finished_ago_h: float | None = None, error: str | None = None, age_h=0):
    now = datetime.datetime.now(datetime.UTC)
    session.add(
        EmailOutbox(
            to_address="a@example.com",
            subject="s",
            body="never shown",
            status=status,
            last_error=error,
            created_at=now - datetime.timedelta(hours=age_h),
            finished_at=None if finished_ago_h is None else now - datetime.timedelta(hours=finished_ago_h),
        )
    )


def test_summary_counts_and_last_error(client, admin, session):
    _outbox(session, OutboxStatus.PENDING)
    _outbox(session, OutboxStatus.SENDING)
    _outbox(session, OutboxStatus.SENT, finished_ago_h=1)
    _outbox(session, OutboxStatus.SENT, finished_ago_h=30)
    _outbox(session, OutboxStatus.FAILED, finished_ago_h=2, error="older error", age_h=5)
    _outbox(session, OutboxStatus.FAILED, finished_ago_h=40, error="ancient", age_h=10)
    _outbox(session, OutboxStatus.PENDING, error="newest error", age_h=1)
    session.commit()
    got = client.get(f"{MAIL_URL}/outbox/summary", headers=auth(admin))
    assert got.status_code == 200
    body = got.json()
    assert body["pending"] == 3
    assert body["sent_24h"] == 1
    assert body["failed_24h"] == 1
    assert body["last_error"] == "newest error"
    assert "never shown" not in got.text


def test_summary_with_an_empty_outbox(client, admin):
    got = client.get(f"{MAIL_URL}/outbox/summary", headers=auth(admin))
    assert got.json() == {"pending": 0, "sent_24h": 0, "failed_24h": 0, "last_error": None}


def test_admin_writes_log_target_none(client, admin, caplog):
    _declare(client, "alpha")
    caplog.set_level(logging.INFO, logger="core_api.admin_users")
    _save(client, admin)
    lines = [r.getMessage() for r in caplog.records if "admin action" in r.getMessage()]
    assert lines == [f"admin action: admin_user_id={admin} target=none action=save_mail_settings"]


def _mail_routes(app) -> list[tuple[str, str]]:
    routes = []
    for route in iter_route_contexts(app.routes):
        if route.path and route.methods and route.path.startswith(MAIL_URL):
            routes.extend((method, route.path) for method in sorted(route.methods))
    return routes


def test_every_route_needs_a_token_and_an_admin(client, app_and_db_url, session):
    app, _ = app_and_db_url
    plain = User(email="plain@example.com", password_hash="x")
    session.add(plain)
    session.commit()
    routes = _mail_routes(app)
    assert {"GET", "PUT", "POST"} <= {method for method, _ in routes}
    for method, path in routes:
        url = path.replace("{provider}", "alpha")
        assert not re.search(r"\{\w+\}", url)
        assert client.request(method, url).status_code == 401, (method, path)
        got = client.request(method, url, headers=auth(plain.id))
        assert got.status_code == 403, (method, path)
        assert got.json()["code"] == "admin_required"


def _queue(session, status: OutboxStatus = OutboxStatus.PENDING, to: str = "a@example.com") -> int:
    row = EmailOutbox(to_address=to, subject="Reset", body=f"link for {to}", status=status)
    session.add(row)
    session.commit()
    return row.id


def _deactivate(client, admin, provider="alpha"):
    return client.post(f"{MAIL_URL}/providers/{provider}/deactivate", headers=auth(admin))


def _reloaded(session, row_id: int) -> EmailOutbox:
    session.expire_all()
    return session.get(EmailOutbox, row_id)


def test_deactivate_sends_queued_mail_through_the_relay(client, admin, session, fake_email_sender):
    _activate_alpha(client, admin)
    first = _queue(session, OutboxStatus.PENDING, "a@example.com")
    second = _queue(session, OutboxStatus.SENDING, "b@example.com")
    got = _deactivate(client, admin)
    assert got.status_code == 200
    assert got.json()["provider"] == "alpha" and got.json()["is_active"] is False
    assert sorted(fake_email_sender.sent) == [
        ("a@example.com", "Reset", "link for a@example.com"),
        ("b@example.com", "Reset", "link for b@example.com"),
    ]
    for row_id in (first, second):
        row = _reloaded(session, row_id)
        assert row.status is OutboxStatus.SENT
        assert row.finished_at is not None and row.body == ""


def test_a_relay_failure_on_deactivate_marks_the_row_failed(client, admin, session, fake_email_sender, monkeypatch):
    _activate_alpha(client, admin)
    row_id = _queue(session)

    def refuse(to: str, subject: str, body: str) -> None:
        raise OSError("relay down")

    monkeypatch.setattr(fake_email_sender, "send", refuse)
    assert _deactivate(client, admin).status_code == 200
    row = _reloaded(session, row_id)
    assert row.status is OutboxStatus.FAILED
    assert row.last_error == "relay send failed"
    assert row.body == "" and row.finished_at is not None


def test_an_undecryptable_body_fails_only_its_row_on_deactivate(client, admin, session, fake_email_sender):
    _activate_alpha(client, admin)
    bad = _queue(session, to="bad@example.com")
    good = _queue(session, to="good@example.com")
    session.execute(sa.text("UPDATE email_outbox SET body = 'not-a-fernet-token' WHERE id = :id"), {"id": bad})
    session.commit()
    assert _deactivate(client, admin).status_code == 200
    assert [to for to, _, _ in fake_email_sender.sent] == ["good@example.com"]
    row = _reloaded(session, bad)
    assert (row.status, row.last_error, row.body) == (OutboxStatus.FAILED, "undecryptable body", "")
    assert _reloaded(session, good).status is OutboxStatus.SENT


def test_deactivate_never_logs_queued_addresses_or_bodies(client, admin, session, fake_email_sender, caplog):
    _activate_alpha(client, admin)
    _queue(session, to="private@example.com")
    caplog.set_level(logging.DEBUG)
    fake_email_sender.send = lambda to, subject, body: (_ for _ in ()).throw(OSError(f"refused {to}"))
    assert _deactivate(client, admin).status_code == 200
    assert "private@example.com" not in caplog.text


def test_deactivate_relays_at_most_the_limit_oldest_first(client, admin, session, fake_email_sender, monkeypatch):
    monkeypatch.setattr("core_api.outbox.RELAY_LIMIT", 1)
    _activate_alpha(client, admin)
    first = _queue(session, to="first@example.com")
    second = _queue(session, to="second@example.com")
    assert _deactivate(client, admin).status_code == 200
    assert [to for to, _, _ in fake_email_sender.sent] == ["first@example.com"]
    assert _reloaded(session, first).status is OutboxStatus.SENT
    assert _reloaded(session, second).status is OutboxStatus.PENDING


def test_deactivating_while_another_provider_stays_active_keeps_the_queue(client, admin, session, fake_email_sender):
    _declare(client, "alpha", "beta")
    _save(client, admin, "alpha")
    _save(client, admin, "beta")
    assert client.post(f"{MAIL_URL}/providers/beta/activate", headers=auth(admin)).status_code == 200
    row_id = _queue(session)
    assert _deactivate(client, admin, "alpha").status_code == 200
    assert fake_email_sender.sent == []
    assert _reloaded(session, row_id).status is OutboxStatus.PENDING


def test_a_relay_failure_logs_the_row_id_and_the_exception_class(client, admin, session, fake_email_sender, caplog):
    _activate_alpha(client, admin)
    row_id = _queue(session, to="private@example.com")
    caplog.set_level(logging.WARNING, logger="core_api.outbox")

    def refuse(to: str, subject: str, body: str) -> None:
        raise ConnectionRefusedError(f"refused {to}")

    fake_email_sender.send = refuse
    assert _deactivate(client, admin).status_code == 200
    messages = [record.getMessage() for record in caplog.records if record.name == "core_api.outbox"]
    assert messages == [f"Relay send failed for outbox message {row_id}: ConnectionRefusedError"]
    assert "private@example.com" not in caplog.text and "refused" not in caplog.text


def _database_error() -> sa.exc.OperationalError:
    return sa.exc.OperationalError("UPDATE email_outbox", {}, Exception("database is locked"))


def test_a_database_error_on_one_row_still_answers_with_the_provider(
    client, admin, session, fake_email_sender, monkeypatch
):
    _activate_alpha(client, admin)
    broken = _queue(session, to="broken@example.com")
    _queue(session, to="good@example.com")
    real_claim = outbox._claim_for_relay

    def claim_or_fail(db, candidate, now):
        if candidate.id == broken:
            raise _database_error()
        return real_claim(db, candidate, now)

    monkeypatch.setattr(outbox, "_claim_for_relay", claim_or_fail)
    got = _deactivate(client, admin)
    assert got.status_code == 200
    assert got.json()["provider"] == "alpha" and got.json()["is_active"] is False
    assert [to for to, _, _ in fake_email_sender.sent] == ["good@example.com"]


def test_a_failed_drain_still_answers_with_the_provider(client, admin, session, fake_email_sender, monkeypatch):
    _activate_alpha(client, admin)
    _queue(session)

    def fail(db):
        raise _database_error()

    monkeypatch.setattr(outbox, "_relay_candidates", fail)
    got = _deactivate(client, admin)
    assert got.status_code == 200
    assert got.json()["provider"] == "alpha" and got.json()["is_active"] is False
    assert fake_email_sender.sent == []
