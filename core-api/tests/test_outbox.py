from __future__ import annotations

import datetime
import threading

import pytest
import sqlalchemy as sa

from core_api import outbox
from core_api.bootstrap import run_migrations
from core_api.db import make_engine, make_session_factory
from core_api.errors import AppError
from core_api.models.mail import EmailOutbox, OutboxStatus
from core_api.time_utils import as_utc

NOW = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.UTC)
BODY = "Reset your password: https://example.test/reset?token=dummy-token-value"


@pytest.fixture
def factory(tmp_path):
    db_url = f"sqlite:///{tmp_path}/outbox.db"
    run_migrations(db_url)
    return make_session_factory(make_engine(db_url))


@pytest.fixture
def db(factory):
    session = factory()
    yield session
    session.close()


def _row(db, **fields) -> int:
    fields.setdefault("to_address", "a@example.com")
    fields.setdefault("subject", "Reset")
    fields.setdefault("body", BODY)
    fields.setdefault("next_attempt_at", NOW)
    row = EmailOutbox(**fields)
    db.add(row)
    db.commit()
    return row.id


def _reload(db, row_id: int) -> EmailOutbox:
    db.expire_all()
    row = db.get(EmailOutbox, row_id)
    assert row is not None
    return row


def test_enqueue_adds_a_pending_row_due_now_without_committing(db, factory):
    row = outbox.enqueue_email(db, "a@example.com", "Reset", BODY)
    assert row.id is not None
    assert row.status is OutboxStatus.PENDING
    assert row.attempts == 0
    assert row.next_attempt_at is not None
    assert abs(as_utc(row.next_attempt_at) - datetime.datetime.now(datetime.UTC)) < datetime.timedelta(seconds=5)

    other = factory()
    try:
        assert other.query(EmailOutbox).count() == 0
    finally:
        other.close()
    db.commit()


def test_claim_marks_rows_sending_and_a_second_claim_returns_none(db):
    first = _row(db)
    second = _row(db)

    claimed = outbox.claim_outbox(db, 10, NOW)
    db.commit()
    assert [r.id for r in claimed] == [first, second]
    for row_id in (first, second):
        row = _reload(db, row_id)
        assert row.status is OutboxStatus.SENDING
        assert as_utc(row.claimed_at) == NOW
        assert row.attempts == 1

    assert outbox.claim_outbox(db, 10, NOW) == []


def test_claim_orders_by_next_attempt_and_respects_the_limit(db):
    later = _row(db, next_attempt_at=NOW - datetime.timedelta(seconds=10))
    earlier = _row(db, next_attempt_at=NOW - datetime.timedelta(seconds=20))
    _row(db, next_attempt_at=NOW)

    claimed = outbox.claim_outbox(db, 2, NOW)
    assert [r.id for r in claimed] == [earlier, later]


def test_a_future_row_is_not_claimable(db):
    _row(db, next_attempt_at=NOW + datetime.timedelta(seconds=1))
    assert outbox.claim_outbox(db, 10, NOW) == []


def test_a_stale_sending_row_is_claimable_again_and_a_fresh_one_is_not(db):
    stale = _row(db, status=OutboxStatus.SENDING, attempts=1, claimed_at=NOW - datetime.timedelta(minutes=3))
    _row(db, status=OutboxStatus.SENDING, attempts=1, claimed_at=NOW - datetime.timedelta(minutes=1))

    claimed = outbox.claim_outbox(db, 10, NOW)
    assert [r.id for r in claimed] == [stale]
    assert _reload(db, stale).attempts == 2


def test_finished_rows_are_not_claimable(db):
    _row(db, status=OutboxStatus.SENT, finished_at=NOW)
    _row(db, status=OutboxStatus.FAILED, finished_at=NOW)
    assert outbox.claim_outbox(db, 10, NOW) == []


def test_claimed_body_is_plaintext_while_the_column_is_not(db):
    _row(db)
    claimed = outbox.claim_outbox(db, 10, NOW)
    assert claimed[0].body == BODY
    raw = db.execute(sa.text("SELECT body FROM email_outbox")).scalar()
    assert "dummy-token-value" not in raw


def test_sent_clears_the_body_and_the_error(db):
    row_id = _row(db, status=OutboxStatus.SENDING, attempts=2, claimed_at=NOW, last_error="earlier failure")
    outbox.finish_outbox(db, row_id, "sent", None, NOW)
    db.commit()
    row = _reload(db, row_id)
    assert row.status is OutboxStatus.SENT
    assert row.body == ""
    assert row.last_error is None
    assert as_utc(row.finished_at) == NOW


