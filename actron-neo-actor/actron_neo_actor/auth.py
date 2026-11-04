"""Sign in to the Actron cloud with a stored refresh token or the device-code flow.

`ensure_session` is called once per tick and never waits for the owner. The
library's `poll_for_token` blocks, so a pending sign-in runs it as one
background task kept in `SignInState`, and each tick only looks at `task.done()`.
The state lives in memory only. Tokens, device codes and user codes never go
into a log line, an exception message or a repr; the user code and verification
URI go to Core API only.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import Callable
from dataclasses import dataclass

from actron_neo_api import ActronAirAPI, ActronAirAuthError
from actron_neo_api.models.auth import ActronAirToken

from actron_neo_actor.core_api_client import CoreApiClient, CoreApiError
from actron_neo_actor.models import Instance

__all__ = ["REJECTED_RETRY", "SIGN_IN_HOLDOFF", "SessionResult", "SignInState", "ensure_session"]

_LOG = logging.getLogger(__name__)

REJECTED_RETRY = datetime.timedelta(minutes=10)
SIGN_IN_HOLDOFF = datetime.timedelta(seconds=30)


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@dataclass(eq=False, repr=False)
class SignInState:
    """What `ensure_session` carries from one tick to the next. Pass back the `state` of the last result.

    `task` is the running `poll_for_token`. `posted` is False until Core API has the code.
    `failed_at` is when the last poll ended without a token. `rejected_token` and `rejected_at`
    record a refused stored token. `session_token` is the stored token the session was restored from.
    """

    task: asyncio.Task[ActronAirToken | None] | None = None
    posted: bool = False
    user_code: str | None = None
    verification_uri: str | None = None
    expires_at: datetime.datetime | None = None
    failed_at: datetime.datetime | None = None
    rejected_token: str | None = None
    rejected_at: datetime.datetime | None = None
    session_token: str | None = None

    def __repr__(self) -> str:
        pending = self.task is not None and not self.task.done()
        return f"SignInState(pending={pending}, posted={self.posted})"

    def cancel(self) -> None:
        """Stop a pending poll. Call it when the instance is dropped."""
        if self.task is not None:
            self.task.cancel()


@dataclass(frozen=True)
class SessionResult:
    signed_in: bool
    state: SignInState | None


async def ensure_session(
    api: ActronAirAPI,
    instance: Instance,
    client: CoreApiClient,
    state: SignInState | None = None,
    *,
    clock: Callable[[], datetime.datetime] = _utc_now,
) -> SessionResult:
    """One non-blocking step towards a signed-in session.

    CoreApiError from storing a token or a rejection propagates; the caller keeps the state it passed in
    and the same step runs again next tick. A failed post of the sign-in code is kept in the state and retried.
    """
    token = instance.config.get("refresh_token")
    if token:
        return await _restore(api, instance, client, state, token, clock())
    return await _sign_in(api, instance, client, state, clock())


async def _restore(
    api: ActronAirAPI,
    instance: Instance,
    client: CoreApiClient,
    state: SignInState | None,
    token: str,
    now: datetime.datetime,
) -> SessionResult:
    if state is not None:
        state.cancel()
        if state.session_token == token:
            return SessionResult(True, state)
        if state.rejected_token == token and state.rejected_at is not None and now - state.rejected_at < REJECTED_RETRY:
            return SessionResult(False, state)
    api.oauth2_auth.refresh_token = token
    try:
        await api.oauth2_auth.refresh_access_token()
    except ActronAirAuthError:
        _LOG.warning("Stored sign-in was rejected (instance %s)", instance.instance_id)
        client.patch_discovered(instance.instance_id, auth_state={"needs_sign_in": True})
        return SessionResult(False, SignInState(rejected_token=token, rejected_at=now))
    return SessionResult(True, SignInState(session_token=_persist_rotation(api, instance, client, token)))


def _persist_rotation(api: ActronAirAPI, instance: Instance, client: CoreApiClient, token: str) -> str:
    """Store a refresh token the server rotated during the refresh; returns the token now in use."""
    current = api.refresh_token_value
    if current and current != token:
        client.patch_discovered(instance.instance_id, config={"refresh_token": current})
        return current
    return token


async def _sign_in(
    api: ActronAirAPI, instance: Instance, client: CoreApiClient, state: SignInState | None, now: datetime.datetime
) -> SessionResult:
    state = state or SignInState()
    if state.task is not None and state.task.done():
        signed_in = _finish_poll(instance, client, state, now)
        if signed_in is not None:
            return signed_in
    if state.task is None:
        if state.failed_at is not None and now - state.failed_at < SIGN_IN_HOLDOFF:
            return SessionResult(False, state)
        await _start_poll(api, state, now)
    _post_code(instance, client, state)
    return SessionResult(False, state)


def _poll_outcome(task: asyncio.Task[ActronAirToken | None]) -> ActronAirToken | None:
    """The token, or None for a timeout, an expired or denied code, or any failure."""
    try:
        return task.result()
    except (asyncio.CancelledError, ActronAirAuthError):
        return None
    except Exception as exc:
        _LOG.warning("Sign-in poll failed: %s", type(exc).__name__)
        return None


def _finish_poll(
    instance: Instance, client: CoreApiClient, state: SignInState, now: datetime.datetime
) -> SessionResult | None:
    """Store an approved token (the done task stays in the state if that fails). None means no token."""
    assert state.task is not None
    token = _poll_outcome(state.task)
    if token is not None and token.refresh_token:
        client.patch_discovered(instance.instance_id, config={"refresh_token": token.refresh_token}, auth_state=None)
        return SessionResult(True, SignInState(session_token=token.refresh_token))
    _LOG.info("Sign-in code ended without a token (instance %s)", instance.instance_id)
    state.task = None
    state.posted = False
    state.failed_at = now
    return None


async def _start_poll(api: ActronAirAPI, state: SignInState, now: datetime.datetime) -> None:
    code = await api.request_device_code()
    state.user_code = code.user_code
    state.verification_uri = code.verification_uri
    state.expires_at = now + datetime.timedelta(seconds=code.expires_in)
    state.failed_at = None
    state.posted = False
    state.task = asyncio.create_task(
        api.poll_for_token(code.device_code, interval=code.interval, timeout=code.expires_in)
    )


def _post_code(instance: Instance, client: CoreApiClient, state: SignInState) -> None:
    if state.posted or state.expires_at is None:
        return
    auth_state = {
        "user_code": state.user_code,
        "verification_uri": state.verification_uri,
        "expires_at": state.expires_at.astimezone(datetime.UTC).isoformat(),
    }
    try:
        client.patch_discovered(instance.instance_id, auth_state=auth_state)
    except CoreApiError as exc:
        _LOG.warning("Could not post the sign-in code (instance %s): %s", instance.instance_id, type(exc).__name__)
        return
    state.posted = True
