from __future__ import annotations

import datetime
from dataclasses import dataclass

_RAIN_SIZE_MM_PER_TIP = {
    1: 0.01 * 25.4,
    2: 0.2,
    3: 0.1,
    4: 0.001 * 25.4,
}


@dataclass
class RainState:
    """Tracks the previous poll's cumulative rain-since-midnight (in mm) so
    map_iss_readings can report only the delta. None means no baseline yet —
    the first poll after startup emits no rain_mm reading at all."""

    last_daily_mm: float | None = None


def rain_size_mm_per_tip(rain_size: int) -> float:
    """Per Davis's documented rain_size table. 0 is reserved/unsupported."""
    if rain_size not in _RAIN_SIZE_MM_PER_TIP:
        raise ValueError(f"Unsupported or reserved rain_size: {rain_size}")
    return _RAIN_SIZE_MM_PER_TIP[rain_size]


def map_iss_readings(iss: dict, ts: datetime.datetime, rain_state: RainState) -> list[dict]:
    """Map a data_structure_type=1 (outdoor ISS) entry to {ts, metric, value}
    dicts, and update rain_state in place. Every value is rounded to one
    decimal place: the readings are for people to read, not for modelling.

    Uses the 1-minute rolling average for wind (wind_speed_avg_last_1_min /
    wind_dir_scalar_avg_last_1_min), not the instantaneous "last" reading —
    matches the ~1-minute poll cadence and smooths gust noise.
    """
    # Every direct/computed field is skipped (not coerced to 0/None) when the
    # device reports it as null -- solar_rad's documented "no sensor
    # installed" case, extended uniformly so a null in any of them costs only
    # that field, not this whole reading batch (Core API's schema requires a
    # non-null float per reading, so passing a None through would 422 the
    # entire push, including the readings that were fine).
    field_specs = (
        ("temp", "temp_c", lambda v: (v - 32) * 5 / 9),
        ("hum", "humidity_pct", lambda v: v),
        ("wind_speed_avg_last_1_min", "wind_speed_kph", lambda v: v * 1.609344),
        ("wind_dir_scalar_avg_last_1_min", "wind_direction_deg", lambda v: v),
        ("solar_rad", "solar_radiation_wm2", lambda v: v),
    )
    readings = []
    for source_key, metric, transform in field_specs:
        value = iss[source_key]
        if value is not None:
            readings.append({"ts": ts, "metric": metric, "value": round(transform(value), 1)})

    mm_per_tip = rain_size_mm_per_tip(iss["rain_size"])
    # Round the running total, not each tip: rounding every 0.254mm tip on its
    # own would drift, while deltas of rounded totals add up exactly to the
    # rounded daily total. The delta is rounded again only to drop float noise
    # such as 0.30000000000000004.
    current_daily_mm = round(iss["rainfall_daily"] * mm_per_tip, 1)
    if rain_state.last_daily_mm is not None:
        delta = current_daily_mm - rain_state.last_daily_mm
        if delta < 0:
            # local-midnight reset: the new count IS this cycle's rain, not
            # (new - old_from_yesterday).
            delta = current_daily_mm
        readings.append({"ts": ts, "metric": "rain_mm", "value": round(delta, 1)})
    rain_state.last_daily_mm = current_daily_mm

    return readings


def map_barometer_readings(barometer: dict, ts: datetime.datetime) -> list[dict]:
    """Map a data_structure_type=3 (barometer) entry. Uses bar_sea_level (the
    sea-level-adjusted reading), not bar_absolute — the standard
    meteorological convention for comparing pressure across locations."""
    return [
        {"ts": ts, "metric": "barometric_pressure_hpa", "value": round(barometer["bar_sea_level"] * 33.8639, 1)}
    ]


def map_live_values(conditions: list[dict]) -> dict[str, float]:
    """Map a current-conditions payload to {metric: value} for the live tiles.

    Runs the stored-poll mappers with a throwaway RainState, so the poll's
    rain baseline is untouched and a fresh state emits no rain_mm. The ISS or
    barometer block is skipped when absent.
    """
    ts = datetime.datetime.now(datetime.UTC)
    readings: list[dict] = []
    iss = next((c for c in conditions if c["data_structure_type"] == 1), None)
    if iss is not None:
        readings += map_iss_readings(iss, ts, RainState())
    barometer = next((c for c in conditions if c["data_structure_type"] == 3), None)
    if barometer is not None:
        readings += map_barometer_readings(barometer, ts)
    return {r["metric"]: r["value"] for r in readings if r["metric"] != "rain_mm"}
