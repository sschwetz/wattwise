from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from core_api.outbox import purge_outbox


def run_mail_outbox_cleanup_job(db: Session, now: datetime.datetime) -> None:
    """Deletes outbox messages that finished more than 30 days ago."""
    purge_outbox(db, now)
    db.commit()
