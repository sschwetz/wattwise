# tests/test_cost.py
from __future__ import annotations

import datetime
import decimal

from core_api.models.billing import (
    BillingCycle,
    ConditionalCreditWindow,
    DemandCharge,
    DemandInterval,
    DemandWindow,
    ExportAdjustmentWindow,
    MonthlyFeeDiscountWindow,
    SiteTariff,
)
from core_api.models.identity import Site, User
from core_api.models.pricing import PriceChannel, PriceReading
from core_api.models.readings import EnergyMetric, EnergyReading
from core_api.models.sources import Source, SourceKind
from core_api.cost import compute_cost, compute_cost_series, monthly_fee_discounts_c
from core_api.export_allowance_data import marginal_adjustment_fn
from core_api.jobs.demand_config import export_adjustment_windows_by_tariff, site_tariffs_for_site
from tests.conftest import sydney_local

UTC = datetime.timezone.utc


def _make_tariff(
    db, site, effective_from, effective_to=None, service_c_per_day="95.0", monthly_fee_c=None
) -> SiteTariff:
    tariff = SiteTariff(
        site_id=site.id,
        effective_from=effective_from,
        effective_to=effective_to,
        service_availability_c_per_day=decimal.Decimal(service_c_per_day),
        monthly_fee_c=decimal.Decimal(monthly_fee_c) if monthly_fee_c is not None else None,
    )
    db.add(tariff)
    db.commit()
    return tariff


def _make_billing_cycle(db, site, effective_from, cycle_months=1, anchor_day=1) -> BillingCycle:
    cycle = BillingCycle(
        site_id=site.id,
        effective_from=effective_from,
        cycle_months=cycle_months,
        anchor_day=anchor_day,
    )
    db.add(cycle)
    db.commit()
    return cycle


def _make_monthly_fee_discount(db, tariff, starts_at, ends_at, discounted_fee_c, label="Promo") -> MonthlyFeeDiscountWindow:
    window = MonthlyFeeDiscountWindow(
        site_tariff_id=tariff.id,
        label=label,
        starts_at=starts_at,
        ends_at=ends_at,
        discounted_fee_c=decimal.Decimal(discounted_fee_c),
    )
    db.add(window)
    db.commit()
    return window


def _make_export_adjustment(
    db, tariff, adjustment_c_per_kwh="5.0", months=None, days_of_week=None, start="00:00", end="23:59",
    daily_allowance_kwh="0",
) -> ExportAdjustmentWindow:
    window = ExportAdjustmentWindow(
        site_tariff_id=tariff.id,
        label="Test adjustment",
        months=months or list(range(1, 13)),
        days_of_week=days_of_week if days_of_week is not None else [0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time.fromisoformat(start),
        end_time=datetime.time.fromisoformat(end),
        adjustment_c_per_kwh=decimal.Decimal(adjustment_c_per_kwh),
        daily_allowance_kwh=decimal.Decimal(daily_allowance_kwh),
    )
    db.add(window)
    db.commit()
    return window


def _make_demand_window(
    db, tariff, label="Test demand window", months=None, days_of_week=None, start="00:00", end="23:59", rate="45.0"
) -> DemandWindow:
    window = DemandWindow(
        site_tariff_id=tariff.id,
        label=label,
        months=months or list(range(1, 13)),
        days_of_week=days_of_week if days_of_week is not None else [0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time.fromisoformat(start),
        end_time=datetime.time.fromisoformat(end),
        rate_c_per_kw_per_day=decimal.Decimal(rate),
        measurement_interval_minutes=30,
    )
    db.add(window)
    db.commit()
    return window


def _make_demand_charge(
    db,
    site,
    billing_period_start,
    billing_period_end,
    peak_interval_start=None,
    charge_amount="24.0",
    is_estimated=False,
) -> DemandCharge:
    peak_start = peak_interval_start or billing_period_start
    db.add(
        DemandInterval(
            site_id=site.id,
            interval_start=peak_start,
            interval_end=peak_start + datetime.timedelta(minutes=5),
            rolling_sum_w=1000.0,
            is_demand_window=True,
            rate_applied=decimal.Decimal("45.0"),
        )
    )
    db.commit()
    charge = DemandCharge(
        site_id=site.id,
        billing_period_start=billing_period_start,
        billing_period_end=billing_period_end,
        peak_interval_start=peak_start,
        peak_avg_import_w=1000.0,
        rate_applied=decimal.Decimal("45.0"),
        charge_amount=decimal.Decimal(charge_amount),
        is_estimated=is_estimated,
    )
    db.add(charge)
    db.commit()
    return charge


def _make_energy_source(db, site, kind_metrics_ok=True) -> Source:
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def _make_pricing_source(db, site, provider="amber") -> Source:
    source = Source(
        site_id=site.id,
        kind=SourceKind.PRICING,
        provider=provider,
        config={},
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def test_compute_cost_net_method(db, site):
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    # 1 minute at 600W import (0.01 kWh) and 300W export (0.005 kWh).
    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=300.0))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.BUY, value=decimal.Decimal("30.0")))
    # Amber's raw feed-in sign: negative means the customer is paid 10c/kWh.
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-10.0")))
    db.commit()

    result = compute_cost(
        db, site.id, ts, ts + datetime.timedelta(minutes=1), method="net", site_timezone=site.timezone
    )

    # import cost = 0.01 kWh * 30 c/kWh = 0.3c; export revenue = 0.005 kWh * 10 c/kWh = 0.05c
    assert result.total_cost_c == decimal.Decimal("0.25")
    assert result.minutes_missing_price == 0


def test_compute_cost_with_a_local_source_adds_the_negative_sell_price(db, site):
    """The local collector pushes `sell` negative, like Amber, so cost math is
    identical for both providers."""
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site, provider="local")
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=300.0))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.BUY, value=decimal.Decimal("30.0")))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-10.0")))
    db.commit()

    result = compute_cost(
        db, site.id, ts, ts + datetime.timedelta(minutes=1), method="net", site_timezone=site.timezone
    )

    assert result.total_cost_c == decimal.Decimal("0.25")


def test_compute_cost_is_negative_when_net_exporting_with_realistic_sell_price(db, site):
    """Regression: Amber stores the feed-in (sell) price with its raw sign --
    negative in the normal case, since the customer is paid to export (see
    webapp's CurrentPriceLine comment on this same convention). A site mostly
    exporting must show net-negative cost (revenue), not positive."""
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    # Barely importing 60W (0.001 kWh) while exporting 6000W (0.1 kWh).
    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=60.0))
    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=6000.0))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.BUY, value=decimal.Decimal("30.0")))
    # Normal Amber feed-in: raw value is negative -- the customer is paid 8c/kWh.
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-8.0")))
    db.commit()

    result = compute_cost(
        db, site.id, ts, ts + datetime.timedelta(minutes=1), method="net", site_timezone=site.timezone
    )

    # import cost = 0.001 kWh * 30c/kWh = 0.03c; export revenue = 0.1 kWh * 8c/kWh = 0.8c paid to the customer.
    assert result.total_cost_c == decimal.Decimal("-0.77")


