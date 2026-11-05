from __future__ import annotations

import datetime
import decimal

from core_api.actor_context import WindowOccurrence, build_context, window_occurrences
from core_api.models.billing import DemandWindow, SiteTariff
from core_api.models.identity import Site
from core_api.models.pricing import PriceChannel, PriceInterval
from core_api.models.readings import EnergyMetric, EnergyReading, WeatherMetric, WeatherReading
from core_api.models.sources import Source, SourceKind
from tests.conftest import UTC, sydney_local

ALL_DAYS = list(range(7))
ALL_MONTHS = list(range(1, 13))
EPOCH = datetime.datetime(2020, 1, 1, tzinfo=UTC)


def _tariff(db, site, effective_from=EPOCH, effective_to=None) -> SiteTariff:
    tariff = SiteTariff(site_id=site.id, effective_from=effective_from, effective_to=effective_to,
                        service_availability_c_per_day=decimal.Decimal("100"))
    db.add(tariff)
    db.commit()
    return tariff


def _window(db, tariff, start, end, *, label="Peak", months=ALL_MONTHS, days=ALL_DAYS, **dates) -> DemandWindow:
    window = DemandWindow(site_tariff_id=tariff.id, label=label, months=months, days_of_week=days,
                          start_time=datetime.time(*start), end_time=datetime.time(*end),
                          rate_c_per_kw_per_day=decimal.Decimal("50"), **dates)
    db.add(window)
    db.commit()
    return window


def _source(db, site, kind) -> Source:
    source = Source(site_id=site.id, kind=kind, provider="test", config={}, effective_from=EPOCH)
    db.add(source)
    db.commit()
    return source


def test_window_crossing_midnight(db, site):
    window = _window(db, _tariff(db, site), (22, 0), (6, 0), label="Overnight")
    now = sydney_local(2026, 1, 14, 12)  # Wednesday noon, Sydney summer time
    occurrences = window_occurrences(db, site, now, horizon=datetime.timedelta(hours=24))
    assert occurrences == [
        WindowOccurrence(window.id, "Overnight", sydney_local(2026, 1, 13, 22), sydney_local(2026, 1, 14, 6)),
        WindowOccurrence(window.id, "Overnight", sydney_local(2026, 1, 14, 22), sydney_local(2026, 1, 15, 6)),
    ]
    assert all(o.start.tzinfo is UTC and o.end.tzinfo is UTC for o in occurrences)


def test_window_crossing_midnight_on_one_day_matches_billing(db, site):
    # in_demand_window_pattern checks each instant's own weekday, so a
    # Friday-only 22:00-06:00 window bills Friday 00:00-06:00 and Friday
    # 22:00-24:00, never Saturday morning.
    window = _window(db, _tariff(db, site), (22, 0), (6, 0), days=[4])
    now = sydney_local(2026, 1, 16, 12)  # Friday noon
    occurrences = window_occurrences(db, site, now, horizon=datetime.timedelta(hours=24))
    assert [(o.start, o.end) for o in occurrences] == [
        (sydney_local(2026, 1, 16, 0), sydney_local(2026, 1, 16, 6)),
        (sydney_local(2026, 1, 16, 22), sydney_local(2026, 1, 17, 0)),
    ]
    assert window.id == occurrences[0].window_id


def _starting(occurrences, label, start):
    return next(o for o in occurrences if o.label == label and o.start == start)


def test_window_on_dst_change_day(db, site):
    tariff = _tariff(db, site)
    _window(db, tariff, (22, 0), (6, 0), label="Overnight")
    _window(db, tariff, (2, 30), (4, 0), label="Early")
    day = datetime.timedelta(hours=24)

    # 2026-10-04: clocks go from 02:00 AEST to 03:00 AEDT. The 22:00-06:00
    # span is 8 wall-clock hours but 7 real hours. 02:30 does not exist, so
    # the Early window starts when the gap ends, 03:00 AEDT (16:00 UTC).
    spring = window_occurrences(db, site, datetime.datetime(2026, 10, 4, 3, 0, tzinfo=UTC), horizon=day)
    overnight = _starting(spring, "Overnight", datetime.datetime(2026, 10, 3, 12, 0, tzinfo=UTC))
    assert overnight.end - overnight.start == datetime.timedelta(hours=7)
    early = _starting(spring, "Early", datetime.datetime(2026, 10, 3, 16, 0, tzinfo=UTC))
    assert early.end == datetime.datetime(2026, 10, 3, 17, 0, tzinfo=UTC)

    # 2026-04-05: clocks go from 03:00 AEDT back to 02:00 AEST. The span is
    # 9 real hours. 02:30 happens twice; a start takes the first (AEDT).
    autumn = window_occurrences(db, site, datetime.datetime(2026, 4, 5, 3, 0, tzinfo=UTC), horizon=day)
    overnight = _starting(autumn, "Overnight", datetime.datetime(2026, 4, 4, 11, 0, tzinfo=UTC))
    assert overnight.end - overnight.start == datetime.timedelta(hours=9)
    early = _starting(autumn, "Early", datetime.datetime(2026, 4, 4, 15, 30, tzinfo=UTC))
    assert early.end == datetime.datetime(2026, 4, 4, 18, 0, tzinfo=UTC)


