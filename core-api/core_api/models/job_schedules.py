from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from core_api.models.base import Base


class JobSchedule(Base):
    """An admin override of one job's timing. A job with no row runs on its registry default."""

    __tablename__ = "job_schedules"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
