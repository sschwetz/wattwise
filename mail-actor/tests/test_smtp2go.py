import json
from collections.abc import Callable

import httpx
import pytest

from mail_actor.providers import PROVIDERS, smtp2go
from mail_actor.providers.base import Field, OutboxMessage, SendError

SETTINGS = {"api_key": "api-SECRET123", "from_address": "WattWise <mail@example.com>"}
MESSAGE = OutboxMessage(id=4, to_address="user@example.com", subject="Hello", body="Plain text body")
OK_BODY = {"request_id": "r1", "data": {"succeeded": 1, "failed": 0, "failures": [], "email_id": "e1"}}


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _responding(status: int, body: object = None) -> httpx.Client:
    return _client(lambda request: httpx.Response(status, json=body))


def _send_error(http: httpx.Client) -> SendError:
    with pytest.raises(SendError) as caught:
        smtp2go.send(SETTINGS, MESSAGE, http)
    return caught.value


def test_registry_lists_smtp2go_with_its_fields():
    assert PROVIDERS == [smtp2go]
    assert (smtp2go.KEY, smtp2go.LABEL) == ("smtp2go", "smtp2go")
    assert smtp2go.FIELDS == [
        Field(name="api_key", label="API key", secret=True, required=True),
        Field(name="from_address", label="From address", secret=False, required=True),
    ]


def test_send_posts_the_documented_request():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_BODY)

    smtp2go.send(SETTINGS, MESSAGE, _client(handler))
    [request] = seen
    assert request.method == "POST"
    assert str(request.url) == "https://api.smtp2go.com/v3/email/send"
    assert request.headers["X-Smtp2go-Api-Key"] == "api-SECRET123"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "sender": "WattWise <mail@example.com>",
        "to": ["user@example.com"],
        "subject": "Hello",
        "text_body": "Plain text body",
    }


def test_send_uses_a_20_second_timeout():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_BODY)

    smtp2go.send(SETTINGS, MESSAGE, _client(handler))
    assert seen[0].extensions["timeout"] == {"connect": 20, "read": 20, "write": 20, "pool": 20}


@pytest.mark.parametrize("status", [500, 502, 503, 429])
def test_server_errors_and_rate_limits_are_retryable(status):
    error = _send_error(_responding(status, {"data": {"error": "busy"}}))
    assert error.retryable is True
    assert error.status == status
    assert error.reason == f"HTTP {status}"


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")])
def test_timeouts_and_transport_errors_are_retryable(exc):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    error = _send_error(_client(handler))
    assert error.retryable is True
    assert error.status is None
    assert error.reason == type(exc).__name__


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_client_errors_are_not_retryable(status):
    error = _send_error(_responding(status, {"data": {"error": "no"}}))
    assert error.retryable is False
    assert error.status == status


def test_zero_succeeded_is_not_retryable():
    body = {"request_id": "r1", "data": {"succeeded": 0, "failed": 1, "failures": ["user@example.com"]}}
    error = _send_error(_responding(200, body))
    assert error.retryable is False
    assert error.reason == "not accepted"


@pytest.mark.parametrize("body", [None, [], {"data": None}, {"data": {}}, {"data": {"succeeded": "1"}}])
def test_unreadable_success_body_is_not_retryable(body):
    error = _send_error(_responding(200, body))
    assert error.retryable is False


def test_non_json_success_body_is_not_retryable():
    error = _send_error(_client(lambda request: httpx.Response(200, text="<html>")))
    assert error.retryable is False


def test_missing_setting_is_not_retryable_and_sends_nothing():
    calls: list[httpx.Request] = []
    with pytest.raises(SendError) as caught:
        smtp2go.send(
            {"from_address": "a@example.com"}, MESSAGE, _client(lambda r: calls.append(r) or httpx.Response(200))
        )
    assert caught.value.retryable is False
    assert calls == []


def test_error_reason_never_echoes_the_response_body():
    error = _send_error(_responding(401, {"data": {"error": f"bad key {SETTINGS['api_key']}"}}))
    assert SETTINGS["api_key"] not in str(error)
    assert SETTINGS["api_key"] not in error.reason
