"""The site context an actor fetches before it runs its schedules: recent
readings, the current buy price, and each demand window's occurrences as UTC
instants, so the actor does no time zone logic."""

from __future__ import annotations

import datetime
import math
import zoneinfo
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.jobs.demand_config import (
    energy_source_ids_for_site,
    in_demand_window_pattern,
    pricing_source_ids_for_site,
    site_tariffs_for_site,
    tariff_effective_at,
    window_in_force,
)
from core_api.models.billing import DemandWindow, SiteTariff
from core_api.models.identity import Site
from core_api.models.pricing import PriceChannel, PriceInterval
from core_api.models.readings import EnergyMetric, EnergyReading, WeatherMetric, WeatherReading
from core_api.models.sources import Source, SourceKind
from core_api.time_utils import as_utc, site_tz

LOOKBACK = datetime.timedelta(hours=24)
DEFAULT_HORIZON = datetime.timedelta(hours=48)
FRESHNESS = datetime.timedelta(minutes=15)
_MIDNIGHT = datetime.time(0, 0)
_ONE_DAY = datetime.timedelta(days=1)


@dataclass(frozen=True)
class WindowOccurrence:
    window_id: int
    label: str
    start: datetime.datetime
    end: datetime.datetime


@dataclass(frozen=True)
class ActorContext:
    now: datetime.datetime
    timezone: str
    outdoor_temp_c: float | None
    battery_soc_pct: float | None
    price_c_per_kwh: float | None
    windows: list[WindowOccurrence]


def build_context(db: Session, site: Site, now: datetime.datetime) -> ActorContext:
    now = as_utc(now)
    return ActorContext(
        now=now,
        timezone=site.timezone,
        outdoor_temp_c=_latest_weather_temp(db, site.id, now),
        battery_soc_pct=_latest_battery_soc(db, site.id, now),
        price_c_per_kwh=_current_buy_price(db, site.id, now),
        windows=window_occurrences(db, site, now),
    )


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _latest_weather_temp(db: Session, site_id: int, now: datetime.datetime) -> float | None:
    source_ids = select(Source.id).where(Source.site_id == site_id, Source.kind == SourceKind.WEATHER)
    stmt = select(WeatherReading.value).where(
        WeatherReading.source_id.in_(source_ids),
        WeatherReading.metric == WeatherMetric.TEMP_C,
        WeatherReading.ts <= now,
        WeatherReading.ts >= now - FRESHNESS,
    ).order_by(WeatherReading.ts.desc()).limit(1)
    return _finite(db.execute(stmt).scalar_one_or_none())


def _latest_battery_soc(db: Session, site_id: int, now: datetime.datetime) -> float | None:
    stmt = select(EnergyReading.value).where(
        EnergyReading.source_id.in_(energy_source_ids_for_site(db, site_id)),
        EnergyReading.metric == EnergyMetric.BATTERY_SOC_PCT,
        EnergyReading.ts <= now,
        EnergyReading.ts >= now - FRESHNESS,
    ).order_by(EnergyReading.ts.desc()).limit(1)
    return _finite(db.execute(stmt).scalar_one_or_none())


def _current_buy_price(db: Session, site_id: int, now: datetime.datetime) -> float | None:
    """The buy price of the interval covering `now`. When two pricing
    sources both cover it, the later-starting interval wins, then the
    lower source id."""
    stmt = select(PriceInterval.price).where(
        PriceInterval.pricing_source_id.in_(pricing_source_ids_for_site(db, site_id)),
        PriceInterval.channel == PriceChannel.BUY,
        PriceInterval.interval_start <= now,
        PriceInterval.interval_end > now,
    ).order_by(PriceInterval.interval_start.desc(), PriceInterval.pricing_source_id).limit(1)
    price = db.execute(stmt).scalar_one_or_none()
    return None if price is None else _finite(float(price))


def window_occurrences(
    db: Session, site: Site, now: datetime.datetime, horizon: datetime.timedelta = DEFAULT_HORIZON
) -> list[WindowOccurrence]:
    """Every demand window occurrence that starts in [now - 24h, now + horizon],
    sorted by start. Each occurrence belongs to the tariff and window in
    force at its start."""
    now = as_utc(now)
    earliest, latest = now - LOOKBACK, now + horizon
    tz = site_tz(site.timezone)
    tariffs = site_tariffs_for_site(db, site.id)
    days = _local_dates(earliest, latest, tz)
    found = [
        occurrence
        for window in _demand_windows(db, tariffs)
        for day in days
        for occurrence in _day_occurrences(window, day, tz)
        if earliest <= occurrence.start <= latest and _applies(window, tariffs, occurrence.start)
    ]
    return sorted(found, key=lambda o: (o.start, o.window_id))


