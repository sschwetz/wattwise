from __future__ import annotations

import datetime
import threading

import pytest
import sqlalchemy as sa

from core_api import outbox
from core_api.bootstrap import run_migrations
from core_api.db import make_engine, make_session_factory
from core_api.email_sender import FakeEmailSender
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


@pytest.mark.parametrize("attempts", [1, 2, 3, 4])
def test_a_stale_row_under_the_attempt_cap_is_reclaimed(db, attempts):
    row_id = _row(db, status=OutboxStatus.SENDING, attempts=attempts, claimed_at=NOW - datetime.timedelta(minutes=3))
    assert [r.id for r in outbox.claim_outbox(db, 10, NOW)] == [row_id]
    assert _reload(db, row_id).attempts == attempts + 1


def test_a_stale_row_at_the_attempt_cap_ends_failed(db):
    row_id = _row(db, status=OutboxStatus.SENDING, attempts=5, claimed_at=NOW - datetime.timedelta(minutes=3))
    assert outbox.claim_outbox(db, 10, NOW) == []
    db.commit()
    row = _reload(db, row_id)
    assert row.status is OutboxStatus.FAILED
    assert row.attempts == 5
    assert row.body == ""
    assert as_utc(row.finished_at) == NOW
    assert row.last_error == "claim expired"


def test_a_fresh_sending_row_at_the_attempt_cap_is_untouched(db):
    claimed_at = NOW - datetime.timedelta(minutes=1)
    row_id = _row(db, status=OutboxStatus.SENDING, attempts=5, claimed_at=claimed_at)
    assert outbox.claim_outbox(db, 10, NOW) == []
    db.commit()
    row = _reload(db, row_id)
    assert row.status is OutboxStatus.SENDING
    assert row.body == BODY
    assert row.finished_at is None and row.last_error is None
    assert as_utc(row.claimed_at) == claimed_at


def test_a_row_whose_claim_keeps_expiring_ends_failed(db):
    row_id = _row(db)
    now = NOW
    for attempt in range(1, 6):
        assert [r.id for r in outbox.claim_outbox(db, 10, now)] == [row_id]
        db.commit()
        assert _reload(db, row_id).attempts == attempt
        now += datetime.timedelta(minutes=3)

    assert outbox.claim_outbox(db, 10, now) == []
    db.commit()
    row = _reload(db, row_id)
    assert row.status is OutboxStatus.FAILED
    assert row.body == ""
    assert row.last_error == "claim expired"
    assert outbox.claim_outbox(db, 10, now + datetime.timedelta(minutes=3)) == []


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


def test_purge_removes_finished_rows_that_finished_more_than_30_days_ago(db):
    old = NOW - datetime.timedelta(days=31)
    recent = NOW - datetime.timedelta(days=29)
    _row(db, status=OutboxStatus.SENT, body="", created_at=old, finished_at=old)
    _row(db, status=OutboxStatus.FAILED, body="", created_at=old, finished_at=old)
    keep = {
        _row(db, status=OutboxStatus.SENT, body="", created_at=old, finished_at=recent),
        _row(db, status=OutboxStatus.FAILED, body="", created_at=old, finished_at=recent),
    }

    assert outbox.purge_outbox(db, NOW) == 2
    db.commit()
    assert {r.id for r in db.query(EmailOutbox)} == keep


