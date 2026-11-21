from __future__ import annotations

import sqlalchemy as sa

from core_api.bootstrap import run_migrations
from core_api.db import make_engine, make_session_factory
from core_api.encrypted_types import EncryptedText, encrypt_text
from core_api.models.mail import EmailOutbox, OutboxStatus

PREVIOUS_REVISION = "f1a3c5e7b9d2"
LONG_BODY = "Your schedule failed. " + "x" * 4978


def _body_type(engine) -> sa.types.TypeEngine:
    columns = {column["name"]: column for column in sa.inspect(engine).get_columns("email_outbox")}
    return columns["body"]["type"]


def test_the_model_body_is_encrypted_text():
    assert isinstance(EmailOutbox.__table__.c.body.type, EncryptedText)


def test_upgrade_turns_the_body_into_text_and_keeps_existing_rows(tmp_path):
    db_url = f"sqlite:///{tmp_path}/migrate.db"
    run_migrations(db_url, PREVIOUS_REVISION)
    engine = make_engine(db_url)
    assert isinstance(_body_type(engine), sa.String) and _body_type(engine).length == 1024
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO email_outbox (to_address, subject, body, status, attempts) VALUES (:t, :s, :b, :st, 0)"
            ),
            {"t": "a@example.com", "s": "Reset", "b": encrypt_text("old body"), "st": "pending"},
        )

    run_migrations(db_url)

    assert isinstance(_body_type(engine), sa.Text)
    with make_session_factory(engine)() as db:
        assert db.scalars(sa.select(EmailOutbox.body)).one() == "old body"
    engine.dispose()


def test_a_long_body_round_trips_and_is_stored_as_ciphertext(tmp_path):
    db_url = f"sqlite:///{tmp_path}/long.db"
    run_migrations(db_url)
    engine = make_engine(db_url)
    with make_session_factory(engine)() as db:
        row = EmailOutbox(to_address="a@example.com", subject="Failed", body=LONG_BODY, status=OutboxStatus.PENDING)
        db.add(row)
        db.commit()
        row_id = row.id
    with make_session_factory(engine)() as db:
        assert db.get(EmailOutbox, row_id).body == LONG_BODY
    with engine.connect() as conn:
        raw = conn.execute(sa.text("SELECT body FROM email_outbox WHERE id = :id"), {"id": row_id}).scalar_one()
    assert raw != LONG_BODY and "x" * 50 not in raw
    assert len(raw) > len(LONG_BODY)
    engine.dispose()
