"""The email outbox: queued mail that the mail actor claims, sends and reports on.

None of these functions commit; the caller owns the transaction.
"""

from __future__ import annotations

import datetime
import logging
import threading
from typing import TYPE_CHECKING, Literal, NamedTuple

from cryptography.fernet import InvalidToken
from sqlalchemy import ColumnElement, and_, delete, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, defer

from core_api.db import rows_affected
from core_api.errors import AppError
from core_api.models.mail import EmailOutbox, MailProvider, OutboxStatus

if TYPE_CHECKING:
    from core_api.email_sender import EmailSender

logger = logging.getLogger(__name__)

# A sending row whose claim is older than this is claimable again: the actor
# that took it is assumed dead.
CLAIM_TIMEOUT = datetime.timedelta(minutes=2)
MAX_ATTEMPTS = 5
RETRY_BASE = datetime.timedelta(seconds=30)
ERROR_LIMIT = 500
RETENTION = datetime.timedelta(days=30)
# Unfinished rows older than this are deleted: no actor is sending them.
UNFINISHED_RETENTION = datetime.timedelta(days=7)
RELAY_LIMIT = 200
UNFINISHED = (OutboxStatus.PENDING, OutboxStatus.SENDING)
FINISHED = (OutboxStatus.SENT, OutboxStatus.FAILED)


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
    # The body loads lazily, so one undecryptable body fails only its own row.
    query = (
        select(EmailOutbox)
        .options(defer(EmailOutbox.body))
        .where(EmailOutbox.id.in_(claimed))
        .order_by(EmailOutbox.next_attempt_at, EmailOutbox.id)
        .execution_options(populate_existing=True)
    )
    return [row for row in db.scalars(query) if _body_readable(db, row, now)]


def _body_readable(db: Session, row: EmailOutbox, now: datetime.datetime) -> bool:
    """Load the row's body. When it cannot be decrypted, end the row `failed`."""
    try:
        row.body  # noqa: B018 - loads and decrypts the deferred column
    except InvalidToken:
        db.execute(
            update(EmailOutbox)
            .where(EmailOutbox.id == row.id)
            .values(status=OutboxStatus.FAILED, finished_at=now, body="", last_error="undecryptable body")
            .execution_options(synchronize_session=False)
        )
        db.expire(row)
        return False
    return True


def _finish_values(
    row: EmailOutbox, outcome: Literal["sent", "failed"], error: str | None, now: datetime.datetime
) -> dict[str, object]:
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


class _Candidate(NamedTuple):
    id: int
    status: OutboxStatus
    claimed_at: datetime.datetime | None


def _relay_candidates(db: Session) -> list[_Candidate]:
    query = (
        select(EmailOutbox.id, EmailOutbox.status, EmailOutbox.claimed_at)
        .where(EmailOutbox.status.in_(UNFINISHED))
        .order_by(EmailOutbox.id)
        .limit(RELAY_LIMIT)
    )
    return [_Candidate(*row) for row in db.execute(query)]


def _claim_for_relay(db: Session, candidate: _Candidate, now: datetime.datetime) -> bool:
    """Take the row for this drain and commit, if it still has the status and
    claim it had when the drain read it. False when the actor, another drain
    or the purge got to it first."""
    same_claim: ColumnElement[bool]
    if candidate.claimed_at is None:
        same_claim = EmailOutbox.claimed_at.is_(None)
    else:
        same_claim = EmailOutbox.claimed_at == candidate.claimed_at
    result = db.execute(
        update(EmailOutbox)
        .where(EmailOutbox.id == candidate.id, EmailOutbox.status == candidate.status, same_claim)
        .values(status=OutboxStatus.SENDING, claimed_at=now)
        .execution_options(synchronize_session=False)
    )
    if rows_affected(result) != 1:
        db.rollback()
        return False
    db.commit()
    return True


def _end_claimed(db: Session, row_id: int, now: datetime.datetime, values: dict[str, object]) -> None:
    """End a row this drain claimed, unless someone has claimed it since."""
    db.execute(
        update(EmailOutbox)
        .where(EmailOutbox.id == row_id, EmailOutbox.status == OutboxStatus.SENDING, EmailOutbox.claimed_at == now)
        .values(body="", finished_at=now, **values)
        .execution_options(synchronize_session=False)
    )


def _relay_one(db: Session, row_id: int, sender: EmailSender, now: datetime.datetime) -> None:
    """Send one claimed row through `sender` and end it. Logs only the row id
    and the exception class: a relay error text can hold the address."""
    query = select(EmailOutbox.to_address, EmailOutbox.subject, EmailOutbox.body).where(EmailOutbox.id == row_id)
    try:
        message = db.execute(query).one_or_none()
    except InvalidToken:
        _end_claimed(db, row_id, now, {"status": OutboxStatus.FAILED, "last_error": "undecryptable body"})
        return
    if message is None:
        return
    try:
        sender.send(to=message.to_address, subject=message.subject, body=message.body)
    except Exception as exc:
        logger.warning("Relay send failed for outbox message %s: %s", row_id, type(exc).__name__)
        _end_claimed(db, row_id, now, {"status": OutboxStatus.FAILED, "last_error": "relay send failed"})
        return
    _end_claimed(db, row_id, now, {"status": OutboxStatus.SENT, "last_error": None})


def _relay_candidate(db: Session, candidate: _Candidate, sender: EmailSender, now: datetime.datetime) -> None:
    """Claim, send and end one row. A database error rolls back and skips the row."""
    try:
        if _claim_for_relay(db, candidate, now):
            _relay_one(db, candidate.id, sender, now)
            db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning("Relay skipped outbox message %s: %s", candidate.id, type(exc).__name__)


# One drain at a time in this process. A second drain would read the first
# drain's claimed rows as `sending` and could take them again.
_DRAIN_LOCK = threading.Lock()


def relay_queued(db: Session, sender: EmailSender, now: datetime.datetime) -> None:
    """Send the oldest RELAY_LIMIT pending and sending rows through `sender`,
    for when no provider is left to send them. Unlike the rest of this module
    it commits: each row is claimed and committed before it is sent, and ended
    and committed after, so a row sent before a crash is not sent again. A
    drain that starts while another runs returns at once. Never raises."""
    if not _DRAIN_LOCK.acquire(blocking=False):
        return
    try:
        for candidate in _relay_candidates(db):
            _relay_candidate(db, candidate, sender, now)
    except Exception as exc:
        db.rollback()
        logger.warning("Relay of queued mail stopped: %s", type(exc).__name__)
    finally:
        _DRAIN_LOCK.release()


def purge_outbox(db: Session, now: datetime.datetime) -> int:
    """Delete sent and failed rows that finished more than 30 days ago, and
    unfinished rows created more than 7 days ago (no actor is sending them)."""
    result = db.execute(
        delete(EmailOutbox)
        .where(
            or_(
                and_(EmailOutbox.status.in_(FINISHED), EmailOutbox.finished_at < now - RETENTION),
                and_(EmailOutbox.status.in_(UNFINISHED), EmailOutbox.created_at < now - UNFINISHED_RETENTION),
            )
        )
        .execution_options(synchronize_session=False)
    )
    return rows_affected(result)
