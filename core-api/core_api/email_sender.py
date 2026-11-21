from __future__ import annotations

import logging
import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from core_api.config import get_settings
from core_api.outbox import enqueue_email, has_active_provider

logger = logging.getLogger(__name__)

# The Deactivate drain sends up to 200 messages inside one request, so a
# relay that stops answering must not hold each one forever.
SMTP_TIMEOUT_SECONDS = 20


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class SmtpEmailSender:
    """Sends via the configured SMTP relay (the mail-relay Postfix container
    by default — see docs/superpowers/specs/2026-10-01-frontend-webapp-design.md).
    No TLS/auth handshake unless smtp_user/smtp_password are set, since the
    default target is an internal, trusted-network relay."""

    def send(self, to: str, subject: str, body: str) -> None:
        settings = get_settings()
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as client:
            if settings.smtp_user and settings.smtp_password:
                client.starttls()
                client.login(settings.smtp_user, settings.smtp_password)
            client.send_message(msg)


class FakeEmailSender:
    """Test double — records sends instead of opening a real SMTP connection."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


class OutboxEmailSender:
    """Queues mail in the outbox for the mail actor while a mail provider is
    active, and sends through `fallback` otherwise.

    It writes through its own short session, so a request handler and a
    scheduler job use it the same way. SQLite allows one writer at a time:
    a caller must commit its own writes before it sends, or this commit
    waits on the caller's write lock."""

    def __init__(self, session_factory: sessionmaker[Session], fallback: EmailSender) -> None:
        self._session_factory = session_factory
        self.fallback = fallback

    def send(self, to: str, subject: str, body: str) -> None:
        """When the provider check or the outbox write fails, the email goes
        through `fallback` instead. The log line names only the exception
        class: a database error's text can hold the address and subject."""
        if not self._enqueued(to, subject, body):
            self.fallback.send(to=to, subject=subject, body=body)

    def _enqueued(self, to: str, subject: str, body: str) -> bool:
        """True when the message is in the outbox. False when no provider is
        active or the outbox cannot be used; nothing here raises."""
        db = self._session_factory()
        try:
            if not has_active_provider(db):
                return False
            enqueue_email(db, to, subject, body)
            db.commit()
            return True
        except Exception as exc:
            logger.warning("Outbox unavailable, sending through the relay: %s", type(exc).__name__)
            _quietly(db.rollback)
            return False
        finally:
            _quietly(db.close)


def _quietly(step: Callable[[], object]) -> None:
    """Run a session cleanup step whose failure must not stop the fallback send."""
    try:
        step()
    except Exception as exc:
        logger.warning("Outbox session cleanup failed: %s", type(exc).__name__)


def try_send_email(email_sender: EmailSender, to: str, subject: str, body: str, description: str) -> bool:
    """Send an email, logging instead of raising on delivery failure.

    Callers use this when a relay outage must not change the HTTP response.
    `description` names the email in the log line. Returns whether it was sent."""
    try:
        email_sender.send(to=to, subject=subject, body=body)
    except Exception:
        logger.exception("Failed to send %s email", description)
        return False
    return True
