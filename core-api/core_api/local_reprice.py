from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from core_api.models.local_rates import LocalRateWindow, LocalRepriceMarker
from core_api.time_utils import as_utc

_PRICE_FIELDS = ("channel", "effective_from", "months", "days_of_week", "start_time", "end_time", "rate_c_per_kwh")


def window_snapshot(window: LocalRateWindow) -> dict:
    """The fields of a window that decide its prices, with its dates as aware
    UTC. The label is left out: renaming a window prices nothing differently."""
    snapshot = {name: getattr(window, name) for name in _PRICE_FIELDS}
    snapshot["effective_from"] = as_utc(window.effective_from)
    snapshot["effective_to"] = as_utc(window.effective_to) if window.effective_to else None
    return snapshot


def reprice_start(old: dict, new: dict) -> datetime.datetime | None:
    """The earliest instant whose price a change to a window may alter, or
    None when nothing that decides a price changed. When only the end moved,
    that is the earlier end (a missing end counts as later than any date)."""
    if any(old[name] != new[name] for name in _PRICE_FIELDS):
        return min(old["effective_from"], new["effective_from"])
    if old["effective_to"] == new["effective_to"]:
        return None
    return min(end for end in (old["effective_to"], new["effective_to"]) if end is not None)


def mark_reprice(db: Session, source_id: int, start: datetime.datetime) -> None:
    """Records that prices from `start` may have changed. Keeps the earlier of
    `start` and any marker already there, and raises the version. Runs in the
    caller's transaction."""
    start = as_utc(start)
    marker = db.get(LocalRepriceMarker, source_id)
    if marker is None:
        db.add(LocalRepriceMarker(source_id=source_id, reprice_from=start, version=1))
        return
    marker.reprice_from = min(as_utc(marker.reprice_from), start)
    marker.version += 1
