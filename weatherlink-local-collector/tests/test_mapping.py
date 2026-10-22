from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from weatherlink_local_collector.mapping import (
    RainState,
    map_barometer_readings,
    map_iss_readings,
    rain_size_mm_per_tip,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TS = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=datetime.timezone.utc)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def iss_entry(**overrides) -> dict:
    data = load_fixture("current_conditions.json")
    iss = next(c for c in data["data"]["conditions"] if c["data_structure_type"] == 1)
    iss.update(overrides)
    return iss


def barometer_entry() -> dict:
    data = load_fixture("current_conditions.json")
    return next(c for c in data["data"]["conditions"] if c["data_structure_type"] == 3)


def test_rain_size_lookup_matches_documented_table():
    assert rain_size_mm_per_tip(2) == 0.2
    assert rain_size_mm_per_tip(3) == 0.1
    assert rain_size_mm_per_tip(1) == pytest.approx(0.01 * 25.4)
    assert rain_size_mm_per_tip(4) == pytest.approx(0.001 * 25.4)


def test_rain_size_zero_is_reserved_and_raises():
    with pytest.raises(ValueError):
        rain_size_mm_per_tip(0)


def test_temp_humidity_wind_conversions():
    readings = map_iss_readings(iss_entry(), TS, RainState())
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["temp_c"] == 22.7  # (72.8 - 32) * 5 / 9 = 22.67
    assert by_metric["humidity_pct"] == 57.3
    assert by_metric["wind_speed_kph"] == 7.1  # 4.43 * 1.609344 = 7.13
    assert by_metric["wind_direction_deg"] == 41


def test_solar_rad_null_is_skipped_not_zeroed():
    readings = map_iss_readings(iss_entry(solar_rad=None), TS, RainState())
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert "solar_radiation_wm2" not in by_metric


def test_null_humidity_and_wind_direction_are_skipped_not_included():
    readings = map_iss_readings(
        iss_entry(hum=None, wind_dir_scalar_avg_last_1_min=None), TS, RainState()
    )
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert "humidity_pct" not in by_metric
    assert "wind_direction_deg" not in by_metric
    assert "temp_c" in by_metric  # other fields still reported


def test_solar_rad_present_is_mapped_directly():
    readings = map_iss_readings(iss_entry(solar_rad=123.4), TS, RainState())
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["solar_radiation_wm2"] == 123.4


def test_first_poll_with_fresh_rain_state_emits_no_rain_reading():
    rain_state = RainState()
    readings = map_iss_readings(iss_entry(rainfall_daily=50, rain_size=2), TS, rain_state)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert "rain_mm" not in by_metric
    assert rain_state.last_daily_mm == pytest.approx(50 * 0.2)


def test_second_poll_reports_delta_since_first():
    rain_state = RainState()
    map_iss_readings(iss_entry(rainfall_daily=50, rain_size=2), TS, rain_state)  # establishes baseline: 10.0mm

    readings = map_iss_readings(iss_entry(rainfall_daily=60, rain_size=2), TS, rain_state)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["rain_mm"] == pytest.approx(2.0)  # (60-50)*0.2
    assert rain_state.last_daily_mm == pytest.approx(12.0)


def test_midnight_reset_reports_new_count_as_full_delta():
    rain_state = RainState()
    map_iss_readings(iss_entry(rainfall_daily=60, rain_size=2), TS, rain_state)  # baseline: 12.0mm

    readings = map_iss_readings(iss_entry(rainfall_daily=5, rain_size=2), TS, rain_state)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["rain_mm"] == pytest.approx(5 * 0.2)  # not negative
    assert rain_state.last_daily_mm == pytest.approx(1.0)


def test_map_iss_readings_raises_for_unsupported_rain_size():
    with pytest.raises(ValueError):
        map_iss_readings(iss_entry(rain_size=0), TS, RainState())


def test_map_barometer_readings_converts_inhg_to_hpa():
    readings = map_barometer_readings(barometer_entry(), TS)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["barometric_pressure_hpa"] == 1030.2  # 30.422 * 33.8639 = 1030.21


def test_rain_is_rounded_to_one_decimal_place():
    rain_state = RainState()
    map_iss_readings(iss_entry(rainfall_daily=0, rain_size=1), TS, rain_state)

    readings = map_iss_readings(iss_entry(rainfall_daily=1, rain_size=1), TS, rain_state)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["rain_mm"] == 0.3  # one 0.254mm tip
    assert rain_state.last_daily_mm == 0.3


def test_rounded_rain_deltas_add_up_to_the_rounded_daily_total():
    rain_state = RainState()
    map_iss_readings(iss_entry(rainfall_daily=0, rain_size=1), TS, rain_state)

    deltas = []
    for tips in range(1, 11):  # ten single-tip polls, 0.254mm each = 2.54mm
        readings = map_iss_readings(iss_entry(rainfall_daily=tips, rain_size=1), TS, rain_state)
        deltas.append(next(r["value"] for r in readings if r["metric"] == "rain_mm"))
    assert sum(deltas) == pytest.approx(2.5)


def test_every_reading_is_rounded_to_one_decimal_place():
    readings = map_iss_readings(iss_entry(solar_rad=123.456), TS, RainState())
    readings += map_barometer_readings(barometer_entry(), TS)
    assert readings
    for reading in readings:
        assert reading["value"] == round(reading["value"], 1), reading["metric"]
