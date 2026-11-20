import logging
import threading

import httpx
import pytest
from fake_core import FakeCore

from mail_actor import main, worker
from mail_actor.core_api_client import ActiveProvider, CoreApiError
from mail_actor.providers import PROVIDERS, smtp2go
from mail_actor.providers.base import Field, MailProvider, OutboxMessage, SendError
from mail_actor.worker import BATCH_SIZE, WorkerState, redact, run_once

KEY = "api-SECRET123"
SETTINGS = {"api_key": KEY, "from_address": "mail@example.com"}
HTTP = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeProvider:
    KEY = "fake"
    LABEL = "Fake"
    FIELDS = [Field(name="api_key", label="API key", secret=True, required=True)]

    def __init__(self) -> None:
        self.sent: list[tuple[dict[str, str], OutboxMessage]] = []
        self.failures: list[Exception] = []  # raised by the next sends, one each

    def send(self, settings: dict[str, str], message: OutboxMessage, http: httpx.Client) -> None:
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append((settings, message))


def _message(id: int) -> OutboxMessage:
    return OutboxMessage(id=id, to_address=f"u{id}@example.com", subject=f"subject {id}", body=f"body {id}")


@pytest.fixture(autouse=True)
def no_report_retry_delay(monkeypatch):
    monkeypatch.setattr(worker, "REPORT_RETRY_DELAY", 0)


@pytest.fixture
def core() -> FakeCore:
    core = FakeCore()
    core.active = ActiveProvider(provider="fake", settings=SETTINGS)
    return core


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def _run(core: FakeCore, provider: MailProvider, clock: Clock, state: WorkerState | None = None) -> int:
    return run_once(core, [provider], HTTP, state or WorkerState(clock=clock))


def test_sends_each_claimed_message_and_reports_sent(core, provider, clock):
    core.add(_message(1), _message(2))
    assert _run(core, provider, clock) == 2
    assert [m.id for _, m in provider.sent] == [1, 2]
    assert provider.sent[0][0] == SETTINGS
    assert core.reports == [(1, "sent", None), (2, "sent", None)]


def test_claims_up_to_ten(core, provider, clock):
    core.add(*[_message(i) for i in range(12)])
    assert _run(core, provider, clock) == 10
    assert BATCH_SIZE == 10
    assert core.claims == [10]


def test_no_active_provider_claims_nothing(core, provider, clock):
    core.active = None
    core.add(_message(1))
    assert _run(core, provider, clock) == 0
    assert core.claims == []
    assert core.reports == []


def test_send_error_reports_failed_with_a_fixed_prefix(core, provider, clock):
    provider.failures = [SendError("HTTP 503", retryable=True, status=503)]
    core.add(_message(1), _message(2))
    _run(core, provider, clock)
    assert core.reports == [(1, "failed", "fake send failed: HTTP 503"), (2, "sent", None)]


def test_unexpected_exception_reports_failed_with_the_class_name(core, provider, clock):
    provider.failures = [ValueError(f"boom {KEY}")]
    core.add(_message(1))
    _run(core, provider, clock)
    assert core.reports == [(1, "failed", "fake send failed: ValueError")]


def test_settings_values_are_redacted_from_the_error(core, provider, clock):
    provider.failures = [SendError(f"rejected {KEY} from mail@example.com", retryable=False, status=400)]
    core.add(_message(1))
    _run(core, provider, clock)
    [(_, outcome, error)] = core.reports
    assert outcome == "failed"
    assert error == "fake send failed: rejected [redacted] from [redacted]"


def test_smtp2go_error_never_carries_the_key_echoed_by_the_response(core, clock):
    core.active = ActiveProvider(provider="smtp2go", settings=SETTINGS)
    echo = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(400, json={"data": {"error": f"bad {KEY}"}}))
    )
    core.add(_message(1))
    run_once(core, PROVIDERS, echo, WorkerState(clock=clock))
    [(_, outcome, error)] = core.reports
    assert outcome == "failed"
    assert error == "smtp2go send failed: HTTP 400"
    assert KEY not in (error or "")


def test_active_provider_is_cached_for_30_seconds(core, provider, clock):
    state = WorkerState(clock=clock)
    _run(core, provider, clock, state)
    clock.now += 29
    _run(core, provider, clock, state)
    assert core.active_calls == 1
    clock.now += 1
    _run(core, provider, clock, state)
    assert core.active_calls == 2


