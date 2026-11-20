from __future__ import annotations

import datetime
import enum

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, false, func, text
from sqlalchemy.orm import Mapped, mapped_column

from core_api.encrypted_types import EncryptedJSON, EncryptedString
from core_api.models.base import Base, enum_check, str_enum


class OutboxStatus(str, enum.Enum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


class MailProvider(Base):
    """One mail provider the mail actor has declared."""

    __tablename__ = "mail_providers"
    __table_args__ = (
        # At most one active provider.
        Index(
            "ix_mail_providers_one_active",
            "is_active",
            unique=True,
            sqlite_where=text("is_active"),
            postgresql_where=text("is_active"),
        ),
    )

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    # List of {name, label, secret, required} from the actor's declaration.
    fields: Mapped[list] = mapped_column(JSON, nullable=False)
    # Encrypted at rest: field name to value. Null until saved.
    settings: Mapped[dict | None] = mapped_column(EncryptedJSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    declared_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    __table_args__ = (
        enum_check("status", OutboxStatus, "ck_email_outbox_status"),
        # Serves the claim query: due rows by status.
        Index("ix_email_outbox_status_next_attempt_at", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    to_address: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    # Encrypted at rest: bodies hold reset and confirmation links.
    body: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        str_enum(OutboxStatus, length=16), nullable=False, default=OutboxStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claimed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
