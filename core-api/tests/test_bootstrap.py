from __future__ import annotations

import sqlite3

from alembic.config import Config

from alembic import command
from core_api.bootstrap import ALEMBIC_INI, CORE_API_ROOT, run_migrations
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


SPIKE_STATUS_REVISION = "b4d7e1a9c3f2"
BEFORE_SPIKE_STATUS_REVISION = "f1a8c3d5e7b2"


def _price_interval_columns(db_path) -> list[str]:
    con = sqlite3.connect(db_path)
    try:
        return [row[1] for row in con.execute("pragma table_info(price_intervals)")]
    finally:
        con.close()


def test_spike_status_migration_upgrades_existing_rows_and_downgrades(tmp_path, monkeypatch):
    db_path = tmp_path / "spike_status.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(CORE_API_ROOT / "alembic"))
    command.upgrade(config, BEFORE_SPIKE_STATUS_REVISION)
    assert "spike_status" not in _price_interval_columns(db_path)

    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "insert into price_intervals (pricing_source_id, channel, interval_start, interval_end, price, is_demand_window) "
            "values (1, 'buy', '2026-01-01 00:00:00', '2026-01-01 00:05:00', 7.0, 0)"
        )
        con.commit()
    finally:
        con.close()

    command.upgrade(config, SPIKE_STATUS_REVISION)
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("select spike_status from price_intervals").fetchone() == ("none",)
        try:
            con.execute("update price_intervals set spike_status = 'bogus'")
            raised = False
        except sqlite3.IntegrityError:
            raised = True
    finally:
        con.close()
    assert raised, "the check constraint should reject an unknown spike_status"

    command.downgrade(config, BEFORE_SPIKE_STATUS_REVISION)
    assert "spike_status" not in _price_interval_columns(db_path)
    assert "price_low" in _price_interval_columns(db_path)


def test_migration_allows_new_energy_metrics(tmp_path):
    db_path = tmp_path / "m.db"
    run_migrations(f"sqlite:///{db_path}")
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "insert into energy_readings (source_id, ts, metric, value) "
            "values (1, '2026-01-01 00:00:00', 'grid_voltage_ph_a_v', 240.0)"
        )
        con.execute(
            "insert into energy_readings_rollup_5m (source_id, ts, metric, value) "
            "values (1, '2026-01-01 00:00:00', 'production_wh_lifetime', 1000.0)"
        )
        for table in ("energy_readings", "energy_readings_rollup_5m"):
            try:
                con.execute(
                    f"insert into {table} (source_id, ts, metric, value) "
                    "values (1, '2026-01-01 00:00:00', 'bogus', 1.0)"
                )
                raised = False
            except sqlite3.IntegrityError:
                raised = True
            assert raised, f"{table} should reject an unknown metric"
    finally:
        con.close()
