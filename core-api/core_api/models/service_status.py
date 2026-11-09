from __future__ import annotations

import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from core_api.models.base import Base


class ServiceStatus(Base):
    """The latest report from one service: the git commit it runs and when it last checked in."""

    __tablename__ = "service_status"

    service: Mapped[str] = mapped_column(String(64), primary_key=True)
    commit: Mapped[str] = mapped_column(String(40), nullable=False)
    reported_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
