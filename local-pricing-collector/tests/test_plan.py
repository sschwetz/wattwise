from __future__ import annotations

import datetime
import zoneinfo

from local_pricing_collector.models import Coverage
from local_pricing_collector.plan import PlannedRange, chunk_range, local_midnight, plan_ranges
from local_pricing_collector.rates import parse_windows
from local_pricing_collector.triggers import Triggers

UTC = datetime.timezone.utc
SYDNEY = zoneinfo.ZoneInfo("Australia/Sydney")
ADELAIDE = zoneinfo.ZoneInfo("Australia/Adelaide")
CATCH_UP = Triggers(catch_up=True, scheduled=False)
SCHEDULED = Triggers(catch_up=False, scheduled=True)
BOTH = Triggers(catch_up=True, scheduled=True)


def at(*args) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=UTC)


def window(**overrides) -> dict:
    base = {
        "channel": "buy",
        "effective_from": "2026-01-01T00:00:00+00:00",
        "effective_to": None,
        "months": list(range(1, 13)),
        "days_of_week": list(range(7)),
        "start_time": "00:00:00",
        "end_time": "00:00:00",
        "rate_c_per_kwh": "30.0000",
    }
    return {**base, **overrides}


def plan(windows, coverage=None, floor=None, tz=SYDNEY, now=at(2026, 6, 15, 0, 0), triggers=CATCH_UP):
    return plan_ranges(parse_windows(windows), coverage or {}, floor, tz, now, triggers)


# now = 2026-06-15T00:00Z = 10:00 in Sydney. Local midnights: today ends 2026-06-15T14:00Z,
# tomorrow ends 2026-06-16T14:00Z.
TODAY_END = at(2026, 6, 15, 14, 0)
TOMORROW_END = at(2026, 6, 16, 14, 0)


def test_local_midnight_is_an_aware_utc_instant():
    assert local_midnight(datetime.date(2026, 6, 16), SYDNEY) == at(2026, 6, 15, 14, 0)
    assert local_midnight(datetime.date(2026, 6, 16), ADELAIDE) == at(2026, 6, 15, 14, 30)


def test_a_new_source_is_filled_from_the_site_data_floor_to_the_end_of_tomorrow():
    ranges = plan([window()], floor=at(2026, 6, 1, 0, 0))
    assert ranges == [PlannedRange("buy", at(2026, 6, 1, 0, 0), TOMORROW_END)]


def test_the_fill_starts_at_the_window_start_when_that_is_after_the_floor():
    ranges = plan([window(effective_from="2026-06-10T00:00:00+00:00")], floor=at(2026, 6, 1, 0, 0))
    assert ranges == [PlannedRange("buy", at(2026, 6, 10, 0, 0), TOMORROW_END)]


def test_the_fill_starts_at_the_floor_when_the_window_started_earlier():
    ranges = plan([window(effective_from="2025-01-01T00:00:00+00:00")], floor=at(2026, 6, 1, 0, 0))
    assert ranges[0].start == at(2026, 6, 1, 0, 0)


def test_a_new_source_on_a_site_with_no_data_starts_now():
    ranges = plan([window()], floor=None, now=at(2026, 6, 15, 0, 0, 40))
    assert ranges == [PlannedRange("buy", at(2026, 6, 15, 0, 0), TOMORROW_END)]


def test_a_source_covered_to_the_end_of_today_needs_nothing_on_a_catch_up():
    coverage = {"buy": Coverage(first=at(2026, 6, 1, 0, 0), last=TODAY_END)}
    assert plan([window()], coverage, floor=at(2026, 6, 1, 0, 0)) == []


def test_an_outage_is_filled_from_the_last_priced_instant():
    coverage = {"buy": Coverage(first=at(2026, 6, 1, 0, 0), last=at(2026, 6, 12, 14, 0))}
    ranges = plan([window()], coverage, floor=at(2026, 6, 1, 0, 0))
    assert ranges == [PlannedRange("buy", at(2026, 6, 12, 14, 0), TOMORROW_END)]


def test_history_before_the_first_priced_instant_is_filled():
    coverage = {"buy": Coverage(first=at(2026, 6, 10, 0, 0), last=TOMORROW_END)}
    ranges = plan([window()], coverage, floor=at(2026, 6, 1, 0, 0))
    assert ranges == [PlannedRange("buy", at(2026, 6, 1, 0, 0), at(2026, 6, 10, 0, 0))]


def test_history_and_a_forward_gap_are_both_planned():
    coverage = {"buy": Coverage(first=at(2026, 6, 10, 0, 0), last=at(2026, 6, 14, 0, 0))}
    ranges = plan([window()], coverage, floor=at(2026, 6, 1, 0, 0))
    assert ranges == [
        PlannedRange("buy", at(2026, 6, 1, 0, 0), at(2026, 6, 10, 0, 0)),
        PlannedRange("buy", at(2026, 6, 14, 0, 0), TOMORROW_END),
    ]


