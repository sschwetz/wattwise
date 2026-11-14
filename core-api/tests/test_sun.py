from __future__ import annotations

import datetime

import pytest

from core_api.models.identity import Site
from core_api.sun import Location, site_location, sun_days, sun_times, zone_location

UTC = datetime.UTC
TOLERANCE = datetime.timedelta(minutes=3)


def at(day: str, clock: str, offset_hours: int = 0) -> datetime.datetime:
    naive = datetime.datetime.fromisoformat(f"{day}T{clock}")
    return naive.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=offset_hours))).astimezone(UTC)


# Reference times from published almanac values for the date and place, to the minute.
@pytest.mark.parametrize(
    ("lat", "lon", "tz", "day", "rise", "set_"),
    [
        (51.5074, -0.1278, "Europe/London", "2024-06-21", at("2024-06-21", "04:43", 1), at("2024-06-21", "21:21", 1)),
        (-33.8688, 151.2093, "Australia/Sydney", "2024-12-21", at("2024-12-21", "05:41", 11), at("2024-12-21", "20:05", 11)),
        (-33.8688, 151.2093, "Australia/Sydney", "2024-06-21", at("2024-06-21", "07:00", 10), at("2024-06-21", "16:54", 10)),
        (40.7128, -74.0060, "America/New_York", "2024-03-20", at("2024-03-20", "06:57", -4), at("2024-03-20", "19:07", -4)),
    ],
)
def test_sun_times_match_published_values(lat, lon, tz, day, rise, set_):
    times = sun_times(Location(lat, lon), datetime.date.fromisoformat(day), tz)
    assert times.sunrise is not None and times.sunset is not None
    assert abs(times.sunrise - rise) <= TOLERANCE
    assert abs(times.sunset - set_) <= TOLERANCE


def test_sun_times_are_aware_utc_and_on_the_local_date():
    times = sun_times(Location(-33.8688, 151.2093), datetime.date(2024, 12, 21), "Australia/Sydney")
    assert times.sunrise.tzinfo is not None and times.sunrise.utcoffset() == datetime.timedelta(0)
    # Sunrise at 05:41 local is the evening before in UTC, but still 21 December locally.
    assert times.sunrise.date() == datetime.date(2024, 12, 20)
    assert times.sunrise < times.sunset


@pytest.mark.parametrize("day", ["2024-06-21", "2024-12-21"])
def test_no_sunrise_or_sunset_in_polar_summer_or_winter(day):
    times = sun_times(Location(69.6492, 18.9553), datetime.date.fromisoformat(day), "Europe/Oslo")
    assert (times.sunrise, times.sunset) == (None, None)


def test_zone_location_reads_zone_tab():
    sydney = zone_location("Australia/Sydney")
    assert sydney is not None
    assert sydney.latitude == pytest.approx(-33.87, abs=0.1)
    assert sydney.longitude == pytest.approx(151.22, abs=0.1)
    assert zone_location("Not/AZone") is None


def make_site(**kw) -> Site:
    return Site(owner_user_id=1, name="Home", timezone="Australia/Sydney", **{"latitude": None, "longitude": None, **kw})


def test_site_location_prefers_the_site_point_and_flags_an_estimate():
    exact = site_location(make_site(latitude=-37.8, longitude=144.9))
    assert exact == Location(-37.8, 144.9, estimated=False)
    guessed = site_location(make_site())
    assert guessed is not None and guessed.estimated is True
    assert site_location(Site(owner_user_id=1, name="x", timezone="Not/AZone", latitude=None, longitude=None)) is None


def test_sun_days_cover_the_days_around_now_in_local_dates():
    now = datetime.datetime(2026, 11, 14, 3, 0, tzinfo=UTC)
    days = sun_days(make_site(latitude=-33.8688, longitude=151.2093), now)
    assert [d.date for d in days] == [datetime.date(2026, 11, n) for n in (13, 14, 15, 16)]
    assert all(d.sunrise is not None and d.sunset is not None for d in days)
    assert sun_days(Site(owner_user_id=1, name="x", timezone="Not/AZone", latitude=None, longitude=None), now) == []
