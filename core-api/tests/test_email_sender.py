from __future__ import annotations

import pytest
from sqlalchemy import select

from core_api import email_sender
from core_api.bootstrap import run_migrations
from core_api.db import make_engine, make_session_factory
from core_api.email_sender import FakeEmailSender, OutboxEmailSender, try_send_email
from core_api.main import create_app
from core_api.models.mail import EmailOutbox, MailProvider, OutboxStatus


@pytest.fixture()
def session_factory(tmp_path):
    url = f"sqlite:///{tmp_path}/outbox_sender.db"
    run_migrations(url)
    engine = make_engine(url)
    yield make_session_factory(engine)
    engine.dispose()


def _add_provider(session_factory, active: bool) -> None:
    with session_factory() as db:
        db.add(MailProvider(provider="smtp2go", label="SMTP2GO", fields=[], settings={}, is_active=active))
        db.commit()


def _outbox(session_factory) -> list[tuple[str, str, str, OutboxStatus]]:
    with session_factory() as db:
        rows = db.scalars(select(EmailOutbox).order_by(EmailOutbox.id))
        return [(r.to_address, r.subject, r.body, r.status) for r in rows]


def test_outbox_sender_queues_when_a_provider_is_active(session_factory):
    _add_provider(session_factory, active=True)
    fallback = FakeEmailSender()
    OutboxEmailSender(session_factory, fallback).send(to="a@example.com", subject="Subject", body="Body")
    assert _outbox(session_factory) == [("a@example.com", "Subject", "Body", OutboxStatus.PENDING)]
    assert fallback.sent == []


@pytest.mark.parametrize("declared", [False, True])
def test_outbox_sender_uses_the_fallback_when_no_provider_is_active(session_factory, declared):
    if declared:
        _add_provider(session_factory, active=False)
    fallback = FakeEmailSender()
    OutboxEmailSender(session_factory, fallback).send(to="a@example.com", subject="Subject", body="Body")
    assert fallback.sent == [("a@example.com", "Subject", "Body")]
    assert _outbox(session_factory) == []


def test_outbox_sender_failure_reaches_try_send_email(session_factory, monkeypatch):
    _add_provider(session_factory, active=True)
    logged = []
    monkeypatch.setattr(email_sender.logger, "exception", lambda *args: logged.append(args))

    def broken_enqueue(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(email_sender, "enqueue_email", broken_enqueue)
    fallback = FakeEmailSender()
    sender = OutboxEmailSender(session_factory, fallback)
    assert try_send_email(sender, "a@example.com", "Subject", "Body", "password-reset") is False
    assert logged == [("Failed to send %s email", "password-reset")]
    assert fallback.sent == []
    assert _outbox(session_factory) == []


def test_create_app_wraps_the_given_sender(tmp_path, fake_email_sender):
    app = create_app(database_url=f"sqlite:///{tmp_path}/wrap.db", email_sender=fake_email_sender)
    assert isinstance(app.state.email_sender, OutboxEmailSender)
    assert app.state.email_sender.fallback is fake_email_sender


def test_smtp_sender_passes_a_timeout(monkeypatch):
    opened: list[tuple[str, int, float]] = []

    class RecordingSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            opened.append((host, port, timeout))

        def __enter__(self) -> RecordingSMTP:
            return self

        def __exit__(self, *exc: object) -> None:
            pass

        def send_message(self, msg: object) -> None:
            pass

    monkeypatch.setattr(email_sender.smtplib, "SMTP", RecordingSMTP)
    email_sender.SmtpEmailSender().send(to="a@example.com", subject="Subject", body="Body")
    assert email_sender.SMTP_TIMEOUT_SECONDS == 20
    assert [timeout for _, _, timeout in opened] == [20]


def test_fake_email_sender_records_sends():
    sender = FakeEmailSender()
    sender.send(to="a@example.com", subject="Subject", body="Body")
    assert sender.sent == [("a@example.com", "Subject", "Body")]


class _RaisingSender:
    def send(self, to: str, subject: str, body: str) -> None:
        raise RuntimeError("relay down")


def test_try_send_email_delivers_through_the_sender():
    sender = FakeEmailSender()
    assert try_send_email(sender, "a@example.com", "Subject", "Body", "test") is True
    assert sender.sent == [("a@example.com", "Subject", "Body")]


def test_try_send_email_logs_and_swallows_delivery_failure(monkeypatch):
    # Patch the logger rather than use caplog: other tests run Alembic's
    # fileConfig, which disables propagation for existing loggers.
    logged = []
    monkeypatch.setattr(email_sender.logger, "exception", lambda *args: logged.append(args))
    assert try_send_email(_RaisingSender(), "a@example.com", "Subject", "Body", "password-reset") is False
    assert logged == [("Failed to send %s email", "password-reset")]
