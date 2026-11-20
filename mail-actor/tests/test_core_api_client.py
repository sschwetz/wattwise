import json
from collections.abc import Callable

import httpx
import pytest

from mail_actor.core_api_client import ActiveProvider, CoreApiClient, CoreApiError
from mail_actor.providers import PROVIDERS
from mail_actor.providers.base import OutboxMessage

BASE = "http://core/"
PREFIX = "http://core/api/v1/actor/mail"
KEY = "actor-secret"


class Recorder:
    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def _client(respond: Callable[[httpx.Request], httpx.Response]) -> tuple[CoreApiClient, Recorder]:
    recorder = Recorder(respond)
    return CoreApiClient(BASE, KEY, httpx.Client(transport=httpx.MockTransport(recorder))), recorder


def _status(code: int, body: object = None) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(code, json=body) if body is not None else httpx.Response(code)


def test_every_request_carries_the_actor_headers():
    client, recorder = _client(_status(204))
    client.get_active()
    assert recorder.last.headers["X-Actor-Key"] == KEY
    assert recorder.last.headers["X-Actor-Provider"] == "mail"


def test_declare_providers_puts_the_field_shape_core_api_expects():
    client, recorder = _client(_status(204))
    client.declare_providers(PROVIDERS)
    request = recorder.last
    assert (request.method, str(request.url)) == ("PUT", f"{PREFIX}/providers")
    assert json.loads(request.content) == [
        {
            "provider": "smtp2go",
            "label": "smtp2go",
            "fields": [
                {"name": "api_key", "label": "API key", "secret": True, "required": True},
                {"name": "from_address", "label": "From address", "secret": False, "required": True},
            ],
        }
    ]


def test_get_active_returns_the_provider_and_settings():
    client, recorder = _client(_status(200, {"provider": "smtp2go", "settings": {"api_key": "k", "from_address": "a"}}))
    assert client.get_active() == ActiveProvider(provider="smtp2go", settings={"api_key": "k", "from_address": "a"})
    assert (recorder.last.method, str(recorder.last.url)) == ("GET", f"{PREFIX}/active")


def test_get_active_204_is_none():
    client, _ = _client(_status(204))
    assert client.get_active() is None


def test_active_provider_repr_hides_settings():
    assert "hidden" not in repr(ActiveProvider(provider="smtp2go", settings={"api_key": "hidden"}))


def test_claim_posts_the_limit_and_parses_messages():
    body = [{"id": 3, "to_address": "a@example.com", "subject": "S", "body": "B"}]
    client, recorder = _client(_status(200, body))
    assert client.claim(10) == [OutboxMessage(id=3, to_address="a@example.com", subject="S", body="B")]
    assert (recorder.last.method, str(recorder.last.url)) == ("POST", f"{PREFIX}/outbox/claim?limit=10")


@pytest.mark.parametrize("error", [None, "smtp2go send failed: HTTP 503"])
def test_report_patches_the_outcome_and_error(error):
    client, recorder = _client(_status(204))
    client.report(3, "failed", error)
    assert (recorder.last.method, str(recorder.last.url)) == ("PATCH", f"{PREFIX}/outbox/3")
    assert json.loads(recorder.last.content) == {"outcome": "failed", "error": error}


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.declare_providers(PROVIDERS),
        lambda c: c.get_active(),
        lambda c: c.claim(10),
        lambda c: c.report(1, "sent", None),
    ],
)
def test_non_success_raises_core_api_error_without_the_body(call):
    client, _ = _client(_status(401, {"code": "invalid_actor_key", "message": "echo actor-secret"}))
    with pytest.raises(CoreApiError) as caught:
        call(client)
    assert "401" in str(caught.value)
    assert "echo" not in str(caught.value)


def test_transport_failure_raises_core_api_error():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client, _ = _client(refuse)
    with pytest.raises(CoreApiError, match="ConnectError"):
        client.claim(1)


@pytest.mark.parametrize(
    "body",
    [
        {"provider": "smtp2go"},
        {"provider": 1, "settings": {}},
        {"provider": "smtp2go", "settings": {"api_key": 5}},
        {"provider": "smtp2go", "settings": ["x"]},
        [],
    ],
)
def test_get_active_unexpected_shape_raises(body):
    client, _ = _client(_status(200, body))
    with pytest.raises(CoreApiError, match="Unexpected"):
        client.get_active()


@pytest.mark.parametrize(
    "body",
    [
        {"id": 1},
        [{"id": 1, "to_address": "a", "subject": "s"}],
        [{"id": "1", "to_address": "a", "subject": "s", "body": "b"}],
        [{"id": True, "to_address": "a", "subject": "s", "body": "b"}],
        [None],
    ],
)
def test_claim_unexpected_shape_raises(body):
    client, _ = _client(_status(200, body))
    with pytest.raises(CoreApiError, match="Unexpected"):
        client.claim(1)


def test_non_json_body_raises():
    client, _ = _client(lambda request: httpx.Response(200, text="<html>"))
    with pytest.raises(CoreApiError, match="Unexpected"):
        client.claim(1)
