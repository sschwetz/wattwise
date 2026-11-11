"""The actor's copy of the unit's state, built from the library's typed status.

The raw status (`last_known_state`) holds the wall controller PIN and the
installer's contact details. Nothing here reads it: every value comes from a
named, typed attribute, so the PIN cannot reach a payload, a repr or a log.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass, field

from actron_neo_api import ActronAirStatus, ActronAirZone
from pydantic import BaseModel

HEAT = "HEAT"
COOL = "COOL"


@dataclass(frozen=True)
class DeviceState:
    """Equality ignores `updated_at`, so two refreshes of an unchanged unit compare equal.

    `setpoint_min_c` and `setpoint_max_c` are the unit's setpoint limits for the
    current mode (the library's `min_temp` and `max_temp`).

    Per zone title: `zone_setpoints_c` is the zone's setpoint for the current mode
    (heat in HEAT, else cool), and `zone_ranges_c` the (min, max) the unit accepts
    for it now (the library's `zone.min_temp` and `zone.max_temp`, which follow the
    main setpoint). `zone_cool_heat_c` holds the zone's (cool, heat) setpoints for
    snapshots and is not sent to Core API.
    """

    is_on: bool
    mode: str
    fan_mode: str
    cool_setpoint_c: float
    heat_setpoint_c: float
    enabled_zones: dict[str, bool]
    indoor_temp_c: float | None
    outdoor_temp_c: float | None
    humidity_pct: float | None
    zone_temps_c: dict[str, float]
    updated_at: datetime.datetime = field(compare=False)
    setpoint_min_c: float = 10.0
    setpoint_max_c: float = 30.0
    zone_setpoints_c: dict[str, float] = field(default_factory=dict)
    zone_ranges_c: dict[str, tuple[float, float]] = field(default_factory=dict)
    zone_cool_heat_c: dict[str, tuple[float, float]] = field(default_factory=dict)

    def to_payload(self) -> dict:
        return {
            "is_on": self.is_on,
            "mode": self.mode,
            "fan_mode": self.fan_mode,
            "cool_setpoint_c": self.cool_setpoint_c,
            "heat_setpoint_c": self.heat_setpoint_c,
            "enabled_zones": dict(self.enabled_zones),
            "indoor_temp_c": self.indoor_temp_c,
            "outdoor_temp_c": self.outdoor_temp_c,
            "humidity_pct": self.humidity_pct,
            "zone_temps_c": dict(self.zone_temps_c),
            "updated_at": self.updated_at.astimezone(datetime.UTC).isoformat(),
            "setpoint_min_c": self.setpoint_min_c,
            "setpoint_max_c": self.setpoint_max_c,
            "zone_setpoints_c": dict(self.zone_setpoints_c),
            "zone_ranges_c": {title: [low, high] for title, (low, high) in self.zone_ranges_c.items()},
        }

    def readings(self) -> dict[str, float]:
        """Values for schedule conditions. A per-zone reading is keyed `zone_temp_c@<title>`."""
        values: dict[str, float] = {}
        for key, value in (
            ("indoor_temp_c", self.indoor_temp_c),
            ("outdoor_temp_c", self.outdoor_temp_c),
            ("humidity_pct", self.humidity_pct),
        ):
            if value is not None:
                values[key] = value
        values["is_on"] = 1.0 if self.is_on else 0.0
        for title, temp in self.zone_temps_c.items():
            values[f"zone_temp_c@{title}"] = temp
        return values


def base_fan_mode(fan_mode: str) -> str:
    """The fan speed without the continuous suffix: 'AUTO+CONT' -> 'AUTO'."""
    return fan_mode.split("+CONT")[0].split("-CONT")[0]


def active_setpoint(state: DeviceState) -> float:
    """The setpoint the library's `set_temperature` writes in the current mode."""
    return state.heat_setpoint_c if state.mode.upper() == HEAT else state.cool_setpoint_c


def clamp_setpoint(state: DeviceState, temperature_c: float) -> float:
    """The setpoint the unit ends up at, as `set_temperature` in actron_neo_api 0.5.16 computes it:
    clamped to the limits in COOL and HEAT, sent unchanged in AUTO."""
    if state.mode.upper() in (COOL, HEAT):
        return max(state.setpoint_min_c, min(state.setpoint_max_c, temperature_c))
    return temperature_c


def zone_titles(status: ActronAirStatus) -> list[str]:
    """Stripped zone titles in zone order. Zones with an empty title are left out."""
    return [title for _, title in titled_zones(status)]


def titled_zones(status: ActronAirStatus) -> list[tuple[int, str]]:
    """(index into remote_zone_info, stripped title). The first zone wins a repeated title."""
    seen: set[str] = set()
    zones: list[tuple[int, str]] = []
    for index, zone in enumerate(status.remote_zone_info):
        title = zone.title.strip()
        if title and title not in seen:
            seen.add(title)
            zones.append((index, title))
    return zones


def _reported(model: BaseModel, name: str) -> float | None:
    """The value when the unit sent it; None when the library filled in its 0.0 default."""
    return float(getattr(model, name)) if name in model.model_fields_set else None


def _zone_temps(status: ActronAirStatus, zones: list[tuple[int, str]]) -> dict[str, float]:
    """Zones whose sensor reported a temperature. A zone without one is left out, so a
    condition on it has no reading rather than the library's 0.0 default."""
    temps = {title: _reported(status.remote_zone_info[i], "live_temp_c") for i, title in zones}
    return {title: temp for title, temp in temps.items() if temp is not None}


def _per_zone[T](
    status: ActronAirStatus, zones: list[tuple[int, str]], read: Callable[[ActronAirZone], T]
) -> dict[str, T]:
    return {title: read(status.remote_zone_info[i]) for i, title in zones}


def _zone_setpoint(zone: ActronAirZone) -> float:
    return float(zone.current_setpoint)


def _zone_range(zone: ActronAirZone) -> tuple[float, float]:
    return float(zone.min_temp), float(zone.max_temp)


def _zone_cool_heat(zone: ActronAirZone) -> tuple[float, float]:
    return float(zone.temperature_setpoint_cool_c), float(zone.temperature_setpoint_heat_c)


def state_from_status(status: ActronAirStatus, now: datetime.datetime) -> DeviceState:
    settings = status.user_aircon_settings
    master = status.master_info
    enabled = settings.enabled_zones
    zones = titled_zones(status)
    return DeviceState(
        is_on=bool(settings.is_on),
        mode=settings.mode,
        fan_mode=settings.fan_mode,
        cool_setpoint_c=float(settings.temperature_setpoint_cool_c),
        heat_setpoint_c=float(settings.temperature_setpoint_heat_c),
        enabled_zones={title: bool(enabled[i]) for i, title in zones if i < len(enabled)},
        indoor_temp_c=_reported(master, "live_temp_c"),
        outdoor_temp_c=_reported(master, "live_outdoor_temp_c"),
        humidity_pct=_reported(master, "live_humidity_pc"),
        zone_temps_c=_zone_temps(status, zones),
        updated_at=now.astimezone(datetime.UTC),
        setpoint_min_c=float(status.min_temp),
        setpoint_max_c=float(status.max_temp),
        zone_setpoints_c=_per_zone(status, zones, _zone_setpoint),
        zone_ranges_c=_per_zone(status, zones, _zone_range),
        zone_cool_heat_c=_per_zone(status, zones, _zone_cool_heat),
    )
