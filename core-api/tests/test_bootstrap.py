from __future__ import annotations

import sqlite3

from core_api.bootstrap import run_migrations
from core_api.models import Base


def test_run_migrations_creates_every_table(tmp_path):
    db_path = tmp_path / "bootstrap_test.db"
    run_migrations(f"sqlite:///{db_path}")

    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "select name from sqlite_master where type='table' "
            "and name not like 'alembic%' and name not like 'sqlite_%'"
        ).fetchall()
    finally:
        con.close()

    actual_tables = {row[0] for row in rows}
    expected_tables = set(Base.metadata.tables.keys())
    assert actual_tables == expected_tables


def test_run_migrations_is_safe_to_run_twice(tmp_path):
    db_path = tmp_path / "bootstrap_twice.db"
    run_migrations(f"sqlite:///{db_path}")
    run_migrations(f"sqlite:///{db_path}")  # should not raise

    con = sqlite3.connect(db_path)
    try:
        (count,) = con.execute("select count(*) from users").fetchone()
    finally:
        con.close()
    assert count == 0


def test_amber_api_key_column_exists_and_defaults_null(db):
    from core_api.models.identity import User

    u = User(email="amber-user@example.com", password_hash="hash")
    db.add(u)
    db.commit()
    db.refresh(u)
    assert u.amber_api_key is None

    u.amber_api_key = "psk_example_token"
    db.commit()
    db.refresh(u)
    assert u.amber_api_key == "psk_example_token"
