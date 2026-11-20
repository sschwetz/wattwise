"""An in-memory stand-in for CoreApiClient.

It follows the rules in core_api/routers/actor_mail.py: `claim` hands out up
to `limit` queued messages and moves them to `sending`, and `report` accepts
only a message that is `sending`, as finish_outbox does (otherwise 409).
"""

from __future__ import annotations

from collections.abc import Sequence

from mail_actor.core_api_client import ActiveProvider, CoreApiError
from mail_actor.providers.base import MailProvider, OutboxMessage


class FakeCore:
    def __init__(self) -> None:
        self.active: ActiveProvider | None = None
        self.queue: list[OutboxMessage] = []
        self.sending: set[int] = set()
        self.declared: list[list[MailProvider]] = []  # providers passed to each declare_providers call
        self.active_calls = 0
        self.claims: list[int] = []  # the limit of each claim call
        self.reports: list[tuple[int, str, str | None]] = []
        self.errors: dict[str, list[Exception]] = {}  # method name -> errors to raise, one per call

    def add(self, *messages: OutboxMessage) -> None:
        self.queue.extend(messages)

    def _enter(self, name: str) -> None:
        pending = self.errors.get(name)
        if pending:
            raise pending.pop(0)

    def declare_providers(self, providers: Sequence[MailProvider]) -> None:
        self._enter("declare_providers")
        self.declared.append(list(providers))

    def get_active(self) -> ActiveProvider | None:
        self._enter("get_active")
        self.active_calls += 1
        return self.active

    def claim(self, limit: int) -> list[OutboxMessage]:
        self._enter("claim")
        self.claims.append(limit)
        taken, self.queue = self.queue[:limit], self.queue[limit:]
        self.sending.update(m.id for m in taken)
        return taken

    def report(self, id: int, outcome: str, error: str | None) -> None:
        self._enter("report")
        if id not in self.sending:
            raise CoreApiError("Failed to report outbox result: 409")
        self.sending.discard(id)
        self.reports.append((id, outcome, error))