def test_compute_cost_flags_minutes_missing_price(db, site):
    energy_source = _make_energy_source(db, site)
    _make_pricing_source(db, site)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
    db.commit()
    # No PriceReading at all for this minute.

    result = compute_cost(
        db, site.id, ts, ts + datetime.timedelta(minutes=1), method="net", site_timezone=site.timezone
    )
    assert result.minutes_missing_price == 1
    assert result.total_cost_c == decimal.Decimal("0")


def test_compute_cost_gross_method_uses_production_and_consumption(db, site):
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.CONSUMPTION_W, value=1200.0))
    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.PRODUCTION_W, value=900.0))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.BUY, value=decimal.Decimal("20.0")))
    # Amber's raw feed-in sign: negative means the customer is paid 8c/kWh.
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-8.0")))
    db.commit()

    result = compute_cost(
        db, site.id, ts, ts + datetime.timedelta(minutes=1), method="gross", site_timezone=site.timezone
    )

    # consumption cost = (1200/60000)*20 = 0.4c; production revenue = (900/60000)*8 = 0.12c
    assert result.total_cost_c == decimal.Decimal("0.28")


def test_compute_cost_matches_prices_fanned_out_from_staggered_interval(db, site):
    """Final review Critical finding: the price side is built by pushing a real
    provider-shaped PriceInterval (staggered :01 interval_start) through the actual
    ingestion fan-out, while the energy side is written at independently-chosen whole
    minutes. The existing cost tests copy the same `ts` object into both sides, so they
    cannot catch a systematic offset between the two."""
    from core_api.routers.ingestion import _fan_out_price_readings, _upsert_price_interval
    from core_api.schemas import PriceIntervalIn

    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)

    # Price side: exactly what Amber publishes -- a 5-minute interval starting at :01s.
    # The feed-in (sell) price is negative, Amber's raw sign for "customer is paid".
    for channel, price in ((PriceChannel.BUY, "30.0"), (PriceChannel.SELL, "-10.0")):
        interval = PriceIntervalIn(
            channel=channel,
            interval_start=datetime.datetime(2026, 1, 1, 23, 55, 1, tzinfo=UTC),
            interval_end=datetime.datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC),
            price=decimal.Decimal(price),
        )
        _upsert_price_interval(db, pricing_source.id, interval)
        assert _fan_out_price_readings(db, pricing_source.id, interval) == 5
    db.commit()

    # Energy side: whole-minute timestamps written independently of the price interval.
    for minute in (55, 56):
        ts = datetime.datetime(2026, 1, 1, 23, minute, tzinfo=UTC)
        db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
        db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=300.0))
    db.commit()

    result = compute_cost(
        db,
        site.id,
        datetime.datetime(2026, 1, 1, 23, 55, tzinfo=UTC),
        datetime.datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
        method="net",
        site_timezone=site.timezone,
    )

    assert result.minutes_missing_price == 0
    # Per minute: 0.01 kWh * 30c - 0.005 kWh * 10c = 0.25c, over two minutes.
    assert result.total_cost_c == decimal.Decimal("0.50")


def test_compute_cost_export_incentive_increases_export_revenue(db, site):
    """A positive adjustment_c_per_kwh is an export incentive -- it must make
    a matching minute's export revenue bigger (more negative cost_c), not
    smaller, honoring Amber's raw sell-price sign convention."""
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=6000.0))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-8.0")))
    db.commit()

    tariff = _make_tariff(db, site, effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC), service_c_per_day="0.0")
    _make_export_adjustment(db, tariff, adjustment_c_per_kwh="5.0")

    result = compute_cost(
        db, site.id, ts, ts + datetime.timedelta(minutes=1), method="net", site_timezone=site.timezone
    )

    # 0.1 kWh exported at (8 + 5)c/kWh paid -> -1.3c (revenue).
    assert result.total_cost_c == decimal.Decimal("-1.3")


def test_compute_cost_export_disincentive_reduces_export_revenue(db, site):
    """A negative adjustment_c_per_kwh is an export disincentive -- it must
    shrink export revenue."""
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=6000.0))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-8.0")))
    db.commit()

    tariff = _make_tariff(db, site, effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC), service_c_per_day="0.0")
    _make_export_adjustment(db, tariff, adjustment_c_per_kwh="-5.0")

    result = compute_cost(
        db, site.id, ts, ts + datetime.timedelta(minutes=1), method="net", site_timezone=site.timezone
    )

    # 0.1 kWh exported at (8 - 5)c/kWh paid -> -0.3c (revenue).
    assert result.total_cost_c == decimal.Decimal("-0.3")


def test_compute_cost_export_adjustment_only_applies_within_its_pattern(db, site):
    """The site fixture is Australia/Sydney (AEDT, UTC+11 in January) -- this
    minute is 23:00 local, outside the adjustment's 06:00-10:00 window, so no
    adjustment should apply."""
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=6000.0))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-8.0")))
    db.commit()

    tariff = _make_tariff(db, site, effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC), service_c_per_day="0.0")
    _make_export_adjustment(db, tariff, adjustment_c_per_kwh="5.0", start="06:00", end="10:00")

    result = compute_cost(
        db, site.id, ts, ts + datetime.timedelta(minutes=1), method="net", site_timezone=site.timezone
    )

    # No adjustment applied: 0.1 kWh exported at 8c/kWh paid -> -0.8c.
    assert result.total_cost_c == decimal.Decimal("-0.8")


def test_compute_cost_series_includes_export_adjustment(db, site):
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=6000.0))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-8.0")))
    db.commit()

    tariff = _make_tariff(db, site, effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC), service_c_per_day="0.0")
    _make_export_adjustment(db, tariff, adjustment_c_per_kwh="5.0")

    points = compute_cost_series(
        db, site.id, ts, ts + datetime.timedelta(minutes=1), method="net", bucket="hour", site_timezone=site.timezone
    )

    assert [p.cost_c for p in points] == [decimal.Decimal("-1.3")]


def _add_export_kwh(db, energy_source, pricing_source, ts, kwh="1", with_price=True):
    """One minute of grid export totalling `kwh` (60000 W for one minute is 1 kWh)."""
    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=float(kwh) * 60000))
    if with_price:
        db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("0")))
    db.commit()


def _allowance_setup(db, site, adjustment="-10", allowance="1", start="00:00", end="23:59", with_cycle=True):
    """A Sydney billing period of 1 Jan - 1 Feb 2026 and one export window
    charging 10c/kWh beyond 1 kWh/day (unless overridden)."""
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    if with_cycle:
        _make_billing_cycle(db, site, sydney_local(2026, 1, 1))
    tariff = _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0")
    _make_export_adjustment(
        db, tariff, adjustment_c_per_kwh=adjustment, daily_allowance_kwh=allowance, start=start, end=end
    )
    return energy_source, pricing_source


def _three_kwh_on_day_one(db, energy_source, pricing_source):
    noon = sydney_local(2026, 1, 1, 12, 0)
    for minute in range(3):
        _add_export_kwh(db, energy_source, pricing_source, noon + datetime.timedelta(minutes=minute))


