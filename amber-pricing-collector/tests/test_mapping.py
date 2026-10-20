from __future__ import annotations

import json
import pathlib

from amber_pricing_collector.mapping import map_price_intervals

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())


def test_maps_general_to_buy_and_feedin_to_sell():
    mapped = map_price_intervals(load_fixture("prices_current.json"))
    channels = {m["channel"] for m in mapped}
    assert channels == {"buy", "sell"}


def test_maps_price_and_interval_bounds_directly():
    mapped = map_price_intervals(load_fixture("prices_current.json"))
    first = next(m for m in mapped if m["channel"] == "buy" and m["interval_start"] == "2026-09-29T23:55:01Z")
    assert first["price"] == "7.37398"
    assert first["interval_end"] == "2026-09-30T00:00:00Z"


def test_actual_current_and_forecast_all_included():
    mapped = map_price_intervals(load_fixture("prices_current.json"))
    # 3 general + 3 feedIn (each actual/current/forecast) = 6
    assert len(mapped) == 6


def test_is_demand_window_never_present_in_output():
    mapped = map_price_intervals(load_fixture("prices_current.json"))
    assert all("is_demand_window" not in m for m in mapped)


def test_unrecognized_channel_type_is_skipped_not_fatal():
    intervals = load_fixture("prices_current.json") + [
        {
            "channelType": "someFutureChannel",
            "startTime": "2026-09-30T00:10:01Z",
            "endTime": "2026-09-30T00:15:00Z",
            "perKwh": 5.0,
        }
    ]
    mapped = map_price_intervals(intervals)
    assert len(mapped) == 6  # the unrecognized entry is dropped, the rest still map


def test_malformed_interval_missing_field_is_skipped_not_fatal():
    intervals = load_fixture("prices_current.json") + [
        {"channelType": "general", "startTime": "2026-09-30T00:10:01Z"}  # missing endTime, perKwh
    ]
    mapped = map_price_intervals(intervals)
    assert len(mapped) == 6


def test_controlled_load_maps_correctly():
    intervals = [
        {
            "channelType": "controlledLoad",
            "startTime": "2026-09-30T00:00:01Z",
            "endTime": "2026-09-30T00:05:00Z",
            "perKwh": 12.3,
        }
    ]
    mapped = map_price_intervals(intervals)
    assert mapped == [{"channel": "controlled_load", "interval_start": "2026-09-30T00:00:01Z", "interval_end": "2026-09-30T00:05:00Z", "price": "12.3"}]


def test_forecast_uses_predicted_price_with_low_and_high():
    mapped = map_price_intervals(load_fixture("prices_current.json"))
    forecast = next(m for m in mapped if m["channel"] == "buy" and m["interval_start"] == "2026-09-30T00:05:01Z")
    assert forecast["price"] == "7.02336"
    assert forecast["price_low"] == "6.34166"
    assert forecast["price_high"] == "7.95185"


def test_feedin_forecast_uses_predicted_price_with_low_and_high():
    mapped = map_price_intervals(load_fixture("prices_current.json"))
    forecast = next(m for m in mapped if m["channel"] == "sell" and m["interval_start"] == "2026-09-30T00:05:01Z")
    assert forecast["price"] == "0.35"
    assert forecast["price_low"] == "-0.5"
    assert forecast["price_high"] == "1.2"


def test_interval_without_advanced_price_keeps_per_kwh_and_has_no_range():
    mapped = map_price_intervals(load_fixture("prices_current.json"))
    actual = next(m for m in mapped if m["channel"] == "buy" and m["interval_start"] == "2026-09-29T23:55:01Z")
    assert actual["price"] == "7.37398"
    assert "price_low" not in actual
    assert "price_high" not in actual


def test_advanced_price_missing_a_key_is_skipped_not_fatal():
    intervals = load_fixture("prices_current.json") + [
        {
            "channelType": "general",
            "startTime": "2026-09-30T00:10:01Z",
            "endTime": "2026-09-30T00:15:00Z",
            "perKwh": 5.0,
            "advancedPrice": {"low": 4.0, "high": 6.0},
        }
    ]
    mapped = map_price_intervals(intervals)
    assert len(mapped) == 6
