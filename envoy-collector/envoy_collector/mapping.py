from __future__ import annotations

import datetime


def map_production_readings(
    production_json: dict,
    ts: datetime.datetime,
    invert_consumption_ct: bool = False,
    battery_net_w: float | None = None,
) -> list[dict]:
    """Map a /production.json response to a list of {ts, metric, value} dicts.

    production_w always comes from the "eim" (CT-metered) entry, never the
    "inverters" entry — the two disagree in practice, and the CT reading is
    the billing-grade one. grid_import_w/grid_export_w are derived from the
    "net-consumption" eim entry's sign (positive = importing, negative =
    exporting), confirmed against real sample data where
    total_consumption - production == net_consumption.

    invert_consumption_ct corrects for a physically reversed consumption CT
    clamp (a known Enphase installation mistake, identifiable by the
    consumption entries reporting pwrFactor: -1.0) by flipping the sign of
    both consumption readings before they're used.

    Envoy's own "total-consumption" is defined as production + net-consumption
    -- it has no visibility into a battery's CT, so on a site with an IQ
    Battery it silently omits that flow. In practice this isn't just "off by
    a bit": observed live, 219W production with the battery discharging
    15360W into a 14338W grid export made Envoy report -14119W of
    "consumption", though the house was actually drawing ~1.24kW. Pass
    battery_net_w (this module's own sign convention: positive = discharging,
    negative = charging, as returned by battery_net_w()) to add the missing
    term back in and recover true household consumption. Omit it (the
    default) for a batteryless site, where Envoy's own total-consumption is
    already correct.
    """
    production_eim = next(p for p in production_json["production"] if p["type"] == "eim")
    consumption_total = next(
        c for c in production_json["consumption"] if c["measurementType"] == "total-consumption"
    )
    consumption_net = next(
        c for c in production_json["consumption"] if c["measurementType"] == "net-consumption"
    )

    sign = -1 if invert_consumption_ct else 1
    consumption_w = sign * consumption_total["wNow"]
    if battery_net_w is not None:
        consumption_w += battery_net_w
    readings = [
        {"ts": ts, "metric": "production_w", "value": production_eim["wNow"]},
        {"ts": ts, "metric": "consumption_w", "value": consumption_w},
    ]
    net = sign * consumption_net["wNow"]
    if net >= 0:
        readings.append({"ts": ts, "metric": "grid_import_w", "value": net})
    else:
        readings.append({"ts": ts, "metric": "grid_export_w", "value": -net})
    return readings


def battery_net_w(ensemble_json: dict) -> float | None:
    """Signed instantaneous total battery power from an /ivp/ensemble/power
    response: positive = discharging (a source, same convention as
    production_w), negative = charging (a sink). None for a batteryless site
    (empty devices list), distinguishing "no battery" from "zero flow" so
    callers like map_production_readings know not to apply a correction.
    """
    devices = ensemble_json.get("devices:", [])
    if not devices:
        return None
    return sum(d["real_power_mw"] for d in devices) / 1000


def map_battery_readings(ensemble_json: dict, ts: datetime.datetime) -> list[dict]:
    """Map an /ivp/ensemble/power response to {ts, metric, value} dicts.

    Note the top-level key is "devices:" with a trailing colon — a real
    quirk in Enphase's API. real_power_mw is milliwatts; negative means
    charging, positive means discharging (confirmed live against real
    hardware: SoC rose across two samples while all values were negative).
    soc is averaged across units (no per-unit capacity data is exposed to
    weight by). A site with no battery (empty devices list) yields no
    readings at all, not a crash.
    """
    total_w = battery_net_w(ensemble_json)
    if total_w is None:
        return []

    devices = ensemble_json["devices:"]
    avg_soc = sum(d["soc"] for d in devices) / len(devices)

    readings = [{"ts": ts, "metric": "battery_soc_pct", "value": avg_soc}]
    if total_w <= 0:
        readings.append({"ts": ts, "metric": "battery_charge_w", "value": -total_w})
    else:
        readings.append({"ts": ts, "metric": "battery_discharge_w", "value": total_w})
    return readings
