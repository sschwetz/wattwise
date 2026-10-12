from __future__ import annotations

import os
import pathlib

from alembic import command
from alembic.config import Config

from core_api.config import get_settings

CORE_API_ROOT = pathlib.Path(__file__).resolve().parent.parent
ALEMBIC_INI = CORE_API_ROOT / "alembic.ini"


def run_migrations(database_url: str | None = None) -> None:
    """Create (or upgrade) the database schema by running every Alembic
    migration up to head. Safe to call repeatedly.

    DATABASE_URL is the single source of truth alembic/env.py reads from, so
    an explicit database_url is applied via the environment (restored after)
    rather than via Config, which env.py would otherwise overwrite.
    """
    previous = os.environ.get("DATABASE_URL")
    if database_url is not None:
        os.environ["DATABASE_URL"] = database_url
    try:
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("script_location", str(CORE_API_ROOT / "alembic"))
        cfg.set_main_option("sqlalchemy.url", database_url or get_settings().database_url)
        command.upgrade(cfg, "head")
    finally:
        if database_url is not None:
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous


def main() -> None:
    run_migrations()


if __name__ == "__main__":
    main()