def _cost(db, site, start, end) -> decimal.Decimal:
    return compute_cost(db, site.id, start, end, method="net", site_timezone=site.timezone).total_cost_c


def test_allowance_overage_is_charged_then_cancelled_by_later_days(db, site):
    energy_source, pricing_source = _allowance_setup(db, site)
    _three_kwh_on_day_one(db, energy_source, pricing_source)
    start = sydney_local(2026, 1, 1)

    # Day 1: 3 kWh exported, 1 kWh allowed -> 2 kWh charged at 10c.
    assert _cost(db, site, start, sydney_local(2026, 1, 2)) == decimal.Decimal("20")
    # Day 2 adds 1 kWh of allowance, which cancels 1 kWh of the day-1 charge.
    assert _cost(db, site, start, sydney_local(2026, 1, 3)) == decimal.Decimal("10")
    # Day 3 cancels the rest.
    assert _cost(db, site, start, sydney_local(2026, 1, 4)) == decimal.Decimal("0")


def test_allowance_cost_over_a_sub_range_is_the_difference_of_its_ends(db, site):
    energy_source, pricing_source = _allowance_setup(db, site)
    _three_kwh_on_day_one(db, energy_source, pricing_source)

    # Charged kWh is 1 just before 12:02 and 0 at the start of 3 Jan, but the
    # end is exclusive, so the 3 Jan refund is not in the range: no change.
    assert _cost(db, site, sydney_local(2026, 1, 1, 12, 2), sydney_local(2026, 1, 3)) == decimal.Decimal("0")


def test_allowance_balance_resets_at_the_next_billing_period(db, site):
    energy_source, pricing_source = _allowance_setup(db, site)
    last_day = sydney_local(2026, 1, 31, 12, 0)
    for minute in range(40):
        _add_export_kwh(db, energy_source, pricing_source, last_day + datetime.timedelta(minutes=minute))

    # 40 kWh against 31 days of allowance leaves 9 kWh charged, and the
    # new period on 1 Feb neither refunds nor repeats it.
    assert _cost(db, site, sydney_local(2026, 1, 31), sydney_local(2026, 2, 3)) == decimal.Decimal("90")


def test_allowance_window_without_a_billing_cycle_adjusts_every_kwh(db, site):
    energy_source, pricing_source = _allowance_setup(db, site, with_cycle=False)
    _add_export_kwh(db, energy_source, pricing_source, sydney_local(2026, 1, 1, 12, 0))

    assert _cost(db, site, sydney_local(2026, 1, 1), sydney_local(2026, 1, 2)) == decimal.Decimal("10")


def test_allowance_only_counts_exports_inside_the_window_pattern(db, site):
    energy_source, pricing_source = _allowance_setup(db, site, start="10:00", end="15:00")
    noon = sydney_local(2026, 1, 1, 12, 0)
    _add_export_kwh(db, energy_source, pricing_source, noon)
    _add_export_kwh(db, energy_source, pricing_source, noon + datetime.timedelta(minutes=1))
    _add_export_kwh(db, energy_source, pricing_source, sydney_local(2026, 1, 1, 20, 0))

    # 2 kWh inside the window, 1 allowed -> 1 kWh charged. The 20:00 export is outside it.
    assert _cost(db, site, sydney_local(2026, 1, 1), sydney_local(2026, 1, 2)) == decimal.Decimal("10")


def test_allowance_counts_export_minutes_that_have_no_sell_price(db, site):
    energy_source, pricing_source = _allowance_setup(db, site)
    noon = sydney_local(2026, 1, 1, 12, 0)
    for minute in range(2):
        _add_export_kwh(db, energy_source, pricing_source, noon + datetime.timedelta(minutes=minute), with_price=False)

    result = compute_cost(
        db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 1, 2), method="net", site_timezone=site.timezone
    )

    assert result.minutes_missing_price == 2
    assert result.total_cost_c == decimal.Decimal("10")


def _two_tariff_allowance_setup(db, site):
    """Tariff A runs 1 Jan - 10 Jan 2026 and tariff B starts on 10 Jan, both
    charging 10c/kWh beyond 1 kWh/day, in one billing period from 1 Jan."""
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    _make_billing_cycle(db, site, sydney_local(2026, 1, 1))
    tariff_a = _make_tariff(
        db, site, effective_from=sydney_local(2026, 1, 1), effective_to=sydney_local(2026, 1, 10), service_c_per_day="0.0"
    )
    tariff_b = _make_tariff(db, site, effective_from=sydney_local(2026, 1, 10), service_c_per_day="0.0")
    for tariff in (tariff_a, tariff_b):
        _make_export_adjustment(db, tariff, adjustment_c_per_kwh="-10", daily_allowance_kwh="1")
    return energy_source, pricing_source


def _add_noon_exports(db, energy_source, pricing_source, year, month, day, kwh):
    noon = sydney_local(year, month, day, 12, 0)
    for minute in range(kwh):
        _add_export_kwh(db, energy_source, pricing_source, noon + datetime.timedelta(minutes=minute))


def test_allowance_charge_of_an_ended_tariff_is_not_refunded_after_it_ends(db, site):
    energy_source, pricing_source = _two_tariff_allowance_setup(db, site)
    _add_noon_exports(db, energy_source, pricing_source, 2026, 1, 9, 20)

    # A's window: 20 kWh exported by day 9 against 9 kWh of allowance -> 11 kWh
    # at 10c = 110c. A ends at the start of 10 Jan, so later midnights refund nothing.
    assert _cost(db, site, sydney_local(2026, 1, 1), sydney_local(2026, 1, 10)) == decimal.Decimal("110")
    assert _cost(db, site, sydney_local(2026, 1, 1), sydney_local(2026, 2, 1)) == decimal.Decimal("110")


def test_allowance_of_a_new_tariff_counts_days_from_its_own_start(db, site):
    energy_source, pricing_source = _two_tariff_allowance_setup(db, site)
    _add_noon_exports(db, energy_source, pricing_source, 2026, 1, 9, 20)
    _add_noon_exports(db, energy_source, pricing_source, 2026, 1, 11, 20)

    # B's window starts on 10 Jan, so 11 Jan is its day 2: 20 - 2 = 18 kWh at
    # 10c = 180c, on top of A's 110c. Range ends before the 12 Jan midnight.
    assert _cost(db, site, sydney_local(2026, 1, 1), sydney_local(2026, 1, 12)) == decimal.Decimal("290")


