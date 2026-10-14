from __future__ import annotations

import datetime

import sqlalchemy as sa

from core_api.bootstrap import run_migrations
from core_api.db import make_engine, make_session_factory
from core_api.encrypted_types import decrypt_text, encrypt_text
from core_api.models import Source, SourceKind, User

ENVOY_CONFIG = {"host": "10.0.0.2", "token": "tok-123", "password": "hunter2"}


def _raw(engine, sql: str):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql)).scalar()


def test_encrypt_text_round_trips_and_differs_from_plaintext():
    token = encrypt_text("hunter2")
    assert "hunter2" not in token
    assert decrypt_text(token) == "hunter2"


def test_source_config_and_amber_key_are_ciphertext_in_the_database(db, user, site):
    user.amber_api_key = "amber-secret"
    user.totp_secret = "JBSWY3DPEHPK3PXP"
    user.pending_totp_secret = "KRSXG5CTMVRXEZLU"
    db.add(Source(site_id=site.id, kind=SourceKind.ENERGY, provider="envoy", config=ENVOY_CONFIG,
                  effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)))
    db.commit()
    db.expire_all()

    engine = db.get_bind()
    raw_config = _raw(engine, "SELECT config FROM sources")
    assert "hunter2" not in raw_config and "tok-123" not in raw_config
    assert "amber-secret" not in _raw(engine, "SELECT amber_api_key FROM users")
    assert "JBSWY3DPEHPK3PXP" not in _raw(engine, "SELECT totp_secret FROM users")
    assert "KRSXG5CTMVRXEZLU" not in _raw(engine, "SELECT pending_totp_secret FROM users")
    assert db.query(Source).one().config == ENVOY_CONFIG
    assert db.get(User, user.id).totp_secret == "JBSWY3DPEHPK3PXP"
    assert db.get(User, user.id).amber_api_key == "amber-secret"


def test_migration_encrypts_existing_plaintext_rows(tmp_path):
    db_url = f"sqlite:///{tmp_path}/legacy.db"
    run_migrations(db_url, "f3b7d2a9c814")
    engine = make_engine(db_url)
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
        conn.execute(sa.text("INSERT INTO users (id, email, amber_api_key, totp_secret, totp_enabled, email_confirmed) VALUES (1, 'a@example.com', 'amber-secret', 'JBSWY3DPEHPK3PXP', 0, 1)"))
        conn.execute(sa.text(
            "INSERT INTO sources (id, site_id, kind, provider, config, enabled, effective_from) "
            "VALUES (1, 1, 'energy', 'envoy', '{\"password\": \"hunter2\"}', 1, '2026-01-01 00:00:00')"))
    run_migrations(db_url)

    assert "hunter2" not in _raw(engine, "SELECT config FROM sources")
    assert "amber-secret" not in _raw(engine, "SELECT amber_api_key FROM users")
    assert "JBSWY3DPEHPK3PXP" not in _raw(engine, "SELECT totp_secret FROM users")
    with make_session_factory(engine)() as session:
        assert session.query(Source).one().config == {"password": "hunter2"}
        assert session.query(User).one().amber_api_key == "amber-secret"
        assert session.query(User).one().totp_secret == "JBSWY3DPEHPK3PXP"


def test_totp_migration_encrypts_secrets_left_plaintext_by_an_earlier_revision(tmp_path):
    db_url = f"sqlite:///{tmp_path}/stale.db"
    run_migrations(db_url, "d3a7b9c1e4f8")
    engine = make_engine(db_url)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO users (id, email, totp_secret, totp_enabled, email_confirmed) VALUES (1, 'a@example.com', 'JBSWY3DPEHPK3PXP', 1, 1)"))
    run_migrations(db_url)

    assert "JBSWY3DPEHPK3PXP" not in _raw(engine, "SELECT totp_secret FROM users")
    with make_session_factory(engine)() as session:
        assert session.query(User).one().totp_secret == "JBSWY3DPEHPK3PXP"