def _demand_windows(db: Session, tariffs: list[SiteTariff]) -> list[DemandWindow]:
    if not tariffs:
        return []
    stmt = select(DemandWindow).where(DemandWindow.site_tariff_id.in_([t.id for t in tariffs]))
    return list(db.execute(stmt.order_by(DemandWindow.id)).scalars())


def _local_dates(earliest: datetime.datetime, latest: datetime.datetime, tz: zoneinfo.ZoneInfo) -> list[datetime.date]:
    first, last = earliest.astimezone(tz).date(), latest.astimezone(tz).date()
    return [first + datetime.timedelta(days=n) for n in range((last - first).days + 1)]


def _applies(window: DemandWindow, tariffs: list[SiteTariff], at: datetime.datetime) -> bool:
    tariff = tariff_effective_at(tariffs, at)
    return tariff is not None and tariff.id == window.site_tariff_id and window_in_force(window, at)


def _day_occurrences(window: DemandWindow, day: datetime.date, tz: zoneinfo.ZoneInfo) -> list[WindowOccurrence]:
    spans = [span for span in (_span_starting(window, day), _morning_span(window, day)) if span is not None]
    occurrences = []
    for local_start, local_end in spans:
        start, end = _local_to_utc(local_start, tz, fold=0), _local_to_utc(local_end, tz, fold=1)
        # A span inside a DST gap collapses to nothing, as it does in billing.
        if end > start:
            occurrences.append(WindowOccurrence(window.id, window.label, start, end))
    return occurrences


def _span_starting(window: DemandWindow, day: datetime.date) -> tuple[datetime.datetime, datetime.datetime] | None:
    """The span that starts at the window's start time on `day`, if `day`
    matches its months and days of week. A window whose end time is before
    its start time runs into the next day, but in_demand_window_pattern
    checks each instant's own date, so the part after midnight only counts
    when the next day matches too. Otherwise the span stops at midnight."""
    start = datetime.datetime.combine(day, window.start_time)
    if not in_demand_window_pattern(window, start):
        return None
    if window.start_time <= window.end_time:
        return start, datetime.datetime.combine(day, window.end_time)
    next_midnight = datetime.datetime.combine(day + _ONE_DAY, _MIDNIGHT)
    if not in_demand_window_pattern(window, next_midnight):
        return start, next_midnight
    return start, datetime.datetime.combine(day + _ONE_DAY, window.end_time)


def _morning_span(window: DemandWindow, day: datetime.date) -> tuple[datetime.datetime, datetime.datetime] | None:
    """From midnight to the end time on `day`, for a cross-midnight window
    whose day matches but whose previous day did not, so no span starting
    the day before already covers it."""
    if window.start_time <= window.end_time:
        return None
    midnight = datetime.datetime.combine(day, _MIDNIGHT)
    previous_start = datetime.datetime.combine(day - _ONE_DAY, window.start_time)
    if not in_demand_window_pattern(window, midnight) or in_demand_window_pattern(window, previous_start):
        return None
    return midnight, datetime.datetime.combine(day, window.end_time)


def _local_to_utc(local: datetime.datetime, tz: zoneinfo.ZoneInfo, *, fold: int) -> datetime.datetime:
    """Convert a naive local wall-clock time to aware UTC.

    DST rules, chosen so an occurrence covers every instant billing counts:
    - A time that happens twice (clocks going back) uses `fold`: starts pass
      0 for the first instance, ends pass 1 for the second.
    - A time that never happens (clocks going forward) moves to the end of
      the gap, the first instant whose wall clock is at or after it.
      Billing never sees a wall time inside the gap, so a span that lies
      wholly inside it ends up with zero length."""
    candidate = local
    while not _exists(candidate, tz):
        candidate += datetime.timedelta(minutes=1)
    return candidate.replace(tzinfo=tz, fold=fold).astimezone(datetime.UTC)


def _exists(local: datetime.datetime, tz: zoneinfo.ZoneInfo) -> bool:
    round_trip = local.replace(tzinfo=tz).astimezone(datetime.UTC).astimezone(tz)
    return round_trip.replace(tzinfo=None) == local
