from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from envoy_collector.mapping import battery_net_w, map_battery_readings, map_production_readings

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TS = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=datetime.timezone.utc)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_production_uses_eim_not_inverters():
    readings = map_production_readings(load_fixture("production.json"), TS)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["production_w"] == 7474.653  # the eim value, not 4920 (inverters)


def test_consumption_and_grid_import_derived_correctly():
    readings = map_production_readings(load_fixture("production.json"), TS)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["consumption_w"] == 7621.994
    assert by_metric["grid_import_w"] == 147.341
    assert "grid_export_w" not in by_metric


def test_negative_net_consumption_yields_grid_export():
    production = load_fixture("production.json")
    production["consumption"][1]["wNow"] = -500.0
    readings = map_production_readings(production, TS)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["grid_export_w"] == 500.0
    assert "grid_import_w" not in by_metric


def test_invert_consumption_ct_flips_consumption_and_grid_sign():
    # A reversed consumption CT clamp reports consumption_w (and the
    # net-consumption used to derive grid import/export) with the opposite
    # sign to reality; invert_consumption_ct corrects it at the source.
    readings = map_production_readings(load_fixture("production.json"), TS, invert_consumption_ct=True)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["consumption_w"] == -7621.994
    assert by_metric["grid_export_w"] == 147.341
    assert "grid_import_w" not in by_metric


def test_invert_consumption_ct_defaults_to_false():
    uninverted = {r["metric"]: r["value"] for r in map_production_readings(load_fixture("production.json"), TS)}
    explicit_false = {
        r["metric"]: r["value"]
        for r in map_production_readings(load_fixture("production.json"), TS, invert_consumption_ct=False)
    }
    assert uninverted == explicit_false


def test_all_production_readings_share_the_given_timestamp():
    readings = map_production_readings(load_fixture("production.json"), TS)
    assert all(r["ts"] == TS for r in readings)


def test_battery_negative_power_yields_charge_and_averaged_soc():
    readings = map_battery_readings(load_fixture("ensemble_power.json"), TS)
    by_metric = {r["metric"]: r["value"] for r in readings}
    # sum(-1826,-1834,-1339,-1327) mW = -6326000 mW = -6326.0 W -> charging 6326.0W
    assert by_metric["battery_charge_w"] == pytest.approx(6326.0)
    assert "battery_discharge_w" not in by_metric
    assert by_metric["battery_soc_pct"] == pytest.approx((95 + 94 + 93 + 91) / 4)


def test_battery_positive_power_yields_discharge():
    ensemble = {"devices:": [{"serial_num": "x", "real_power_mw": 2000000, "soc": 50}]}
    readings = map_battery_readings(ensemble, TS)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["battery_discharge_w"] == pytest.approx(2000.0)
    assert "battery_charge_w" not in by_metric


def test_no_battery_devices_yields_no_battery_readings():
    readings = map_battery_readings({"devices:": []}, TS)
    assert readings == []


def test_battery_net_w_returns_signed_total_for_devices():
    # Same fixture as test_battery_negative_power_yields_charge_and_averaged_soc:
    # sum(-1826,-1834,-1339,-1327) mW = -6326.0 W (charging).
    assert battery_net_w(load_fixture("ensemble_power.json")) == pytest.approx(-6326.0)


def test_battery_net_w_returns_none_for_no_devices():
    assert battery_net_w({"devices:": []}) is None


def test_consumption_unaffected_when_battery_net_w_omitted():
    # Default (no battery info available) must match today's behavior exactly
    # -- this is the only correct formula for a site with no battery.
    readings = map_production_readings(load_fixture("production.json"), TS)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["consumption_w"] == 7621.994


def test_consumption_adds_battery_discharge_when_battery_net_w_given():
    # Envoy's own "total-consumption" is production + net-consumption only --
    # it has no visibility into the battery CT. Passing battery_net_w adds
    # the battery's own contribution back in to recover true household
    # consumption.
    readings = map_production_readings(load_fixture("production.json"), TS, battery_net_w=100.0)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["consumption_w"] == pytest.approx(7621.994 + 100.0)


def test_consumption_subtracts_battery_charge_when_battery_net_w_given():
    readings = map_production_readings(load_fixture("production.json"), TS, battery_net_w=-100.0)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["consumption_w"] == pytest.approx(7621.994 - 100.0)


def test_consumption_matches_live_observed_bug_report():
    # Reproduces the real scenario that exposed this bug: 219W production,
    # 15360W battery discharge, 14338W grid export (net-consumption -14338).
    # Envoy's own total-consumption reported -14119W (physically impossible);
    # the true household draw, confirmed against the user's own meter
    # observation (~1-1.2kW), was ~1241W.
    production = {
        "production": [{"type": "eim", "measurementType": "production", "wNow": 219.0}],
        "consumption": [
            {"type": "eim", "measurementType": "total-consumption", "wNow": -14119.0},
            {"type": "eim", "measurementType": "net-consumption", "wNow": -14338.0},
        ],
    }
    readings = map_production_readings(production, TS, battery_net_w=15360.0)
    by_metric = {r["metric"]: r["value"] for r in readings}
    assert by_metric["consumption_w"] == pytest.approx(1241.0)
