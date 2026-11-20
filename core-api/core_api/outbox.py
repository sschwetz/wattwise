"""The email outbox: queued mail that the mail actor claims, sends and reports on.

None of these functions commit; the caller owns the transaction.
"""

from __future__ import annotations

import datetime
from typing import Literal

from sqlalchemy import ColumnElement, and_, delete, or_, select, update
from sqlalchemy.orm import Session

from core_api.db import rows_affected
from core_api.errors import AppError
from core_api.models.mail import EmailOutbox, MailProvider, OutboxStatus

# A sending row whose claim is older than this is claimable again: the actor
# that took it is assumed dead.
CLAIM_TIMEOUT = datetime.timedelta(minutes=2)
MAX_ATTEMPTS = 5
RETRY_BASE = datetime.timedelta(seconds=30)
ERROR_LIMIT = 500
RETENTION = datetime.timedelta(days=30)


def has_active_provider(db: Session) -> bool:
    return db.scalar(select(MailProvider.provider).where(MailProvider.is_active)) is not None


def enqueue_email(db: Session, to: str, subject: str, body: str) -> EmailOutbox:
    """Add a pending message, due now. Flushes so the row has an id."""
    row = EmailOutbox(
        to_address=to,
        subject=subject,
        body=body,
        status=OutboxStatus.PENDING,
        attempts=0,
        next_attempt_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(row)
    db.flush()
    return row


def _stale(now: datetime.datetime) -> ColumnElement[bool]:
    return and_(EmailOutbox.status == OutboxStatus.SENDING, EmailOutbox.claimed_at < now - CLAIM_TIMEOUT)


def _claimable(now: datetime.datetime) -> ColumnElement[bool]:
    return or_(
        and_(EmailOutbox.status == OutboxStatus.PENDING, EmailOutbox.next_attempt_at <= now),
        and_(_stale(now), EmailOutbox.attempts < MAX_ATTEMPTS),
    )


def _fail_expired_claims(db: Session, now: datetime.datetime) -> None:
    """End rows whose last allowed claim expired without a result. Without
    this, a message that crashes the actor would be reclaimed forever. The
    SELECT first means a poll with nothing expired takes no write lock."""
    expired = and_(_stale(now), EmailOutbox.attempts >= MAX_ATTEMPTS)
    ids = list(db.scalars(select(EmailOutbox.id).where(expired)))
    if not ids:
        return
    db.execute(
        update(EmailOutbox)
        .where(EmailOutbox.id.in_(ids), expired)
        .values(status=OutboxStatus.FAILED, finished_at=now, body="", last_error="claim expired")
        .execution_options(synchronize_session=False)
    )


def _due_ids(db: Session, limit: int, now: datetime.datetime) -> list[int]:
    query = (
        select(EmailOutbox.id).where(_claimable(now)).order_by(EmailOutbox.next_attempt_at, EmailOutbox.id).limit(limit)
    )
    return list(db.scalars(query))


def claim_outbox(db: Session, limit: int, now: datetime.datetime) -> list[EmailOutbox]:
    """Mark up to `limit` due rows `sending` and return them. Each row is taken
    with its own conditional UPDATE that repeats the claimable test, so when
    two actors read the same due rows only one of them gets each row."""
    _fail_expired_claims(db, now)
    claimed = []
    for row_id in _due_ids(db, limit, now):
        result = db.execute(
            update(EmailOutbox)
            .where(EmailOutbox.id == row_id, _claimable(now))
            .values(status=OutboxStatus.SENDING, claimed_at=now, attempts=EmailOutbox.attempts + 1)
            .execution_options(synchronize_session=False)
        )
        if rows_affected(result) == 1:
            claimed.append(row_id)
    if not claimed:
        return []
    query = (
        select(EmailOutbox)
        .where(EmailOutbox.id.in_(claimed))
        .order_by(EmailOutbox.next_attempt_at, EmailOutbox.id)
        .execution_options(populate_existing=True)
    )
    return list(db.scalars(query))


def _finish_values(row: EmailOutbox, outcome: str, error: str | None, now: datetime.datetime) -> dict:
    if outcome == "sent":
        return {"status": OutboxStatus.SENT, "finished_at": now, "body": "", "last_error": None}
    stored_error = None if error is None else error[:ERROR_LIMIT]
    if row.attempts < MAX_ATTEMPTS:
        retry_at = now + RETRY_BASE * 2 ** (row.attempts - 1)
        return {"status": OutboxStatus.PENDING, "next_attempt_at": retry_at, "last_error": stored_error}
    return {"status": OutboxStatus.FAILED, "finished_at": now, "body": "", "last_error": stored_error}


def finish_outbox(
    db: Session,
    id: int,
    outcome: Literal["sent", "failed"],
    error: str | None,
    now: datetime.datetime,
) -> None:
    """Record the actor's result for a claimed row. `failed` puts the row back
    to pending with a doubling delay (30s after the first attempt) until the
    fifth attempt fails, which ends it `failed`. A finished row clears its body."""
    row = db.get(EmailOutbox, id)
    if row is None:
        raise AppError(404, "not_found", f"Outbox message {id} not found")
    if row.status is not OutboxStatus.SENDING:
        raise AppError(409, "not_sending", "This message is not being sent")
    result = db.execute(
        update(EmailOutbox)
        .where(EmailOutbox.id == id, EmailOutbox.status == OutboxStatus.SENDING)
        .values(**_finish_values(row, outcome, error, now))
        .execution_options(synchronize_session=False)
    )
    if rows_affected(result) != 1:
        raise AppError(409, "not_sending", "This message is not being sent")
    db.expire(row)


def purge_outbox(db: Session, now: datetime.datetime) -> int:
    """Delete sent and failed rows that finished more than 30 days ago."""
    result = db.execute(
        delete(EmailOutbox)
        .where(
            EmailOutbox.status.in_((OutboxStatus.SENT, OutboxStatus.FAILED)),
            EmailOutbox.finished_at < now - RETENTION,
        )
        .execution_options(synchronize_session=False)
    )
    return rows_affected(result)
