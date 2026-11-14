"""Sunrise and sunset for a site, as UTC instants, so the actor does no astronomy.

The maths is the NOAA solar calculator (declination and equation of time, then
the hour angle at 90.833 degrees, which allows for refraction and the sun's
radius). It is good to about a minute away from the poles. A site with no
point of its own gets an estimate from its time zone's reference city in the
system tz database."""

from __future__ import annotations

import datetime
import functools
import math
import pathlib
import zoneinfo
from dataclasses import dataclass

from core_api.models.identity import Site
from core_api.time_utils import site_tz

_ZONE_TAB = pathlib.Path("/usr/share/zoneinfo/zone.tab")
_SUNRISE_ZENITH_DEG = 90.833
# Local dates `sun_days` returns around today: yesterday to two days ahead, enough for a lockout
# that waits for the next sunrise after a trigger up to a day old.
_DAYS_BEFORE = 1
_DAYS_AFTER = 2


@dataclass(frozen=True)
class Location:
    latitude: float
    longitude: float
    # True when taken from the time zone rather than set on the site.
    estimated: bool = False


@dataclass(frozen=True)
class SunTimes:
    """None for an event that does not happen that day: polar summer and winter."""

    sunrise: datetime.datetime | None
    sunset: datetime.datetime | None


@dataclass(frozen=True)
class SunDay:
    date: datetime.date
    sunrise: datetime.datetime | None
    sunset: datetime.datetime | None


def _iso6709(text: str) -> float:
    """`+3352` or `-0335230` style degrees, minutes and optional seconds, as decimal degrees."""
    sign = -1 if text[0] == "-" else 1
    digits = text[1:]
    degree_width = 2 if len(digits) in (4, 6) else 3
    degrees, rest = int(digits[:degree_width]), digits[degree_width:]
    minutes = int(rest[:2])
    seconds = int(rest[2:4]) if len(rest) >= 4 else 0
    return sign * (degrees + minutes / 60 + seconds / 3600)


def _split_iso6709(coordinates: str) -> tuple[float, float]:
    cut = max(coordinates.rfind("+"), coordinates.rfind("-"))
    return _iso6709(coordinates[:cut]), _iso6709(coordinates[cut:])


@functools.cache
def _zone_table() -> dict[str, tuple[float, float]]:
    try:
        lines = _ZONE_TAB.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    table: dict[str, tuple[float, float]] = {}
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        _, coordinates, name, *_ = line.split("\t")
        table[name] = _split_iso6709(coordinates)
    return table


def zone_location(timezone: str) -> Location | None:
    """The reference city of an IANA zone, or None when the tz database has no table or no such zone."""
    found = _zone_table().get(timezone)
    return None if found is None else Location(found[0], found[1], estimated=True)


def site_location(site: Site) -> Location | None:
    """The site's own point, else an estimate from its time zone, else None."""
    if site.latitude is not None and site.longitude is not None:
        return Location(site.latitude, site.longitude)
    return zone_location(site.timezone)


def _sun_position(century: float) -> tuple[float, float]:
    """The sun's declination in degrees and the equation of time in minutes, `century` Julian centuries after J2000."""
    mean_long = (280.46646 + century * (36000.76983 + century * 0.0003032)) % 360
    anomaly = math.radians(357.52911 + century * (35999.05029 - 0.0001537 * century))
    eccentricity = 0.016708634 - century * (0.000042037 + 0.0000001267 * century)
    centre = (
        math.sin(anomaly) * (1.914602 - century * (0.004817 + 0.000014 * century))
        + math.sin(2 * anomaly) * (0.019993 - 0.000101 * century)
        + math.sin(3 * anomaly) * 0.000289
    )
    node = math.radians(125.04 - 1934.136 * century)
    apparent_long = math.radians(mean_long + centre - 0.00569 - 0.00478 * math.sin(node))
    mean_obliquity = 23 + (26 + (21.448 - century * (46.815 + century * (0.00059 - century * 0.001813))) / 60) / 60
    obliquity = math.radians(mean_obliquity + 0.00256 * math.cos(node))
    declination = math.degrees(math.asin(math.sin(obliquity) * math.sin(apparent_long)))
    y = math.tan(obliquity / 2) ** 2
    long_rad = math.radians(mean_long)
    equation = 4 * math.degrees(
        y * math.sin(2 * long_rad)
        - 2 * eccentricity * math.sin(anomaly)
        + 4 * eccentricity * y * math.sin(anomaly) * math.cos(2 * long_rad)
        - 0.5 * y * y * math.sin(4 * long_rad)
        - 1.25 * eccentricity * eccentricity * math.sin(2 * anomaly)
    )
    return declination, equation


def _event_minutes(location: Location, century: float, rising: bool) -> float | None:
    """Minutes after 00:00 UTC of the UTC date at `century` when the sun crosses the horizon, or None at the poles."""
    declination, equation = _sun_position(century)
    lat, dec = math.radians(location.latitude), math.radians(declination)
    cos_hour_angle = math.cos(math.radians(_SUNRISE_ZENITH_DEG)) / (math.cos(lat) * math.cos(dec)) - math.tan(lat) * math.tan(dec)
    if abs(cos_hour_angle) > 1:
        return None
    hour_angle = math.degrees(math.acos(cos_hour_angle))
    offset = hour_angle if rising else -hour_angle
    return 720 - 4 * (location.longitude + offset) - equation


def _century(moment: datetime.datetime) -> float:
    return (moment.timestamp() / 86400 + 2440587.5 - 2451545.0) / 36525


def _utc_event(location: Location, utc_day: datetime.date, rising: bool) -> datetime.datetime | None:
    """The event near `utc_day`, found at noon UTC and then again at the time first found."""
    midnight = datetime.datetime.combine(utc_day, datetime.time(), tzinfo=datetime.UTC)
    first = _event_minutes(location, _century(midnight + datetime.timedelta(hours=12)), rising)
    if first is None:
        return None
    second = _event_minutes(location, _century(midnight + datetime.timedelta(minutes=first)), rising)
    return None if second is None else midnight + datetime.timedelta(minutes=second)


def _on_local_date(
    location: Location, day: datetime.date, tz: zoneinfo.ZoneInfo, rising: bool
) -> datetime.datetime | None:
    """The sunrise or sunset that falls on local date `day`. Around the date line it can come from the
    neighbouring UTC date, so the three are tried."""
    for shift in (-1, 0, 1):
        event = _utc_event(location, day + datetime.timedelta(days=shift), rising)
        if event is not None and event.astimezone(tz).date() == day:
            return event
    return None


def sun_times(location: Location, day: datetime.date, timezone: str) -> SunTimes:
    """Sunrise and sunset on local date `day` in `timezone`, as UTC instants."""
    tz = site_tz(timezone)
    return SunTimes(_on_local_date(location, day, tz, True), _on_local_date(location, day, tz, False))


def sun_days(site: Site, now: datetime.datetime) -> list[SunDay]:
    """Sun times for the local dates around `now` (see _DAYS_BEFORE), or [] when the site has no usable location."""
    location = site_location(site)
    if location is None:
        return []
    today = now.astimezone(site_tz(site.timezone)).date()
    days = [today + datetime.timedelta(days=n) for n in range(-_DAYS_BEFORE, _DAYS_AFTER + 1)]
    times = [sun_times(location, day, site.timezone) for day in days]
    return [SunDay(day, t.sunrise, t.sunset) for day, t in zip(days, times, strict=True)]
