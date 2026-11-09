from __future__ import annotations

import logging
import multiprocessing

from local_pricing_collector.config import get_settings
from local_pricing_collector.poll import run_forever
from local_pricing_collector.service_report import start_commit_reporter


def run_commit_reporter(core_api_url: str, collector_key: str) -> None:
    """Entry point of the reporter process. The reporter is a thread, so it runs
    in its own process to keep the main process single-threaded (see main())."""
    start_commit_reporter(core_api_url, "X-Collector-Key", collector_key, "local-pricing-collector").join()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    # A process, not a thread: run_forever forks per source and stays single-threaded.
    multiprocessing.Process(
        target=run_commit_reporter, args=(settings.core_api_url, settings.collector_key), daemon=True
    ).start()
    run_forever(settings)


if __name__ == "__main__":
    main()
