from __future__ import annotations

import datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from core_api.db import make_engine
from core_api.models.sources import Source, SourceKind

UTC = datetime.timezone.utc


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
    source = Source(
        site_id=site.id, kind=SourceKind.PRICING, provider="local", name="Fixed", config={},
        effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(source)
    db.commit()

    # A raw execute runs the INSERT at once, so SQLite rejects it here, not at commit.
    with pytest.raises(IntegrityError):
        db.execute(
            sa.text(
                "INSERT INTO local_rate_windows (source_id, channel, label, effective_from, months, "
                "days_of_week, start_time, end_time, rate_c_per_kwh) "
                "VALUES (:s, 'bogus', 'x', '2026-01-01 00:00:00', '[1]', '[0]', '00:00:00', '00:00:00', 1)"
            ),
            {"s": source.id},
        )
