from __future__ import annotations

from core_api import email_sender
from core_api.email_sender import FakeEmailSender, try_send_email


def test_fake_email_sender_records_sends():
    sender = FakeEmailSender()
    sender.send(to="a@example.com", subject="Subject", body="Body")
    assert sender.sent == [("a@example.com", "Subject", "Body")]


class _RaisingSender:
    def send(self, to: str, subject: str, body: str) -> None:
        raise RuntimeError("relay down")


def test_try_send_email_delivers_through_the_sender():
    sender = FakeEmailSender()
    try_send_email(sender, "a@example.com", "Subject", "Body", "test")
    assert sender.sent == [("a@example.com", "Subject", "Body")]


def test_try_send_email_logs_and_swallows_delivery_failure(monkeypatch):
    # Patch the logger rather than use caplog: other tests run Alembic's
    # fileConfig, which disables propagation for existing loggers.
    logged = []
    monkeypatch.setattr(email_sender.logger, "exception", lambda *args: logged.append(args))
    try_send_email(_RaisingSender(), "a@example.com", "Subject", "Body", "password-reset")
    assert logged == [("Failed to send %s email", "password-reset")]
