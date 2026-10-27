from __future__ import annotations

import base64
import datetime
import json

import httpx
import pytest
import respx

from envoy_collector.enlighten_client import (
    EnlightenAuthError,
    EnlightenError,
    fetch_token,
    token_expires_at,
)

LOGIN_URL = "https://enlighten.enphaseenergy.com/login/login.json"
TOKENS_URL = "https://entrez.enphaseenergy.com/tokens"


def make_jwt(payload: dict) -> str:
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    return f"{b64(b'{}')}.{b64(json.dumps(payload).encode())}.sig"


@respx.mock
def test_fetch_token_runs_login_then_token_request():
    login = respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={"session_id": "sess-1"}))
    tokens = respx.post(TOKENS_URL).mock(return_value=httpx.Response(200, text="the-token\n"))

    assert fetch_token("u@example.com", "pw", "123456789012") == "the-token"

    assert b"user%5Bemail%5D=u%40example.com" in login.calls[0].request.content
    assert b"user%5Bpassword%5D=pw" in login.calls[0].request.content
    assert json.loads(tokens.calls[0].request.content) == {
        "session_id": "sess-1",
        "serial_num": "123456789012",
        "username": "u@example.com",
    }


@respx.mock
def test_fetch_token_accepts_a_json_token_response():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={"session_id": "s"}))
    respx.post(TOKENS_URL).mock(return_value=httpx.Response(200, json={"token": "json-token"}))
    assert fetch_token("u", "p", "1") == "json-token"


@respx.mock
def test_login_200_without_session_id_is_a_rejected_login():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={"message": "User not found"}))
    with pytest.raises(EnlightenAuthError):
        fetch_token("u", "p", "1")


@respx.mock
def test_login_401_is_a_rejected_login():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(EnlightenAuthError):
        fetch_token("u", "p", "1")


@respx.mock
def test_login_with_non_json_body_is_a_rejected_login():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(EnlightenAuthError):
        fetch_token("u", "p", "1")


@respx.mock
def test_login_server_error_is_a_generic_error_not_an_auth_error():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(EnlightenError) as excinfo:
        fetch_token("u", "p", "1")
    assert not isinstance(excinfo.value, EnlightenAuthError)


@respx.mock
def test_token_endpoint_failure_raises_enlighten_error():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={"session_id": "s"}))
    respx.post(TOKENS_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(EnlightenError):
        fetch_token("u", "p", "1")


@respx.mock
def test_empty_token_body_raises_enlighten_error():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={"session_id": "s"}))
    respx.post(TOKENS_URL).mock(return_value=httpx.Response(200, text="  "))
    with pytest.raises(EnlightenError):
        fetch_token("u", "p", "1")


@respx.mock
def test_transport_failure_raises_enlighten_error_without_secrets():
    respx.post(LOGIN_URL).mock(side_effect=httpx.ConnectError("boom pw-secret"))
    with pytest.raises(EnlightenError) as excinfo:
        fetch_token("u@example.com", "pw-secret", "1")
    assert "pw-secret" not in str(excinfo.value)


@respx.mock
def test_error_messages_never_contain_the_password_or_session_id():
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json={"session_id": "sess-secret"}))
    respx.post(TOKENS_URL).mock(return_value=httpx.Response(500, text="sess-secret pw-secret"))
    with pytest.raises(EnlightenError) as excinfo:
        fetch_token("u", "pw-secret", "1")
    assert "sess-secret" not in str(excinfo.value)
    assert "pw-secret" not in str(excinfo.value)


def test_token_expires_at_reads_the_exp_claim():
    token = make_jwt({"exp": 1_800_000_000})
    assert token_expires_at(token) == datetime.datetime.fromtimestamp(1_800_000_000, tz=datetime.UTC)


@pytest.mark.parametrize(
    "token",
    [None, "", "not-a-jwt", "a.b.c", "a.!!!.c", make_jwt({}), make_jwt({"exp": "soon"}), make_jwt({"exp": 1e30})],
)
def test_token_expires_at_returns_none_for_unreadable_tokens(token):
    assert token_expires_at(token) is None
