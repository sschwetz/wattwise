"""smtp2go's Send Email API: https://developers.smtp2go.com/reference/send-standard-email

The key goes in the `X-Smtp2go-Api-Key` header. smtp2go can answer 200 for a
message it did not accept, so success also needs `data.succeeded == 1`, which
the API returns while `fastaccept` is off (its default)."""

from __future__ import annotations

import re

import httpx

from mail_actor.providers.base import Field, OutboxMessage, SendError

KEY = "smtp2go"
LABEL = "smtp2go"
FIELDS = [
    Field(name="api_key", label="API key", secret=True, required=True),
    Field(name="from_address", label="From address", secret=False, required=True),
]

URL = "https://api.smtp2go.com/v3/email/send"
TIMEOUT = 20.0
_MESSAGE_ID = re.compile(r"[A-Za-z0-9._:-]{1,100}")


def _post(api_key: str, payload: dict[str, object], http: httpx.Client) -> httpx.Response:
    headers = {"X-Smtp2go-Api-Key": api_key, "Content-Type": "application/json"}
    try:
        return http.post(URL, json=payload, headers=headers, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise SendError(type(exc).__name__, retryable=True) from None


def _check(response: httpx.Response) -> str | None:
    """Raises unless smtp2go accepted the message. Returns its `email_id`, or
    None when the field is missing or is not a plain token (it ends up in a log line)."""
    status = response.status_code
    if status == 429 or status >= 500:
        raise SendError(f"HTTP {status}", retryable=True, status=status)
    if status != 200:
        raise SendError(f"HTTP {status}", retryable=False, status=status)
    try:
        data = response.json()["data"]
        succeeded = data["succeeded"]
    except (ValueError, KeyError, TypeError):
        raise SendError("unreadable response", retryable=False, status=status) from None
    if type(succeeded) is not int or succeeded != 1:
        raise SendError("not accepted", retryable=False, status=status)
    email_id = data.get("email_id") if isinstance(data, dict) else None
    return email_id if isinstance(email_id, str) and _MESSAGE_ID.fullmatch(email_id) else None


def send(settings: dict[str, str], message: OutboxMessage, http: httpx.Client) -> str | None:
    api_key = settings.get("api_key")
    sender = settings.get("from_address")
    if not api_key or not sender:
        raise SendError("settings incomplete", retryable=False)
    payload: dict[str, object] = {
        "sender": sender,
        "to": [message.to_address],
        "subject": message.subject,
        "text_body": message.body,
    }
    return _check(_post(api_key, payload, http))
