from __future__ import annotations

import base64
import datetime
import json

import httpx

LOGIN_URL = "https://enlighten.enphaseenergy.com/login/login.json"
TOKENS_URL = "https://entrez.enphaseenergy.com/tokens"
_TIMEOUT_SECONDS = 10


class EnlightenError(Exception):
    """Any failure getting a token from Enlighten. Messages never include the
    password, session ID, or token."""


class EnlightenAuthError(EnlightenError):
    """Enlighten rejected the username/password."""


def _post(url: str, **kwargs) -> httpx.Response:
    try:
        return httpx.post(url, timeout=_TIMEOUT_SECONDS, **kwargs)
    except httpx.HTTPError as exc:
        # `from None`: the original exception text can echo request details.
        raise EnlightenError(f"Could not reach {httpx.URL(url).host}: {type(exc).__name__}") from None


def _login(username: str, password: str) -> str:
    response = _post(LOGIN_URL, data={"user[email]": username, "user[password]": password})
    if response.status_code in (401, 403):
        raise EnlightenAuthError("Enlighten rejected the username or password")
    if response.status_code != 200:
        raise EnlightenError(f"Enlighten login returned HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        body = None
    session_id = body.get("session_id") if isinstance(body, dict) else None
    if not session_id:
        # Enlighten answers 200 with an error message instead of a session
        # when the credentials are wrong.
        raise EnlightenAuthError("Enlighten login did not return a session; check the username and password")
    return session_id


def _parse_token_body(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    token = body.get("token") if isinstance(body, dict) else response.text
    token = (token or "").strip()
    if not token:
        raise EnlightenError("Enlighten returned an empty token")
    return token


def fetch_token(username: str, password: str, serial_num: str) -> str:
    """Logs in to Enlighten and requests a one-year Envoy token for `serial_num`."""
    session_id = _login(username, password)
    response = _post(TOKENS_URL, json={"session_id": session_id, "serial_num": serial_num, "username": username})
    if response.status_code != 200:
        raise EnlightenError(f"Enlighten token request returned HTTP {response.status_code}")
    return _parse_token_body(response)


def token_expires_at(token: str | None) -> datetime.datetime | None:
    """Reads the `exp` claim from a JWT without verifying the signature (the
    collector only needs the date). None if the token isn't a readable JWT."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        return datetime.datetime.fromtimestamp(exp, tz=datetime.timezone.utc)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
