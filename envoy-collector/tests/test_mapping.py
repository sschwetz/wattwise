from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from envoy_collector.mapping import (
    LiveSample,
    battery_net_w,
    map_battery_readings,
    map_live_sample_values,
    map_livedata_readings,
    map_meter_readings,
    map_meter_power_readings,
    parse_livedata,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TS = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=datetime.timezone.utc)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


METERS = load_fixture("meters.json")


def power_readings(production: float, net: float) -> list[dict]:
    return [
        {"eid": 704643328, "activePower": production},
        {"eid": 704643584, "activePower": net},
        {"eid": 1023410688, "activePower": 0.0},
    ]


def power_metrics(production=7474.653, net=147.341, **kwargs) -> dict:
    rows = map_meter_power_readings(METERS, power_readings(production, net), TS, **kwargs)
    return {r["metric"]: r["value"] for r in rows}


def test_production_comes_from_the_production_meter():
    assert power_metrics()["production_w"] == 7474.653


def test_consumption_and_grid_import_derived_correctly():
    by_metric = power_metrics()
    assert by_metric["consumption_w"] == pytest.approx(7474.653 + 147.341)
    assert by_metric["grid_import_w"] == 147.341
    assert "grid_export_w" not in by_metric


def test_negative_net_consumption_yields_grid_export():
    by_metric = power_metrics(net=-500.0)
    assert by_metric["grid_export_w"] == 500.0
    assert "grid_import_w" not in by_metric


def test_invert_consumption_ct_flips_consumption_and_grid_sign():
    # A reversed consumption CT clamp reports the grid flow with the opposite
    # sign to reality; invert_consumption_ct corrects it at the source.
    by_metric = power_metrics(invert_consumption_ct=True)
    assert by_metric["consumption_w"] == pytest.approx(-(7474.653 + 147.341))
    assert by_metric["grid_export_w"] == 147.341
    assert "grid_import_w" not in by_metric


def test_invert_consumption_ct_defaults_to_false():
    assert power_metrics() == power_metrics(invert_consumption_ct=False)


def test_all_power_readings_share_the_given_timestamp():
    rows = map_meter_power_readings(METERS, power_readings(1.0, 2.0), TS)
    assert all(r["ts"] == TS for r in rows)


def test_disabled_meters_and_meters_without_a_reading_are_ignored():
    meters = [{**m, "state": "disabled"} if m["measurementType"] == "production" else m for m in METERS]
    with pytest.raises(ValueError, match="production"):
        map_meter_power_readings(meters, power_readings(1.0, 2.0), TS)


def test_missing_net_consumption_reading_raises_value_error():
    with pytest.raises(ValueError, match="net-consumption"):
        map_meter_power_readings(METERS, power_readings(1.0, 2.0)[:1], TS)


def test_reading_without_active_power_raises_value_error():
    readings = [{"eid": 704643328}, {"eid": 704643584, "activePower": 2.0}]
    with pytest.raises(ValueError, match="production"):
        map_meter_power_readings(METERS, readings, TS)


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
    # No battery info available: production + grid is the right figure for a
    # site with no battery.
    assert power_metrics()["consumption_w"] == pytest.approx(7474.653 + 147.341)


def test_consumption_adds_battery_discharge_when_battery_net_w_given():
    # The meters cannot see the battery CT. Passing battery_net_w adds the
    # battery's own contribution back in to recover true household consumption.
    assert power_metrics(battery_net_w=100.0)["consumption_w"] == pytest.approx(7474.653 + 147.341 + 100.0)


def test_consumption_subtracts_battery_charge_when_battery_net_w_given():
    assert power_metrics(battery_net_w=-100.0)["consumption_w"] == pytest.approx(7474.653 + 147.341 - 100.0)


def test_consumption_matches_live_observed_bug_report():
    # Reproduces the real scenario that exposed this bug: 219W production,
    # 15360W battery discharge, 14338W grid export (net-consumption -14338).
    # Production + grid alone gives -14119W (physically impossible); the true
    # household draw, confirmed against the user's own meter observation
    # (~1-1.2kW), was ~1241W.
    by_metric = power_metrics(production=219.0, net=-14338.0, battery_net_w=15360.0)
    assert by_metric["consumption_w"] == pytest.approx(1241.0)


def make_sample(**overrides) -> LiveSample:
    fields = dict(
        last_update=1, production_w=0.0, grid_w=0.0, battery_w=0.0, soc=50, relay_state=1, has_battery=True
    )
    fields.update(overrides)
    return LiveSample(**fields)


def metrics(rows: list[dict]) -> dict:
    return {r["metric"]: r["value"] for r in rows}


def test_parse_livedata_converts_milliwatts_and_sums_phase_storage():
    s = parse_livedata(load_fixture("livedata_status.json"))
    assert s.production_w == pytest.approx(5.394)
    assert s.grid_w == pytest.approx(1121.227)
    assert s.battery_w == pytest.approx(-44.0)
    assert s.soc == 10
    assert s.relay_state == 1
    assert s.last_update == 1792768566
    assert s.has_battery is True