def test_ambiguous_end_takes_the_later_instant(db, site):
    # 01:00-02:30 on 2026-04-05: billing matches 02:00-02:30 on both passes
    # of the clock, so the occurrence runs to the second 02:30 (AEST).
    _window(db, _tariff(db, site), (1, 0), (2, 30), months=[4], days=[6])
    occurrences = window_occurrences(db, site, datetime.datetime(2026, 4, 5, 3, 0, tzinfo=UTC))
    assert [(o.start, o.end) for o in occurrences] == [
        (datetime.datetime(2026, 4, 4, 14, 0, tzinfo=UTC), datetime.datetime(2026, 4, 4, 16, 30, tzinfo=UTC)),
    ]


def test_window_inside_dst_gap_is_dropped(db, site):
    # 02:15-02:45 on 2026-10-04 never shows on a Sydney clock, so billing
    # never matches it either.
    _window(db, _tariff(db, site), (2, 15), (2, 45), months=[10], days=[6])
    now = datetime.datetime(2026, 10, 4, 3, 0, tzinfo=UTC)
    assert window_occurrences(db, site, now) == []


def test_months_and_days_filter(db, site):
    tariff = _tariff(db, site)
    weekdays = _window(db, tariff, (15, 0), (21, 0), label="Summer weekdays", months=[11, 12, 1, 2, 3], days=[0, 1, 2, 3, 4])
    _window(db, tariff, (15, 0), (21, 0), label="Winter", months=[6, 7, 8])
    now = sydney_local(2026, 11, 6, 12)  # Friday
    occurrences = window_occurrences(db, site, now, horizon=datetime.timedelta(hours=96))
    assert occurrences == [
        WindowOccurrence(weekdays.id, "Summer weekdays", sydney_local(2026, 11, 5, 15), sydney_local(2026, 11, 5, 21)),
        WindowOccurrence(weekdays.id, "Summer weekdays", sydney_local(2026, 11, 6, 15), sydney_local(2026, 11, 6, 21)),
        WindowOccurrence(weekdays.id, "Summer weekdays", sydney_local(2026, 11, 9, 15), sydney_local(2026, 11, 9, 21)),
    ]


def test_months_filter_at_month_boundary(db, site):
    _window(db, _tariff(db, site), (15, 0), (21, 0), months=[11])
    now = sydney_local(2026, 11, 30, 16)
    occurrences = window_occurrences(db, site, now)
    # 1 and 2 December fall in the horizon but outside the window's months.
    assert [o.start for o in occurrences] == [sydney_local(2026, 11, 30, 15)]


def test_tariff_change_uses_window_in_force(db, site):
    change = sydney_local(2026, 11, 7)
    old = _tariff(db, site, effective_to=change)
    new = _tariff(db, site, effective_from=change)
    old_window = _window(db, old, (15, 0), (21, 0), label="Old")
    new_window = _window(db, new, (16, 0), (20, 0), label="New")
    retired = _window(db, new, (7, 0), (9, 0), label="Retired", effective_to=sydney_local(2026, 11, 8))
    now = sydney_local(2026, 11, 6, 12)
    assert window_occurrences(db, site, now) == [
        WindowOccurrence(old_window.id, "Old", sydney_local(2026, 11, 5, 15), sydney_local(2026, 11, 5, 21)),
        WindowOccurrence(old_window.id, "Old", sydney_local(2026, 11, 6, 15), sydney_local(2026, 11, 6, 21)),
        WindowOccurrence(retired.id, "Retired", sydney_local(2026, 11, 7, 7), sydney_local(2026, 11, 7, 9)),
        WindowOccurrence(new_window.id, "New", sydney_local(2026, 11, 7, 16), sydney_local(2026, 11, 7, 20)),
    ]


def test_occurrence_range_includes_last_24_hours(db, site):
    _window(db, _tariff(db, site), (15, 0), (21, 0))
    now = sydney_local(2026, 11, 6, 16)
    starts = [o.start for o in window_occurrences(db, site, now)]
    assert starts == [sydney_local(2026, 11, 6, 15), sydney_local(2026, 11, 7, 15), sydney_local(2026, 11, 8, 15)]


def test_no_tariff_gives_no_occurrences(db, site):
    assert window_occurrences(db, site, sydney_local(2026, 11, 6, 12)) == []