def test_cached_none_is_refetched_after_30_seconds(core, provider, clock):
    state = WorkerState(clock=clock)
    core.active = None
    _run(core, provider, clock, state)
    core.active = ActiveProvider(provider="fake", settings=SETTINGS)
    core.add(_message(1))
    assert _run(core, provider, clock, state) == 0
    clock.now += 30
    assert _run(core, provider, clock, state) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_refetches_the_active_provider_before_the_next_send(core, provider, clock, status):
    state = WorkerState(clock=clock)
    provider.failures = [SendError(f"HTTP {status}", retryable=False, status=status)]
    core.add(_message(1), _message(2))
    refreshed = ActiveProvider(provider="fake", settings={**SETTINGS, "api_key": "api-ROTATED456"})
    fetched = [core.active, refreshed]
    core.get_active = lambda: (setattr(core, "active_calls", core.active_calls + 1), fetched.pop(0))[1]  # type: ignore[method-assign]
    _run(core, provider, clock, state)
    assert core.active_calls == 2
    assert provider.sent[0][0]["api_key"] == "api-ROTATED456"
    assert core.reports == [(1, "failed", f"fake send failed: HTTP {status}"), (2, "sent", None)]


def test_other_failures_keep_the_cache(core, provider, clock):
    state = WorkerState(clock=clock)
    provider.failures = [SendError("HTTP 400", retryable=False, status=400)]
    core.add(_message(1), _message(2))
    _run(core, provider, clock, state)
    assert core.active_calls == 1


def test_provider_deactivated_mid_batch_fails_the_rest(core, provider, clock):
    provider.failures = [SendError("HTTP 401", retryable=False, status=401)]
    core.add(_message(1), _message(2))

    def deactivate_after_first_fetch() -> ActiveProvider | None:
        core.active_calls += 1
        return ActiveProvider(provider="fake", settings=SETTINGS) if core.active_calls == 1 else None

    core.get_active = deactivate_after_first_fetch  # type: ignore[method-assign]
    _run(core, provider, clock)
    assert core.reports == [(1, "failed", "fake send failed: HTTP 401"), (2, "failed", "no usable mail provider")]


def test_unknown_active_provider_claims_nothing_and_warns_once_a_minute(core, provider, clock, caplog):
    core.active = ActiveProvider(provider="mailgun", settings=SETTINGS)
    core.add(_message(1))
    state = WorkerState(clock=clock)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert _run(core, provider, clock, state) == 0
            clock.now += 30
    assert core.claims == []
    assert core.reports == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert all("mailgun" not in w for w in warnings)


def test_logs_never_carry_settings_or_message_content(core, provider, clock, caplog):
    provider.failures = [SendError(f"HTTP 401 {KEY}", retryable=False, status=401), RuntimeError(KEY)]
    core.add(_message(1), _message(2), _message(3))
    with caplog.at_level(logging.DEBUG):
        _run(core, provider, clock)
    text = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (KEY, "mail@example.com", "u1@example.com", "subject 1", "body 1"):
        assert secret not in text


# main.py


class StopAfter(threading.Event):
    """Records each wait and sets itself once `waits` waits have happened."""

    def __init__(self, waits: int) -> None:
        super().__init__()
        self.waits: list[float] = []
        self._limit = waits

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout or 0)
        if len(self.waits) >= self._limit:
            self.set()
        return self.is_set()


def test_startup_declares_every_provider(core):
    stop = StopAfter(waits=10)
    assert main.declare_until_ready(core, PROVIDERS, stop) is True
    assert core.declared == [PROVIDERS]
    assert PROVIDERS == [smtp2go]
    assert stop.waits == []


def test_startup_retries_every_5_seconds_until_core_api_answers(core, caplog):
    core.errors["declare_providers"] = [CoreApiError("Failed to reach Core API: ConnectError")] * 2
    stop = StopAfter(waits=10)
    assert main.declare_until_ready(core, PROVIDERS, stop) is True
    assert stop.waits == [5, 5]
    assert core.declared == [PROVIDERS]


def test_startup_gives_up_when_stopped(core):
    core.errors["declare_providers"] = [CoreApiError("down")] * 5
    assert main.declare_until_ready(core, PROVIDERS, StopAfter(waits=1)) is False


def test_loop_runs_every_5_seconds_and_survives_errors(core, provider, caplog):
    core.errors["get_active"] = [CoreApiError("Failed to fetch active provider: 500"), KeyError(KEY)]
    stop = StopAfter(waits=3)
    with caplog.at_level(logging.DEBUG):
        main.run_forever(core, [provider], HTTP, stop, WorkerState(clock=Clock()))
    assert stop.waits == [5, 5, 5]
    assert core.active_calls == 1
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "KeyError" in text
    assert KEY not in text