def test_battery_sum_ignores_the_aggregate_storage_figure():
    status = load_fixture("livedata_status.json")
    status["meters"]["storage"]["agg_p_mw"] = 999999
    assert parse_livedata(status).battery_w == pytest.approx(-44.0)


def _zero_battery_status() -> dict:
    status = load_fixture("livedata_status.json")
    meters = status["meters"]
    meters.update(enc_agg_energy=0, acb_agg_energy=0, soc=0)
    meters["storage"] = {"agg_p_mw": 0, "agg_p_ph_a_mw": 0, "agg_p_ph_b_mw": 0, "agg_p_ph_c_mw": 0}
    return status


def test_parse_livedata_batteryless_site():
    s = parse_livedata(_zero_battery_status())
    assert s.has_battery is False
    assert s.battery_w == 0.0


def test_parse_livedata_drained_battery_with_phase_power_has_battery():
    status = _zero_battery_status()
    status["meters"]["storage"]["agg_p_ph_a_mw"] = -15000
    s = parse_livedata(status)
    assert s.has_battery is True
    assert s.battery_w == pytest.approx(-15.0)


def test_parse_livedata_null_phase_storage_counts_as_zero():
    status = load_fixture("livedata_status.json")
    status["meters"]["storage"]["agg_p_ph_b_mw"] = None
    assert parse_livedata(status).battery_w == pytest.approx(-29.0)


@pytest.mark.parametrize("section", ["pv", "grid"])
def test_parse_livedata_null_pv_or_grid_raises(section):
    status = load_fixture("livedata_status.json")
    status["meters"][section]["agg_p_mw"] = None
    with pytest.raises(ValueError, match="unexpected null in live feed"):
        parse_livedata(status)


def test_parse_livedata_soc_alone_means_battery():
    status = _zero_battery_status()
    status["meters"]["soc"] = 5
    assert parse_livedata(status).has_battery is True


def test_parse_livedata_single_phase_sums_only_present_phase_keys():
    status = load_fixture("livedata_status.json")
    status["meters"]["storage"] = {"agg_p_mw": 5000, "agg_p_ph_a_mw": 2000}
    assert parse_livedata(status).battery_w == pytest.approx(2.0)


def test_map_livedata_empty_list():
    assert map_livedata_readings([], TS) == []


def test_map_livedata_averages_samples():
    rows = map_livedata_readings([make_sample(production_w=100.0, soc=40), make_sample(production_w=300.0, soc=42)], TS)
    m = metrics(rows)
    assert m["production_w"] == 200
    assert m["battery_soc_pct"] == 42
    assert all(r["ts"] == TS for r in rows)


def test_map_livedata_splits_grid_import_and_export():
    m = metrics(map_livedata_readings([make_sample(grid_w=500.0)], TS))
    assert m["grid_import_w"] == 500.0 and "grid_export_w" not in m
    m = metrics(map_livedata_readings([make_sample(grid_w=-500.0)], TS))
    assert m["grid_export_w"] == 500.0 and "grid_import_w" not in m


def test_map_livedata_consumption_is_production_plus_grid_plus_battery():
    s = parse_livedata(load_fixture("livedata_status.json"))
    assert metrics(map_livedata_readings([s], TS))["consumption_w"] == pytest.approx(1082.621)


def test_map_livedata_charging_yields_battery_charge_w():
    m = metrics(map_livedata_readings([make_sample(battery_w=-44.0)], TS))
    assert m["battery_charge_w"] == 44.0
    assert "battery_discharge_w" not in m


def test_map_livedata_discharging_yields_battery_discharge_w():
    m = metrics(map_livedata_readings([make_sample(battery_w=800.0)], TS))
    assert m["battery_discharge_w"] == 800.0
    assert "battery_charge_w" not in m


def test_map_livedata_batteryless_writes_no_battery_rows():
    m = metrics(map_livedata_readings([make_sample(has_battery=False, production_w=10.0, grid_w=5.0)], TS))
    assert not {"battery_charge_w", "battery_discharge_w", "battery_soc_pct"} & m.keys()
    assert m["consumption_w"] == 15.0


def test_map_livedata_invert_consumption_ct():
    # Raw grid -300 flips to +300 (import); consumption = -(1000 + -300) + 200.
    s = make_sample(production_w=1000.0, grid_w=-300.0, battery_w=200.0)
    m = metrics(map_livedata_readings([s], TS, invert_consumption_ct=True))
    assert m["grid_import_w"] == 300.0
    assert m["consumption_w"] == -500.0
    assert m["battery_discharge_w"] == 200.0


def test_map_livedata_includes_grid_relay_state():
    m = metrics(map_livedata_readings([make_sample(relay_state=1), make_sample(relay_state=0)], TS))
    assert m["grid_relay_state"] == 0


