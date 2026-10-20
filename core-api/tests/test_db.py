from sqlalchemy import text

from core_api.db import make_engine


def _pragma(engine, name: str):
    with engine.connect() as connection:
        return connection.execute(text(f"PRAGMA {name}")).scalar()


def test_sqlite_engine_uses_wal_for_slow_disks(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/wal.db")

    assert _pragma(engine, "journal_mode") == "wal"
    # 1 is NORMAL. FULL (2) syncs on every commit, which is what made
    # concurrent collector pushes time out on a spinning disk.
    assert _pragma(engine, "synchronous") == 1


def test_sqlite_engine_waits_for_a_held_write_lock(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/busy.db")

    assert _pragma(engine, "busy_timeout") == 30000


def test_sqlite_engine_still_enforces_foreign_keys(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/fk.db")

    assert _pragma(engine, "foreign_keys") == 1


def test_sqlite_file_engine_has_room_for_a_burst_of_chart_queries(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/pool.db")

    # The webapp fires ~15 chart queries at once; with the default 5+10 pool a
    # slow disk made the extras wait out the 30s pool timeout and return 500.
    assert engine.pool.size() >= 20
    assert engine.pool._max_overflow >= 20


def test_in_memory_sqlite_engine_still_builds():
    make_engine("sqlite://")
