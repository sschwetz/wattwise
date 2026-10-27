from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from core_api.config import get_settings

logger = logging.getLogger(__name__)


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
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as client:
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


def try_send_email(email_sender: EmailSender, to: str, subject: str, body: str, description: str) -> None:
    """Send an email, logging instead of raising on delivery failure.

    Callers use this when a relay outage must not change the HTTP response.
    `description` names the email in the log line."""
    try:
        email_sender.send(to=to, subject=subject, body=body)
    except Exception:
        logger.exception("Failed to send %s email", description)
