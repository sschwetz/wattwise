from __future__ import annotations

import datetime
import decimal
import logging
import zoneinfo

from local_pricing_collector.rates import CHANNELS, ceil_minute, channel_intervals, parse_windows, price_at

UTC = datetime.UTC
SYDNEY = zoneinfo.ZoneInfo("Australia/Sydney")
D = decimal.Decimal


def build_intervals(windows, tz, now, horizon_hours):
    """The old 'from the top of the hour for N hours' shape, over every
    channel that has a window, built on the range function."""
    start = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(hours=horizon_hours)
    return [
        interval
        for channel in CHANNELS
        if any(w.channel == channel for w in windows)
        for interval in channel_intervals(windows, channel, tz, start, end)
    ]


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


def at(*args) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=UTC)


def test_a_flat_window_prices_every_minute():
    windows = parse_windows([window()])
    assert price_at(windows, "buy", at(2026, 6, 15, 3, 7), UTC) == D("30.0000")


def test_other_channels_have_no_price():
    windows = parse_windows([window()])
    assert price_at(windows, "sell", at(2026, 6, 15, 3, 7), UTC) is None


def test_the_end_time_is_excluded():
    windows = parse_windows([window(start_time="07:00:00", end_time="09:00:00")])
    assert price_at(windows, "buy", at(2026, 6, 15, 8, 59), UTC) == D("30.0000")
    assert price_at(windows, "buy", at(2026, 6, 15, 9, 0), UTC) is None


def test_an_overnight_window_wraps_past_midnight():
    windows = parse_windows([window(start_time="22:00:00", end_time="07:00:00")])
    assert price_at(windows, "buy", at(2026, 6, 15, 23, 0), UTC) is not None
    assert price_at(windows, "buy", at(2026, 6, 15, 6, 59), UTC) is not None
    assert price_at(windows, "buy", at(2026, 6, 15, 7, 0), UTC) is None


def test_months_and_days_filter_the_window():
    windows = parse_windows([window(months=[12], days_of_week=[0])])
    assert price_at(windows, "buy", at(2026, 12, 14, 12, 0), UTC) is not None  # Monday in December
    assert price_at(windows, "buy", at(2026, 12, 15, 12, 0), UTC) is None  # Tuesday
    assert price_at(windows, "buy", at(2026, 11, 16, 12, 0), UTC) is None  # Monday in November


def test_the_window_is_read_in_the_site_time_zone():
    windows = parse_windows([window(start_time="07:00:00", end_time="09:00:00")])
    # 21:30Z on 14 June is 07:30 on 15 June in Sydney (UTC+10).
    assert price_at(windows, "buy", at(2026, 6, 14, 21, 30), SYDNEY) is not None
    assert price_at(windows, "buy", at(2026, 6, 14, 21, 30), UTC) is None


def test_effective_dates_bound_the_window():
    windows = parse_windows(
        [window(effective_from="2026-07-01T00:00:00+00:00", effective_to="2026-08-01T00:00:00+00:00")]
    )
    assert price_at(windows, "buy", at(2026, 6, 30, 23, 59), UTC) is None
    assert price_at(windows, "buy", at(2026, 7, 1, 0, 0), UTC) is not None
    assert price_at(windows, "buy", at(2026, 7, 31, 23, 59), UTC) is not None
    assert price_at(windows, "buy", at(2026, 8, 1, 0, 0), UTC) is None


def test_a_malformed_window_is_skipped_and_logged(caplog):
    with caplog.at_level(logging.ERROR):
        windows = parse_windows([{"channel": "buy"}, window()])
    assert len(windows) == 1
    assert "Skipping malformed rate window" in caplog.text


def test_a_row_with_null_months_is_skipped_and_the_rest_still_priced(caplog):
    with caplog.at_level(logging.ERROR):
        windows = parse_windows([window(months=None), window(rate_c_per_kwh="25.0000")])
    assert len(windows) == 1
    assert "Skipping malformed rate window" in caplog.text
    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 0), 1)
    assert [i["price"] for i in intervals] == ["25.0000"]


def test_a_row_with_no_utc_offset_is_skipped_and_logged(caplog):
    with caplog.at_level(logging.ERROR):
        windows = parse_windows([window(effective_from="2026-01-01T00:00:00"), window(effective_to="2027-01-01T00:00:00")])
    assert windows == []
    assert caplog.text.count("Skipping malformed rate window") == 2


def test_no_windows_builds_no_intervals():
    assert build_intervals([], UTC, at(2026, 6, 15, 10, 30), 48) == []


def test_a_flat_tariff_builds_one_interval_per_hour_from_the_top_of_the_hour():
    windows = parse_windows([window()])

    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 30), 3)

    assert [(i["interval_start"], i["interval_end"]) for i in intervals] == [
        ("2026-06-15T10:00:00+00:00", "2026-06-15T11:00:00+00:00"),
        ("2026-06-15T11:00:00+00:00", "2026-06-15T12:00:00+00:00"),
        ("2026-06-15T12:00:00+00:00", "2026-06-15T13:00:00+00:00"),
    ]
    assert {i["channel"] for i in intervals} == {"buy"}
    assert {i["price"] for i in intervals} == {"30.0000"}


def test_interval_starts_do_not_move_within_an_hour():
    windows = parse_windows([window()])
    first = build_intervals(windows, UTC, at(2026, 6, 15, 10, 5), 2)
    later = build_intervals(windows, UTC, at(2026, 6, 15, 10, 55), 2)
    assert first == later


