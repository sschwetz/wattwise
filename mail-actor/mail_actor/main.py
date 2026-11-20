"""Entry point: declare the providers, then poll the outbox every 5 seconds.

Log lines use fixed text, ids and exception class names only, so no settings
value, address, subject or body reaches the container log."""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Sequence
from typing import Protocol

import httpx

from mail_actor.config import get_settings
from mail_actor.core_api_client import CoreApiClient, CoreApiError
from mail_actor.providers import PROVIDERS
from mail_actor.providers.base import MailProvider
from mail_actor.service_report import start_commit_reporter
from mail_actor.worker import CoreApi, WorkerState, run_once

_LOG = logging.getLogger(__name__)
POLL_SECONDS = 5.0
DECLARE_RETRY_SECONDS = 5.0


class Declarer(Protocol):
    def declare_providers(self, providers: Sequence[MailProvider]) -> None: ...


def configure_logging() -> None:
    """httpx logs every request URL at INFO; keep it to warnings."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def declare_until_ready(client: Declarer, providers: Sequence[MailProvider], stop: threading.Event) -> bool:
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


def _tick(client: CoreApi, providers: Sequence[MailProvider], http: httpx.Client, state: WorkerState) -> None:
    try:
        run_once(client, providers, http, state)
    except CoreApiError:
        _LOG.warning("Core API call failed during the outbox poll")
    except Exception as exc:
        _LOG.error("Outbox poll failed: %s", type(exc).__name__)


def run_forever(
    client: CoreApi, providers: Sequence[MailProvider], http: httpx.Client, stop: threading.Event, state: WorkerState
) -> None:
    while not stop.is_set():
        _tick(client, providers, http, state)
        stop.wait(POLL_SECONDS)


def install_stop_handlers(stop: threading.Event) -> None:
    """Python is PID 1 in the container, so without a handler `docker stop`
    waits out its grace period and then kills the process."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda signum, frame: stop.set())


def main() -> None:
    configure_logging()
    settings = get_settings()
    stop = threading.Event()
    install_stop_handlers(stop)
    with httpx.Client() as http:
        client = CoreApiClient(settings.core_api_url, settings.actor_key)
        start_commit_reporter(settings.core_api_url, "X-Actor-Key", settings.actor_key, "mail-actor")
        if declare_until_ready(client, PROVIDERS, stop):
            run_forever(client, PROVIDERS, http, stop, WorkerState())
    _LOG.info("Stopped")


if __name__ == "__main__":
    main()
