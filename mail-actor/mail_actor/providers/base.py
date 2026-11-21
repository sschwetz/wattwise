"""What every mail provider module exposes, and the types it works with."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx


@dataclass(frozen=True)
class OutboxMessage:
    """A claimed message. The address, subject and body stay out of repr so a
    logged or raised object does not carry them."""

    id: int
    to_address: str = field(repr=False)
    subject: str = field(repr=False)
    body: str = field(repr=False)


@dataclass(frozen=True)
class Field:
    """One setting the admin fills in. Core API stores a `secret` field
    encrypted and never returns its value to the webapp."""

    name: str
    label: str
    secret: bool
    required: bool


class SendError(Exception):
    """A failed send. `reason` is fixed text built from an HTTP status or an
    exception class name, never from a response body or a settings value.
    `status` is the provider's HTTP status when there was a response; 401 and
    403 make the worker fetch the active settings again."""

    def __init__(self, reason: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.status = status


class MailProvider(Protocol):
    KEY: str
    LABEL: str
    FIELDS: list[Field]

    def send(self, settings: dict[str, str], message: OutboxMessage, http: httpx.Client) -> str | None:
        """Send one message or raise SendError. Returns the provider's own id
        for the message when it gives one, so a log line can be matched to the
        provider's activity page."""
        ...
