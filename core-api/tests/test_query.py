# tests/test_query.py
from __future__ import annotations

import datetime
import decimal

from sqlalchemy import event
from sqlalchemy.engine import Engine

from core_api.db import make_engine, make_session_factory
from core_api.models.billing import (
    BillingCycle,
    DemandCharge,
    DemandInterval,
    DemandWindow,
    ExportAdjustmentWindow,
    MonthlyFeeDiscountWindow,
    SiteTariff,
)
from core_api.models.identity import Site, User
from core_api.models.pricing import PriceChannel, PriceInterval, PriceReading, SpikeStatus
from core_api.models.readings import EnergyMetric, EnergyReading
from core_api.models.sources import Source, SourceKind
from core_api.security import create_access_token

UTC = datetime.timezone.utc


def seed_energy_reading(db_url: str) -> tuple[int, int]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="q-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    session.add(source)
    session.commit()
    ts = datetime.datetime.now(UTC) - datetime.timedelta(hours=1)
    session.add(EnergyReading(source_id=source.id, ts=ts, metric=EnergyMetric.PRODUCTION_W, value=1234.0))
    session.commit()
    ids = (owner.id, site.id)
    session.close()
    return ids


def test_get_energy_readings_for_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_energy_reading(db_url)
    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/readings/energy",
        params={
            "metric": "production_w",
            "start": (now - datetime.timedelta(hours=2)).isoformat(),
            "end": now.isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["value"] == 1234.0


def test_get_latest_energy_reading_time_returns_newest_across_metrics(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_energy_reading(db_url)
    newest = (datetime.datetime.now(UTC) - datetime.timedelta(minutes=3)).replace(microsecond=0)
    session = make_session_factory(make_engine(db_url))()
    source_id = session.query(Source).one().id
    session.add(EnergyReading(source_id=source_id, ts=newest, metric=EnergyMetric.CONSUMPTION_W, value=500.0))
    session.commit()
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/readings/energy/latest",
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )

    assert response.status_code == 200, response.text
    # Serialised with a UTC offset so a browser does not parse it as local time.
    assert datetime.datetime.fromisoformat(response.json()["ts"]) == newest


def test_get_latest_energy_reading_time_is_null_without_readings(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_energy_reading(db_url)
    session = make_session_factory(make_engine(db_url))()
    session.query(EnergyReading).delete()
    session.commit()
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/readings/energy/latest",
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"ts": None}


def test_get_latest_energy_reading_time_requires_site_access(client, app_and_db_url):
    _, db_url = app_and_db_url
    _, site_id = seed_energy_reading(db_url)

    response = client.get(f"/api/v1/sites/{site_id}/readings/energy/latest")

    assert response.status_code in (401, 403)


def test_get_energy_readings_accepts_naive_datetime_params(client, app_and_db_url):
    """Finding 1/C2: a naive (no offset) start/end must not 500 with
    'can't compare offset-naive and offset-aware datetimes'; it should be
    treated as UTC and return a sensible (200) result."""
    _, db_url = app_and_db_url
    owner_id, site_id = seed_energy_reading(db_url)
    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/readings/energy",
        params={
            "metric": "production_w",
            "start": (now - datetime.timedelta(hours=2)).replace(tzinfo=None).isoformat(),
            "end": now.replace(tzinfo=None).isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1


def test_get_energy_readings_denies_non_member(client, app_and_db_url):
    _, db_url = app_and_db_url
    _owner_id, site_id = seed_energy_reading(db_url)
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    stranger = User(email="q-stranger@example.com", password_hash="x")
    session.add(stranger)
    session.commit()
    stranger_id = stranger.id
    session.close()

    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/readings/energy",
        params={
            "metric": "production_w",
            "start": (now - datetime.timedelta(hours=2)).isoformat(),
            "end": now.isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(stranger_id)}"},
    )
    assert response.status_code == 404


def seed_price_reading(db_url: str) -> tuple[int, int]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="pq-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.PRICING,
        provider="amber",
        config={},
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    session.add(source)
    session.commit()
    ts = datetime.datetime.now(UTC) - datetime.timedelta(hours=1)
    session.add(PriceReading(pricing_source_id=source.id, ts=ts, channel=PriceChannel.BUY, value=25.5))
    session.commit()
    ids = (owner.id, site.id)
    session.close()
    return ids


def test_get_prices_for_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_price_reading(db_url)
    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/prices",
        params={
            "channel": "buy",
            "start": (now - datetime.timedelta(hours=2)).isoformat(),
            "end": now.isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1


def seed_price_reading_with_export_adjustment(db_url: str, adjustment_c_per_kwh: str) -> tuple[int, int]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="pq-adj-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.PRICING,
        provider="amber",
        config={},
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    session.add(source)
    session.commit()
    tariff = SiteTariff(
        site_id=site.id,
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
        service_availability_c_per_day=decimal.Decimal("0"),
    )
    session.add(tariff)
    session.commit()
    session.add(
        ExportAdjustmentWindow(
            site_tariff_id=tariff.id,
            label="Bonus credit",
            months=list(range(1, 13)),
            days_of_week=[0, 1, 2, 3, 4, 5, 6],
            start_time=datetime.time(0, 0),
            end_time=datetime.time.max,
            adjustment_c_per_kwh=decimal.Decimal(adjustment_c_per_kwh),
        )
    )
    session.commit()
    ts = datetime.datetime.now(UTC) - datetime.timedelta(hours=1)
    session.add(PriceReading(pricing_source_id=source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-10.0")))
    session.add(PriceReading(pricing_source_id=source.id, ts=ts, channel=PriceChannel.BUY, value=decimal.Decimal("30.0")))
    session.commit()
    ids = (owner.id, site.id)
    session.close()
    return ids


def test_get_prices_sell_channel_applies_export_adjustment(client, app_and_db_url):
    """Finding: the 'today's pricing' chart calls this endpoint directly --
    it must reflect a configured export incentive/disincentive just like
    compute_cost already does, not only the $ cost totals."""
    _, db_url = app_and_db_url
    owner_id, site_id = seed_price_reading_with_export_adjustment(db_url, "5.0")
    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/prices",
        params={
            "channel": "sell",
            "start": (now - datetime.timedelta(hours=2)).isoformat(),
            "end": now.isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    # Raw sell price -10.0 (Amber's paid-when-negative convention) minus a
    # +5 incentive -> -15.0, i.e. more revenue shown in the displayed price.
    assert body[0]["value"] == -15.0


def test_get_prices_buy_channel_is_unaffected_by_export_adjustment(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_price_reading_with_export_adjustment(db_url, "5.0")
    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/prices",
        params={
            "channel": "buy",
            "start": (now - datetime.timedelta(hours=2)).isoformat(),
            "end": now.isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["value"] == 30.0


def seed_allowance_window(db_url: str, exported_kwh: int) -> tuple[int, int]:
    """A UTC site with a 100-year billing period starting 2020-01-01 (so the
    test cannot straddle a period boundary), one all-day window charging 3c/kWh
    beyond 0.001 kWh/day (about 2.5 kWh accumulated by now), and `exported_kwh` kWh of
    grid export 3 hours ago. Sell price readings at 1 hour ago and 1 hour ahead
    are -10c."""
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="pq-allow-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    effective_from = datetime.datetime(2020, 1, 1, tzinfo=UTC)
    pricing = Source(site_id=site.id, kind=SourceKind.PRICING, provider="amber", config={}, effective_from=effective_from)
    energy = Source(site_id=site.id, kind=SourceKind.ENERGY, provider="envoy", config={}, effective_from=effective_from)
    session.add_all([pricing, energy])
    session.add(BillingCycle(site_id=site.id, effective_from=effective_from, cycle_months=1200, anchor_day=1))
    tariff = SiteTariff(site_id=site.id, effective_from=effective_from, service_availability_c_per_day=decimal.Decimal("0"))
    session.add(tariff)
    session.commit()
    session.add(
        ExportAdjustmentWindow(
            site_tariff_id=tariff.id,
            label="Export fee",
            months=list(range(1, 13)),
            days_of_week=[0, 1, 2, 3, 4, 5, 6],
            start_time=datetime.time(0, 0),
            end_time=datetime.time.max,
            adjustment_c_per_kwh=decimal.Decimal("-3"),
            daily_allowance_kwh=decimal.Decimal("0.001"),
        )
    )
    now = datetime.datetime.now(UTC).replace(second=0, microsecond=0)
    for minute in range(exported_kwh):
        ts = now - datetime.timedelta(hours=3) + datetime.timedelta(minutes=minute)
        session.add(EnergyReading(source_id=energy.id, ts=ts, metric=EnergyMetric.GRID_EXPORT_W, value=60000.0))
    for offset_hours in (-1, 1):
        ts = now + datetime.timedelta(hours=offset_hours)
        session.add(PriceReading(pricing_source_id=pricing.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-10.0")))
    session.commit()
    ids = (owner.id, site.id)
    session.close()
    return ids


def _get_sell_prices(client, owner_id, site_id) -> list[float]:
    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/prices",
        params={
            "channel": "sell",
            "start": (now - datetime.timedelta(hours=2)).isoformat(),
            "end": (now + datetime.timedelta(hours=2)).isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    return [row["value"] for row in response.json()]


def test_get_prices_sell_channel_applies_charge_once_the_allowance_is_used_up(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_allowance_window(db_url, exported_kwh=5)

    # 5 kWh exported is past the allowance so far. Both the past reading and
    # the future one (which assumes no further export) show -10 - (-3) = -7.
    assert _get_sell_prices(client, owner_id, site_id) == [-7.0, -7.0]


def test_get_prices_sell_channel_shows_the_raw_price_while_the_allowance_lasts(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_allowance_window(db_url, exported_kwh=1)

    assert _get_sell_prices(client, owner_id, site_id) == [-10.0, -10.0]


def seed_forecast_intervals(db_url: str) -> tuple[int, int]:
    """One settled interval and two forecast intervals on the buy channel: a
    banded forecast and an unbanded one (the fallback). Each has one reading
    at its whole-minute start."""
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="pq-fallback-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.PRICING,
        provider="amber",
        config={},
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    session.add(source)
    session.commit()
    base = datetime.datetime.now(UTC).replace(second=0, microsecond=0)
    five = datetime.timedelta(minutes=5)
    one_second = datetime.timedelta(seconds=1)
    # (offset from now, price_low): settled (no band), forecast with band, forecast without band
    for i, (offset, low) in enumerate([(-10, None), (10, decimal.Decimal("20")), (20, None)]):
        start = base + datetime.timedelta(minutes=offset)
        session.add(
            PriceInterval(
                pricing_source_id=source.id,
                channel=PriceChannel.BUY,
                interval_start=start + one_second,
                interval_end=start + five,
                price=decimal.Decimal("25"),
                price_low=low,
                price_high=None if low is None else decimal.Decimal("30"),
            )
        )
        session.add(PriceReading(pricing_source_id=source.id, ts=start, channel=PriceChannel.BUY, value=decimal.Decimal("25")))
    session.commit()
    ids = (owner.id, site.id)
    session.close()
    return ids


def test_get_prices_flags_only_future_readings_without_a_band(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_forecast_intervals(db_url)
    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/prices",
        params={
            "channel": "buy",
            "start": (now - datetime.timedelta(hours=1)).isoformat(),
            "end": (now + datetime.timedelta(hours=1)).isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    # settled (-10 min), banded forecast (+10), fallback forecast (+20)
    assert [r["forecast_fallback"] for r in response.json()] == [False, False, True]


def test_get_prices_without_interval_rows_flags_nothing(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_price_reading(db_url)
    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/prices",
        params={
            "channel": "buy",
            "start": (now - datetime.timedelta(hours=2)).isoformat(),
            "end": now.isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert [r["forecast_fallback"] for r in response.json()] == [False]


def test_get_prices_sell_channel_flags_fallback(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_forecast_intervals(db_url)
    session = make_session_factory(make_engine(db_url))()
    for interval in session.query(PriceInterval).all():
        interval.channel = PriceChannel.SELL
    for reading in session.query(PriceReading).all():
        reading.channel = PriceChannel.SELL
    session.commit()
    session.close()
    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/prices",
        params={
            "channel": "sell",
            "start": (now - datetime.timedelta(hours=1)).isoformat(),
            "end": (now + datetime.timedelta(hours=1)).isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert [r["forecast_fallback"] for r in response.json()] == [False, False, True]


def _get_buy_prices(client, owner_id, site_id):
    now = datetime.datetime.now(UTC)
    return client.get(
        f"/api/v1/sites/{site_id}/prices",
        params={
            "channel": "buy",
            "start": (now - datetime.timedelta(hours=1)).isoformat(),
            "end": (now + datetime.timedelta(hours=1)).isoformat(),
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )


def test_get_prices_returns_band_and_spike_status_for_forecast_rows(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_forecast_intervals(db_url)
    session = make_session_factory(make_engine(db_url))()
    banded = session.query(PriceInterval).filter(PriceInterval.price_low.is_not(None)).one()
    banded.spike_status = SpikeStatus.POTENTIAL
    session.commit()
    session.close()

    rows = _get_buy_prices(client, owner_id, site_id).json()
    # settled (-10 min), banded forecast (+10, potential), fallback forecast (+20)
    assert [(r["price_low"], r["price_high"], r["spike_status"]) for r in rows] == [
        (None, None, "none"),
        (20.0, 30.0, "potential"),
        (None, None, "none"),
    ]


def test_get_prices_without_interval_rows_returns_band_defaults(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_price_reading(db_url)
    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/prices",
        params={"channel": "buy", "start": (now - datetime.timedelta(hours=2)).isoformat(), "end": now.isoformat()},
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    row = response.json()[0]
    assert (row["price_low"], row["price_high"], row["spike_status"]) == (None, None, "none")


def test_get_prices_reads_price_intervals_in_a_fixed_number_of_queries(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = seed_forecast_intervals(db_url)
    statements: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany):
        if "FROM price_intervals" in statement:
            statements.append(statement)

    event.listen(Engine, "before_cursor_execute", count)
    try:
        response = _get_buy_prices(client, owner_id, site_id)
    finally:
        event.remove(Engine, "before_cursor_execute", count)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 3
    # One for the fallback ranges, one for the bands -- not one per reading.
    assert len(statements) == 2


def test_get_cost_for_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="cost-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/cost",
        params={
            "start": (now - datetime.timedelta(hours=1)).isoformat(),
            "end": now.isoformat(),
            "method": "net",
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["minutes_missing_price"] == 0  # no data at all in range -> nothing to flag


def test_get_cost_prorate_fees_false_includes_the_full_service_charge_immediately(client, app_and_db_url):
    """The live tiles opt out of proration (dashboard's Today's cost) --
    `prorate_fees=false` must charge a day barely started its full service
    fee, not a sliver of it."""
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="cost-no-prorate-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    now = datetime.datetime.now(UTC)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    session.add(
        SiteTariff(site_id=site.id, effective_from=start_of_day, service_availability_c_per_day=decimal.Decimal("100.0"))
    )
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/cost",
        params={
            "start": start_of_day.isoformat(),
            "end": (start_of_day + datetime.timedelta(days=1)).isoformat(),
            "method": "net",
            "prorate_fees": "false",
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert decimal.Decimal(response.json()["total_cost_c"]) == decimal.Decimal("100.0")


def test_get_cost_prorates_the_service_charge_by_default(client, app_and_db_url):
    """Without prorate_fees, the chart card's "Cost" figure must not jump
    to the full day's service charge the instant a new day starts."""
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="cost-default-prorate-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    now = datetime.datetime.now(UTC)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    session.add(
        SiteTariff(site_id=site.id, effective_from=start_of_day, service_availability_c_per_day=decimal.Decimal("100.0"))
    )
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/cost",
        params={
            "start": start_of_day.isoformat(),
            "end": (start_of_day + datetime.timedelta(days=1)).isoformat(),
            "method": "net",
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert decimal.Decimal(response.json()["total_cost_c"]) < decimal.Decimal("100.0")


def test_get_cost_series_for_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="cost-series-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    session.add(source)
    session.commit()
    pricing_source = Source(
        site_id=site.id,
        kind=SourceKind.PRICING,
        provider="amber",
        config={},
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
    )
    session.add(pricing_source)
    session.commit()
    ts = datetime.datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - datetime.timedelta(minutes=30)
    session.add(EnergyReading(source_id=source.id, ts=ts, metric=EnergyMetric.GRID_IMPORT_W, value=600.0))
    session.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.BUY, value=decimal.Decimal("30.0")))
    session.add(PriceReading(pricing_source_id=pricing_source.id, ts=ts, channel=PriceChannel.SELL, value=decimal.Decimal("-10.0")))
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    now = datetime.datetime.now(UTC)
    response = client.get(
        f"/api/v1/sites/{site_id}/cost/series",
        params={
            "start": (now - datetime.timedelta(hours=2)).isoformat(),
            "end": now.isoformat(),
            "method": "net",
            "bucket": "hour",
        },
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    # 0.01 kWh * 30c/kWh = 0.3c
    assert decimal.Decimal(body[0]["cost_c"]) == decimal.Decimal("0.3")


def test_get_billing_period_cost_for_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="bp-cost-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    now = datetime.datetime.now(UTC)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    session.add(
        BillingCycle(
            site_id=site.id,
            effective_from=start_of_month,
            cycle_months=1,
            anchor_day=1,
        )
    )
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/cost/billing-period",
        params={"method": "net"},
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["minutes_missing_price"] == 0  # no data at all in range -> nothing to flag


def test_get_billing_period_cost_includes_the_full_service_charge_for_a_day_just_started(client, app_and_db_url):
    """The billing-period live tile must show the committed service charge
    in full, unlike /cost's own default proration (see
    test_get_cost_prorates_the_service_charge_by_default) -- it's meant to
    read as "what's due", not a smoothly ticking total."""
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="bp-cost-full-fee@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    now = datetime.datetime.now(UTC)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    session.add(
        BillingCycle(site_id=site.id, effective_from=start_of_day, cycle_months=1, anchor_day=start_of_day.day)
    )
    session.add(
        SiteTariff(site_id=site.id, effective_from=start_of_day, service_availability_c_per_day=decimal.Decimal("100.0"))
    )
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/cost/billing-period",
        params={"method": "net"},
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert decimal.Decimal(response.json()["total_cost_c"]) == decimal.Decimal("100.0")


def test_get_billing_period_cost_reports_the_discount_for_the_whole_period(client, app_and_db_url):
    """monthly_fee_discounts_c covers the full billing period, not just the
    days elapsed so far, and is not reduced by the normal fee it replaced."""
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="bp-cost-discount@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    now = datetime.datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    session.add(BillingCycle(site_id=site.id, effective_from=month_start, cycle_months=1, anchor_day=1))
    tariff = SiteTariff(
        site_id=site.id,
        effective_from=month_start,
        service_availability_c_per_day=decimal.Decimal("0"),
        monthly_fee_c=decimal.Decimal("2500"),
    )
    session.add(tariff)
    session.commit()
    session.add(
        MonthlyFeeDiscountWindow(
            site_tariff_id=tariff.id,
            label="Promo",
            starts_at=month_start - datetime.timedelta(days=40),
            ends_at=month_start + datetime.timedelta(days=400),
            discounted_fee_c=decimal.Decimal("2500"),
        )
    )
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/cost/billing-period",
        params={"method": "net"},
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert round(decimal.Decimal(response.json()["monthly_fee_discounts_c"]), 4) == decimal.Decimal("2500")


def test_get_billing_period_cost_reports_the_full_discount_before_the_first_anchor_day(client, app_and_db_url):
    """With the cycle added before its first anchor day, the discount is the
    window's full discounted fee, not prorated to the partial period."""
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="bp-cost-partial@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    now = datetime.datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    anchor_day = today.day + 1 if today.day < 28 else 1
    session.add(BillingCycle(site_id=site.id, effective_from=today, cycle_months=1, anchor_day=anchor_day))
    tariff = SiteTariff(
        site_id=site.id,
        effective_from=today,
        service_availability_c_per_day=decimal.Decimal("0"),
        monthly_fee_c=decimal.Decimal("2500"),
    )
    session.add(tariff)
    session.commit()
    session.add(
        MonthlyFeeDiscountWindow(
            site_tariff_id=tariff.id,
            label="Promo",
            starts_at=today - datetime.timedelta(days=1),
            ends_at=today + datetime.timedelta(days=400),
            discounted_fee_c=decimal.Decimal("2500"),
        )
    )
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/cost/billing-period",
        params={"method": "net"},
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert decimal.Decimal(response.json()["monthly_fee_discounts_c"]) == decimal.Decimal("2500")


def test_get_billing_period_cost_404s_with_no_active_cycle(client, app_and_db_url):
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="bp-cost-no-cycle@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/cost/billing-period",
        params={"method": "net"},
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "no_active_billing_cycle"


def test_get_demand_charges_for_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="dc-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    peak_start = datetime.datetime(2026, 1, 1, 18, 0, tzinfo=UTC)
    session.add(
        DemandInterval(
            site_id=site.id,
            interval_start=peak_start,
            interval_end=peak_start + datetime.timedelta(minutes=30),
            rolling_sum_w=5000.0,
            is_demand_window=True,
        )
    )
    session.commit()
    session.add(
        DemandCharge(
            site_id=site.id,
            billing_period_start=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            billing_period_end=datetime.datetime(2026, 2, 1, tzinfo=UTC),
            peak_interval_start=peak_start,
            peak_avg_import_w=5000.0,
            rate_applied=decimal.Decimal("45.0"),
            charge_amount=decimal.Decimal("450.0"),
        )
    )
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/demand-charges",
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.json()[0]["charge_amount"] == "450.0000"


def test_get_active_demand_window_when_one_currently_matches(client, app_and_db_url):
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="dw-active-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    tariff = SiteTariff(
        site_id=site.id,
        effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
        service_availability_c_per_day=decimal.Decimal("95.0"),
    )
    session.add(tariff)
    session.commit()
    session.add(
        DemandWindow(
            site_tariff_id=tariff.id,
            label="Peak",
            months=list(range(1, 13)),
            days_of_week=[0, 1, 2, 3, 4, 5, 6],
            start_time=datetime.time(0, 0),
            end_time=datetime.time.max,
            rate_c_per_kw_per_day=decimal.Decimal("45.0"),
            measurement_interval_minutes=30,
        )
    )
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/demand-window/active",
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["label"] == "Peak"


def test_get_active_demand_window_404s_with_no_match(client, app_and_db_url):
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="dw-inactive-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/demand-window/active",
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "no_active_demand_window"


def test_get_billing_period_bounds_returns_the_current_period(client, app_and_db_url):
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="bp-bounds-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    now = datetime.datetime.now(UTC)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    session.add(BillingCycle(site_id=site.id, effective_from=start_of_day, cycle_months=1, anchor_day=start_of_day.day))
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/billing-period",
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    start = datetime.datetime.fromisoformat(body["start"].replace("Z", "+00:00"))
    end = datetime.datetime.fromisoformat(body["end"].replace("Z", "+00:00"))
    assert start == start_of_day
    assert end > now  # the period runs through its end date, not just up to now


def test_get_billing_period_bounds_404s_without_a_billing_cycle(client, app_and_db_url):
    _, db_url = app_and_db_url
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="bp-bounds-none@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    owner_id, site_id = owner.id, site.id
    session.close()

    response = client.get(
        f"/api/v1/sites/{site_id}/billing-period",
        headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "no_active_billing_cycle"
