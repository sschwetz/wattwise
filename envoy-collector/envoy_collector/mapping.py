from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

_STORAGE_PHASE_KEY = re.compile(r"^agg_p_ph_.+_mw$")
_PHASE_LETTERS = ("a", "b", "c")


def map_meter_power_readings(
    meters: list[dict],
    readings: list[dict],
    ts: datetime.datetime,
    invert_consumption_ct: bool = False,
    battery_net_w: float | None = None,
) -> list[dict]:
    """Map /ivp/meters and /ivp/meters/readings to instantaneous power rows.

    production_w is the production meter's activePower (the CT reading, not
    the inverters' own figure, which disagrees with it in practice). The
    net-consumption meter's activePower is the grid flow: positive means
    importing, negative means exporting. consumption_w is production + grid,
    the same figure the Envoy's total-consumption used to report.

    invert_consumption_ct corrects a physically reversed consumption CT clamp
    (a known Enphase installation mistake) by flipping the sign of the grid
    flow and of the production + grid term.

    The Envoy cannot see a battery's CT, so on a site with a battery that sum
    omits the battery's flow: 219W of production with the battery discharging
    15360W into a 14338W grid export gave -14119W of "consumption" when the
    house drew about 1.24kW. Pass battery_net_w (positive = discharging,
    negative = charging, as returned by battery_net_w()) to add the missing
    term back. Omit it for a batteryless site.

    Raises ValueError when no enabled production or net-consumption meter has
    a reading, so the caller skips the cycle.
    """
    power = _meter_powers(meters, readings)
    for kind in ("production", "net-consumption"):
        if kind not in power:
            raise ValueError(f"no {kind} meter reading")
    sign = -1 if invert_consumption_ct else 1
    production = power["production"]
    grid = sign * power["net-consumption"]
    values = _power_values(production, grid, battery_net_w or 0.0, sign)
    return [{"ts": ts, "metric": m, "value": v} for m, v in values.items()]


def _meter_powers(meters: list[dict], readings: list[dict]) -> dict[str, float]:
    """activePower by measurementType, for enabled meters that have a reading."""
    by_eid = {m["eid"]: m["measurementType"] for m in meters if m.get("state") == "enabled"}
    return {
        by_eid[r["eid"]]: r["activePower"]
        for r in readings
        if r["eid"] in by_eid and r.get("activePower") is not None
    }


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


@dataclass(frozen=True)
class LiveSample:
    """One reading from /ivp/livedata/status, already converted to watts.

    grid_w is positive when importing. battery_w is positive when
    discharging, the same convention as battery_net_w().
    """

    last_update: int
    production_w: float
    grid_w: float
    battery_w: float
    soc: float
    relay_state: int
    has_battery: bool


def _required_mw(meters: dict, section: str) -> float:
    """meters[section]["agg_p_mw"], raising ValueError when the feed sends null for it."""
    value = meters[section]["agg_p_mw"]
    if value is None:
        raise ValueError("unexpected null in live feed")
    return value


def parse_livedata(status_json: dict) -> LiveSample:
    """Convert a /ivp/livedata/status response into a LiveSample.

    The feed reports milliwatts. Battery power is the sum of the per-phase
    storage fields (agg_p_ph_*_mw), not storage.agg_p_mw: the aggregate
    disagreed with the phase figures on the owner's gateway. A site counts
    as having a battery when enc_agg_energy, acb_agg_energy or soc is above
    zero, or any per-phase storage power is non-zero, so a drained battery
    (stored energy and soc both 0) still counts while it draws power.
    Otherwise battery power is 0. A null per-phase storage value counts as
    0; a null pv or grid power raises ValueError, which the sampler treats
    as a malformed response.

    The per-phase battery figure is not yet checked against the gateway's
    own battery readout (see "Open check" in
    docs/superpowers/specs/2026-10-24-envoy-live-data-design.md). The
    LIVE_BATTERY_SOURCE setting defaults to "livedata" at the owner's
    request, so these fields drive the stored battery figures; set it to
    "ensemble" to take them from /ivp/ensemble/power instead.
    """
    meters = status_json["meters"]
    storage = meters.get("storage", {})
    phase_mw = [v or 0 for k, v in storage.items() if _STORAGE_PHASE_KEY.match(k)]
    battery_mw = sum(phase_mw)
    has_battery = (
        any((meters.get(key) or 0) > 0 for key in ("enc_agg_energy", "acb_agg_energy", "soc"))
        or any(phase_mw)
    )
    return LiveSample(
        last_update=meters["last_update"],
        production_w=_required_mw(meters, "pv") / 1000,
        grid_w=_required_mw(meters, "grid") / 1000,
        battery_w=battery_mw / 1000 if has_battery else 0.0,
        soc=meters["soc"],
        relay_state=meters["main_relay_state"],
        has_battery=has_battery,
    )


def _battery_values(battery_w: float, soc: float) -> dict[str, float]:
    key = "battery_charge_w" if battery_w <= 0 else "battery_discharge_w"
    return {"battery_soc_pct": soc, key: abs(battery_w)}


def _battery_rows(battery_w: float, soc: float, ts: datetime.datetime) -> list[dict]:
    return [{"ts": ts, "metric": m, "value": v} for m, v in _battery_values(battery_w, soc).items()]