def test_purge_removes_unfinished_rows_created_more_than_7_days_ago(db):
    eight_days = NOW - datetime.timedelta(days=8)
    six_days = NOW - datetime.timedelta(days=6)
    _row(db, created_at=eight_days, next_attempt_at=eight_days)
    _row(db, status=OutboxStatus.SENDING, created_at=eight_days, claimed_at=eight_days)
    keep = {
        _row(db, created_at=six_days, next_attempt_at=six_days),
        _row(db, status=OutboxStatus.SENDING, created_at=six_days, claimed_at=six_days),
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


def test_claim_fails_a_row_with_an_undecryptable_body_and_returns_the_others(db):
    bad = _row(db)
    good = _row(db)
    db.execute(sa.text("UPDATE email_outbox SET body = 'not-a-fernet-token' WHERE id = :id"), {"id": bad})
    db.commit()

    claimed = outbox.claim_outbox(db, 10, NOW)
    db.commit()

    assert [r.id for r in claimed] == [good]
    assert claimed[0].body == BODY
    row = _reload(db, bad)
    assert row.status is OutboxStatus.FAILED
    assert as_utc(row.finished_at) == NOW
    assert row.body == ""
    assert row.last_error == "undecryptable body"
    assert outbox.claim_outbox(db, 10, NOW) == []


class _NoLock:
    """Stands in for the drain lock, so two drains in one process overlap
    and only the per-row claim keeps them apart."""

    def acquire(self, blocking: bool = True) -> bool:
        return True

    def release(self) -> None:
        pass


class _HookedSender(FakeEmailSender):
    """Records sends and runs `hook` once, during the first send."""

    def __init__(self, hook) -> None:
        super().__init__()
        self._hook = hook

    def send(self, to: str, subject: str, body: str) -> None:
        hook, self._hook = self._hook, None
        if hook is not None:
            hook()
        super().send(to, subject, body)


def _drain(factory, sender) -> None:
    session = factory()
    try:
        outbox.relay_queued(session, sender, NOW)
    finally:
        session.close()


def test_two_overlapping_drains_send_each_row_once(factory, monkeypatch):
    seed = factory()
    ids = [_row(seed, to_address=f"user{n}@example.com") for n in range(10)]
    seed.close()
    monkeypatch.setattr(outbox, "_DRAIN_LOCK", _NoLock())
    # Both drains read the same rows before either claims one.
    barrier = threading.Barrier(2, timeout=10)
    real_candidates = outbox._relay_candidates

    def candidates_then_wait(db):
        found = real_candidates(db)
        barrier.wait()
        return found

    monkeypatch.setattr(outbox, "_relay_candidates", candidates_then_wait)
    sender = FakeEmailSender()
    threads = [threading.Thread(target=_drain, args=(factory, sender)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(to for to, _, _ in sender.sent) == sorted(f"user{n}@example.com" for n in range(10))
    check = factory()
    assert {_reload(check, row_id).status for row_id in ids} == {OutboxStatus.SENT}
    check.close()


def test_a_drain_started_while_another_runs_sends_nothing(factory, db):
    _row(db, to_address="first@example.com")
    _row(db, to_address="second@example.com")
    inner = FakeEmailSender()
    outer = _HookedSender(lambda: _drain(factory, inner))
    _drain(factory, outer)
    assert inner.sent == []
    assert [to for to, _, _ in outer.sent] == ["first@example.com", "second@example.com"]


def test_a_row_the_actor_claims_mid_drain_is_skipped(factory, db):
    first = _row(db, to_address="first@example.com")
    second = _row(db, to_address="second@example.com")

    def actor_claims_the_second():
        actor = factory()
        claimed = [row.id for row in outbox.claim_outbox(actor, 10, NOW)]
        actor.commit()
        actor.close()
        assert claimed == [second]

    sender = _HookedSender(actor_claims_the_second)
    _drain(factory, sender)
    assert [to for to, _, _ in sender.sent] == ["first@example.com"]
    assert _reload(db, first).status is OutboxStatus.SENT
    row = _reload(db, second)
    assert (row.status, row.attempts, row.body) == (OutboxStatus.SENDING, 1, BODY)


def test_a_stale_sending_row_is_drained(factory, db):
    row_id = _row(db, status=OutboxStatus.SENDING, attempts=1, claimed_at=NOW - datetime.timedelta(minutes=10))
    sender = FakeEmailSender()
    _drain(factory, sender)
    assert len(sender.sent) == 1
    assert _reload(db, row_id).status is OutboxStatus.SENT


def test_a_row_deleted_mid_drain_is_skipped_and_the_others_still_go(factory, db):
    _row(db, to_address="first@example.com")
    doomed = _row(db, to_address="doomed@example.com")
    _row(db, to_address="third@example.com")

    def purge_the_second():
        other = factory()
        other.execute(sa.delete(EmailOutbox).where(EmailOutbox.id == doomed))
        other.commit()
        other.close()

    sender = _HookedSender(purge_the_second)
    _drain(factory, sender)
    assert [to for to, _, _ in sender.sent] == ["first@example.com", "third@example.com"]


def test_a_database_error_on_one_row_does_not_stop_the_drain(factory, db, monkeypatch):
    broken = _row(db, to_address="broken@example.com")
    good = _row(db, to_address="good@example.com")
    real_claim = outbox._claim_for_relay

    def claim_or_fail(session, candidate, now):
        if candidate.id == broken:
            raise sa.exc.OperationalError("UPDATE email_outbox", {}, Exception("database is locked"))
        return real_claim(session, candidate, now)

    monkeypatch.setattr(outbox, "_claim_for_relay", claim_or_fail)
    sender = FakeEmailSender()
    _drain(factory, sender)
    assert [to for to, _, _ in sender.sent] == ["good@example.com"]
    assert _reload(db, broken).status is OutboxStatus.PENDING
    assert _reload(db, good).status is OutboxStatus.SENT
