"""Emails the site owner once about each action run that is holding a unit
off past its restore time, or whose actor has stopped reporting.

The run is marked before the email is sent and a failed send is not retried,
so a mail outage never turns into repeated emails about the same run."""

from __future__ import annotations

import datetime
from typing import cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from core_api.actor_email import site_local_time, site_owner
from core_api.actor_failures import HeldOffFailure, held_off_failures
from core_api.email_sender import EmailSender, try_send_email
from core_api.models.actors import ActorInstance, ActorRun
from core_api.models.identity import Site
from core_api.time_utils import as_utc


def run_actor_watchdog_job(db: Session, now: datetime.datetime, email_sender: EmailSender) -> None:
    now = as_utc(now)
    for instance, site in db.execute(select(ActorInstance, Site).join(Site, Site.id == ActorInstance.site_id)).all():
        for held in held_off_failures(db, site, instance, now):
            if held.schedule.notify_email and held.run.watchdog_notified_at is None:
                _notify_once(db, site, held, now, email_sender)


def _notify_once(
    db: Session, site: Site, held: HeldOffFailure, now: datetime.datetime, email_sender: EmailSender
) -> None:
    """Claim the run with a conditional update and email only if the claim won,
    so two job runs never both email about it."""
    result = cast(
        CursorResult,
        db.execute(
            update(ActorRun)
            .where(ActorRun.id == held.run.id, ActorRun.watchdog_notified_at.is_(None))
            .values(watchdog_notified_at=now)
        ),
    )
    claimed = result.rowcount
    db.commit()
    if claimed != 1:
        return
    owner = site_owner(db, site)
    if owner is None:
        return
    subject, body = _email_text(site, held)
    try_send_email(email_sender, owner.email, subject, body, "actor watchdog")


def _email_text(site: Site, held: HeldOffFailure) -> tuple[str, str]:
    name = held.schedule.name
    when = site_local_time(held.failure.since, site)
    if held.failure.kind == "still_off":
        problem = f"The unit is still off. It should have been turned back on at {when}."
    else:
        problem = f"The unit is held off and the actor has not reported since {when}."
    body = (
        f'The schedule "{name}" for the site "{site.name}" needs your attention.\n\n'
        f"{problem}\n\n"
        "Check the unit and turn it back on if you need it.\n"
    )
    return f"Schedule needs attention: {name}", body
