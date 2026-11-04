from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from core_api.models.actors import ActorRun, ActorSchedule, RunKind, RunOutcome

_OPEN_ACTION_OUTCOMES = (RunOutcome.PENDING, RunOutcome.RUNNING, RunOutcome.OK)
_CLOSING_RESTORE_OUTCOMES = (RunOutcome.OK, RunOutcome.SKIPPED, RunOutcome.FAILED)


def unfinished_action_runs(db: Session, instance_id: int) -> list[ActorRun]:
    """Action runs on this instance that still owe a restore: outcome pending,
    running or ok, with no restore child that is ok, skipped or failed."""
    restore = aliased(ActorRun)
    closed = exists().where(
        restore.parent_run_id == ActorRun.id,
        restore.kind == RunKind.RESTORE,
        restore.outcome.in_(_CLOSING_RESTORE_OUTCOMES),
    )
    stmt = (
        select(ActorRun)
        .join(ActorSchedule, ActorSchedule.id == ActorRun.schedule_id)
        .where(
            ActorSchedule.instance_id == instance_id,
            ActorRun.kind == RunKind.ACTION,
            ActorRun.outcome.in_(_OPEN_ACTION_OUTCOMES),
            ~closed,
        )
        .order_by(ActorRun.id)
    )
    return list(db.scalars(stmt))


def has_unfinished_action_run(db: Session, instance_id: int) -> bool:
    return bool(unfinished_action_runs(db, instance_id))