def _power_values(production: float, grid: float, battery: float, sign: int) -> dict[str, float]:
    """production_w, consumption_w and the import or export figure.

    `grid` is already multiplied by `sign` (-1 for a reversed consumption CT).
    consumption_w is floored at 0: the gateway's PV, grid and storage meters
    update at slightly different instants, so a sharp drop in load can make
    the sum dip below zero for a sample or two.
    """
    return {
        "production_w": production,
        "consumption_w": max(0.0, sign * production + grid + battery),
        "grid_import_w" if grid >= 0 else "grid_export_w": abs(grid),
    }


def _live_battery(
    samples: list[LiveSample], ts: datetime.datetime, battery_source: str, battery_json: dict | None
) -> tuple[float, list[dict]]:
    """Return (battery power in watts, battery rows) for a minute of live samples.

    "ensemble" takes both from an /ivp/ensemble/power response; None there
    means no battery data this minute (power 0, no rows). "livedata"
    averages the samples' battery power and takes the last sample's soc.
    """
    if battery_source == "ensemble":
        if battery_json is None:
            return 0.0, []
        return battery_net_w(battery_json) or 0.0, map_battery_readings(battery_json, ts)
    last = samples[-1]
    if not last.has_battery:
        return 0.0, []
    battery = sum(s.battery_w for s in samples) / len(samples)
    return battery, _battery_rows(battery, last.soc, ts)


def map_livedata_readings(
    samples: list[LiveSample],
    ts: datetime.datetime,
    invert_consumption_ct: bool = False,
    *,
    battery_source: str = "livedata",
    battery_json: dict | None = None,
) -> list[dict]:
    """Average a minute of LiveSamples into {ts, metric, value} dicts.

    Power figures are averaged across the samples; grid_relay_state takes
    the last sample's value. consumption_w is production + grid + battery
    rather than the feed's own load figure. invert_consumption_ct flips the
    grid sign and the production + grid term (a reversed consumption CT
    clamp); battery power is not flipped.

    battery_source picks where battery figures come from (see _live_battery):
    "livedata" uses the samples, "ensemble" uses battery_json. A site with
    no battery data gets no battery rows and counts battery as 0.
    """
    if not samples:
        return []
    n = len(samples)
    production = sum(s.production_w for s in samples) / n
    sign = -1 if invert_consumption_ct else 1
    grid = sign * sum(s.grid_w for s in samples) / n
    battery, battery_rows = _live_battery(samples, ts, battery_source, battery_json)
    rows = [{"ts": ts, "metric": m, "value": v} for m, v in _power_values(production, grid, battery, sign).items()]
    rows.append({"ts": ts, "metric": "grid_relay_state", "value": samples[-1].relay_state})
    return rows + battery_rows


def map_live_sample_values(
    sample: LiveSample, invert_consumption_ct: bool = False, include_battery: bool = True
) -> dict[str, float]:
    """Map one LiveSample to {metric: value} for the live push.

    Uses the same sign and consumption rules as map_livedata_readings, with
    battery figures taken from the sample. Omits grid_relay_state.

    include_battery=False (LIVE_BATTERY_SOURCE=ensemble) drops the battery
    keys and consumption_w, which includes the battery term, so those tiles
    keep the stored ensemble-based readings.
    """
    sign = -1 if invert_consumption_ct else 1
    if not include_battery:
        values = _power_values(sample.production_w, sign * sample.grid_w, 0.0, sign)
        values.pop("consumption_w", None)
        return values
    battery = sample.battery_w if sample.has_battery else 0.0
    values = _power_values(sample.production_w, sign * sample.grid_w, battery, sign)
    if sample.has_battery:
        values.update(_battery_values(battery, sample.soc))
    return values


def map_meter_readings(
    meters: list[dict],
    readings: list[dict],
    ts: datetime.datetime,
) -> list[dict]:
    """Map /ivp/meters and /ivp/meters/readings to counter and voltage rows.

    Entries in readings are matched to meters by eid. An entry whose eid is
    not listed, or whose meter state is not "enabled", is skipped. The
    production meter gives production_wh_lifetime. The net-consumption (grid)
    meter gives grid_import_wh_lifetime (actEnergyDlvd), grid_export_wh_lifetime
    (actEnergyRcvd) and per-phase voltages from channels, limited to the
    meter's phaseCount. Counters are stored raw. The top-level voltage field
    is the sum of the phases, so it is ignored.
    """
    by_eid = {m["eid"]: m for m in meters if m.get("state") == "enabled"}
    rows: list[dict] = []
    for reading in readings:
        meter = by_eid.get(reading["eid"])
        if meter is None:
            continue
        kind = meter.get("measurementType")
        if kind == "production":
            rows.append({"ts": ts, "metric": "production_wh_lifetime", "value": reading["actEnergyDlvd"]})
        elif kind == "net-consumption":
            rows.append({"ts": ts, "metric": "grid_import_wh_lifetime", "value": reading["actEnergyDlvd"]})
            rows.append({"ts": ts, "metric": "grid_export_wh_lifetime", "value": reading["actEnergyRcvd"]})
            phases = min(meter.get("phaseCount", 3), len(_PHASE_LETTERS))
            for letter, channel in zip(_PHASE_LETTERS[:phases], reading.get("channels", [])):
                rows.append({"ts": ts, "metric": f"grid_voltage_ph_{letter}_v", "value": channel["voltage"]})
    return rows
