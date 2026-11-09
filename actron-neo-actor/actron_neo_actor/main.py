from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from actron_neo_actor.config import Settings, get_settings
from actron_neo_actor.runner import run_forever
from actron_neo_actor.service_report import start_commit_reporter

_LOG = logging.getLogger(__name__)
_STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def configure_logging() -> None:
    """The library's warnings can quote raw status values, which include the wall controller PIN."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("actron_neo_api").setLevel(logging.ERROR)


def install_stop_handlers(task: asyncio.Task[None]) -> None:
    """Cancel `task` on SIGTERM or SIGINT so `run_forever` stops its workers and puts held units back.

    Python is PID 1 in the container, so without a handler `docker stop` sends a
    SIGTERM that nothing acts on and Docker kills the process after the grace period.
    Where the loop cannot take signal handlers the default behaviour stays."""
    loop = asyncio.get_running_loop()
    for sig in _STOP_SIGNALS:
        try:
            loop.add_signal_handler(sig, task.cancel)
        except (NotImplementedError, RuntimeError, ValueError):
            _LOG.warning("Cannot handle %s on this platform", sig.name)


async def serve(settings: Settings) -> None:
    task = asyncio.current_task()
    if task is not None:
        install_stop_handlers(task)
    with contextlib.suppress(asyncio.CancelledError):
        await run_forever(settings)
    _LOG.info("Stopped")


def main() -> None:
    configure_logging()
    settings = get_settings()
    start_commit_reporter(settings.core_api_url, "X-Actor-Key", settings.actor_key, "actron-neo-actor")
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
