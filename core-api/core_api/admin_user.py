from __future__ import annotations

import datetime
import logging
import os
import pathlib
import secrets
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from core_api.config import Settings
from core_api.models.identity import User
from core_api.security import hash_password

logger = logging.getLogger(__name__)

PASSWORD_FILE_MAX_AGE = datetime.timedelta(days=5)


def password_file_path(database_url: str) -> pathlib.Path:
    """Same directory as the sqlite DB file itself -- already a restricted,
    operator-controlled data volume, unlike a log sink which is often shipped
    to a less-trusted, longer-retention aggregator."""
    db_path = database_url.removeprefix("sqlite:///")
    return pathlib.Path(db_path).resolve().parent / "initial-admin-password.txt"


def ensure_admin_user(session_factory: sessionmaker[Session], settings: Settings, database_url: str) -> None:
    """Creates a single login account on first boot, so a fresh deployment
    has something to log in with instead of needing a manual /auth/register
    call. Only fires when the users table is completely empty, so it never
    touches a deployment that already has an account."""
    db = session_factory()
    try:
        user_count = db.execute(select(func.count()).select_from(User)).scalar_one()
        if user_count > 0:
            return
        password = secrets.token_urlsafe(16)
        db.add(User(email=settings.admin_email, password_hash=hash_password(password)))
        db.commit()

        # The ONLY record of the plaintext password -- only the bcrypt hash is
        # persisted to the database. Written to a file rather than logged: a
        # log message routinely ships to an aggregator with broader access
        # and longer retention than this data volume has.
        password_file = password_file_path(database_url)
        password_file.write_text(
            f"Initial admin user: {settings.admin_email}\n"
            f"Password: {password}\n"
            "Log in, change this password, then delete this file.\n"
        )
        os.chmod(password_file, 0o600)
        logger.warning(
            "Created initial admin user %s. The one-time password was written to %s "
            "-- log in, change it, then delete that file.",
            settings.admin_email,
            password_file,
        )
    finally:
        db.close()


def expire_password_file(password_file: pathlib.Path) -> bool:
    """Deletes the one-time password file once it is older than
    PASSWORD_FILE_MAX_AGE, so a forgotten file does not sit on the data volume
    indefinitely. Returns True if it deleted the file. Age is the file's
    mtime, which is its write time: the file is written once and never
    touched again."""
    try:
        age = time.time() - password_file.stat().st_mtime
    except FileNotFoundError:
        return False
    if age < PASSWORD_FILE_MAX_AGE.total_seconds():
        return False
    password_file.unlink(missing_ok=True)
    logger.warning("Deleted %s: it was older than %s and had not been removed.", password_file, PASSWORD_FILE_MAX_AGE)
    return True
