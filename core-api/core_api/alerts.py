"""Set and clear the Dashboard alerts services report (see models/alerts.py)."""

from __future__ import annotations

import datetime
from typing import Annotated, cast

from fastapi import Path
from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from core_api.models.alerts import SEVERITY_FAILURE, SUBJECT_SITE, Alert

# The path parameter naming an alert, for example "token" or "cert".
AlertCode = Annotated[str, Path(pattern=r"^[a-z0-9_]{1,64}$")]


def set_alert(
    db: Session,
    site_id: int,
    subject_kind: str,
    subject_id: int,
    code: str,
    message: str,
    severity: str = SEVERITY_FAILURE,
    sticky: bool = False,
) -> None:
    """Raises the alert. An unresolved alert keeps its start time and takes the new message and
    severity. A resolved or sticky row is replaced in place, with a start time of now."""
    now = datetime.datetime.now(datetime.UTC)
    alert = db.scalar(
        select(Alert).where(Alert.subject_kind == subject_kind, Alert.subject_id == subject_id, Alert.code == code)
    )
    if alert is None:
        alert = Alert(site_id=site_id, subject_kind=subject_kind, subject_id=subject_id, code=code, since=now)
        db.add(alert)
    elif sticky or alert.cleared_at is not None:
        alert.since = now
    alert.message = message
    alert.severity = severity
    alert.sticky = sticky
    alert.cleared_at = None
    db.commit()


def clear_alert(db: Session, subject_kind: str, subject_id: int, code: str) -> None:
    """Marks the alert resolved. Sticky and already-cleared rows are left as they are."""
    result = db.execute(
        update(Alert)
        .where(
            Alert.subject_kind == subject_kind,
            Alert.subject_id == subject_id,
            Alert.code == code,
            Alert.sticky.is_(False),
            Alert.cleared_at.is_(None),
        )
        .values(cleared_at=datetime.datetime.now(datetime.UTC))
    )
    if cast(CursorResult, result).rowcount:
        db.commit()


def report_notification_result(db: Session, site_id: int, sent: bool) -> None:
    """Shows an alert while schedule notification emails fail to send, and clears it on the next that sends."""
    if sent:
        clear_alert(db, SUBJECT_SITE, site_id, "notify_email")
    else:
        message = "A schedule notification email could not be sent. Check the mail relay settings."
        set_alert(db, site_id, SUBJECT_SITE, site_id, "notify_email", message)