def test_marginal_price_of_a_new_tariff_uses_its_own_day_count(db, site):
    energy_source, pricing_source = _two_tariff_allowance_setup(db, site)
    _add_noon_exports(db, energy_source, pricing_source, 2026, 1, 10, 3)
    start, end, now = sydney_local(2026, 1, 1), sydney_local(2026, 2, 1), sydney_local(2026, 1, 12)

    adjustment_at = marginal_adjustment_fn(
        db, site.id, site.timezone, site_tariffs_for_site(db, site.id),
        export_adjustment_windows_by_tariff(db, site.id), start, end, now,
    )

    # B has exported 3 kWh. On 11 Jan (its day 2) it has 2 kWh of allowance, so
    # the next kWh is charged; on 14 Jan (day 5) it has 5 kWh, so it is not.
    assert adjustment_at(sydney_local(2026, 1, 11, 18, 0)) == decimal.Decimal("-10")
    assert adjustment_at(sydney_local(2026, 1, 14, 18, 0)) == decimal.Decimal("0")


def test_allowance_cost_stops_at_now_for_a_range_ending_in_the_future(db, site):
    energy_source, pricing_source = _allowance_setup(db, site)
    _add_noon_exports(db, energy_source, pricing_source, 2026, 1, 1, 20)
    start, end = sydney_local(2026, 1, 1), sydney_local(2026, 2, 1)

    def cost_at(now):
        return compute_cost(db, site.id, start, end, method="net", site_timezone=site.timezone, now=now).total_cost_c

    # 20 kWh against 5 days of allowance leaves 15 kWh at 10c as of 5 Jan.
    assert cost_at(sydney_local(2026, 1, 5)) == decimal.Decimal("150")
    # Past the period end the 20 kWh has been fully cancelled (31 days).
    assert cost_at(sydney_local(2026, 2, 2)) == decimal.Decimal("0")


def test_compute_cost_series_books_no_refund_after_now(db, site):
    energy_source, pricing_source = _allowance_setup(db, site)
    _add_noon_exports(db, energy_source, pricing_source, 2026, 1, 1, 20)
    now = sydney_local(2026, 1, 5)

    points = compute_cost_series(
        db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 2, 1), method="net", bucket="hour",
        site_timezone=site.timezone, now=now,
    )

    # 190c charged at noon on 1 Jan, then one 10c refund at each midnight up to 5 Jan.
    assert [p.cost_c for p in points] == [decimal.Decimal(c) for c in ("190", "-10", "-10", "-10", "-10")]
    assert all(p.bucket_start <= now for p in points)


def test_compute_cost_series_shows_the_charge_and_the_midnight_refund(db, site):
    energy_source, pricing_source = _allowance_setup(db, site)
    _three_kwh_on_day_one(db, energy_source, pricing_source)

    points = compute_cost_series(
        db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 1, 3), method="net", bucket="hour",
        site_timezone=site.timezone,
    )

    assert [(p.bucket_start, p.cost_c) for p in points] == [
        (datetime.datetime(2026, 1, 1, 1, 0, tzinfo=UTC), decimal.Decimal("20")),
        (datetime.datetime(2026, 1, 1, 13, 0, tzinfo=UTC), decimal.Decimal("-10")),
    ]


def test_compute_cost_includes_prorated_share_of_estimated_demand_charge(db, site):
    """The total must include a demand charge's best-known amount even
    before it's finalized -- prorated to the queried sub-range, not dumped
    in full, since the charge covers the whole billing period."""
    tariff = _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0")
    _make_demand_window(db, tariff)  # matches every moment
    period_start = sydney_local(2026, 1, 1, 0, 0)
    period_end = sydney_local(2026, 1, 1, 1, 0)  # 1 hour = twelve 5-minute slots
    _make_demand_charge(db, site, period_start, period_end, charge_amount="12.0", is_estimated=True)

    result = compute_cost(
        db,
        site.id,
        period_start,
        period_start + datetime.timedelta(minutes=30),  # six of the twelve slots
        method="net",
        site_timezone=site.timezone,
    )

    # 12c spread over 12 slots = 1c/slot; six slots in range = 6c.
    assert result.total_cost_c == decimal.Decimal("6.0")


def test_compute_cost_demand_charge_contribution_prorates_to_query_subrange(db, site):
    tariff = _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0")
    _make_demand_window(db, tariff)
    period_start = sydney_local(2026, 1, 1, 0, 0)
    period_end = sydney_local(2026, 1, 1, 2, 0)  # 2 hours = twenty-four 5-minute slots
    _make_demand_charge(db, site, period_start, period_end, charge_amount="24.0", is_estimated=False)

    result = compute_cost(db, site.id, period_start, period_end, method="net", site_timezone=site.timezone)
    assert result.total_cost_c == decimal.Decimal("24.0")

    half_range_result = compute_cost(
        db, site.id, period_start, period_start + datetime.timedelta(hours=1), method="net", site_timezone=site.timezone
    )
    assert half_range_result.total_cost_c == decimal.Decimal("12.0")


def test_compute_cost_series_includes_finalized_demand_charge_share_in_matching_buckets(db, site):
    tariff = _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0")
    _make_demand_window(db, tariff)  # matches every moment
    period_start = sydney_local(2026, 1, 1, 0, 0)
    period_end = sydney_local(2026, 1, 1, 2, 0)  # 2 hours = twenty-four 5-minute slots
    _make_demand_charge(db, site, period_start, period_end, charge_amount="24.0", is_estimated=False)

    points = compute_cost_series(
        db, site.id, period_start, period_end, method="net", bucket="hour", site_timezone=site.timezone
    )

    # 24c over 24 slots = 1c/slot; twelve slots per hour-bucket = 12c/bucket.
    assert [(p.bucket_start, p.cost_c) for p in points] == [
        (period_start, decimal.Decimal("12.0")),
        (period_start + datetime.timedelta(hours=1), decimal.Decimal("12.0")),
    ]


def test_compute_cost_series_excludes_estimated_demand_charge(db, site):
    tariff = _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0")
    _make_demand_window(db, tariff)
    period_start = sydney_local(2026, 1, 1, 0, 0)
    period_end = sydney_local(2026, 1, 1, 2, 0)
    _make_demand_charge(db, site, period_start, period_end, charge_amount="24.0", is_estimated=True)

    points = compute_cost_series(
        db, site.id, period_start, period_end, method="net", bucket="hour", site_timezone=site.timezone
    )

    assert points == []


def test_compute_cost_series_demand_charge_only_spreads_across_window_matching_buckets(db, site):
    """A demand charge must be spread only across the buckets that fall
    inside the DemandWindow's own pattern, not evenly across the whole
    billing period -- the charge was earned by peak demand during that
    window specifically."""
    tariff = _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0")
    _make_demand_window(db, tariff, start="01:00", end="01:59")
    period_start = sydney_local(2026, 1, 1, 0, 0)
    period_end = sydney_local(2026, 1, 1, 2, 0)  # 2 hours, only the second hour matches
    _make_demand_charge(
        db, site, period_start, period_end, peak_interval_start=sydney_local(2026, 1, 1, 1, 0),
        charge_amount="12.0", is_estimated=False,
    )

    points = compute_cost_series(
        db, site.id, period_start, period_end, method="net", bucket="hour", site_timezone=site.timezone
    )

    # Only the 01:00-01:59 hour-bucket matches the window pattern.
    assert [(p.bucket_start, p.cost_c) for p in points] == [
        (period_start + datetime.timedelta(hours=1), decimal.Decimal("12.0")),
    ]


