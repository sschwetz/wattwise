"""One poll: find the active provider, claim queued mail, send it and report
each result. Core API retries a `failed` message with backoff, so a failure
here is reported once and not retried locally.

Nothing logged or reported carries a settings value, an address, a subject or
a body. Error text is a fixed prefix plus a status or exception class name,
and any settings value that still appears in it is replaced."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Protocol

import httpx

from mail_actor.core_api_client import ActiveProvider, CoreApiError
from mail_actor.providers.base import MailProvider, OutboxMessage, SendError

_LOG = logging.getLogger(__name__)

BATCH_SIZE = 10
ACTIVE_TTL = 30.0
UNKNOWN_WARNING_INTERVAL = 60.0
AUTH_STATUSES = frozenset({401, 403})
REDACTED = "[redacted]"
NO_PROVIDER_ERROR = "no usable mail provider"


class CoreApi(Protocol):
    def get_active(self) -> ActiveProvider | None: ...

    def claim(self, limit: int) -> list[OutboxMessage]: ...

    def report(self, id: int, outcome: str, error: str | None) -> None: ...


class WorkerState:
    """Caches the active provider for ACTIVE_TTL seconds and limits the
    unknown-provider warning to one per UNKNOWN_WARNING_INTERVAL."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._active: ActiveProvider | None = None
        self._fetched_at: float | None = None
        self._warned_at: float | None = None

    def active(self, client: CoreApi) -> ActiveProvider | None:
        now = self._clock()
        if self._fetched_at is None or now - self._fetched_at >= ACTIVE_TTL:
            self._active = client.get_active()
            self._fetched_at = now
        return self._active

    def invalidate(self) -> None:
        self._fetched_at = None

    def warn_unknown(self) -> None:
        now = self._clock()
        if self._warned_at is None or now - self._warned_at >= UNKNOWN_WARNING_INTERVAL:
            self._warned_at = now
            _LOG.warning("The active mail provider is not one this actor implements; no mail is sent")


_DEFAULT_STATE = WorkerState()


def _resolve(
    client: CoreApi, providers: Sequence[MailProvider], state: WorkerState
) -> tuple[MailProvider, dict[str, str]] | None:
    active = state.active(client)
    if active is None:
        return None
    provider = next((p for p in providers if p.KEY == active.provider), None)
    if provider is None:
        state.warn_unknown()
        return None
    return provider, active.settings


def redact(text: str, settings: dict[str, str]) -> str:
    for value in sorted(settings.values(), key=len, reverse=True):
        if value:
            text = text.replace(value, REDACTED)
    return text


def _send(
    provider: MailProvider, settings: dict[str, str], message: OutboxMessage, http: httpx.Client
) -> tuple[str | None, bool]:
    """Send one message. Returns the unredacted error text (None on success)
    and whether the provider refused the credentials."""
    try:
        provider.send(settings, message, http)
    except SendError as exc:
        return f"{provider.KEY} send failed: {exc.reason}", exc.status in AUTH_STATUSES
    except Exception as exc:
        return f"{provider.KEY} send failed: {type(exc).__name__}", False
    return None, False


def _report(client: CoreApi, message: OutboxMessage, error: str | None) -> None:
    try:
        client.report(message.id, "failed" if error else "sent", error)
    except CoreApiError:
        _LOG.warning("Could not report the result of outbox message %d", message.id)


def _handle(
    client: CoreApi, providers: Sequence[MailProvider], http: httpx.Client, state: WorkerState, message: OutboxMessage
) -> None:
    """A provider that went away mid-batch, or a failed refetch, fails the
    message so it goes back to the queue now rather than after the claim expires."""
    try:
        resolved = _resolve(client, providers, state)
    except CoreApiError:
        resolved = None
    if resolved is None:
        _report(client, message, NO_PROVIDER_ERROR)
        return
    provider, settings = resolved
    error, refused = _send(provider, settings, message, http)
    if refused:
        state.invalidate()
    if error is not None:
        error = redact(error, settings)
        _LOG.warning("Outbox message %d failed: %s", message.id, error)
    _report(client, message, error)


def run_once(
    client: CoreApi, providers: Sequence[MailProvider], http: httpx.Client, state: WorkerState = _DEFAULT_STATE
) -> int:
    """Claim and send up to BATCH_SIZE messages. Returns how many were handled."""
    if _resolve(client, providers, state) is None:
        return 0
    messages = client.claim(BATCH_SIZE)
    for message in messages:
        _handle(client, providers, http, state, message)
    return len(messages)
