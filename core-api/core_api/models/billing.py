from __future__ import annotations

import datetime
import decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from core_api.models.base import Base


class SiteTariff(Base):
    __tablename__ = "site_tariffs"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    effective_from: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    service_availability_c_per_day: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    monthly_fee_c: Mapped[decimal.Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)


class BillingCycle(Base):
    """How often a site's bills close and on what day. Kept separate from
    SiteTariff — cycle shape and tariff rates can change independently — and
    effective-dated so a later change doesn't rewrite already-finalized
    DemandCharge periods."""

    __tablename__ = "billing_cycles"
    __table_args__ = (
        CheckConstraint("cycle_months > 0", name="ck_billing_cycles_cycle_months_positive"),
        CheckConstraint("anchor_day BETWEEN 1 AND 31", name="ck_billing_cycles_anchor_day_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    effective_from: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cycle_months: Mapped[int] = mapped_column(Integer, nullable=False)
    anchor_day: Mapped[int] = mapped_column(Integer, nullable=False)


class DemandWindow(Base):
    __tablename__ = "demand_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_tariff_id: Mapped[int] = mapped_column(ForeignKey("site_tariffs.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    months: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    days_of_week: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    start_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    rate_c_per_kw_per_day: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    measurement_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)


class ExportAdjustmentWindow(Base):
    """A time-based incentive (positive) or disincentive (negative) applied
    to the export (feed-in) price during a recurring months/days-of-week/
    time-of-day window -- same pattern shape as DemandWindow, attached to a
    SiteTariff the same way. Multiple matching windows stack (cost.py sums
    them), unlike a DemandWindow's 'first match wins', since each is an
    independent price adjustment rather than a single applicable rate."""

    __tablename__ = "export_adjustment_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_tariff_id: Mapped[int] = mapped_column(ForeignKey("site_tariffs.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    months: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    days_of_week: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    start_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    adjustment_c_per_kwh: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    # kWh of exports per day, accumulating through the billing period, that
    # are exempt from adjustment_c_per_kwh. 0 means every exported kWh is
    # adjusted. See export_allowance.py.
    daily_allowance_kwh: Mapped[decimal.Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=decimal.Decimal("0"), server_default="0"
    )


class ConditionalCreditWindow(Base):
    """A flat credit earned on each matching day whose average grid import
    over [start_time, end_time) is strictly below max_avg_import_w. Same
    months/days-of-week/time-of-day pattern as ExportAdjustmentWindow,
    attached to a SiteTariff the same way. cost.py works it out on the fly
    and stacks every matching window; nothing is stored per day."""

    __tablename__ = "conditional_credit_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_tariff_id: Mapped[int] = mapped_column(ForeignKey("site_tariffs.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    months: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    days_of_week: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    start_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    max_avg_import_w: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    credit_c: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 4), nullable=False)


class MonthlyFeeDiscountWindow(Base):
    """A date-bounded discount on a SiteTariff's monthly_fee_c -- e.g. a
    retailer's promotional period waiving or reducing its normally-full
    fee. Unlike ExportAdjustmentWindow's recurring months/days-of-week
    pattern, this is a one-off absolute span [starts_at, ends_at); unlike
    ExportAdjustmentWindow's stacking, only one fee can apply to a given
    day, so overlapping windows use DemandWindow's first-match-wins rule
    instead (cost.py)."""

    __tablename__ = "monthly_fee_discount_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_tariff_id: Mapped[int] = mapped_column(ForeignKey("site_tariffs.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    starts_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    discounted_fee_c: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 4), nullable=False)


class DemandInterval(Base):
    """Derived from EnergyReading.grid_import_w by a periodic job. PK is the
    natural key so re-running the job is a safe upsert.

    rolling_sum_w is the sum of the last N 5-minute slots' contributions
    (each zeroed unless the slot falls inside a demand window), not a single
    slot's average — see 'Granularity' in the background-jobs design spec."""

    __tablename__ = "demand_intervals"

    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), primary_key=True)
    interval_start: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    interval_end: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rolling_sum_w: Mapped[float] = mapped_column(Float, nullable=False)
    is_demand_window: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rate_applied: Mapped[decimal.Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)


class DemandCharge(Base):
    """One finalized row per site per billing period — an invoice line item,
    stable once written. References its peak DemandInterval by a composite FK
    on (site_id, peak_interval_start) rather than a surrogate id, since
    DemandInterval's PK is the natural key above. billing_period_days is
    computed on the fly from billing_period_start/end, never stored."""

    __tablename__ = "demand_charges"
    __table_args__ = (
        UniqueConstraint("site_id", "billing_period_start", name="uq_demand_charges_site_period"),
        ForeignKeyConstraint(
            ["site_id", "peak_interval_start"],
            ["demand_intervals.site_id", "demand_intervals.interval_start"],
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    billing_period_start: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    billing_period_end: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    peak_interval_start: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    peak_avg_import_w: Mapped[float] = mapped_column(Float, nullable=False)
    rate_applied: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    charge_amount: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