def test_an_interval_ends_where_the_rate_changes():
    windows = parse_windows(
        [
            window(label="a", start_time="00:00:00", end_time="10:30:00", rate_c_per_kwh="18.0000"),
            window(label="b", start_time="10:30:00", end_time="00:00:00", rate_c_per_kwh="40.0000"),
        ]
    )

    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 0), 2)

    assert [(i["interval_start"], i["interval_end"], i["price"]) for i in intervals] == [
        ("2026-06-15T10:00:00+00:00", "2026-06-15T10:30:00+00:00", "18.0000"),
        ("2026-06-15T10:30:00+00:00", "2026-06-15T11:00:00+00:00", "40.0000"),
        ("2026-06-15T11:00:00+00:00", "2026-06-15T12:00:00+00:00", "40.0000"),
    ]


def test_a_gap_produces_no_interval():
    windows = parse_windows([window(start_time="10:00:00", end_time="10:30:00")])

    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 0), 2)

    assert [(i["interval_start"], i["interval_end"]) for i in intervals] == [
        ("2026-06-15T10:00:00+00:00", "2026-06-15T10:30:00+00:00"),
    ]


def test_sell_is_pushed_negative():
    windows = parse_windows([window(channel="sell", rate_c_per_kwh="5.0000")])
    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 0), 1)
    assert [i["price"] for i in intervals] == ["-5.0000"]


def test_a_free_sell_rate_is_pushed_as_zero_not_negative_zero():
    windows = parse_windows([window(channel="sell", rate_c_per_kwh="0")])
    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 0), 1)
    assert [i["price"] for i in intervals] == ["0"]


def test_buy_and_controlled_load_are_pushed_as_entered():
    windows = parse_windows(
        [window(channel="buy", rate_c_per_kwh="30"), window(channel="controlled_load", rate_c_per_kwh="12")]
    )
    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 0), 1)
    assert {(i["channel"], i["price"]) for i in intervals} == {("buy", "30"), ("controlled_load", "12")}


def test_a_window_that_ends_inside_the_horizon_stops_pricing_at_that_instant():
    windows = parse_windows(
        [
            window(label="old", effective_to="2026-06-15T10:30:00+00:00", rate_c_per_kwh="30.0000"),
            window(label="new", effective_from="2026-06-15T10:30:00+00:00", rate_c_per_kwh="35.0000"),
        ]
    )

    intervals = build_intervals(windows, UTC, at(2026, 6, 15, 10, 0), 1)

    assert [(i["interval_start"], i["interval_end"], i["price"]) for i in intervals] == [
        ("2026-06-15T10:00:00+00:00", "2026-06-15T10:30:00+00:00", "30.0000"),
        ("2026-06-15T10:30:00+00:00", "2026-06-15T11:00:00+00:00", "35.0000"),
    ]


def test_a_daylight_saving_change_moves_the_peak_start_in_utc():
    """Sydney moves to daylight saving at 02:00 on 4 October 2026. Local 07:00
    on 4 October is 20:00Z on 3 October; without the change it would have been
    21:00Z."""
    windows = parse_windows(
        [
            window(label="peak", start_time="07:00:00", end_time="22:00:00", rate_c_per_kwh="40.0000"),
            window(label="off-peak", start_time="22:00:00", end_time="07:00:00", rate_c_per_kwh="20.0000"),
        ]
    )

    intervals = build_intervals(windows, SYDNEY, at(2026, 10, 3, 0, 0), 24)

    peak_starts = [i["interval_start"] for i in intervals if i["price"] == "40.0000"]
    off_peak_ends = [i["interval_end"] for i in intervals if i["price"] == "20.0000"]
    assert "2026-10-03T20:00:00+00:00" in peak_starts
    assert "2026-10-03T20:00:00+00:00" in off_peak_ends
    assert "2026-10-03T21:00:00+00:00" not in off_peak_ends


def test_a_range_that_starts_and_ends_mid_hour_is_cut_at_the_hour():
    adelaide = zoneinfo.ZoneInfo("Australia/Adelaide")
    windows = parse_windows([window()])

    intervals = channel_intervals(windows, "buy", adelaide, at(2026, 6, 14, 14, 30), at(2026, 6, 14, 16, 30))

    assert [(i["interval_start"], i["interval_end"]) for i in intervals] == [
        ("2026-06-14T14:30:00+00:00", "2026-06-14T15:00:00+00:00"),
        ("2026-06-14T15:00:00+00:00", "2026-06-14T16:00:00+00:00"),
        ("2026-06-14T16:00:00+00:00", "2026-06-14T16:30:00+00:00"),
    ]


def test_ceil_minute_keeps_a_whole_minute_and_rounds_up_otherwise():
    assert ceil_minute(at(2026, 6, 15, 10, 0)) == at(2026, 6, 15, 10, 0)
    off = datetime.datetime(2026, 6, 15, 10, 0, 12, 345000, tzinfo=UTC)
    assert ceil_minute(off) == at(2026, 6, 15, 10, 1)


def test_channel_intervals_over_off_minute_bounds_stay_on_whole_minutes():
    windows = parse_windows([window()])
    start = datetime.datetime(2026, 6, 15, 10, 0, 12, 345000, tzinfo=UTC)
    end = datetime.datetime(2026, 6, 15, 12, 47, 30, tzinfo=UTC)

    intervals = channel_intervals(windows, "buy", UTC, start, end)

    stamps = [i[k] for i in intervals for k in ("interval_start", "interval_end")]
    assert all(t.endswith(":00+00:00") for t in stamps)
    assert intervals[0]["interval_start"] == "2026-06-15T10:01:00+00:00"
    assert intervals[-1]["interval_end"] == "2026-06-15T12:48:00+00:00"
