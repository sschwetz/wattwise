from __future__ import annotations

import datetime
import decimal

from core_api.jobs.demand_config import (
    any_configured_demand_window,
    effective_site_tariff_at,
    energy_source_ids_for_site,
    export_adjustment_windows_by_tariff,
    first_matching_demand_window,
    pricing_source_ids_for_site,
    price_interval_flags_demand,
    total_export_adjustment_c_per_kwh,
)
from core_api.models.billing import DemandWindow, ExportAdjustmentWindow, SiteTariff
from core_api.models.pricing import PriceChannel, PriceInterval
from core_api.models.sources import Source, SourceKind
from core_api.time_utils import as_utc
from tests.conftest import SYDNEY, UTC, sydney_local

# conftest's `site` fixture is Australia/Sydney -- deliberately kept (not
# overridden to UTC) so these tests exercise the real local<->UTC
# conversion in demand_config.py, not a no-op. AEDT (Sydney's southern
# summer offset) is UTC+11.


def make_tariff(db, site, effective_from, effective_to=None, rate="40.0"):
    tariff = SiteTariff(
        site_id=site.id, effective_from=effective_from, effective_to=effective_to,
        service_availability_c_per_day="80.0",
    )
    db.add(tariff)
    db.commit()
    return tariff


def make_window(db, tariff, label="Peak", months=None, days_of_week=None, start="15:00", end="21:00", rate="45.0", minutes=30):
    window = DemandWindow(
        site_tariff_id=tariff.id,
        label=label,
        months=months or list(range(1, 13)),
        days_of_week=days_of_week if days_of_week is not None else [0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time.fromisoformat(start),
        end_time=datetime.time.fromisoformat(end),
        rate_c_per_kw_per_day=rate,
        measurement_interval_minutes=minutes,
    )
    db.add(window)
    db.commit()
    return window


def make_export_adjustment(
    db, tariff, label="Solar soak bonus", months=None, days_of_week=None, start="10:00", end="15:00", adjustment="5.0"
):
    window = ExportAdjustmentWindow(
        site_tariff_id=tariff.id,
        label=label,
        months=months or list(range(1, 13)),
        days_of_week=days_of_week if days_of_week is not None else [0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time.fromisoformat(start),
        end_time=datetime.time.fromisoformat(end),
        adjustment_c_per_kwh=adjustment,
    )
    db.add(window)
    db.commit()
    return window


def test_as_utc_attaches_utc_to_naive_value():
    naive = datetime.datetime(2026, 1, 1, 12, 0)
    assert as_utc(naive) == datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_as_utc_leaves_aware_value_unchanged():
    aware = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert as_utc(aware) is aware or as_utc(aware) == aware


def test_effective_site_tariff_at_picks_non_retroactive_tariff(db, site):
    old = make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC), datetime.datetime(2026, 6, 1, tzinfo=UTC))
    new = make_tariff(db, site, datetime.datetime(2026, 6, 1, tzinfo=UTC), None)

    assert effective_site_tariff_at(db, site.id, datetime.datetime(2026, 3, 1, tzinfo=UTC)).id == old.id
    assert effective_site_tariff_at(db, site.id, datetime.datetime(2026, 9, 1, tzinfo=UTC)).id == new.id


def test_first_matching_demand_window_at_pattern_boundary_is_inclusive(db, site):
    """`at` is passed in as a UTC instant (as the job code always does); the
    window's 15:00-21:00 bounds are Sydney *local* time, so this only
    passes if first_matching_demand_window actually converts before
    comparing -- a UTC-timezone site fixture couldn't catch a regression
    here, since the conversion would be a no-op either way."""
    tariff = make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC))
    make_window(db, tariff, start="15:00", end="21:00")

    at_open = sydney_local(2026, 1, 5, 15, 0)
    at_close = sydney_local(2026, 1, 5, 21, 0)
    just_before = sydney_local(2026, 1, 5, 14, 59)

    assert first_matching_demand_window(db, site.id, at_open, site.timezone) is not None
    assert first_matching_demand_window(db, site.id, at_close, site.timezone) is not None
    assert first_matching_demand_window(db, site.id, just_before, site.timezone) is None