def test_compute_cost_series_buckets_by_hour(db, site):
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)

    # Two minutes in the 12:00 hour, one minute in the 13:00 hour, same flow each minute:
    # 600W import (0.01 kWh) at 30c/kWh = 0.3c; 300W export (0.005 kWh) at 10c/kWh = 0.05c
    # revenue (Amber's raw feed-in sign is negative -- the customer is paid).
    # Net cost per minute = 0.25c.
    for hour, minute in ((12, 0), (12, 30), (13, 0)):
        ts = datetime.datetime(2026, 1, 1, hour, minute, tzinfo=UTC)
        db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
        db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=300.0))
        db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.BUY, value=decimal.Decimal("30.0")))
        db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-10.0")))
    db.commit()

    points = compute_cost_series(
        db,
        site.id,
        datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        datetime.datetime(2026, 1, 1, 14, 0, tzinfo=UTC),
        method="net",
        bucket="hour",
        site_timezone=site.timezone,
    )

    assert [(p.bucket_start, p.cost_c) for p in points] == [
        (datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC), decimal.Decimal("0.50")),
        (datetime.datetime(2026, 1, 1, 13, 0, tzinfo=UTC), decimal.Decimal("0.25")),
    ]


def test_compute_cost_series_buckets_by_5min(db, site):
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)

    # Minutes 2 and 4 share the :00-:04 bucket; minute 7 is in :05-:09.
    for minute in (2, 4, 7):
        ts = datetime.datetime(2026, 1, 1, 12, minute, tzinfo=UTC)
        db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
        db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.BUY, value=decimal.Decimal("30.0")))
        db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-10.0")))
    db.commit()

    points = compute_cost_series(
        db,
        site.id,
        datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        datetime.datetime(2026, 1, 1, 12, 10, tzinfo=UTC),
        method="net",
        bucket="5min",
        site_timezone=site.timezone,
    )

    assert [p.bucket_start for p in points] == [
        datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        datetime.datetime(2026, 1, 1, 12, 5, tzinfo=UTC),
    ]
    assert [p.cost_c for p in points] == [decimal.Decimal("0.6"), decimal.Decimal("0.3")]


def test_compute_cost_series_buckets_by_15min(db, site):
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)

    # One minute in the :00-:14 quarter-hour, one in the :15-:29 quarter-hour.
    for minute in (5, 20):
        ts = datetime.datetime(2026, 1, 1, 12, minute, tzinfo=UTC)
        db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
        db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.BUY, value=decimal.Decimal("30.0")))
        db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-10.0")))
    db.commit()

    points = compute_cost_series(
        db,
        site.id,
        datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        datetime.datetime(2026, 1, 1, 12, 30, tzinfo=UTC),
        method="net",
        bucket="15min",
        site_timezone=site.timezone,
    )

    assert [p.bucket_start for p in points] == [
        datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        datetime.datetime(2026, 1, 1, 12, 15, tzinfo=UTC),
    ]
    # 0.01 kWh * 30c/kWh = 0.3c each, one minute per quarter-hour.
    assert [p.cost_c for p in points] == [decimal.Decimal("0.3"), decimal.Decimal("0.3")]


def test_compute_cost_series_buckets_by_6hour(db, site):
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)

    # One minute in the 00:00-05:59 window, one in the 06:00-11:59 window.
    for hour in (1, 7):
        ts = datetime.datetime(2026, 1, 1, hour, 0, tzinfo=UTC)
        db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
        db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.BUY, value=decimal.Decimal("30.0")))
        db.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-10.0")))
    db.commit()

    points = compute_cost_series(
        db,
        site.id,
        datetime.datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        method="net",
        bucket="6hour",
        site_timezone=site.timezone,
    )

    assert [p.bucket_start for p in points] == [
        datetime.datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime.datetime(2026, 1, 1, 6, 0, tzinfo=UTC),
    ]
    # 0.01 kWh * 30c/kWh = 0.3c each, one minute per 6-hour window.
    assert [p.cost_c for p in points] == [decimal.Decimal("0.3"), decimal.Decimal("0.3")]


def test_compute_cost_series_skips_minutes_missing_price(db, site):
    energy_source = _make_energy_source(db, site)
    _make_pricing_source(db, site)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    db.add(EnergyReading(source_id=energy_source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
    db.commit()
    # No PriceReading at all for this minute.

    points = compute_cost_series(
        db, site.id, ts, ts + datetime.timedelta(minutes=1), method="net", bucket="hour", site_timezone=site.timezone
    )
    assert points == []


def test_compute_cost_adds_daily_service_charge_once_for_a_full_local_day(db, site):
    """site's timezone is Australia/Sydney (conftest) -- a day runs from
    local midnight to local midnight, not UTC midnight to UTC midnight."""
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="95.0")

    result = compute_cost(
        db,
        site.id,
        sydney_local(2026, 1, 1),
        sydney_local(2026, 1, 2),
        method="net",
        site_timezone=site.timezone,
    )

    assert result.total_cost_c == decimal.Decimal("95.0")


def test_compute_cost_charges_once_per_local_day_over_a_multi_day_range(db, site):
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="95.0")

    result = compute_cost(
        db,
        site.id,
        sydney_local(2026, 1, 1),
        sydney_local(2026, 1, 3),
        method="net",
        site_timezone=site.timezone,
    )

    assert result.total_cost_c == decimal.Decimal("190.0")


def test_compute_cost_omits_service_charge_with_no_tariff_configured(db, site):
    result = compute_cost(
        db,
        site.id,
        sydney_local(2026, 1, 1),
        sydney_local(2026, 1, 2),
        method="net",
        site_timezone=site.timezone,
    )

    assert result.total_cost_c == decimal.Decimal("0")


def test_compute_cost_service_charge_is_non_retroactive_past_a_tariff_change(db, site):
    """A tariff ending exactly at a day's local midnight must not cover that
    day -- same non-retroactive rule effective_site_tariff_at already
    enforces for demand charges."""
    _make_tariff(
        db, site, effective_from=sydney_local(2026, 1, 1), effective_to=sydney_local(2026, 1, 2), service_c_per_day="95.0"
    )

    result = compute_cost(
        db,
        site.id,
        sydney_local(2026, 1, 1),
        sydney_local(2026, 1, 3),
        method="net",
        site_timezone=site.timezone,
    )

    # Jan 1 is charged (tariff in force); Jan 2 is not (no tariff covers it).
    assert result.total_cost_c == decimal.Decimal("95.0")


