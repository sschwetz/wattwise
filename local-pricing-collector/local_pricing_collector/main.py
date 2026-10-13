from __future__ import annotations

import logging

from local_pricing_collector.config import get_settings
from local_pricing_collector.poll import run_forever


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run_forever(get_settings())


if __name__ == "__main__":
    main()
