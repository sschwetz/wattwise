"""Admin side of mail settings: provider settings, activation, test email and
the outbox summary. None of these functions commit; the router does."""

from __future__ import annotations

import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from core_api.admin_mail_schemas import OutboxSummaryOut, ProviderFieldOut, ProviderOut
from core_api.errors import AppError
from core_api.models.identity import User
from core_api.models.mail import EmailOutbox, MailProvider, OutboxStatus
from core_api.outbox import enqueue_email, has_active_provider

TEST_SUBJECT = "WattWise test email"
TEST_BODY = "This is a test email from WattWise.\nIf you can read it, mail delivery works."
TEST_LIMIT = 5
TEST_WINDOW = datetime.timedelta(minutes=10)
SUMMARY_WINDOW = datetime.timedelta(hours=24)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _field_out(field: dict, stored: dict[str, str]) -> ProviderFieldOut:
    value = stored.get(field["name"])
    return ProviderFieldOut(
        name=field["name"],
        label=field["label"],
        secret=field["secret"],
        required=field["required"],
        value=None if field["secret"] else value,
        is_set=bool(value),
    )


def _newest_declared_at(db: Session) -> datetime.datetime | None:
    return db.scalar(select(func.max(MailProvider.declared_at)))


def provider_out(row: MailProvider, newest: datetime.datetime | None) -> ProviderOut:
    stored = row.settings or {}
    return ProviderOut(
        provider=row.provider,
        label=row.label,
        available=row.declared_at == newest,
        is_active=row.is_active,
        fields=[_field_out(f, stored) for f in row.fields],
    )


def list_providers(db: Session) -> list[ProviderOut]:
    newest = _newest_declared_at(db)
    rows = db.scalars(select(MailProvider).order_by(MailProvider.provider)).all()
    return [provider_out(row, newest) for row in rows]


def present_provider(db: Session, row: MailProvider) -> ProviderOut:
    return provider_out(row, _newest_declared_at(db))


def get_provider_or_404(db: Session, provider: str) -> MailProvider:
    row = db.get(MailProvider, provider)
    if row is None:
        raise AppError(404, "not_found", f"Mail provider {provider} not found")
    return row


def _merge_settings(row: MailProvider, incoming: dict[str, str]) -> dict[str, str]:
    """Stored settings with the incoming values applied. A blank secret keeps
    the stored one; a blank plain value removes it."""
    fields = {f["name"]: f for f in row.fields}
    unknown = sorted(set(incoming) - set(fields))
    if unknown:
        raise AppError(422, "unknown_field", f"Unknown setting: {', '.join(unknown)}")
    merged = dict(row.settings or {})
    for name, value in incoming.items():
        if value:
            merged[name] = value
        elif not fields[name]["secret"]:
            merged.pop(name, None)
    return merged


def _missing_required(row: MailProvider, settings: dict[str, str]) -> list[str]:
    return [f["label"] for f in row.fields if f["required"] and not settings.get(f["name"])]


def save_settings(row: MailProvider, incoming: dict[str, str]) -> None:
    merged = _merge_settings(row, incoming)
    missing = _missing_required(row, merged)
    if missing:
        raise AppError(422, "field_required", f"Required: {', '.join(missing)}")
    row.settings = merged
    row.updated_at = _now()


def activate(db: Session, row: MailProvider) -> None:
    if row.declared_at != _newest_declared_at(db):
        raise AppError(409, "provider_unavailable", "The mail actor no longer offers this provider")
    if row.settings is None or _missing_required(row, row.settings):
        raise AppError(409, "settings_incomplete", "Save every required setting before activating")
    if row.is_active:
        return
    # Deactivate first: the partial unique index allows one active row.
    db.execute(
        update(MailProvider)
        .where(MailProvider.is_active)
        .values(is_active=False, updated_at=_now())
        .execution_options(synchronize_session="fetch")
    )
    row.is_active = True
    row.updated_at = _now()


def deactivate(row: MailProvider) -> None:
    if row.is_active:
        row.is_active = False
        row.updated_at = _now()


def _recent_test_count(db: Session, address: str) -> int:
    return _count(
        db,
        EmailOutbox.subject == TEST_SUBJECT,
        EmailOutbox.to_address == address,
        EmailOutbox.created_at >= _now() - TEST_WINDOW,
    )


def queue_test_email(db: Session, admin: User) -> None:
    if not has_active_provider(db):
        raise AppError(409, "no_active_provider", "Activate a mail provider first")
    if _recent_test_count(db, admin.email) >= TEST_LIMIT:
        raise AppError(429, "too_many_tests", "Too many test emails; try again in a few minutes")
    enqueue_email(db, admin.email, TEST_SUBJECT, TEST_BODY)


def _count(db: Session, *conditions) -> int:
    return db.scalar(select(func.count()).select_from(EmailOutbox).where(*conditions)) or 0


def outbox_summary(db: Session) -> OutboxSummaryOut:
    since = _now() - SUMMARY_WINDOW
    last_error = db.scalar(
        select(EmailOutbox.last_error)
        .where(EmailOutbox.last_error.is_not(None))
        .order_by(EmailOutbox.created_at.desc(), EmailOutbox.id.desc())
        .limit(1)
    )
    return OutboxSummaryOut(
        pending=_count(db, EmailOutbox.status.in_((OutboxStatus.PENDING, OutboxStatus.SENDING))),
        sent_24h=_count(db, EmailOutbox.status == OutboxStatus.SENT, EmailOutbox.finished_at >= since),
        failed_24h=_count(db, EmailOutbox.status == OutboxStatus.FAILED, EmailOutbox.finished_at >= since),
        last_error=last_error,
    )
