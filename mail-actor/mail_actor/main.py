"""Entry point: declare the providers, then poll the outbox every 5 seconds.

Log lines use fixed text, ids and exception class names only, so no settings
value, address, subject or body reaches the container log."""

from __future__ import annotations

import logging
import math
import signal
import time
from collections.abc import Sequence
from typing import Protocol

import httpx

from mail_actor.config import get_settings
from mail_actor.core_api_client import TIMEOUT, CoreApiClient, CoreApiError
from mail_actor.providers import PROVIDERS
from mail_actor.providers.base import MailProvider
from mail_actor.service_report import start_commit_reporter
from mail_actor.worker import CoreApi, WorkerState, run_once

_LOG = logging.getLogger(__name__)
POLL_SECONDS = 5.0
DECLARE_RETRY_SECONDS = 5.0
STOP_POLL_SECONDS = 0.5


class Stopper(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class StopFlag:
    """A stop request set from a signal handler. The handler only assigns a
    boolean: calling `threading.Event.set` there can deadlock when the signal
    interrupts the main thread inside `Event.wait`. `wait` polls the flag."""

    def __init__(self) -> None:
        self._stopped = False

    def set(self) -> None:
        self._stopped = True

    def is_set(self) -> bool:
        return self._stopped

    def wait(self, timeout: float | None = None) -> bool:
        """Sleep until the flag is set or `timeout` seconds pass. None waits until set."""
        deadline = math.inf if timeout is None else time.monotonic() + timeout
        while not self._stopped and (remaining := deadline - time.monotonic()) > 0:
            time.sleep(min(STOP_POLL_SECONDS, remaining))
        return self._stopped


class Declarer(Protocol):
    def declare_providers(self, providers: Sequence[MailProvider]) -> None: ...


def configure_logging() -> None:
    """httpx logs every request URL at INFO; keep it to warnings."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def declare_until_ready(client: Declarer, providers: Sequence[MailProvider], stop: Stopper) -> bool:
    """Declare the providers, retrying until Core API answers. False if stopped first."""
    while not stop.is_set():
        try:
            client.declare_providers(providers)
        except CoreApiError:
            _LOG.warning("Could not declare mail providers to Core API; retrying in 5 seconds")
            stop.wait(DECLARE_RETRY_SECONDS)
            continue
        _LOG.info("Declared %d mail provider(s)", len(providers))
        return True
    return False


def _tick(
    client: CoreApi, providers: Sequence[MailProvider], http: httpx.Client, state: WorkerState, stop: Stopper
) -> None:
    try:
        run_once(client, providers, http, state, stop.is_set)
    except CoreApiError:
        _LOG.warning("Core API call failed during the outbox poll")
    except Exception as exc:
        _LOG.error("Outbox poll failed: %s", type(exc).__name__)


def run_forever(
    client: CoreApi, providers: Sequence[MailProvider], http: httpx.Client, stop: Stopper, state: WorkerState
) -> None:
    while not stop.is_set():
        _tick(client, providers, http, state, stop)
        stop.wait(POLL_SECONDS)


def install_stop_handlers(stop: StopFlag) -> None:
    """Python is PID 1 in the container, so without a handler `docker stop`
    waits out its grace period and then kills the process."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda signum, frame: stop.set())


def main() -> None:
    configure_logging()
    settings = get_settings()
    stop = StopFlag()
    install_stop_handlers(stop)
    with httpx.Client() as http, httpx.Client(timeout=TIMEOUT) as core_http:
        client = CoreApiClient(settings.core_api_url, settings.mail_actor_key, core_http)
        start_commit_reporter(settings.core_api_url, "X-Actor-Key", settings.mail_actor_key, "mail-actor")
        if declare_until_ready(client, PROVIDERS, stop):
            run_forever(client, PROVIDERS, http, stop, WorkerState())
    _LOG.info("Stopped")


if __name__ == "__main__":
    main()