def test_map_livedata_ensemble_source_takes_battery_from_ensemble_json():
    # Live battery -44 W is ignored; the ensemble fixture sums to -6326 W, soc 93.25.
    s = make_sample(production_w=1000.0, grid_w=200.0, battery_w=-44.0, soc=10)
    rows = map_livedata_readings([s], TS, battery_source="ensemble", battery_json=load_fixture("ensemble_power.json"))
    m = metrics(rows)
    assert m["battery_charge_w"] == pytest.approx(6326.0)
    assert m["battery_soc_pct"] == pytest.approx(93.25)
    assert "battery_discharge_w" not in m
    assert m["consumption_w"] == pytest.approx(1000.0 + 200.0 - 6326.0)


def test_map_livedata_ensemble_source_without_battery_json_writes_no_battery_rows():
    s = make_sample(production_w=1000.0, grid_w=200.0, battery_w=-44.0)
    m = metrics(map_livedata_readings([s], TS, battery_source="ensemble", battery_json=None))
    assert not {"battery_charge_w", "battery_discharge_w", "battery_soc_pct"} & m.keys()
    assert m["consumption_w"] == pytest.approx(1200.0)


def test_map_livedata_livedata_source_ignores_battery_json():
    s = make_sample(production_w=1000.0, grid_w=200.0, battery_w=-44.0, soc=10)
    rows = map_livedata_readings([s], TS, battery_source="livedata", battery_json=load_fixture("ensemble_power.json"))
    m = metrics(rows)
    assert m["battery_charge_w"] == pytest.approx(44.0)
    assert m["battery_soc_pct"] == 10
    assert m["consumption_w"] == pytest.approx(1156.0)


def test_map_meter_readings_counters_and_voltages():
    rows = map_meter_readings(load_fixture("meters.json"), load_fixture("meter_readings.json"), TS)
    assert metrics(rows) == {
        "production_wh_lifetime": 2449808.591,
        "grid_import_wh_lifetime": 957182.412,
        "grid_export_wh_lifetime": 1269929.026,
        "grid_voltage_ph_a_v": 238.232,
        "grid_voltage_ph_b_v": 239.839,
        "grid_voltage_ph_c_v": 239.17,
    }
    assert all(r["ts"] == TS for r in rows)


def test_map_meter_readings_skips_disabled_meter():
    meters = load_fixture("meters.json")
    meters[1]["state"] = "disabled"
    m = metrics(map_meter_readings(meters, load_fixture("meter_readings.json"), TS))
    assert set(m) == {"production_wh_lifetime"}


def test_map_meter_readings_single_phase_gives_only_ph_a():
    meters = load_fixture("meters.json")
    meters[1]["phaseCount"] = 1
    m = metrics(map_meter_readings(meters, load_fixture("meter_readings.json"), TS))
    assert m["grid_voltage_ph_a_v"] == 238.232
    assert "grid_voltage_ph_b_v" not in m and "grid_voltage_ph_c_v" not in m


def test_live_values_match_stored_mapping():
    sample = make_sample(production_w=3000, grid_w=500, battery_w=-200, soc=80)
    assert map_live_sample_values(sample) == {
        "production_w": 3000,
        "consumption_w": 3300,
        "grid_import_w": 500,
        "battery_charge_w": 200,
        "battery_soc_pct": 80,
    }


def test_live_values_export_emits_only_grid_export():
    values = map_live_sample_values(make_sample(production_w=3000, grid_w=-500))
    assert values["grid_export_w"] == 500
    assert "grid_import_w" not in values


def test_live_values_discharge_emits_only_discharge():
    values = map_live_sample_values(make_sample(battery_w=200))
    assert values["battery_discharge_w"] == 200
    assert "battery_charge_w" not in values


def test_live_values_without_battery_omit_battery_keys():
    values = map_live_sample_values(make_sample(battery_w=0.0, has_battery=False))
    assert set(values) == {"production_w", "consumption_w", "grid_import_w"}


def test_live_values_invert_consumption_ct_matches_stored_mapping():
    sample = make_sample(production_w=3000, grid_w=-3400, battery_w=-200)
    live = map_live_sample_values(sample, invert_consumption_ct=True)
    stored = metrics(map_livedata_readings([sample], TS, invert_consumption_ct=True))
    assert live["grid_import_w"] == 3400
    assert live["consumption_w"] == -3000 + 3400 - 200
    assert live == {k: stored[k] for k in live}


@pytest.mark.parametrize("invert", [False, True])
@pytest.mark.parametrize(
    "fields",
    [
        dict(production_w=3000, grid_w=500, battery_w=-200),
        dict(production_w=100, grid_w=-900, battery_w=300),
        dict(production_w=0, grid_w=0, battery_w=0),
    ],
)
def test_live_values_agree_with_stored_mapping_for_one_sample(fields, invert):
    sample = make_sample(**fields)
    live = map_live_sample_values(sample, invert_consumption_ct=invert)
    stored = metrics(map_livedata_readings([sample], TS, invert_consumption_ct=invert))
    assert live == {k: stored[k] for k in live}
    assert set(live) == set(stored) - {"grid_relay_state"}
