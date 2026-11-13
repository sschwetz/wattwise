"""Set and clear the Dashboard alerts services report (see models/alerts.py)."""

from __future__ import annotations

import datetime
from typing import Annotated, cast

from fastapi import Path
from sqlalchemy import CursorResult, delete, select
from sqlalchemy.orm import Session

from core_api.models.alerts import SUBJECT_SITE, Alert

# The path parameter naming an alert, for example "token" or "cert".
AlertCode = Annotated[str, Path(pattern=r"^[a-z0-9_]{1,64}$")]


def set_alert(db: Session, site_id: int, subject_kind: str, subject_id: int, code: str, message: str) -> None:
    """Sets the alert. A repeat report updates the message and keeps the original start time."""
    alert = db.scalar(
        select(Alert).where(Alert.subject_kind == subject_kind, Alert.subject_id == subject_id, Alert.code == code)
    )
    if alert is None:
        db.add(
            Alert(
                site_id=site_id,
                subject_kind=subject_kind,
                subject_id=subject_id,
                code=code,
                message=message,
                since=datetime.datetime.now(datetime.UTC),
            )
        )
    else:
        alert.message = message
    db.commit()


def clear_alert(db: Session, subject_kind: str, subject_id: int, code: str) -> None:
    result = db.execute(
        delete(Alert).where(Alert.subject_kind == subject_kind, Alert.subject_id == subject_id, Alert.code == code)
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