def test_a_direction_added_later_is_filled_without_touching_the_others():
    windows = [window(), window(channel="sell")]
    coverage = {"buy": Coverage(first=at(2026, 6, 1, 0, 0), last=TOMORROW_END)}
    ranges = plan(windows, coverage, floor=at(2026, 6, 1, 0, 0))
    assert ranges == [PlannedRange("sell", at(2026, 6, 1, 0, 0), TOMORROW_END)]


def test_a_direction_without_windows_is_never_planned():
    assert {r.channel for r in plan([window()], floor=at(2026, 6, 1, 0, 0))} == {"buy"}


def test_ended_windows_are_not_planned_past_their_end():
    ended = window(effective_to="2026-03-01T00:00:00+00:00")
    coverage = {"buy": Coverage(first=at(2026, 1, 1, 0, 0), last=at(2026, 3, 1, 0, 0))}
    assert plan([ended], coverage, floor=at(2026, 1, 1, 0, 0)) == []


def test_a_range_is_cut_at_the_latest_end_when_every_window_has_one():
    ended = window(effective_to="2026-06-14T00:00:00+00:00")
    ranges = plan([ended], floor=at(2026, 6, 1, 0, 0))
    assert ranges == [PlannedRange("buy", at(2026, 6, 1, 0, 0), at(2026, 6, 14, 0, 0))]


def test_one_open_ended_window_removes_the_end_limit():
    windows = [window(effective_to="2026-06-14T00:00:00+00:00"), window(effective_from="2026-06-14T00:00:00+00:00")]
    ranges = plan(windows, floor=at(2026, 6, 1, 0, 0))
    assert ranges[-1].end == TOMORROW_END


def test_a_window_that_has_not_started_is_not_planned_before_it_starts():
    future = window(effective_from="2026-06-20T00:00:00+00:00")
    assert plan([future], floor=at(2026, 6, 1, 0, 0)) == []


def test_the_scheduled_push_is_the_whole_next_local_day():
    ranges = plan([window()], now=at(2026, 6, 15, 13, 59), triggers=SCHEDULED)
    assert ranges == [PlannedRange("buy", at(2026, 6, 15, 14, 0), at(2026, 6, 16, 14, 0))]


def test_the_scheduled_push_before_the_clocks_go_back_is_25_hours_long():
    """Sydney leaves daylight saving at 03:00 on 4 April 2027, so that local day has 25 hours."""
    now = at(2027, 4, 3, 12, 59)  # 23:59 on 3 April in Sydney (UTC+11)
    ranges = plan([window()], now=now, triggers=SCHEDULED)
    assert len(ranges) == 1
    assert ranges[0].end - ranges[0].start == datetime.timedelta(hours=25)
    assert ranges[0].start == at(2027, 4, 3, 13, 0)
    assert ranges[0].end == at(2027, 4, 4, 14, 0)


def test_the_scheduled_push_in_a_half_hour_zone_starts_at_local_midnight():
    now = at(2026, 6, 15, 14, 29)  # 23:59 in Adelaide
    ranges = plan([window()], now=now, tz=ADELAIDE, triggers=SCHEDULED)
    assert ranges == [PlannedRange("buy", at(2026, 6, 15, 14, 30), at(2026, 6, 16, 14, 30))]


def test_a_catch_up_and_a_scheduled_push_together_become_one_range():
    now = at(2026, 6, 15, 13, 59)
    ranges = plan([window()], floor=at(2026, 6, 15, 0, 0), now=now, triggers=BOTH)
    assert ranges == [PlannedRange("buy", at(2026, 6, 15, 0, 0), at(2026, 6, 16, 14, 0))]


def test_a_scheduled_push_overwrites_tomorrow_even_when_it_is_already_covered():
    coverage = {"buy": Coverage(first=at(2026, 6, 1, 0, 0), last=TOMORROW_END)}
    ranges = plan([window()], coverage, now=at(2026, 6, 15, 13, 59), triggers=SCHEDULED)
    assert ranges == [PlannedRange("buy", TODAY_END, TOMORROW_END)]


def test_the_chunks_of_a_long_range_are_seven_days_or_less_and_join_up():
    planned = PlannedRange("buy", at(2026, 5, 1, 0, 0), at(2026, 6, 16, 14, 0))

    chunks = chunk_range(planned)

    assert len(chunks) == 7
    assert chunks[0].start == planned.start
    assert chunks[-1].end == planned.end
    assert all(c.end - c.start <= datetime.timedelta(days=7) for c in chunks)
    assert all(a.end == b.start for a, b in zip(chunks, chunks[1:]))


def test_a_short_range_is_one_chunk():
    planned = PlannedRange("buy", at(2026, 6, 15, 0, 0), at(2026, 6, 16, 0, 0))
    assert chunk_range(planned) == [planned]
