from __future__ import annotations

import datetime


def next_aligned_tick(
    now: datetime.datetime, *, interval_minutes: int = 5, buffer_seconds: int = 15
) -> datetime.datetime:
    """The next wall-clock :00/:05/.../:55-style boundary (whatever
    interval_minutes divides the hour into), plus buffer_seconds -- Amber
    settles a new interval right at the boundary, so a short buffer avoids
    polling before it's actually published. Always strictly after `now`."""
    epoch_minute = now.replace(second=0, microsecond=0)
    minutes_since_boundary = epoch_minute.minute % interval_minutes
    boundary = epoch_minute - datetime.timedelta(minutes=minutes_since_boundary)
    candidate = boundary + datetime.timedelta(seconds=buffer_seconds)
    if candidate <= now:
        candidate += datetime.timedelta(minutes=interval_minutes)
    return candidate
