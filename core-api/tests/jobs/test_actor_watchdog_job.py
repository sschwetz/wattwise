from __future__ import annotations

import datetime

from core_api.actor_failures import held_off_failures
from core_api.email_sender import FakeEmailSender
from core_api.jobs.actor_watchdog_job import _held_off_text, _notify_once, run_actor_watchdog_job
from core_api.models import ActorInstance, ActorSchedule, RunOutcome
from tests.test_actor_failures import NOW, make_instance, make_run, make_schedule


def stuck_run(db, site, *, notify=True, last_seen_at=NOW):
    schedule = make_schedule(db, make_instance(db, site, last_seen_at=last_seen_at), notify=notify, give_up=15)
    return make_run(db, schedule, NOW - datetime.timedelta(hours=2), RunOutcome.OK)


def test_watchdog_emails_once(db, site):
    run = stuck_run(db, site)
    sender = FakeEmailSender()
    run_actor_watchdog_job(db, NOW, sender)
    run_actor_watchdog_job(db, NOW + datetime.timedelta(minutes=1), sender)
    assert len(sender.sent) == 1
    to, subject, body = sender.sent[0]
    assert to == "owner@example.com"
    assert "Peak off" in subject and "Peak off" in body
    assert "Home" in body
    assert "still off" in body
    assert "secret" not in body
    db.refresh(run)
    assert run.watchdog_notified_at is not None


def test_watchdog_emails_silent_actor(db, site):
    stuck = make_schedule(db, make_instance(db, site, last_seen_at=NOW - datetime.timedelta(minutes=10)), notify=True)
    make_run(db, stuck, NOW - datetime.timedelta(minutes=20), RunOutcome.OK)
    sender = FakeEmailSender()
    run_actor_watchdog_job(db, NOW, sender)
    [(_, _, body)] = sender.sent
    assert "not reported" in body


def test_watchdog_skips_restore_disabled_run_with_silent_actor(db, site):
    instance = make_instance(db, site, last_seen_at=None)
    schedule = make_schedule(db, instance, restore={"enabled": False}, notify=True)
    run = make_run(db, schedule, NOW - datetime.timedelta(hours=2), RunOutcome.OK)
    sender = FakeEmailSender()
    run_actor_watchdog_job(db, NOW, sender)
    assert sender.sent == []
    db.refresh(run)
    assert run.watchdog_notified_at is None


def test_watchdog_silent_when_notify_off(db, site):
    run = stuck_run(db, site, notify=False)
    sender = FakeEmailSender()
    run_actor_watchdog_job(db, NOW, sender)
    assert sender.sent == []
    db.refresh(run)
    assert run.watchdog_notified_at is None


def test_watchdog_emails_a_failed_run_once(db, site):
    schedule = make_schedule(db, make_instance(db, site), notify=True)
    run = make_run(db, schedule, NOW - datetime.timedelta(hours=3), RunOutcome.FAILED)
    sender = FakeEmailSender()
    run_actor_watchdog_job(db, NOW, sender)
    run_actor_watchdog_job(db, NOW, sender)
    [(_, subject, body)] = sender.sent
    assert "Peak off" in subject and "unit did not answer" in body
    db.refresh(run)
    assert run.watchdog_notified_at is not None


def test_watchdog_ignores_a_failed_run_a_later_run_cleared(db, site):
    schedule = make_schedule(db, make_instance(db, site), notify=True)
    make_run(db, schedule, NOW - datetime.timedelta(hours=3), RunOutcome.FAILED)
    make_run(db, schedule, NOW - datetime.timedelta(minutes=30), RunOutcome.OK)
    sender = FakeEmailSender()
    run_actor_watchdog_job(db, NOW, sender)
    assert sender.sent == []


def test_watchdog_does_not_email_when_another_worker_claimed_the_run(db, site):
    run = stuck_run(db, site)
    run.watchdog_notified_at = NOW - datetime.timedelta(minutes=1)
    db.commit()
    sender = FakeEmailSender()
    run_actor_watchdog_job(db, NOW, sender)
    assert sender.sent == []


def test_watchdog_keeps_going_when_send_fails(db, site):
    class Broken:
        def send(self, to, subject, body):
            raise OSError("relay down")

    run = stuck_run(db, site)
    run_actor_watchdog_job(db, NOW, Broken())
    db.refresh(run)
    assert run.watchdog_notified_at is not None


def test_claim_lost_to_a_concurrent_job_sends_nothing(db, site):
    """The job saw the run unclaimed, but another job claimed it before the update ran."""
    run = stuck_run(db, site)
    instance = db.get(ActorInstance, db.get(ActorSchedule, run.schedule_id).instance_id)
    [held] = held_off_failures(db, site, instance, NOW)
    run.watchdog_notified_at = NOW
    db.commit()
    sender = FakeEmailSender()
    _notify_once(db, site, held.run, _held_off_text(site, held), NOW, sender)
    assert sender.sent == []
