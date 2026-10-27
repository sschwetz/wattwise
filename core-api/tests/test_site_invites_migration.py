from __future__ import annotations

from sqlalchemy import text

from core_api.bootstrap import run_migrations
from core_api.db import make_engine

PREVIOUS_REVISION = "d4f6a8b1c3e5"


def test_migration_backfills_accepted_invites_for_existing_access(tmp_path):
    db_url = f"sqlite:///{tmp_path}/migrate.db"
    run_migrations(db_url, PREVIOUS_REVISION)
    engine = make_engine(db_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, email_confirmed, created_at) VALUES "
                "(1, 'owner@example.com', 'h', 1, CURRENT_TIMESTAMP), "
                "(2, 'Viewer@Example.com', 'h', 1, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sites (id, owner_user_id, name, timezone, enabled) "
                "VALUES (1, 1, 'Home', 'Australia/Sydney', 1)"
            )
        )
        conn.execute(text("INSERT INTO site_access (site_id, user_id, role) VALUES (1, 2, 'viewer')"))

    run_migrations(db_url)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT site_id, email, inviter_user_id, invitee_user_id, status, accepted_at "
                "FROM site_invites"
            )
        ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.site_id == 1
    assert row.email == "viewer@example.com"
    assert row.inviter_user_id == 1
    assert row.invitee_user_id == 2
    assert row.status == "accepted"
    assert row.accepted_at is not None
