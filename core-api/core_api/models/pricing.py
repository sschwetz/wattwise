from __future__ import annotations

import datetime
import decimal
import enum

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from core_api.models.base import Base, enum_check, str_enum
from core_api.models.readings import AggregationMethod


class PriceChannel(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"
    CONTROLLED_LOAD = "controlled_load"
    DEMAND = "demand"


class SpikeStatus(str, enum.Enum):
    """Amber's spikeStatus: whether an interval may spike or is spiking now."""

    NONE = "none"
    POTENTIAL = "potential"
    SPIKE = "spike"


# All price channels are continuous values (no state-type price channel
# exists), so all average today -- mirrors ENERGY_METRIC_AGGREGATION /
# WEATHER_METRIC_AGGREGATION's lookup-table pattern (spec: 'Retention &
# rollup').
PRICE_CHANNEL_AGGREGATION: dict[PriceChannel, AggregationMethod] = {
    channel: AggregationMethod.AVERAGE for channel in PriceChannel
}


class PriceInterval(Base):
    """Authoritative record of what the pricing provider published. PK is the
    natural key (pricing_source_id, channel, interval_start) — same
    idempotent-upsert-on-natural-key pattern used everywhere else in this
    schema, since the spec doesn't name a surrogate id for this table."""

    __tablename__ = "price_intervals"
    __table_args__ = (
        enum_check("channel", PriceChannel, "ck_price_intervals_channel"),
        enum_check("spike_status", SpikeStatus, "ck_price_intervals_spike_status"),
    )

    pricing_source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    channel: Mapped[PriceChannel] = mapped_column(str_enum(PriceChannel, length=32), primary_key=True)
    interval_start: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    interval_end: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # Low/high of Amber's advancedPrice forecast band; NULL for intervals
    # that carry no band (settled actuals, other providers).
    price_low: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    price_high: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    spike_status: Mapped[SpikeStatus] = mapped_column(
        str_enum(SpikeStatus, length=16), nullable=False, default=SpikeStatus.NONE, server_default=SpikeStatus.NONE.value
    )
    is_demand_window: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class _PriceReadingColumns:
    """Narrow/long shape shared by PriceReading and its 5-min rollup twin —
    same columns, only __tablename__ differs (see 'Time-series: pricing' in
    the design spec for why PriceReading itself is a deliberate derived
    expansion of PriceInterval)."""

    pricing_source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    channel: Mapped[PriceChannel] = mapped_column(str_enum(PriceChannel, length=32), primary_key=True)
    value: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 4), nullable=False)


class PriceReading(_PriceReadingColumns, Base):
    __tablename__ = "price_readings"
    __table_args__ = (enum_check("channel", PriceChannel, "ck_price_readings_channel"),)


class PriceReadingRollup5m(_PriceReadingColumns, Base):
    __tablename__ = "price_readings_rollup_5m"
    __table_args__ = (enum_check("channel", PriceChannel, "ck_price_readings_rollup_5m_channel"),)


class PriceGapMarker(Base):
    """Records a calendar day the pricing provider has confirmed has no data
    for a channel, so the backfill job stops asking about it forever."""

    __tablename__ = "price_gap_markers"
    __table_args__ = (enum_check("channel", PriceChannel, "ck_price_gap_markers_channel"),)

    pricing_source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    channel: Mapped[PriceChannel] = mapped_column(str_enum(PriceChannel, length=32), primary_key=True)
    gap_date: Mapped[datetime.date] = mapped_column(primary_key=True)