def test_compute_cost_series_excludes_the_daily_service_charge(db, site):
    """The history chart (fed by compute_cost_series) must show usage-driven
    cost only -- a flat per-day fee spiking one bucket would skew its y-axis
    for the rest of the range. compute_cost's total (the figure above the
    chart) still includes it (see the test below)."""
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    midnight = sydney_local(2026, 1, 2)

    db.add(EnergyReading(source_id=energy_source.id, ts=midnight, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=midnight, channel=PriceChannel.BUY, value=decimal.Decimal("30.0")))
    db.commit()

    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="95.0")

    points = compute_cost_series(
        db,
        site.id,
        sydney_local(2026, 1, 1, 23, 0),
        sydney_local(2026, 1, 2, 1, 0),
        method="net",
        bucket="hour",
        site_timezone=site.timezone,
    )

    # 0.01 kWh * 30c/kWh = 0.3c energy cost only -- no 95c service charge mixed in.
    assert [(p.bucket_start, p.cost_c) for p in points] == [(midnight, decimal.Decimal("0.3"))]


def test_compute_cost_total_still_includes_the_daily_service_charge_over_the_same_window(db, site):
    """Same data/window as the series test above -- compute_cost's total (the
    dashboard's top-line cost figure) must still include the charge the
    chart deliberately omits."""
    energy_source = _make_energy_source(db, site)
    pricing_source = _make_pricing_source(db, site)
    midnight = sydney_local(2026, 1, 2)

    db.add(EnergyReading(source_id=energy_source.id, ts=midnight, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
    db.add(PriceReading(pricing_source_id=pricing_source.id, ts=midnight, channel=PriceChannel.BUY, value=decimal.Decimal("30.0")))
    db.commit()

    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="95.0")

    result = compute_cost(
        db,
        site.id,
        sydney_local(2026, 1, 1, 23, 0),
        sydney_local(2026, 1, 2, 1, 0),
        method="net",
        site_timezone=site.timezone,
    )

    assert result.total_cost_c == decimal.Decimal("95.3")


def test_compute_cost_service_charge_prorates_for_a_day_still_in_progress(db, site):
    """A day that hasn't finished yet -- `now` falls before its local
    midnight rollover -- must not have its full day's service charge
    stamped on the instant the day starts; only the fraction of the day
    that has actually elapsed so far."""
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="96.0")
    midnight = sydney_local(2026, 1, 1)
    six_hours_in = midnight + datetime.timedelta(hours=6)

    result = compute_cost(
        db,
        site.id,
        midnight,
        midnight + datetime.timedelta(days=1),
        method="net",
        site_timezone=site.timezone,
        now=six_hours_in,
    )

    # 6 of 24 hours elapsed -> a quarter of the 96c daily fee.
    assert result.total_cost_c == decimal.Decimal("24.0")


def test_compute_cost_service_charge_is_zero_the_instant_a_new_day_starts(db, site):
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="96.0")
    midnight = sydney_local(2026, 1, 1)

    result = compute_cost(
        db,
        site.id,
        midnight,
        midnight + datetime.timedelta(days=1),
        method="net",
        site_timezone=site.timezone,
        now=midnight,
    )

    assert result.total_cost_c == decimal.Decimal("0")


def test_compute_cost_service_charge_is_full_once_the_day_has_fully_elapsed(db, site):
    """`now` on or after the day's own rollover (even if `end` extends
    further into the future, as the dashboard's fixed-span "today" query
    does) still charges the full day -- proration only holds back the
    in-progress day, not ones that have already finished."""
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="96.0")
    midnight = sydney_local(2026, 1, 1)
    next_midnight = midnight + datetime.timedelta(days=1)

    result = compute_cost(
        db,
        site.id,
        midnight,
        next_midnight,
        method="net",
        site_timezone=site.timezone,
        now=next_midnight,
    )

    assert result.total_cost_c == decimal.Decimal("96.0")


def test_compute_cost_service_charge_is_full_immediately_when_proration_is_disabled(db, site):
    """prorate_fees=False is the live tiles' escape hatch (dashboard's
    Today's cost / Billing period cost): they want to read as "what's
    committed today", not a smoothly ticking total, so a day barely
    started must still show its full service charge."""
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="96.0")
    midnight = sydney_local(2026, 1, 1)
    one_minute_in = midnight + datetime.timedelta(minutes=1)

    result = compute_cost(
        db,
        site.id,
        midnight,
        midnight + datetime.timedelta(days=1),
        method="net",
        site_timezone=site.timezone,
        now=one_minute_in,
        prorate_fees=False,
    )

    assert result.total_cost_c == decimal.Decimal("96.0")


def test_compute_cost_monthly_fee_is_full_immediately_when_proration_is_disabled(db, site):
    _make_billing_cycle(db, site, effective_from=sydney_local(2026, 1, 1), cycle_months=1, anchor_day=1)
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0", monthly_fee_c="310.0")
    midnight = sydney_local(2026, 1, 1)
    one_minute_in = midnight + datetime.timedelta(minutes=1)

    result = compute_cost(
        db,
        site.id,
        midnight,
        midnight + datetime.timedelta(days=1),
        method="net",
        site_timezone=site.timezone,
        now=one_minute_in,
        prorate_fees=False,
    )

    # 310c / 31 days = 10c/day, in full despite `now` being 1 minute in.
    assert result.total_cost_c == decimal.Decimal("10.0")


def test_compute_cost_monthly_fee_prorates_for_a_day_still_in_progress(db, site):
    _make_billing_cycle(db, site, effective_from=sydney_local(2026, 1, 1), cycle_months=1, anchor_day=1)
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0", monthly_fee_c="310.0")
    midnight = sydney_local(2026, 1, 1)
    six_hours_in = midnight + datetime.timedelta(hours=6)

    result = compute_cost(
        db,
        site.id,
        midnight,
        midnight + datetime.timedelta(days=1),
        method="net",
        site_timezone=site.timezone,
        now=six_hours_in,
    )

    # 310c / 31 days = 10c/day; a quarter of today elapsed -> 2.5c.
    assert result.total_cost_c == decimal.Decimal("2.5")


def test_compute_cost_amortizes_monthly_fee_across_the_billing_cycle(db, site):
    """310c/month over January's 31-day calendar-month billing cycle ->
    exactly 10c/day, charged at local midnight like the service charge."""
    _make_billing_cycle(db, site, effective_from=sydney_local(2026, 1, 1), cycle_months=1, anchor_day=1)
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0", monthly_fee_c="310.0")

    result = compute_cost(
        db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 1, 2), method="net", site_timezone=site.timezone
    )

    assert result.total_cost_c == decimal.Decimal("10.0")


def test_compute_cost_monthly_fee_amortizes_over_multiple_days(db, site):
    _make_billing_cycle(db, site, effective_from=sydney_local(2026, 1, 1), cycle_months=1, anchor_day=1)
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0", monthly_fee_c="310.0")

    result = compute_cost(
        db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 1, 4), method="net", site_timezone=site.timezone
    )

    assert result.total_cost_c == decimal.Decimal("30.0")


def test_compute_cost_omits_monthly_fee_when_tariff_has_none_set(db, site):
    _make_billing_cycle(db, site, effective_from=sydney_local(2026, 1, 1), cycle_months=1, anchor_day=1)
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0")

    result = compute_cost(
        db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 1, 2), method="net", site_timezone=site.timezone
    )

    assert result.total_cost_c == decimal.Decimal("0")


