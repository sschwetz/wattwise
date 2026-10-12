from __future__ import annotations

import datetime

BUCKET_WIDTH = datetime.timedelta(minutes=5)


def floor_to_5min(ts: datetime.datetime) -> datetime.datetime:
    floored_minute = ts.minute - (ts.minute % 5)
    return ts.replace(minute=floored_minute, second=0, microsecond=0)
