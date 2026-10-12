from __future__ import annotations

import time


def pad_to_minimum_duration(started: float, floor_seconds: float) -> None:
    """Sleeps out the remainder of `floor_seconds` since `started`
    (time.monotonic()), if any. Used where two branches of a response do
    genuinely different amounts of work (a DB write plus an email send vs.
    nothing) that would otherwise make response time an oracle for which
    branch ran, even when the response body is already identical."""
    remaining = floor_seconds - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)
