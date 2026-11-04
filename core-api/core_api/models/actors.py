from __future__ import annotations

import datetime
import enum

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from core_api.encrypted_types import EncryptedJSON
from core_api.models.base import Base, enum_check, str_enum


class RunKind(str, enum.Enum):
    ACTION = "action"
    RESTORE = "restore"
    TEST = "test"


class RunOutcome(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"
    OVERRIDDEN = "overridden"


class ActorInstance(Base):
    """One signed-in device account (for example an Actron Neo unit) at a site."""

    __tablename__ = "actor_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Encrypted at rest: holds the provider refresh token.
    config: Mapped[dict] = mapped_column(EncryptedJSON, nullable=False, default=dict)
    # Sign-in code and link while waiting for approval, or "needs_sign_in". Cleared on approval.
    auth_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    capabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    capabilities_updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_readings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Set on each call from the actor.
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActorSchedule(Base):
    __tablename__ = "actor_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("actor_instances.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trigger: Mapped[dict] = mapped_column(JSON, nullable=False)
    conditions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    action: Mapped[dict] = mapped_column(JSON, nullable=False)
    restore: Mapped[dict] = mapped_column(JSON, nullable=False)
    retry: Mapped[dict] = mapped_column(JSON, nullable=False)
    notify_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ActorRun(Base):
    __tablename__ = "actor_runs"
    __table_args__ = (
        enum_check("kind", RunKind, "ck_actor_runs_kind"),
        enum_check("outcome", RunOutcome, "ck_actor_runs_outcome"),
        # A restart or a second actor process cannot run the same trigger twice.
        UniqueConstraint("schedule_id", "kind", "trigger_at", name="uq_actor_runs_schedule_kind_trigger"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("actor_schedules.id"), nullable=False, index=True)
    kind: Mapped[RunKind] = mapped_column(str_enum(RunKind, length=16), nullable=False)
    trigger_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[RunOutcome] = mapped_column(str_enum(RunOutcome, length=16), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Restore rows only: the action run they undo.
    parent_run_id: Mapped[int | None] = mapped_column(ForeignKey("actor_runs.id"), nullable=True)
    # State saved before acting.
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Set when the watchdog has emailed about this run.
    watchdog_notified_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