def test_compute_cost_omits_monthly_fee_when_no_billing_cycle_configured(db, site):
    """A monthly_fee_c with no BillingCycle configured has nothing to
    amortize over, so it's omitted entirely rather than guessing a divisor."""
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0", monthly_fee_c="310.0")

    result = compute_cost(
        db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 1, 2), method="net", site_timezone=site.timezone
    )

    assert result.total_cost_c == decimal.Decimal("0")


def test_compute_cost_monthly_fee_discount_window_reduces_the_daily_rate(db, site):
    """93c/month discounted rate over the same 31-day cycle -> 3c/day for
    any day the discount window covers."""
    _make_billing_cycle(db, site, effective_from=sydney_local(2026, 1, 1), cycle_months=1, anchor_day=1)
    tariff = _make_tariff(
        db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0", monthly_fee_c="310.0"
    )
    _make_monthly_fee_discount(
        db, tariff, starts_at=sydney_local(2026, 1, 10), ends_at=sydney_local(2026, 1, 11), discounted_fee_c="93.0"
    )

    result = compute_cost(
        db, site.id, sydney_local(2026, 1, 10), sydney_local(2026, 1, 11), method="net", site_timezone=site.timezone
    )

    assert result.total_cost_c == decimal.Decimal("3.0")


def test_compute_cost_monthly_fee_discount_window_does_not_apply_outside_its_dates(db, site):
    _make_billing_cycle(db, site, effective_from=sydney_local(2026, 1, 1), cycle_months=1, anchor_day=1)
    tariff = _make_tariff(
        db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0", monthly_fee_c="310.0"
    )
    _make_monthly_fee_discount(
        db, tariff, starts_at=sydney_local(2026, 1, 10), ends_at=sydney_local(2026, 1, 11), discounted_fee_c="93.0"
    )

    result = compute_cost(
        db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 1, 2), method="net", site_timezone=site.timezone
    )

    assert result.total_cost_c == decimal.Decimal("10.0")


def test_monthly_fee_discounts_c_is_the_gross_discounted_fee_over_a_covered_period(db, site):
    """A window covering the whole January cycle totals its discounted fee,
    even when it equals the normal fee (no net saving)."""
    _make_billing_cycle(db, site, effective_from=sydney_local(2026, 1, 1), cycle_months=1, anchor_day=1)
    tariff = _make_tariff(
        db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0", monthly_fee_c="2500.0"
    )
    _make_monthly_fee_discount(
        db, tariff, starts_at=sydney_local(2026, 1, 1), ends_at=sydney_local(2026, 3, 1), discounted_fee_c="2500.0"
    )

    total = monthly_fee_discounts_c(db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 2, 1), site.timezone)

    assert round(total, 4) == decimal.Decimal("2500.0")


def test_monthly_fee_discounts_c_counts_only_discounted_days(db, site):
    """One discounted day at 93c/month over a 31-day cycle -> 3c."""
    _make_billing_cycle(db, site, effective_from=sydney_local(2026, 1, 1), cycle_months=1, anchor_day=1)
    tariff = _make_tariff(
        db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0", monthly_fee_c="310.0"
    )
    _make_monthly_fee_discount(
        db, tariff, starts_at=sydney_local(2026, 1, 10), ends_at=sydney_local(2026, 1, 11), discounted_fee_c="93.0"
    )

    total = monthly_fee_discounts_c(db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 2, 1), site.timezone)

    assert total == decimal.Decimal("3.0")


def test_monthly_fee_discounts_c_is_zero_without_a_discount(db, site):
    _make_billing_cycle(db, site, effective_from=sydney_local(2026, 1, 1), cycle_months=1, anchor_day=1)
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0", monthly_fee_c="310.0")

    total = monthly_fee_discounts_c(db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 2, 1), site.timezone)

    assert total == decimal.Decimal("0")


def test_compute_cost_series_excludes_the_monthly_fee(db, site):
    """The usage chart must not show a lump-sum spike for the monthly fee,
    same reasoning as the daily service charge."""
    _make_billing_cycle(db, site, effective_from=sydney_local(2026, 1, 1), cycle_months=1, anchor_day=1)
    _make_tariff(db, site, effective_from=sydney_local(2026, 1, 1), service_c_per_day="0.0", monthly_fee_c="310.0")

    points = compute_cost_series(
        db, site.id, sydney_local(2026, 1, 1), sydney_local(2026, 1, 2), method="net", bucket="hour", site_timezone=site.timezone
    )

    assert points == []


def _make_credit(
    db, tariff, credit_c="50", max_avg_import_w="300", months=None, days_of_week=None, start="10:00", end="12:00"
) -> ConditionalCreditWindow:
    window = ConditionalCreditWindow(
        site_tariff_id=tariff.id,
        label="Low import credit",
        months=months or list(range(1, 13)),
        days_of_week=days_of_week if days_of_week is not None else [0, 1, 2, 3, 4, 5, 6],
        start_time=datetime.time.fromisoformat(start),
        end_time=datetime.time.fromisoformat(end),
        max_avg_import_w=decimal.Decimal(max_avg_import_w),
        credit_c=decimal.Decimal(credit_c),
    )
    db.add(window)
    db.commit()
    return window


def _add_readings(db, source, start, minutes, watts, metric=EnergyMetric.GRID_IMPORT_W):
    for i in range(minutes):
        db.add(
            EnergyReading(
                source_id=source.id, ts=start + datetime.timedelta(minutes=i), metric=metric, value=float(watts)
            )
        )
    db.commit()


# 2026-01-07 is a Wednesday (weekday 2); Sydney is UTC+11 in January.
CREDIT_DAY = (2026, 1, 7)
AFTER_WINDOW = sydney_local(2026, 1, 7, 13, 0)


def _credit_total(db, site, now=AFTER_WINDOW, start=None, end=None, method="net"):
    return compute_cost(
        db,
        site.id,
        start or sydney_local(*CREDIT_DAY),
        end or sydney_local(2026, 1, 8),
        method=method,
        site_timezone=site.timezone,
        now=now,
    ).total_cost_c


def _credit_setup(db, site, watts=200, minutes=120, **credit_kwargs):
    """A zero-service-charge tariff with one 10:00-12:00 credit, and `minutes`
    of grid import at `watts` from 10:00 on CREDIT_DAY."""
    source = _make_energy_source(db, site)
    tariff = _make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC), service_c_per_day="0")
    _make_credit(db, tariff, **credit_kwargs)
    _add_readings(db, source, sydney_local(*CREDIT_DAY, 10, 0), minutes, watts)
    return source, tariff


def test_credit_applies_when_average_import_is_below_the_threshold(db, site):
    _credit_setup(db, site, watts=200)

    assert _credit_total(db, site) == decimal.Decimal("-50")


def test_no_credit_when_average_import_equals_the_threshold(db, site):
    _credit_setup(db, site, watts=300)

    assert _credit_total(db, site) == decimal.Decimal("0")


def test_credit_uses_the_average_not_every_minute(db, site):
    source, _ = _credit_setup(db, site, watts=100, minutes=60)
    _add_readings(db, source, sydney_local(*CREDIT_DAY, 11, 0), 60, 450)  # average 275 W

    assert _credit_total(db, site) == decimal.Decimal("-50")


