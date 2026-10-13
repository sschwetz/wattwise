from __future__ import annotations

import datetime


def next_aligned_tick(
    now: datetime.datetime, *, interval_minutes: int = 5, buffer_seconds: int = 5
) -> datetime.datetime:
    """The next wall-clock boundary that interval_minutes divides the hour
    into, plus a short buffer. Always strictly after `now`."""
    minute_start = now.replace(second=0, microsecond=0)
    boundary = minute_start - datetime.timedelta(minutes=minute_start.minute % interval_minutes)
    candidate = boundary + datetime.timedelta(seconds=buffer_seconds)
    if candidate <= now:
        candidate += datetime.timedelta(minutes=interval_minutes)
    return candidate
