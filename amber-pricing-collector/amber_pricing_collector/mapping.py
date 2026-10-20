from __future__ import annotations

import logging

logger = logging.getLogger("amber_pricing_collector")

CHANNEL_MAP = {"general": "buy", "feedIn": "sell", "controlledLoad": "controlled_load"}
SPIKE_STATUSES = frozenset({"none", "potential", "spike"})


def _spike_status(interval: dict) -> str:
    """Amber's spikeStatus, or "none" when it is missing or not one Core API
    accepts -- an unfamiliar value must not get the whole interval rejected."""
    status = interval.get("spikeStatus")
    return status if status in SPIKE_STATUSES else "none"


def _price_fields(interval: dict) -> dict:
    """Forecast intervals carry advancedPrice (a low/predicted/high band);
    store predicted as the price and keep the band. Every other interval
    keeps perKwh, the price actually billed. Raises KeyError if advancedPrice
    is present but missing a key."""
    advanced = interval.get("advancedPrice")
    if advanced is None:
        return {"price": str(interval["perKwh"])}
    return {
        "price": str(advanced["predicted"]),
        "price_low": str(advanced["low"]),
        "price_high": str(advanced["high"]),
    }


def map_price_intervals(intervals: list[dict]) -> list[dict]:
    """Maps every Amber interval entry to a Core API PriceIntervalIn dict.
    ActualInterval/CurrentInterval/ForecastInterval all go through the same
    path -- inclusion is decided by which Amber endpoint was called, not by
    this function. `tariffInformation`/`is_demand_window` are never read (demand-
    window determination is entirely Core API's own SiteTariff/DemandWindow
    concern). price is kept as a string (Amber's own JSON number, restring-
    ified) rather than a float or Decimal, matching how Core API's own tests
    send it -- avoiding float precision issues without adding a Decimal
    dependency to the wire format. Forecast intervals send advancedPrice's
    predicted as price plus price_low/price_high; see _price_fields."""
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
                    "spike_status": _spike_status(interval),
                    **_price_fields(interval),
                }
            )
        except KeyError:
            logger.exception("Skipping malformed price interval: %r", interval)
    return mapped
