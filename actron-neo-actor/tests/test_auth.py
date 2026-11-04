import asyncio
import datetime
import functools
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from actron_neo_api import ActronAirAuthError

from actron_neo_actor.auth import SessionResult, SignInState, ensure_session
from actron_neo_actor.core_api_client import UNSET, CoreApiError
from actron_neo_actor.models import Instance
from tests.fakes import FakeActronApi, FakeClock

STORED = "REFRESH-STORED"
NEW = "REFRESH-NEW"
SECRETS = [STORED, NEW, "ACCESS-NEW", "DEVICE-1", "DEVICE-2", "USER-1", "USER-2", "access-token-fake"]


def async_test(fn: Callable[..., Awaitable[None]]) -> Callable[..., None]:
    @functools.wraps(fn)
    def wrapper(**fixtures: Any) -> None:
        asyncio.run(fn(**fixtures))

    return wrapper


class FakeCore:
    """Records `patch_discovered` calls; `fail_posts` makes the next N calls raise CoreApiError."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_posts = 0
        self.threads: set[int] = set()

    def patch_discovered(self, instance_id: int, config: dict | None = None, auth_state: Any = UNSET) -> None:
        self.threads.add(threading.get_ident())
        if self.fail_posts:
            self.fail_posts -= 1
            raise CoreApiError("Failed to report discovered config: 500")
        self.calls.append({"instance_id": instance_id, "config": config, "auth_state": auth_state})


class Harness:
    def __init__(self, token: str | None = None) -> None:
        self.api = FakeActronApi()
        self.core = FakeCore()
        self.clock = FakeClock()
        self.token = token
        self.state: SignInState | None = None

    def instance(self) -> Instance:
        config = {} if self.token is None else {"refresh_token": self.token}
        return Instance(instance_id=7, site_id=1, name=None, timezone="UTC", restore_only=False, config=config)

    async def tick(self) -> SessionResult:
        result = await ensure_session(
            self.api,
            self.instance(),
            self.core,
            self.state,
            clock=self.clock.now,  # type: ignore[arg-type]
        )
        self.state = result.state
        await settle()  # lets a newly started poll task reach its first await
        return result

    def advance(self, seconds: float) -> None:
        self.clock.current += datetime.timedelta(seconds=seconds)


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def posted_codes(core: FakeCore) -> list[dict]:
    return [c for c in core.calls if c["auth_state"] not in (None, UNSET) and "user_code" in c["auth_state"]]


# New instance


@async_test
async def test_new_instance_posts_code_then_stores_token_on_approval():
    h = Harness()
    first = await h.tick()
    assert first.signed_in is False
    assert h.core.calls == [
        {
            "instance_id": 7,
            "config": None,
            "auth_state": {
                "user_code": "USER-1",
                "verification_uri": "https://example.test/verify",
                "expires_at": "2026-11-05T04:10:00+00:00",
            },
        }
    ]
    assert h.api.poll_calls == [("DEVICE-1", 5, 600)]
    h.api.approve(NEW)
    await settle()
    second = await h.tick()
    assert second.signed_in is True
    assert second.state is not None and second.state.task is None
    assert h.core.calls[1:] == [{"instance_id": 7, "config": {"refresh_token": NEW}, "auth_state": None}]


@async_test
async def test_pending_poll_posts_nothing_and_starts_one_task():
    h = Harness()
    await h.tick()
    state = h.state
    for _ in range(3):
        result = await h.tick()
        assert result.signed_in is False
        assert result.state is state
    await settle()
    assert len(h.core.calls) == 1
    assert len(h.api.poll_calls) == 1
    assert h.api.codes_issued == 1


@async_test
async def test_expired_code_requests_a_new_one_after_holdoff():
    h = Harness()
    await h.tick()
    h.api.fail_poll()
    await settle()
    assert (await h.tick()).signed_in is False
    assert h.api.codes_issued == 1
    h.advance(29)
    await h.tick()
    assert h.api.codes_issued == 1
    h.advance(1)
    await h.tick()
    assert h.api.codes_issued == 2
    assert [c["auth_state"]["user_code"] for c in posted_codes(h.core)] == ["USER-1", "USER-2"]
    assert [call[0] for call in h.api.poll_calls] == ["DEVICE-1", "DEVICE-2"]


@async_test
async def test_poll_timeout_and_unexpected_errors_get_a_new_code():
    for end in (h_time_out, h_crash):
        h = Harness()
        await h.tick()
        end(h)
        await settle()
        await h.tick()
        h.advance(30)
        await h.tick()
        assert h.api.codes_issued == 2


def h_time_out(h: Harness) -> None:
    h.api.time_out()


def h_crash(h: Harness) -> None:
    h.api.fail_poll(RuntimeError("boom"))


@async_test
async def test_failed_code_post_retries_without_losing_the_code():
    h = Harness()
    h.core.fail_posts = 1
    first = await h.tick()
    assert first.signed_in is False
    assert first.state is not None and first.state.posted is False
    assert h.core.calls == []
    second = await h.tick()
    assert second.state is not None and second.state.posted is True
    assert [c["auth_state"]["user_code"] for c in posted_codes(h.core)] == ["USER-1"]
    assert h.api.codes_issued == 1
    assert len(h.api.poll_calls) == 1


@async_test
async def test_token_stored_even_if_core_api_failed_the_first_time():
    h = Harness()
    await h.tick()
    h.api.approve(NEW)
    await settle()
    h.core.fail_posts = 1
    try:
        await h.tick()
    except CoreApiError:
        pass
    else:
        raise AssertionError("expected CoreApiError")
    assert h.state is not None  # the caller keeps the state it passed in
    assert (await h.tick()).signed_in is True
    assert h.core.calls[-1] == {"instance_id": 7, "config": {"refresh_token": NEW}, "auth_state": None}


@async_test
async def test_cancel_cancels_the_pending_task():
    h = Harness()
    await h.tick()
    assert h.state is not None and h.state.task is not None
    task = h.state.task
    h.state.cancel()
    await settle()
    assert task.cancelled()
    h.state.cancel()


@async_test
async def test_cancelled_ensure_session_leaves_no_task():
    h = Harness()
    h.api.code_gate = asyncio.Event()
    before = set(asyncio.all_tasks())
    run = asyncio.ensure_future(h.tick())
    await settle()
    assert not run.done()  # suspended inside request_device_code
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)
    await settle()
    assert run.cancelled()
    assert asyncio.all_tasks() - before == set()
    assert h.api.poll_calls == []
    assert h.core.calls == []


@async_test
async def test_stored_token_arrives_while_signing_in_cancels_the_pending_task():
    h = Harness()
    await h.tick()
    task = h.state.task  # type: ignore[union-attr]
    h.token = STORED
    assert (await h.tick()).signed_in is True
    await settle()
    assert task is not None and task.cancelled()


# Stored token


@async_test
async def test_stored_token_restores_without_prompting():
    h = Harness(STORED)
    result = await h.tick()
    assert result.signed_in is True
    assert h.api.oauth2_auth.refresh_token == STORED
    assert h.api.oauth2_auth.refreshes == 1
    assert h.core.calls == []
    assert h.api.codes_issued == 0
    await h.tick()
    assert h.api.oauth2_auth.refreshes == 1


@async_test
async def test_rotated_refresh_token_is_persisted():
    h = Harness(STORED)
    h.api.oauth2_auth.rotate_to = NEW
    assert (await h.tick()).signed_in is True
    assert h.core.calls == [{"instance_id": 7, "config": {"refresh_token": NEW}, "auth_state": UNSET}]


def fail_refresh(h: Harness) -> None:
    h.api.oauth2_auth.refresh_error = ActronAirAuthError("invalid_grant")


async def reject(h: Harness) -> None:
    """Three failed refreshes, 60 s apart."""
    fail_refresh(h)
    await h.tick()
    h.advance(60)
    await h.tick()
    h.advance(60)
    await h.tick()


@async_test
async def test_rejected_token_sets_needs_sign_in_after_three_failures():
    h = Harness(STORED)
    fail_refresh(h)
    assert (await h.tick()).signed_in is False
    h.advance(60)
    assert (await h.tick()).signed_in is False
    assert h.core.calls == []
    h.advance(60)
    result = await h.tick()
    assert result.signed_in is False
    assert h.api.oauth2_auth.refreshes == 3
    assert h.core.calls == [{"instance_id": 7, "config": None, "auth_state": {"needs_sign_in": True}}]
    assert h.api.codes_issued == 0


@async_test
async def test_failures_inside_the_gap_do_not_count_or_call_the_api():
    h = Harness(STORED)
    fail_refresh(h)
    await h.tick()
    for _ in range(5):
        h.advance(10)
        await h.tick()
    assert h.api.oauth2_auth.refreshes == 1
    assert h.core.calls == []


@async_test
async def test_a_successful_refresh_resets_the_failure_count():
    h = Harness(STORED)
    fail_refresh(h)
    await h.tick()
    h.advance(60)
    await h.tick()
    h.api.oauth2_auth.refresh_error = None
    h.advance(60)
    assert (await h.tick()).signed_in is True
    h.state.session_token = None  # type: ignore[union-attr]  # force a new refresh
    fail_refresh(h)
    for _ in range(2):
        h.advance(60)
        await h.tick()
    assert h.core.calls == []


@async_test
async def test_rejected_token_is_retried_only_after_ten_minutes():
    h = Harness(STORED)
    await reject(h)
    assert h.api.oauth2_auth.refreshes == 3
    h.advance(599)
    await h.tick()
    assert h.api.oauth2_auth.refreshes == 3
    assert len(h.core.calls) == 1
    h.advance(1)
    await h.tick()
    assert h.api.oauth2_auth.refreshes == 4


@async_test
async def test_retry_succeeds_once_the_owner_fixed_it_and_clears_needs_sign_in():
    h = Harness(STORED)
    await reject(h)
    h.advance(600)
    h.api.oauth2_auth.refresh_error = None
    assert (await h.tick()).signed_in is True
    assert h.core.calls[-1] == {"instance_id": 7, "config": None, "auth_state": None}
    await h.tick()
    assert len(h.core.calls) == 2


@async_test
async def test_failed_clear_of_needs_sign_in_is_retried():
    h = Harness(STORED)
    await reject(h)
    h.advance(600)
    h.api.oauth2_auth.refresh_error = None
    h.core.fail_posts = 1
    assert (await h.tick()).signed_in is True
    assert len(h.core.calls) == 1
    await h.tick()
    assert h.core.calls[-1] == {"instance_id": 7, "config": None, "auth_state": None}


@async_test
async def test_changed_token_resets_the_rejection():
    h = Harness(STORED)
    await reject(h)
    h.token = "REFRESH-OTHER"
    h.api.oauth2_auth.refresh_error = None
    assert (await h.tick()).signed_in is True
    assert h.api.oauth2_auth.refreshes == 4
    assert h.core.calls[-1]["auth_state"] is None


@async_test
async def test_cleared_token_after_rejection_starts_a_sign_in():
    h = Harness(STORED)
    await reject(h)
    h.token = None
    await h.tick()
    assert h.api.codes_issued == 1
    assert len(posted_codes(h.core)) == 1


@async_test
async def test_failed_persist_of_rotated_token_keeps_it_and_retries():
    h = Harness(STORED)
    h.api.oauth2_auth.rotate_to = NEW
    h.core.fail_posts = 1
    result = await h.tick()
    assert result.signed_in is True
    assert h.core.calls == []
    # The instance config still has the old token; the next tick must not go back to it.
    result = await h.tick()
    assert result.signed_in is True
    assert h.api.oauth2_auth.refreshes == 1
    assert h.api.oauth2_auth.refresh_token == NEW
    assert h.core.calls == [{"instance_id": 7, "config": {"refresh_token": NEW}, "auth_state": UNSET}]
    await h.tick()
    assert len(h.core.calls) == 1
    h.token = NEW  # Core API now carries the rotated token
    assert (await h.tick()).signed_in is True
    assert h.api.oauth2_auth.refreshes == 1


@async_test
async def test_stale_pending_rotation_is_dropped_when_the_owner_signs_in_again():
    h = Harness(STORED)
    h.api.oauth2_auth.rotate_to = NEW
    await h.tick()
    h.token = "REFRESH-OTHER"
    h.api.oauth2_auth.rotate_to = None
    assert (await h.tick()).signed_in is True
    assert h.api.oauth2_auth.refreshes == 2
    assert h.api.oauth2_auth.refresh_token == "REFRESH-OTHER"


# Secrets


@async_test
async def test_tokens_never_logged(caplog):
    caplog.set_level(logging.DEBUG, logger="actron_neo_actor")
    caplog.set_level(logging.DEBUG)
    h = Harness()
    h.core.fail_posts = 1
    await h.tick()
    await h.tick()
    h.api.fail_poll(RuntimeError("boom DEVICE-1 USER-1"))
    await settle()
    await h.tick()
    h.advance(30)
    await h.tick()
    h.api.approve(NEW, "ACCESS-NEW")
    await settle()
    await h.tick()
    h2 = Harness(STORED)
    h2.api.oauth2_auth.rotate_to = NEW
    await h2.tick()
    h3 = Harness(STORED)
    h3.api.oauth2_auth.refresh_error = ActronAirAuthError(f"bad {STORED}")
    await reject(h3)
    text = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    for secret in SECRETS:
        assert secret not in text


@async_test
async def test_state_and_result_reprs_hide_secrets():
    h = Harness()
    result = await h.tick()
    for text in (repr(result), str(result), repr(result.state), str(result.state)):
        for secret in SECRETS:
            assert secret not in text
    h.api.fail_poll(RuntimeError("DEVICE-1"))
    await settle()
    assert "DEVICE-1" not in repr(h.state)


@async_test
async def test_core_api_calls_run_off_the_event_loop_thread() -> None:
    h = Harness()
    await h.tick()  # posts the sign-in code
    h.api.approve()
    await settle()
    assert (await h.tick()).signed_in  # stores the approved token
    h.token = STORED
    h.state = None
    h.api.oauth2_auth.refresh_error = ActronAirAuthError("rejected")
    for _ in range(3):
        await h.tick()  # the third failure posts needs_sign_in
        h.advance(61)
    assert len(h.core.calls) == 3
    assert h.core.threads and threading.get_ident() not in h.core.threads