def test_context_values_none_when_stale(db, site):
    now = datetime.datetime(2026, 11, 5, 6, 0, tzinfo=UTC)
    stale = now - datetime.timedelta(minutes=16)
    weather = _source(db, site, SourceKind.WEATHER)
    energy = _source(db, site, SourceKind.ENERGY)
    pricing = _source(db, site, SourceKind.PRICING)
    db.add(WeatherReading(source_id=weather.id, ts=stale, metric=WeatherMetric.TEMP_C, value=21.5))
    db.add(EnergyReading(source_id=energy.id, ts=stale, metric=EnergyMetric.BATTERY_SOC_PCT, value=80.0))
    # The only buy interval ended before now.
    db.add(PriceInterval(pricing_source_id=pricing.id, channel=PriceChannel.BUY, interval_start=now - datetime.timedelta(minutes=30),
                         interval_end=now, price=decimal.Decimal("30")))
    db.commit()
    context = build_context(db, site, now)
    assert (context.outdoor_temp_c, context.battery_soc_pct, context.price_c_per_kwh) == (None, None, None)
    assert context.now == now
    assert context.timezone == "Australia/Sydney"
    assert context.windows == []


def test_context_values_none_without_sources(db, site):
    context = build_context(db, site, datetime.datetime(2026, 11, 5, 6, 0, tzinfo=UTC))
    assert (context.outdoor_temp_c, context.battery_soc_pct, context.price_c_per_kwh) == (None, None, None)


def test_context_picks_latest_weather_and_soc(db, site):
    now = datetime.datetime(2026, 11, 5, 6, 0, tzinfo=UTC)
    minutes = datetime.timedelta(minutes=1)
    weather = _source(db, site, SourceKind.WEATHER)
    energy = _source(db, site, SourceKind.ENERGY)
    pricing = _source(db, site, SourceKind.PRICING)
    db.add_all([
        WeatherReading(source_id=weather.id, ts=now - 10 * minutes, metric=WeatherMetric.TEMP_C, value=20.0),
        WeatherReading(source_id=weather.id, ts=now - 2 * minutes, metric=WeatherMetric.TEMP_C, value=24.5),
        WeatherReading(source_id=weather.id, ts=now - 1 * minutes, metric=WeatherMetric.HUMIDITY_PCT, value=60.0),
        EnergyReading(source_id=energy.id, ts=now - 5 * minutes, metric=EnergyMetric.BATTERY_SOC_PCT, value=50.0),
        EnergyReading(source_id=energy.id, ts=now - 1 * minutes, metric=EnergyMetric.BATTERY_SOC_PCT, value=55.0),
        EnergyReading(source_id=energy.id, ts=now, metric=EnergyMetric.PRODUCTION_W, value=3000.0),
        PriceInterval(pricing_source_id=pricing.id, channel=PriceChannel.BUY, interval_start=now - 3 * minutes,
                      interval_end=now + 2 * minutes, price=decimal.Decimal("32.5")),
        PriceInterval(pricing_source_id=pricing.id, channel=PriceChannel.SELL, interval_start=now - 3 * minutes,
                      interval_end=now + 2 * minutes, price=decimal.Decimal("5")),
    ])
    db.commit()
    context = build_context(db, site, now)
    assert context.outdoor_temp_c == 24.5
    assert context.battery_soc_pct == 55.0
    assert context.price_c_per_kwh == 32.5


def test_context_ignores_readings_after_now_and_other_sites(db, site, user):
    now = datetime.datetime(2026, 11, 5, 6, 0, tzinfo=UTC)
    other = Site(owner_user_id=user.id, name="Other", timezone="Australia/Sydney")
    db.add(other)
    db.commit()
    mine = _source(db, site, SourceKind.WEATHER)
    theirs = _source(db, other, SourceKind.WEATHER)
    db.add_all([
        WeatherReading(source_id=mine.id, ts=now - datetime.timedelta(minutes=1), metric=WeatherMetric.TEMP_C, value=18.0),
        WeatherReading(source_id=mine.id, ts=now + datetime.timedelta(minutes=1), metric=WeatherMetric.TEMP_C, value=99.0),
        WeatherReading(source_id=theirs.id, ts=now, metric=WeatherMetric.TEMP_C, value=30.0),
    ])
    db.commit()
    assert build_context(db, site, now).outdoor_temp_c == 18.0


def test_context_non_finite_reading_is_none(db, site):
    now = datetime.datetime(2026, 11, 5, 6, 0, tzinfo=UTC)
    weather = _source(db, site, SourceKind.WEATHER)
    db.add(WeatherReading(source_id=weather.id, ts=now, metric=WeatherMetric.TEMP_C, value=float("inf")))
    db.commit()
    assert build_context(db, site, now).outdoor_temp_c is None
