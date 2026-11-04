from __future__ import annotations

import asyncio
import logging

from actron_neo_actor.config import get_settings
from actron_neo_actor.runner import run_forever


def configure_logging() -> None:
    """The library's warnings can quote raw status values, which include the wall controller PIN."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("actron_neo_api").setLevel(logging.ERROR)


def main() -> None:
    configure_logging()
    asyncio.run(run_forever(get_settings()))


if __name__ == "__main__":
    main()