def test_failures_back_off_then_end_failed_with_the_body_cleared(db):
    row_id = _row(db)
    now = NOW
    for expected in (30, 60, 120, 240):
        claimed = outbox.claim_outbox(db, 10, now)
        assert [r.id for r in claimed] == [row_id]
        outbox.finish_outbox(db, row_id, "failed", "smtp 451", now)
        db.commit()
        row = _reload(db, row_id)
        assert row.status is OutboxStatus.PENDING
        assert as_utc(row.next_attempt_at) == now + datetime.timedelta(seconds=expected)
        assert row.last_error == "smtp 451"
        assert row.body == BODY
        assert row.finished_at is None
        now = as_utc(row.next_attempt_at)

    outbox.claim_outbox(db, 10, now)
    outbox.finish_outbox(db, row_id, "failed", "smtp 550", now)
    db.commit()
    row = _reload(db, row_id)
    assert row.status is OutboxStatus.FAILED
    assert row.attempts == 5
    assert row.body == ""
    assert row.last_error == "smtp 550"
    assert as_utc(row.finished_at) == now


def test_the_stored_error_is_truncated_to_500_characters(db):
    row_id = _row(db, status=OutboxStatus.SENDING, attempts=1, claimed_at=NOW)
    error = "  " + "x" * 600
    outbox.finish_outbox(db, row_id, "failed", error, NOW)
    db.commit()
    assert _reload(db, row_id).last_error == error[:500]


def test_finishing_a_row_that_is_not_sending_is_409_and_changes_nothing(db):
    row_id = _row(db, status=OutboxStatus.SENT, attempts=1, body="", finished_at=NOW)
    with pytest.raises(AppError) as info:
        outbox.finish_outbox(db, row_id, "failed", "late", NOW + datetime.timedelta(minutes=1))
    assert (info.value.status_code, info.value.code) == (409, "not_sending")
    row = _reload(db, row_id)
    assert row.status is OutboxStatus.SENT
    assert row.last_error is None
    assert as_utc(row.finished_at) == NOW


def test_finishing_an_unknown_row_is_404(db):
    with pytest.raises(AppError) as info:
        outbox.finish_outbox(db, 9999, "sent", None, NOW)
    assert (info.value.status_code, info.value.code) == (404, "not_found")


def test_purge_removes_only_finished_rows_older_than_30_days(db):
    old = NOW - datetime.timedelta(days=31)
    recent = NOW - datetime.timedelta(days=29)
    _row(db, status=OutboxStatus.SENT, body="", finished_at=old)
    _row(db, status=OutboxStatus.FAILED, body="", finished_at=old)
    keep = {
        _row(db, status=OutboxStatus.SENT, body="", finished_at=recent),
        _row(db, status=OutboxStatus.FAILED, body="", finished_at=recent),
        _row(db, created_at=old, next_attempt_at=old),
        _row(db, status=OutboxStatus.SENDING, created_at=old, claimed_at=old),
    }

    assert outbox.purge_outbox(db, NOW) == 2
    db.commit()
    assert {r.id for r in db.query(EmailOutbox)} == keep


def test_two_sessions_claiming_at_once_never_take_the_same_row(factory, monkeypatch):
    seed = factory()
    ids = {_row(seed) for _ in range(20)}
    seed.close()

    # Both sessions read the same due rows before either updates one, so the
    # conditional UPDATE is the only thing keeping them apart.
    barrier = threading.Barrier(2, timeout=10)
    real_due_ids = outbox._due_ids

    def due_ids_then_wait(*args):
        found = real_due_ids(*args)
        barrier.wait()
        return found

    monkeypatch.setattr(outbox, "_due_ids", due_ids_then_wait)
    results: list[list[int]] = [[], []]
    errors: list[BaseException] = []

    def claim(slot: int) -> None:
        session = factory()
        try:
            results[slot] = [r.id for r in outbox.claim_outbox(session, 50, NOW)]
            session.commit()
        except BaseException as exc:  # surfaced below; a thread cannot raise into the test
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=claim, args=(slot,)) for slot in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    combined = results[0] + results[1]
    assert len(combined) == len(set(combined))
    assert set(combined) == ids
