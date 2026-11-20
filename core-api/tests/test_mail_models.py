from __future__ import annotations

import datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from core_api.bootstrap import run_migrations
from core_api.db import make_engine, make_session_factory
from core_api.models.mail import EmailOutbox, MailProvider, OutboxStatus

FIELDS = [{"name": "api_key", "label": "API key", "secret": True, "required": True}]
SETTINGS = {"api_key": "dummy-api-key-value"}
BODY = "Reset your password: https://example.test/reset?token=dummy-token-value"


@pytest.fixture
def migrated(tmp_path):
    """A database built by the real migrations, not Base.metadata.create_all."""
    db_url = f"sqlite:///{tmp_path}/mail.db"
    run_migrations(db_url)
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    yield session
    session.close()


def _provider(key: str, active: bool = False, settings: dict | None = None) -> MailProvider:
    return MailProvider(provider=key, label=key.title(), fields=FIELDS, settings=settings, is_active=active)


def test_two_active_providers_violate_the_partial_unique_index(migrated):
    migrated.add_all([_provider("smtp2go", active=True), _provider("other", active=True)])
    with pytest.raises(IntegrityError):
        migrated.commit()


def test_one_active_and_one_inactive_provider_is_allowed(migrated):
    migrated.add_all([_provider("smtp2go", active=True), _provider("other"), _provider("third")])
    migrated.commit()
    assert migrated.query(MailProvider).filter_by(is_active=True).count() == 1


def test_provider_settings_are_ciphertext_and_round_trip(migrated):
    migrated.add(_provider("smtp2go", settings=SETTINGS))
    migrated.commit()
    migrated.expire_all()

    raw = migrated.execute(sa.text("SELECT settings FROM mail_providers")).scalar()
    assert "dummy-api-key-value" not in raw
    row = migrated.get(MailProvider, "smtp2go")
    assert row.settings == SETTINGS
    assert row.fields == FIELDS
    assert row.declared_at is not None and row.updated_at is not None


def test_provider_settings_default_to_null(migrated):
    migrated.add(_provider("smtp2go"))
    migrated.commit()
    assert migrated.get(MailProvider, "smtp2go").settings is None


def test_outbox_body_is_ciphertext_and_round_trips(migrated):
    migrated.add(EmailOutbox(to_address="a@example.com", subject="Reset", body=BODY))
    migrated.commit()
    migrated.expire_all()

    raw = migrated.execute(sa.text("SELECT body FROM email_outbox")).scalar()
    assert "dummy-token-value" not in raw
    row = migrated.query(EmailOutbox).one()
    assert row.body == BODY
    assert row.status is OutboxStatus.PENDING
    assert row.attempts == 0
    assert row.last_error is None
    assert row.created_at is not None
    assert row.claimed_at is None and row.next_attempt_at is None and row.finished_at is None


def test_outbox_timestamps_round_trip(migrated):
    now = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)
    migrated.add(
        EmailOutbox(
            to_address="a@example.com",
            subject="s",
            body="b",
            status=OutboxStatus.SENT,
            claimed_at=now,
            next_attempt_at=now,
            finished_at=now,
        )
    )
    migrated.commit()
    assert migrated.query(EmailOutbox).one().status is OutboxStatus.SENT


def test_invalid_outbox_status_violates_the_check_constraint(migrated):
    # Raw SQL: the ORM's str_enum rejects a bad value before the database sees it.
    with pytest.raises(IntegrityError):
        migrated.execute(
            sa.text(
                "INSERT INTO email_outbox (to_address, subject, body, status, attempts, created_at) "
                "VALUES ('a@example.com', 's', 'b', 'bogus', 0, CURRENT_TIMESTAMP)"
            )
        )


def test_outbox_has_a_claim_index(migrated):
    indexes = sa.inspect(migrated.get_bind()).get_indexes("email_outbox")
    assert ["status", "next_attempt_at"] in [ix["column_names"] for ix in indexes]