def test_no_credit_when_one_minute_has_no_reading(db, site):
    _credit_setup(db, site, watts=200, minutes=119)

    assert _credit_total(db, site) == decimal.Decimal("0")


def test_credit_applies_when_every_reading_is_zero_watts(db, site):
    _credit_setup(db, site, watts=0)

    assert _credit_total(db, site) == decimal.Decimal("-50")


def test_credit_counts_exporting_minutes_as_zero_import(db, site):
    """The Envoy collector writes grid_export_w and no grid_import_w while the
    site exports, so an exporting minute has a reading but no import row."""
    source, _ = _credit_setup(db, site, watts=200, minutes=60)
    _add_readings(db, source, sydney_local(*CREDIT_DAY, 11, 0), 60, 500, metric=EnergyMetric.GRID_EXPORT_W)

    assert _credit_total(db, site) == decimal.Decimal("-50")  # (200 W x 60 + 0 W x 60) / 120 = 100 W


def test_exporting_minutes_do_not_hide_high_import(db, site):
    source, _ = _credit_setup(db, site, watts=700, minutes=60)
    _add_readings(db, source, sydney_local(*CREDIT_DAY, 11, 0), 60, 500, metric=EnergyMetric.GRID_EXPORT_W)

    assert _credit_total(db, site) == decimal.Decimal("0")  # (700 W x 60 + 0 W x 60) / 120 = 350 W


def test_no_credit_before_the_window_has_ended(db, site):
    _credit_setup(db, site, watts=200)

    assert _credit_total(db, site, now=sydney_local(2026, 1, 7, 11, 0)) == decimal.Decimal("0")


def test_credit_counts_once_the_window_end_is_reached(db, site):
    _credit_setup(db, site, watts=200)

    assert _credit_total(db, site, now=sydney_local(2026, 1, 7, 12, 0)) == decimal.Decimal("-50")


def test_no_credit_on_a_day_that_does_not_match(db, site):
    _credit_setup(db, site, watts=200, days_of_week=[0])  # Monday only

    assert _credit_total(db, site) == decimal.Decimal("0")


def test_no_credit_in_a_month_that_does_not_match(db, site):
    _credit_setup(db, site, watts=200, months=[2])

    assert _credit_total(db, site) == decimal.Decimal("0")


def test_credit_matches_weekday_and_month_in_site_local_time(db, site):
    """10:00-12:00 Sydney on Monday 2026-02-02 is Sunday 2026-02-01 in UTC."""
    source = _make_energy_source(db, site)
    tariff = _make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC), service_c_per_day="0")
    _make_credit(db, tariff, months=[2], days_of_week=[0])
    _add_readings(db, source, sydney_local(2026, 2, 2, 10, 0), 120, 200)

    total = _credit_total(
        db, site, now=sydney_local(2026, 2, 2, 13, 0), start=sydney_local(2026, 2, 2), end=sydney_local(2026, 2, 3)
    )

    assert total == decimal.Decimal("-50")


def test_two_matching_credits_both_apply(db, site):
    _, tariff = _credit_setup(db, site, watts=200)
    _make_credit(db, tariff, credit_c="30", max_avg_import_w="250")

    assert _credit_total(db, site) == decimal.Decimal("-80")


def test_credit_comes_from_the_tariff_in_force_that_day(db, site):
    source = _make_energy_source(db, site)
    old = _make_tariff(
        db, site, datetime.datetime(2025, 12, 1, tzinfo=UTC), effective_to=sydney_local(*CREDIT_DAY), service_c_per_day="0"
    )
    new = _make_tariff(db, site, sydney_local(*CREDIT_DAY), service_c_per_day="0")
    _make_credit(db, old, credit_c="50")
    _make_credit(db, new, credit_c="30")
    _add_readings(db, source, sydney_local(*CREDIT_DAY, 10, 0), 120, 200)

    assert _credit_total(db, site) == decimal.Decimal("-30")


def test_credit_is_the_same_under_net_and_gross(db, site):
    source, _ = _credit_setup(db, site, watts=200)
    _add_readings(db, source, sydney_local(*CREDIT_DAY, 10, 0), 120, 5000, metric=EnergyMetric.CONSUMPTION_W)

    assert _credit_total(db, site, method="net") == decimal.Decimal("-50")
    assert _credit_total(db, site, method="gross") == decimal.Decimal("-50")


def test_credit_is_found_when_the_range_ends_before_the_window(db, site):
    _credit_setup(db, site, watts=200)

    total = _credit_total(db, site, end=sydney_local(2026, 1, 7, 9, 0))

    assert total == decimal.Decimal("-50")


def test_credit_is_skipped_when_the_site_has_no_energy_source(db, site):
    tariff = _make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC), service_c_per_day="0")
    _make_credit(db, tariff)

    assert _credit_total(db, site) == decimal.Decimal("0")


def test_credit_window_on_dst_end_day_needs_every_real_minute(db, site):
    """Sydney clocks go back on 2026-04-05 (03:00 AEDT -> 02:00 AEST), so
    01:00-04:00 local is 240 real minutes."""
    source = _make_energy_source(db, site)
    tariff = _make_tariff(db, site, datetime.datetime(2026, 1, 1, tzinfo=UTC), service_c_per_day="0")
    _make_credit(db, tariff, start="01:00", end="04:00")
    start = sydney_local(2026, 4, 5, 1, 0)
    now = sydney_local(2026, 4, 5, 6, 0)
    day_start, day_end = sydney_local(2026, 4, 5), sydney_local(2026, 4, 6)

    _add_readings(db, source, start, 180, 200)  # the 180 minutes a 3-hour window would need
    assert _credit_total(db, site, now=now, start=day_start, end=day_end) == decimal.Decimal("0")

    _add_readings(db, source, start + datetime.timedelta(minutes=180), 60, 200)
    assert _credit_total(db, site, now=now, start=day_start, end=day_end) == decimal.Decimal("-50")


def test_series_leaves_the_credit_out(db, site):
    _credit_setup(db, site, watts=200)

    points = compute_cost_series(
        db,
        site.id,
        sydney_local(*CREDIT_DAY),
        sydney_local(2026, 1, 8),
        method="net",
        bucket="hour",
        site_timezone=site.timezone,
    )

    assert all(p.cost_c >= 0 for p in points)


def test_credit_is_not_earned_before_the_windows_start_date(db, site):
    _, tariff = _credit_setup(db, site, watts=200)
    window = db.query(ConditionalCreditWindow).one()
    window.effective_from = sydney_local(2026, 1, 8)
    db.commit()

    assert _credit_total(db, site) == decimal.Decimal("0")


def test_credit_is_not_earned_after_the_windows_end_date(db, site):
    _credit_setup(db, site, watts=200)
    window = db.query(ConditionalCreditWindow).one()
    window.effective_to = sydney_local(*CREDIT_DAY)
    db.commit()

    assert _credit_total(db, site) == decimal.Decimal("0")
