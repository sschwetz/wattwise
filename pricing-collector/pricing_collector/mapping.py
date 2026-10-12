from __future__ import annotations

import logging

logger = logging.getLogger("pricing_collector")

CHANNEL_MAP = {"general": "buy", "feedIn": "sell", "controlledLoad": "controlled_load"}


def map_price_intervals(intervals: list[dict]) -> list[dict]:
    """Maps every Amber interval entry to a Core API PriceIntervalIn dict.
    ActualInterval/CurrentInterval/ForecastInterval all map identically --
    inclusion is decided by which Amber endpoint was called, not by this
    function. `tariffInformation`/`is_demand_window` are never read (demand-
    window determination is entirely Core API's own SiteTariff/DemandWindow
    concern). price is kept as a string (Amber's own JSON number, restring-
    ified) rather than a float or Decimal, matching how Core API's own tests
    send it -- avoiding float precision issues without adding a Decimal
    dependency to the wire format."""
    mapped = []
    for interval in intervals:
        try:
            channel_type = interval["channelType"]
            if channel_type not in CHANNEL_MAP:
                logger.warning("Skipping price interval with unrecognized channelType: %r", channel_type)
                continue
            mapped.append(
                {
                    "channel": CHANNEL_MAP[channel_type],
                    "interval_start": interval["startTime"],
                    "interval_end": interval["endTime"],
                    "price": str(interval["perKwh"]),
                }
            )
        except KeyError:
            logger.exception("Skipping malformed price interval: %r", interval)
    return mapped