def test_an_unchanged_key_after_a_refusal_fails_the_rest_of_the_batch_without_sending(core, provider, clock):
    provider.failures = [SendError("HTTP 401", retryable=False, status=401)]
    core.add(*[_message(i) for i in range(10)])
    assert _run(core, provider, clock) == 10
    assert provider.sent == []
    assert core.active_calls == 2
    assert core.reports[0] == (0, "failed", "fake send failed: HTTP 401")
    assert core.reports[1:] == [(i, "failed", "no usable mail provider") for i in range(1, 10)]


def test_the_refusal_is_forgotten_by_the_next_poll(core, provider, clock):
    provider.failures = [SendError("HTTP 401", retryable=False, status=401)]
    state = WorkerState(clock=clock)
    core.add(_message(1))
    _run(core, provider, clock, state)
    core.add(_message(2))
    assert _run(core, provider, clock, state) == 1
    assert core.reports[-1] == (2, "sent", None)


def test_the_sent_report_is_retried_twice_and_the_message_is_sent_once(core, provider, clock):
    core.errors["report"] = [CoreApiError("500")] * 2
    core.add(_message(1))
    calls: list[int] = []
    real_report = core.report
    core.report = lambda id, outcome, error: (calls.append(id), real_report(id, outcome, error))[1]  # type: ignore[method-assign]
    _run(core, provider, clock)
    assert len(provider.sent) == 1
    assert calls == [1, 1, 1]
    assert core.reports == [(1, "sent", None)]


def test_report_failure_does_not_stop_the_batch(core, provider, clock):
    core.errors["report"] = [CoreApiError("500")] * 3
    core.add(_message(1), _message(2))
    assert _run(core, provider, clock) == 2
    assert [m.id for _, m in provider.sent] == [1, 2]
    assert core.reports == [(2, "sent", None)]


def test_a_failed_report_is_attempted_once(core, provider, clock):
    provider.failures = [SendError("HTTP 400", retryable=False, status=400)]
    core.errors["report"] = [CoreApiError("500")]
    core.add(_message(1))
    _run(core, provider, clock)
    assert core.reports == []
    assert core.errors["report"] == []  # only the one queued error was consumed, no second report call


def test_run_once_without_a_state_does_not_share_a_cache(core, provider):
    core.add(_message(1))
    run_once(core, [provider], HTTP)
    core.add(_message(2))
    run_once(core, [provider], HTTP)
    assert core.active_calls == 2


def test_redact_ignores_values_shorter_than_four_characters():
    assert redact("HTTP 401 key api-SECRET123", {"port": "4", "ab": "ab", "api_key": KEY}) == (
        "HTTP 401 key [redacted]"
    )


def test_stop_between_messages_leaves_the_rest_claimed(core, provider, clock):
    core.add(_message(1), _message(2), _message(3))
    stops = iter([False, True])
    assert run_once(core, [provider], HTTP, WorkerState(clock=clock), lambda: next(stops)) == 1
    assert core.reports == [(1, "sent", None)]


# StopFlag and the process wiring


def test_stop_flag_wait_returns_early_once_set(monkeypatch):
    flag = main.StopFlag()
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        flag.set()

    monkeypatch.setattr(main.time, "sleep", sleep)
    assert flag.is_set() is False
    assert flag.wait(5) is True
    assert sleeps == [0.5]


def test_stop_flag_wait_ends_when_the_timeout_passes(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(main.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    assert main.StopFlag().wait(1.2) is False
    assert now[0] == pytest.approx(1.2)


def test_stop_flag_wait_without_a_timeout_waits_until_stopped(monkeypatch):
    flag = main.StopFlag()
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 3:
            flag.set()

    monkeypatch.setattr(main.time, "sleep", sleep)
    assert flag.wait(None) is True
    assert sleeps == [0.5, 0.5, 0.5]


def test_install_stop_handlers_sets_the_flag_for_sigterm_and_sigint(monkeypatch):
    handlers = {}
    monkeypatch.setattr(main.signal, "signal", lambda sig, handler: handlers.__setitem__(sig, handler))
    flag = main.StopFlag()
    main.install_stop_handlers(flag)
    assert set(handlers) == {main.signal.SIGTERM, main.signal.SIGINT}
    handlers[main.signal.SIGTERM](main.signal.SIGTERM, None)
    assert flag.is_set()


def test_loop_stops_mid_batch_when_the_flag_is_set(core, provider):
    flag = main.StopFlag()
    core.add(_message(1), _message(2))
    real_send = provider.send

    def send_then_stop(settings, message, http):
        real_send(settings, message, http)
        flag.set()

    provider.send = send_then_stop  # type: ignore[method-assign]
    main.run_forever(core, [provider], HTTP, flag, WorkerState(clock=Clock()))
    assert [m.id for _, m in provider.sent] == [1]
