from __future__ import annotations

import datetime
import decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Time
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from core_api.models.base import Base, str_enum
from core_api.models.pricing import PriceChannel

# A local tariff has no demand channel, so the CHECK lists these three and
# not every PriceChannel member.
LOCAL_RATE_CHANNELS = (PriceChannel.BUY, PriceChannel.SELL, PriceChannel.CONTROLLED_LOAD)
_CHANNEL_VALUES = ", ".join(repr(channel.value) for channel in LOCAL_RATE_CHANNELS)


class LocalRateWindow(Base):
    """One per-kWh rate on one channel for a recurring months/days/time-of-day
    pattern, owned by a `local` pricing source. The pattern shape matches
    DemandWindow, but the end time is excluded and equal start and end times
    mean all day. The rate is stored positive for every channel; the local
    collector flips the sign of `sell` when it pushes, matching Amber's
    negative-when-paid convention."""

    __tablename__ = "local_rate_windows"
    __table_args__ = (CheckConstraint(f"channel IN ({_CHANNEL_VALUES})", name="ck_local_rate_windows_channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    channel: Mapped[PriceChannel] = mapped_column(str_enum(PriceChannel, length=32), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    effective_from: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    months: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    days_of_week: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    start_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    end_time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    rate_c_per_kwh: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 4), nullable=False)
