"""Client for Core API's mail actor routes under /api/v1/actor/mail."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field

import httpx

from mail_actor.providers.base import MailProvider, OutboxMessage

PROVIDER = "mail"
_TIMEOUT = 29


class CoreApiError(Exception):
    """Any failure talking to Core API: a non-success response, a transport
    failure or a body of an unexpected shape. The message never carries a
    request or response body, since both can hold provider settings or mail."""


@dataclass(frozen=True)
class ActiveProvider:
    provider: str
    settings: dict[str, str] = field(repr=False)


def _declaration(provider: MailProvider) -> dict[str, object]:
    return {"provider": provider.KEY, "label": provider.LABEL, "fields": [asdict(f) for f in provider.FIELDS]}


def _active(body: object) -> ActiveProvider:
    if not isinstance(body, dict):
        raise TypeError
    provider, settings = body["provider"], body["settings"]
    if not isinstance(provider, str) or not isinstance(settings, dict):
        raise TypeError
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in settings.items()):
        raise TypeError
    return ActiveProvider(provider=provider, settings=settings)


def _message(item: object) -> OutboxMessage:
    if not isinstance(item, dict):
        raise TypeError
    id_, to, subject, body = item["id"], item["to_address"], item["subject"], item["body"]
    if type(id_) is not int or not all(isinstance(v, str) for v in (to, subject, body)):
        raise TypeError
    return OutboxMessage(id=id_, to_address=to, subject=subject, body=body)


def _messages(body: object) -> list[OutboxMessage]:
    if not isinstance(body, list):
        raise TypeError
    return [_message(item) for item in body]


def _parsed[T](what: str, body: object, build: Callable[[object], T]) -> T:
    try:
        return build(body)
    except (KeyError, TypeError, ValueError) as exc:
        raise CoreApiError(f"Unexpected {what} body") from exc


class CoreApiClient:
    def __init__(self, base_url: str, actor_key: str, http: httpx.Client | None = None) -> None:
        self._base = f"{base_url.rstrip('/')}/api/v1/actor/mail"
        self._headers = {"X-Actor-Key": actor_key, "X-Actor-Provider": PROVIDER}
        self._http = http or httpx.Client(timeout=_TIMEOUT)

    def _request(
        self, method: str, path: str, what: str, *, json: object = None, params: dict[str, int] | None = None
    ) -> object:
        """The decoded JSON body, or None for a 204."""
        try:
            response = self._http.request(
                method, f"{self._base}{path}", headers=self._headers, json=json, params=params
            )
        except httpx.HTTPError as exc:
            raise CoreApiError(f"Failed to reach Core API: {type(exc).__name__}") from None
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            raise CoreApiError(f"Failed to {what}: {response.status_code}")
        try:
            return response.json()
        except ValueError:
            raise CoreApiError(f"Unexpected {what} body") from None

    def _no_content(self, method: str, path: str, what: str, json: object) -> None:
        if self._request(method, path, what, json=json) is not None:
            raise CoreApiError(f"Unexpected {what} body")

    def declare_providers(self, providers: Sequence[MailProvider]) -> None:
        body = [_declaration(p) for p in providers]
        self._no_content("PUT", "/providers", "declare providers", body)

    def get_active(self) -> ActiveProvider | None:
        """None when no provider is active (a 204)."""
        body = self._request("GET", "/active", "fetch active provider")
        return None if body is None else _parsed("active provider", body, _active)

    def claim(self, limit: int) -> list[OutboxMessage]:
        body = self._request("POST", "/outbox/claim", "claim outbox", params={"limit": limit})
        return _parsed("outbox claim", body, _messages)

    def report(self, id: int, outcome: str, error: str | None) -> None:
        self._no_content("PATCH", f"/outbox/{id}", "report outbox result", {"outcome": outcome, "error": error})
