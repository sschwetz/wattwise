from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from core_api.models.base import Base

SUBJECT_SOURCE = "source"
SUBJECT_INSTANCE = "instance"
SUBJECT_SITE = "site"

SEVERITY_FAILURE = "failure"
SEVERITY_PARTIAL = "partial"
SEVERITY_NOTICE = "notice"


class Alert(Base):
    """A problem a service can't fix itself, shown on the Dashboard until the
    service clears it. Clearing keeps the row and sets cleared_at, so the
    Dashboard can show it as resolved for an hour. A sticky alert stays until a
    user dismisses it. A subject holds at most one alert per code, so
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
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default=SEVERITY_FAILURE, server_default=SEVERITY_FAILURE)
    cleared_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sticky: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
