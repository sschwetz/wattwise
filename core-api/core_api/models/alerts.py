from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from core_api.models.base import Base

SUBJECT_SOURCE = "source"
SUBJECT_INSTANCE = "instance"
SUBJECT_SITE = "site"


class Alert(Base):
    """A problem a service can't fix itself, shown on the Dashboard until the
    service clears it. A subject holds at most one alert per code, so
    different problems on one source or actor instance show side by side.
    For subject_kind "site", subject_id is the site id."""

    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("subject_kind", "subject_id", "code", name="uq_alerts_subject_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[int] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    since: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
