from __future__ import annotations

import datetime
import decimal

from core_api.export_allowance_data import allowance_window_spans
from core_api.jobs.billing_periods import BillingPeriod
from core_api.jobs.demand_config import (
    first_matching_demand_window,
    total_export_adjustment_c_per_kwh,
    window_in_force,
)
from core_api.models.billing import DemandWindow, ExportAdjustmentWindow, SiteTariff
from tests.conftest import sydney_local

TZ_NAME = "Australia/Sydney"
ALL_MONTHS = list(range(1, 13))
ALL_DAYS = list(range(7))
JUL_1 = sydney_local(2026, 7, 1)


def _tariff(db, site) -> SiteTariff:
    tariff = SiteTariff(
        site_id=site.id,
        effective_from=sydney_local(2026, 1, 1),
        service_availability_c_per_day=decimal.Decimal("100"),
    )
    db.add(tariff)
    db.commit()
    return tariff


def _export_window(effective_from=None, effective_to=None, rate="2", allowance="0") -> ExportAdjustmentWindow:
    window = ExportAdjustmentWindow(
        site_tariff_id=1,
        label="w",
        months=ALL_MONTHS,
        days_of_week=ALL_DAYS,
        start_time=datetime.time(0, 0),
        end_time=datetime.time(23, 59),
        adjustment_c_per_kwh=decimal.Decimal(rate),
        daily_allowance_kwh=decimal.Decimal(allowance),
    )
    window.effective_from = effective_from
    window.effective_to = effective_to
    return window


def test_window_in_force_is_half_open():
    window = _export_window(JUL_1, sydney_local(2026, 8, 1))

    assert not window_in_force(window, sydney_local(2026, 6, 30, 23, 59))
    assert window_in_force(window, JUL_1)
    assert window_in_force(window, sydney_local(2026, 7, 31, 23, 59))
    assert not window_in_force(window, sydney_local(2026, 8, 1))


def test_window_in_force_with_no_end_has_no_upper_limit():
    assert window_in_force(_export_window(JUL_1, None), sydney_local(2040, 1, 1))


def test_demand_window_is_only_matched_inside_its_dates(db, site):
    tariff = _tariff(db, site)
    db.add(
        DemandWindow(
            site_tariff_id=tariff.id,
            label="peak",
            months=ALL_MONTHS,
            days_of_week=ALL_DAYS,
            start_time=datetime.time(0, 0),
            end_time=datetime.time(23, 59),
            rate_c_per_kw_per_day=decimal.Decimal("10"),
            effective_from=JUL_1,
        )
    )
    db.commit()

    assert first_matching_demand_window(db, site.id, sydney_local(2026, 6, 30, 12), TZ_NAME) is None
    assert first_matching_demand_window(db, site.id, sydney_local(2026, 7, 1, 12), TZ_NAME) is not None


def test_export_adjustment_total_ignores_windows_out_of_dates():
    at = sydney_local(2026, 7, 15, 12)
    windows = [
        _export_window(sydney_local(2026, 1, 1), JUL_1, rate="5"),
        _export_window(JUL_1, None, rate="2"),
    ]

    assert total_export_adjustment_c_per_kwh(windows, at) == decimal.Decimal("2")
    assert total_export_adjustment_c_per_kwh(windows, sydney_local(2026, 6, 15, 12)) == decimal.Decimal("5")


def test_allowance_span_is_clipped_to_the_window_dates():
    tariff = SiteTariff(
        id=1, site_id=1, effective_from=sydney_local(2026, 1, 1), service_availability_c_per_day=decimal.Decimal("1")
    )
    window = _export_window(sydney_local(2026, 7, 10), sydney_local(2026, 7, 20), allowance="10")
    period = BillingPeriod(start=JUL_1, end=sydney_local(2026, 8, 1), cycle=None)

    spans = list(allowance_window_spans([period], [tariff], {1: [window]}, TZ_NAME))

    assert [(s.start, s.end) for s in spans] == [(sydney_local(2026, 7, 10), sydney_local(2026, 7, 20))]


def test_migration_gives_existing_windows_their_tariffs_dates(tmp_path):
    import sqlalchemy as sa

    from core_api.bootstrap import run_migrations

    url = f"sqlite:///{tmp_path}/migrate.db"
    run_migrations(url, revision="a7e2c9d4f1b8")
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO users (id, email, password_hash) VALUES (1, 'a@b.c', 'x')"))
        conn.execute(sa.text("INSERT INTO sites (id, owner_user_id, name, timezone) VALUES (1, 1, 'H', 'Australia/Sydney')"))
        conn.execute(
            sa.text(
                "INSERT INTO site_tariffs (id, site_id, effective_from, effective_to, service_availability_c_per_day) "
                "VALUES (1, 1, '2026-01-01 00:00:00', '2026-07-01 00:00:00', 100)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO demand_windows (site_tariff_id, label, months, days_of_week, start_time, end_time, "
                "rate_c_per_kw_per_day, measurement_interval_minutes) "
                "VALUES (1, 'p', '[1]', '[0]', '00:00:00', '01:00:00', 5, 30)"
            )
        )

    run_migrations(url)

    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT effective_from, effective_to FROM demand_windows")).one()
    assert (row.effective_from, row.effective_to) == ("2026-01-01 00:00:00", "2026-07-01 00:00:00")
