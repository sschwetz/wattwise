from __future__ import annotations

from collections.abc import Iterator
from typing import cast

from sqlalchemy import CursorResult, Engine, Result, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from core_api.config import get_settings


def rows_affected(result: Result) -> int:
    """Row count of an UPDATE/DELETE. `Session.execute` is typed as returning
    a plain Result, but a DML statement always yields a CursorResult."""
    return cast(CursorResult, result).rowcount


def _pool_options(url: str) -> dict:
    """Pool sizing for file-backed SQLite.

    Handlers run in a 40-thread pool and hold a connection while they wait on
    a write lock. The default 5+10 pool made a burst of ~15 chart queries
    queue for the 30s pool timeout and return 500 when the disk stalled.
    In-memory SQLite uses a different pool class that takes no size options.
    """
    if not url.startswith("sqlite") or url in ("sqlite://", "sqlite:///:memory:"):
        return {}
    return {"pool_size": 20, "max_overflow": 20}


def make_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, **_pool_options(url))

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            # SQLite defaults foreign_keys off per-connection; the schema
            # relies on FK/composite-FK constraints being enforced.
            cursor.execute("PRAGMA foreign_keys=ON")
            # On a spinning disk the default rollback journal syncs so often
            # that simultaneous collector pushes hit "database is locked".
            # WAL with synchronous=NORMAL syncs less and lets reads run during
            # a write. A power cut can drop the last few commits but cannot
            # corrupt the file.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            # Wait up to 30s for a held write lock instead of the 5s default.
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
