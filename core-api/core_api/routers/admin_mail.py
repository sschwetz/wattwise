from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core_api import admin_mail, admin_users
from core_api.admin_mail_schemas import OutboxSummaryOut, ProviderOut, ProviderSettingsIn, TestQueuedOut
from core_api.deps import get_db_session, get_relay_sender, require_admin
from core_api.email_sender import EmailSender
from core_api.models.identity import User
from core_api.models.mail import MailProvider
from core_api.outbox import has_active_provider, relay_queued

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/mail", tags=["admin"], dependencies=[Depends(require_admin)])


def _commit_and_report(db: Session, admin: User, row: MailProvider, action: str) -> ProviderOut:
    """Every provider write ends here: commit, log the action, return the provider."""
    db.commit()
    db.refresh(row)
    admin_users.log_admin_action(admin, None, action)
    return admin_mail.present_provider(db, row)


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(db: Session = Depends(get_db_session)) -> list[ProviderOut]:
    return admin_mail.list_providers(db)


@router.put("/providers/{provider}", response_model=ProviderOut)
def save_provider_settings(
    provider: str,
    payload: ProviderSettingsIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
) -> ProviderOut:
    row = admin_mail.get_provider_or_404(db, provider)
    admin_mail.save_settings(row, payload.settings)
    return _commit_and_report(db, admin, row, "save_mail_settings")


@router.post("/providers/{provider}/activate", response_model=ProviderOut)
def activate_provider(
    provider: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
) -> ProviderOut:
    row = admin_mail.get_provider_or_404(db, provider)
    admin_mail.activate(db, row)
    return _commit_and_report(db, admin, row, "activate_mail_provider")


@router.post("/providers/{provider}/deactivate", response_model=ProviderOut)
def deactivate_provider(
    provider: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db_session),
    relay_sender: EmailSender = Depends(get_relay_sender),
) -> ProviderOut:
    """With no provider left active, queued mail goes out through the relay
    now, after the deactivation is committed. A relay problem never changes
    the answer: the deactivation has already happened."""
    row = admin_mail.get_provider_or_404(db, provider)
    admin_mail.deactivate(row)
    out = _commit_and_report(db, admin, row, "deactivate_mail_provider")
    _relay_if_no_provider(db, relay_sender)
    return out


def _relay_if_no_provider(db: Session, relay_sender: EmailSender) -> None:
    try:
        if has_active_provider(db):
            return
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning("Relay of queued mail skipped: %s", type(exc).__name__)
        return
    relay_queued(db, relay_sender, datetime.datetime.now(datetime.UTC))


@router.post("/test", response_model=TestQueuedOut, status_code=status.HTTP_202_ACCEPTED)
def send_test_email(admin: User = Depends(require_admin), db: Session = Depends(get_db_session)) -> TestQueuedOut:
    admin_mail.queue_test_email(db, admin)
    db.commit()
    admin_users.log_admin_action(admin, None, "send_test_email")
    return TestQueuedOut()


@router.get("/outbox/summary", response_model=OutboxSummaryOut)
def outbox_summary(db: Session = Depends(get_db_session)) -> OutboxSummaryOut:
    return admin_mail.outbox_summary(db)
