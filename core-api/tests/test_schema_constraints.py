from __future__ import annotations

import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, StatementError

from core_api.models.billing import BillingCycle, DemandCharge, DemandInterval
from core_api.models.identity import SiteAccess, SiteAccessRole, User
from core_api.models.readings import (
    ENERGY_METRIC_AGGREGATION,
    AggregationMethod,
    EnergyMetric,
    EnergyReading,
)
from core_api.models.sources import Source, SourceKind
from tests.conftest import UTC


def make_source(db, site, kind, provider="envoy"):
    source = Source(
        site_id=site.id,
        kind=kind,
        provider=provider,
        config={},
        effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def make_demand_charge(db, site, peak_start, **overrides):
    fields = dict(
        site_id=site.id,
        billing_period_start=datetime.datetime(2026, 1, 1, tzinfo=UTC),
        billing_period_end=datetime.datetime(2026, 2, 1, tzinfo=UTC),
        peak_interval_start=peak_start,
        peak_avg_import_w=4000.0,
        rate_applied=12.5,
        charge_amount=100.0,
    )
    fields.update(overrides)
    charge = DemandCharge(**fields)
    db.add(charge)
    return charge


def test_energy_reading_upsert_on_composite_pk_is_idempotent(db, site):
    source = make_source(db, site, SourceKind.ENERGY)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    stmt = sqlite_insert(EnergyReading).values(
        source_id=source.id, ts=ts, metric=EnergyMetric.PRODUCTION_W, value=100.0
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["source_id", "ts", "metric"], set_={"value": stmt.excluded.value}
    )
    db.execute(stmt)
    db.commit()

    # Simulated collector retry after a network blip, with a corrected value.
    stmt = sqlite_insert(EnergyReading).values(
        source_id=source.id, ts=ts, metric=EnergyMetric.PRODUCTION_W, value=105.0
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["source_id", "ts", "metric"], set_={"value": stmt.excluded.value}
    )
    db.execute(stmt)
    db.commit()

    rows = db.execute(select(EnergyReading)).scalars().all()
    assert len(rows) == 1
    assert rows[0].value == 105.0


def test_only_one_weather_source_per_site(db, site):
    make_source(db, site, SourceKind.WEATHER)
    with pytest.raises(IntegrityError):
        make_source(db, site, SourceKind.WEATHER)


def test_multiple_energy_sources_per_site_allowed(db, site):
    make_source(db, site, SourceKind.ENERGY)
    make_source(db, site, SourceKind.ENERGY)
    sources = db.execute(select(Source)).scalars().all()
    assert len(sources) == 2


def test_site_access_role_rejects_owner_value(db, site):
    other = User(email="viewer@example.com", password_hash="hash")
    db.add(other)
    db.commit()

    db.add(SiteAccess(site_id=site.id, user_id=other.id, role="owner"))
    with pytest.raises(StatementError):
        db.commit()


def test_site_access_allows_viewer(db, site):
    other = User(email="viewer2@example.com", password_hash="hash")
    db.add(other)
    db.commit()

    db.add(SiteAccess(site_id=site.id, user_id=other.id, role=SiteAccessRole.VIEWER))
    db.commit()

    access = db.get(SiteAccess, (site.id, other.id))
    assert access.role == SiteAccessRole.VIEWER


def test_billing_cycle_rejects_non_positive_cycle_months(db, site):
    db.add(
        BillingCycle(
            site_id=site.id,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            cycle_months=0,
            anchor_day=1,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_billing_cycle_rejects_anchor_day_out_of_range(db, site):
    db.add(
        BillingCycle(
            site_id=site.id,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            cycle_months=1,
            anchor_day=32,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_billing_cycle_accepts_quarterly_with_anchor_day(db, site):
    cycle = BillingCycle(
        site_id=site.id,
        effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
        cycle_months=3,
        anchor_day=18,
    )
    db.add(cycle)
    db.commit()
    assert cycle.id is not None


def test_demand_charge_requires_matching_demand_interval(db, site):
    peak_start = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    make_demand_charge(db, site, peak_start)
    with pytest.raises(IntegrityError):
        db.commit()


def test_demand_charge_succeeds_with_matching_demand_interval(db, site):
    peak_start = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    db.add(
        DemandInterval(
            site_id=site.id,
            interval_start=peak_start,
            interval_end=peak_start + datetime.timedelta(minutes=30),
            rolling_sum_w=4000.0,
            is_demand_window=True,
        )
    )
    db.commit()

    charge = make_demand_charge(db, site, peak_start)
    db.commit()
    assert charge.id is not None


def test_demand_charge_upsert_unique_per_site_and_billing_period(db, site):
    peak_start = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    db.add(
        DemandInterval(
            site_id=site.id,
            interval_start=peak_start,
            interval_end=peak_start + datetime.timedelta(minutes=30),
            rolling_sum_w=4000.0,
            is_demand_window=True,
        )
    )
    db.commit()

    period_start = datetime.datetime(2026, 1, 1, tzinfo=UTC)
    make_demand_charge(db, site, peak_start, billing_period_start=period_start)
    db.commit()

    make_demand_charge(
        db, site, peak_start, billing_period_start=period_start, peak_avg_import_w=4100.0, charge_amount=105.0
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_user_totp_defaults_to_disabled(db):
    from core_api.models.identity import User

    u = User(email="totp-default@example.com", password_hash="hash")
    db.add(u)
    db.commit()
    db.refresh(u)
    assert u.totp_enabled is False
    assert u.totp_secret is None


def test_recovery_code_round_trip(db, user):
    from core_api.models.identity import RecoveryCode

    code = RecoveryCode(user_id=user.id, code_hash="a" * 64)
    db.add(code)
    db.commit()
    db.refresh(code)
    assert code.used_at is None


def test_password_reset_token_round_trip(db, user):
    import datetime as dt

    from core_api.models.identity import PasswordResetToken

    token = PasswordResetToken(
        user_id=user.id,
        token_hash="b" * 64,
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    assert token.used_at is None


def test_user_pending_totp_secret_defaults_to_none(db):
    from core_api.models.identity import User

    u = User(email="pending-totp@example.com", password_hash="hash")
    db.add(u)
    db.commit()
    db.refresh(u)
    assert u.pending_totp_secret is None


def test_email_change_token_round_trip(db, user):
    from core_api.models.identity import EmailChangeToken

    token = EmailChangeToken(
        user_id=user.id,
        new_email="new@example.com",
        token_hash="c" * 64,
        expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    assert token.used_at is None


NEW_LIVE_METRICS = [
    EnergyMetric.PRODUCTION_WH_LIFETIME,
    EnergyMetric.GRID_IMPORT_WH_LIFETIME,
    EnergyMetric.GRID_EXPORT_WH_LIFETIME,
    EnergyMetric.GRID_VOLTAGE_PH_A_V,
    EnergyMetric.GRID_VOLTAGE_PH_B_V,
    EnergyMetric.GRID_VOLTAGE_PH_C_V,
    EnergyMetric.GRID_RELAY_STATE,
]


def test_every_energy_metric_has_an_aggregation_method():
    assert set(ENERGY_METRIC_AGGREGATION) == set(EnergyMetric)


def test_new_live_metrics_aggregate_as_specified():
    voltages = {
        EnergyMetric.GRID_VOLTAGE_PH_A_V,
        EnergyMetric.GRID_VOLTAGE_PH_B_V,
        EnergyMetric.GRID_VOLTAGE_PH_C_V,
    }
    for metric in NEW_LIVE_METRICS:
        expected = AggregationMethod.AVERAGE if metric in voltages else AggregationMethod.LAST_VALUE
        assert ENERGY_METRIC_AGGREGATION[metric] == expected


def test_energy_reading_accepts_new_metrics(db, site):
    source = make_source(db, site, SourceKind.ENERGY)
    ts = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    for metric in NEW_LIVE_METRICS:
        db.add(EnergyReading(source_id=source.id, ts=ts, metric=metric, value=1.0))
    db.commit()
    assert len(db.execute(select(EnergyReading)).scalars().all()) == len(NEW_LIVE_METRICS)
