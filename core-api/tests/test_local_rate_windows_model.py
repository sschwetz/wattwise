from __future__ import annotations

import datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, User
from core_api.models.sources import Source, SourceKind

UTC = datetime.timezone.utc


def make_pricing_source(db: Session, site_id: int) -> Source:
    source = Source(
        site_id=site_id, kind=SourceKind.PRICING, provider="local", name="Fixed", config={},
        effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()
    return source


def insert_window_row(db: Session, source_id: int, channel: str) -> None:
    """Raw INSERT so the CHECK constraint, not the ORM enum type, sees the
    channel. The execute runs at once, so SQLite rejects a bad row here."""
    db.execute(
        sa.text(
            "INSERT INTO local_rate_windows (source_id, channel, label, effective_from, months, "
            "days_of_week, start_time, end_time, rate_c_per_kwh) "
            "VALUES (:s, :c, 'x', '2026-01-01 00:00:00', '[1]', '[0]', '00:00:00', '00:00:00', 1)"
        ),
        {"s": source_id, "c": channel},
    )


def test_migration_creates_the_table(app_and_db_url):
    _, db_url = app_and_db_url
    inspector = sa.inspect(make_engine(db_url))

    assert "local_rate_windows" in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns("local_rate_windows")}
    assert columns == {
        "id", "source_id", "channel", "label", "effective_from", "effective_to",
        "months", "days_of_week", "start_time", "end_time", "rate_c_per_kwh",
    }


def test_check_constraint_rejects_an_unknown_channel(db, site):
    source = make_pricing_source(db, site.id)

    with pytest.raises(IntegrityError, match="CHECK constraint failed"):
        insert_window_row(db, source.id, "bogus")


def test_model_check_constraint_rejects_the_demand_channel(db, site):
    source = make_pricing_source(db, site.id)

    with pytest.raises(IntegrityError, match="CHECK constraint failed"):
        insert_window_row(db, source.id, "demand")


def test_migrated_check_constraint_rejects_the_demand_channel(app_and_db_url):
    _, db_url = app_and_db_url
    session = make_session_factory(make_engine(db_url))()
    try:
        user = User(email="rates@example.com", password_hash="x")
        session.add(user)
        session.commit()
        site = Site(owner_user_id=user.id, name="Home", timezone="UTC")
        session.add(site)
        session.commit()
        source = make_pricing_source(session, site.id)

        with pytest.raises(IntegrityError, match="CHECK constraint failed"):
            insert_window_row(session, source.id, "demand")
    finally:
        session.close()


def test_migration_creates_the_reprice_marker_table(app_and_db_url):
    _, db_url = app_and_db_url
    inspector = sa.inspect(make_engine(db_url))

    assert "local_reprice_markers" in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns("local_reprice_markers")}
    assert columns == {"source_id", "reprice_from", "version"}
