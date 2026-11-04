"""Sign in to the Actron cloud with a stored refresh token or the device-code flow.

`ensure_session` is called once per tick and never waits for the owner. The
library's `poll_for_token` blocks, so a pending sign-in runs it as one
background task kept in `SignInState`, and each tick only looks at `task.done()`.
The state lives in memory only. Every Core API call runs through
`asyncio.to_thread`, as the client is synchronous. Tokens, device codes and user codes never go
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

__all__ = [
    "REFRESH_FAILURES_TO_REJECT",
    "REFRESH_GAP",
    "REJECTED_RETRY",
    "Rotation",
    "SIGN_IN_HOLDOFF",
    "SessionResult",
    "SignInState",
    "ensure_session",
]

_LOG = logging.getLogger(__name__)

REJECTED_RETRY = datetime.timedelta(minutes=10)
REFRESH_GAP = datetime.timedelta(seconds=60)
REFRESH_FAILURES_TO_REJECT = 3
SIGN_IN_HOLDOFF = datetime.timedelta(seconds=30)


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@dataclass(eq=False, repr=False)
class Rotation:
    """A refresh token the server issued in place of `old`. `stored` is True once Core API has `new`."""

    old: str
    new: str
    stored: bool = False


@dataclass(eq=False, repr=False)
class SignInState:
    """What `ensure_session` carries from one tick to the next. Pass back the `state` of the last result.

    `task` is the running `poll_for_token`. `posted` is False until Core API has the code.
    `failed_at` is when the last poll ended without a token. For a stored token, `failing_token`,
    `refresh_failures` and `last_failure_at` count consecutive failed refreshes, `rejected_at` is when
    the third one was declared a rejection, and `needs_sign_in_posted` says Core API shows "needs sign-in".
    `session_token` is the token the session runs on. `pending_rotation` is a token the server rotated
    that Core API may not have yet.
    """

    task: asyncio.Task[ActronAirToken | None] | None = None
    posted: bool = False
    user_code: str | None = None
    verification_uri: str | None = None
    expires_at: datetime.datetime | None = None
    failed_at: datetime.datetime | None = None
    failing_token: str | None = None
    refresh_failures: int = 0
    last_failure_at: datetime.datetime | None = None
    rejected_at: datetime.datetime | None = None
    needs_sign_in_posted: bool = False
    session_token: str | None = None
    pending_rotation: Rotation | None = None

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
    stored: str,
    now: datetime.datetime,
) -> SessionResult:
    state = state or SignInState()
    state.cancel()
    state.task = None
    token = _token_in_use(state, stored)
    if state.session_token != token and not await _refresh(api, instance, client, state, token, now):
        return SessionResult(False, state)
    await _tidy_up(instance, client, state)
    return SessionResult(True, state)


def _token_in_use(state: SignInState, stored: str) -> str:
    """The stored token, or the rotated one while Core API still returns the token it replaced."""
    rotation = state.pending_rotation
    if rotation is not None and stored == rotation.old and stored != rotation.new:
        return rotation.new
    state.pending_rotation = None
    return stored


async def _refresh(
    api: ActronAirAPI, instance: Instance, client: CoreApiClient, state: SignInState, token: str, now: datetime.datetime
) -> bool:
    """Check the token against the cloud. False when it is not usable (yet)."""
    if state.failing_token != token:
        state.failing_token, state.refresh_failures = token, 0
        state.last_failure_at = state.rejected_at = None
    if _waiting_to_retry(state, now):
        return False
    api.oauth2_auth.refresh_token = token
    try:
        await api.oauth2_auth.refresh_access_token()
    except ActronAirAuthError:
        await _note_failure(instance, client, state, now)
        return False
    state.failing_token, state.refresh_failures = None, 0
    state.last_failure_at = state.rejected_at = None
    _note_session(api, state, token)
    return True


def _waiting_to_retry(state: SignInState, now: datetime.datetime) -> bool:
    if state.rejected_at is not None:
        return now - state.rejected_at < REJECTED_RETRY
    return state.last_failure_at is not None and now - state.last_failure_at < REFRESH_GAP


async def _note_failure(
    instance: Instance, client: CoreApiClient, state: SignInState, now: datetime.datetime
) -> None:
    """The library reports network errors and 5xx as auth errors, so only repeated failures count as a rejection."""
    state.refresh_failures += 1
    state.last_failure_at = now
    if state.refresh_failures < REFRESH_FAILURES_TO_REJECT:
        return
    _LOG.warning("Stored sign-in was rejected (instance %s)", instance.instance_id)
    await asyncio.to_thread(client.patch_discovered, instance.instance_id, auth_state={"needs_sign_in": True})
    state.needs_sign_in_posted = True
    state.rejected_at = now


def _note_session(api: ActronAirAPI, state: SignInState, token: str) -> None:
    """Remember the token the session runs on; a token the server rotated must be stored in Core API."""
    current = api.refresh_token_value
    if current and current != token:
        state.pending_rotation = Rotation(old=token, new=current)
        token = current
    state.session_token = token


async def _tidy_up(instance: Instance, client: CoreApiClient, state: SignInState) -> None:
    """Store a rotated token and clear a posted needs_sign_in. A failed post is retried next tick."""
    rotation = state.pending_rotation
    try:
        if rotation is not None and not rotation.stored:
            await asyncio.to_thread(client.patch_discovered, instance.instance_id, config={"refresh_token": rotation.new})
            rotation.stored = True
        if state.needs_sign_in_posted:
            await asyncio.to_thread(client.patch_discovered, instance.instance_id, auth_state=None)
            state.needs_sign_in_posted = False
    except CoreApiError as exc:
        _LOG.warning("Could not update Core API (instance %s): %s", instance.instance_id, type(exc).__name__)


async def _sign_in(
    api: ActronAirAPI, instance: Instance, client: CoreApiClient, state: SignInState | None, now: datetime.datetime
) -> SessionResult:
    state = state or SignInState()
    if state.task is not None and state.task.done():
        signed_in = await _finish_poll(instance, client, state, now)
        if signed_in is not None:
            return signed_in
    if state.task is None:
        if state.failed_at is not None and now - state.failed_at < SIGN_IN_HOLDOFF:
            return SessionResult(False, state)
        await _start_poll(api, state, now)
    await _post_code(instance, client, state)
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


async def _finish_poll(
    instance: Instance, client: CoreApiClient, state: SignInState, now: datetime.datetime
) -> SessionResult | None:
    """Store an approved token (the done task stays in the state if that fails). None means no token."""
    assert state.task is not None
    token = _poll_outcome(state.task)
    if token is not None and token.refresh_token:
        await asyncio.to_thread(
            client.patch_discovered, instance.instance_id, config={"refresh_token": token.refresh_token}, auth_state=None
        )
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


async def _post_code(instance: Instance, client: CoreApiClient, state: SignInState) -> None:
    if state.posted or state.expires_at is None:
        return
    auth_state = {
        "user_code": state.user_code,
        "verification_uri": state.verification_uri,
        "expires_at": state.expires_at.astimezone(datetime.UTC).isoformat(),
    }
    try:
        await asyncio.to_thread(client.patch_discovered, instance.instance_id, auth_state=auth_state)
    except CoreApiError as exc:
        _LOG.warning("Could not post the sign-in code (instance %s): %s", instance.instance_id, type(exc).__name__)
        return
    state.posted = True
    state.needs_sign_in_posted = False
