from __future__ import annotations

import datetime
import enum

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from core_api.encrypted_types import EncryptedJSON
from core_api.models.base import Base, enum_check, str_enum


class SourceKind(str, enum.Enum):
    ENERGY = "energy"
    PRICING = "pricing"
    WEATHER = "weather"


class Source(Base):
    """Unified pluggable connection abstraction shared by energy, pricing, and
    weather integrations — see 'Sources' in the design spec."""

    __tablename__ = "sources"
    __table_args__ = (
        enum_check("kind", SourceKind, "ck_sources_kind"),
        # At most one weather source per site (spec: 'Sources' invariants).
        Index(
            "uq_sources_site_weather",
            "site_id",
            unique=True,
            sqlite_where=text("kind = 'weather'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    kind: Mapped[SourceKind] = mapped_column(str_enum(SourceKind, length=16), nullable=False)
    # Open-ended list (spec: "envoy, amber, weatherlink_live, ..."). Not a DB
    # enum: each new provider means new collector code + a deploy anyway, but
    # shouldn't additionally require a schema migration to add the value.
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    # Free-text label distinguishing multiple sources of the same kind+provider
    # at one site (e.g. two Envoy units in different buildings) -- optional,
    # purely for the operator's own benefit, no uniqueness constraint.
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Encrypted at rest: holds device tokens and the Enlighten password.
    config: Mapped[dict] = mapped_column(EncryptedJSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