def test_first_matching_demand_window_rejects_wrong_month_or_day(db, site):
    tariff = make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC))
    make_window(db, tariff, months=[12, 1, 2], days_of_week=[0, 1, 2, 3, 4])

    # 2026-01-05 is a Monday in January (Sydney local date) -> matches.
    assert first_matching_demand_window(db, site.id, sydney_local(2026, 1, 5, 17, 0), site.timezone)
    # 2026-07-05 is in July -> wrong month.
    assert first_matching_demand_window(db, site.id, sydney_local(2026, 7, 5, 17, 0), site.timezone) is None
    # 2026-01-10 is a Saturday -> wrong day-of-week.
    assert first_matching_demand_window(db, site.id, sydney_local(2026, 1, 10, 17, 0), site.timezone) is None


def test_first_matching_demand_window_first_match_wins(db, site):
    tariff = make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC))
    first = make_window(db, tariff, label="First", rate="10.0")
    make_window(db, tariff, label="Second", rate="99.0")

    found = first_matching_demand_window(db, site.id, sydney_local(2026, 1, 5, 17, 0), site.timezone)
    assert found.id == first.id


def test_any_configured_demand_window_ignores_pattern(db, site):
    tariff = make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC))
    window = make_window(db, tariff, months=[6], days_of_week=[0])  # won't match a January Monday evening

    found = any_configured_demand_window(db, site.id, sydney_local(2026, 1, 5, 17, 0))
    assert found.id == window.id


def test_no_tariff_configured_returns_none(db, site):
    assert effective_site_tariff_at(db, site.id, datetime.datetime(2026, 1, 1, tzinfo=UTC)) is None
    assert first_matching_demand_window(db, site.id, datetime.datetime(2026, 1, 1, tzinfo=UTC), site.timezone) is None
    assert any_configured_demand_window(db, site.id, datetime.datetime(2026, 1, 1, tzinfo=UTC)) is None


def test_source_id_helpers_filter_by_kind(db, site):
    db.add(Source(site_id=site.id, kind=SourceKind.ENERGY, provider="envoy", config={}, effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC)))
    db.add(Source(site_id=site.id, kind=SourceKind.PRICING, provider="amber", config={}, effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC)))
    db.commit()

    assert len(energy_source_ids_for_site(db, site.id)) == 1
    assert len(pricing_source_ids_for_site(db, site.id)) == 1


def test_total_export_adjustment_c_per_kwh_sums_matching_windows(db, site):
    tariff = make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC))
    make_export_adjustment(db, tariff, label="Bonus", adjustment="5.0")
    make_export_adjustment(db, tariff, label="Extra bonus", adjustment="2.5")

    windows = export_adjustment_windows_by_tariff(db, site.id)[tariff.id]
    at_local = datetime.datetime(2026, 1, 5, 12, 0, tzinfo=SYDNEY)
    assert total_export_adjustment_c_per_kwh(windows, at_local) == decimal.Decimal("7.5")


def test_total_export_adjustment_c_per_kwh_ignores_non_matching_pattern(db, site):
    tariff = make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC))
    make_export_adjustment(db, tariff, start="10:00", end="15:00", adjustment="5.0")

    windows = export_adjustment_windows_by_tariff(db, site.id)[tariff.id]
    outside_window = datetime.datetime(2026, 1, 5, 20, 0, tzinfo=SYDNEY)
    assert total_export_adjustment_c_per_kwh(windows, outside_window) == decimal.Decimal("0")


def test_export_adjustment_windows_by_tariff_groups_by_tariff(db, site):
    tariff = make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC))
    make_export_adjustment(db, tariff)

    grouped = export_adjustment_windows_by_tariff(db, site.id)
    assert list(grouped) == [tariff.id]
    assert len(grouped[tariff.id]) == 1


def test_export_adjustment_windows_by_tariff_empty_when_none_configured(db, site):
    make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC))
    assert export_adjustment_windows_by_tariff(db, site.id) == {}


def test_price_interval_flags_demand_true_when_covering_interval_exists(db, site):
    source = Source(site_id=site.id, kind=SourceKind.PRICING, provider="amber", config={}, effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC))
    db.add(source)
    db.commit()
    start = datetime.datetime(2026, 1, 5, 17, 0, tzinfo=UTC)
    db.add(PriceInterval(
        pricing_source_id=source.id, channel=PriceChannel.DEMAND, interval_start=start,
        interval_end=start + datetime.timedelta(minutes=30), price="0.0", is_demand_window=True,
    ))
    db.commit()

    bucket_start = start
    bucket_end = start + datetime.timedelta(minutes=5)
    assert price_interval_flags_demand(db, [source.id], bucket_start, bucket_end) is True
    assert price_interval_flags_demand(db, [source.id], start - datetime.timedelta(minutes=5), start) is False
