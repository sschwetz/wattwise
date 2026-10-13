from __future__ import annotations

import logging
import multiprocessing

from amber_pricing_collector.backfill import run_backfill_forever
from amber_pricing_collector.config import get_settings
from amber_pricing_collector.live_poll import run_live_poll_forever, run_recent_poll_forever


def main() -> None:
    """Run the backfill loop and the recent-revalidation loop as sibling
    processes, and the wide live-poll loop here.

    Each of the three loops forks a child per source on its own cadence, and on
    Linux (the Docker target) multiprocessing's default start method is fork.
    Forking a multi-threaded process is a documented deadlock hazard -- the child
    inherits any lock held at fork time (logging's internal lock, httpx/ssl locks)
    already locked and with no owner, and can deadlock on its first use of it.
    Keeping this process single-threaded makes the only concurrency sibling OS
    processes, which share no locks.

    recent_poll_process itself forks a child per source every minute (same as
    run_live_poll_forever), so it cannot be daemonic -- Python refuses to let a
    daemonic process have children ("daemonic processes are not allowed to have
    children"). backfill_process never forks, so daemon=True is fine there.
    """
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    backfill_process = multiprocessing.Process(
        target=run_backfill_forever, args=(settings.core_api_url, settings.collector_key), daemon=True
    )
    backfill_process.start()

    recent_poll_process = multiprocessing.Process(
        target=run_recent_poll_forever, args=(settings.core_api_url, settings.collector_key), daemon=False
    )
    recent_poll_process.start()

    run_live_poll_forever(settings.core_api_url, settings.collector_key)


if __name__ == "__main__":
    main()
