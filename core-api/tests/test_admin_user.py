from __future__ import annotations

import logging
import pathlib

from fastapi.testclient import TestClient
from sqlalchemy import select

from core_api.main import create_app
from core_api.models.identity import User
from core_api.security import verify_password


def _password_file_for(db_url: str) -> pathlib.Path:
    from core_api.admin_user import password_file_path

    return password_file_path(db_url)


def test_boots_create_one_admin_user_and_writes_the_password_to_a_restricted_file_not_the_log(
    app_and_db_url, monkeypatch, caplog
):
    """The initial admin password must never land in application logs: those
    often ship to a less-trusted, longer-retention aggregator than the DB
    itself. Writing it to a single file on the same restricted data volume,
    chmod 600, keeps it recoverable exactly once without that exposure."""
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    app, db_url = app_and_db_url
    app = create_app(database_url=db_url)

    # Alembic's env.py runs logging.config.fileConfig() during the migration
    # that app_and_db_url just performed, which disables every logger that
    # already existed (ours included) unless listed in alembic.ini. Harmless
    # in production (migrations run in a separate process from uvicorn, per
    # docker-entrypoint.sh), but it would silence this assertion in-process.
    logging.getLogger("core_api.admin_user").disabled = False

    with caplog.at_level(logging.WARNING, logger="core_api.admin_user"):
        with TestClient(app) as client:
            db = client.app.state.session_factory()
            try:
                user = db.execute(select(User).where(User.email == "admin@example.com")).scalar_one()
            finally:
                db.close()

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "admin@example.com" in message

    password_file = _password_file_for(db_url)
    assert password_file.exists()
    assert str(password_file) in message

    mode = password_file.stat().st_mode & 0o777
    assert mode == 0o600, f"password file must be owner-only, got {oct(mode)}"

    content = password_file.read_text()
    logged_password = content.split("Password: ")[1].split("\n")[0]
    assert verify_password(logged_password, user.password_hash)

    # The plaintext password must appear in the file, never in the log.
    assert logged_password not in message


def test_boots_do_not_create_a_second_admin_user_once_one_exists(app_and_db_url, monkeypatch, caplog):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    app, db_url = app_and_db_url
    app = create_app(database_url=db_url)

    with TestClient(app):
        pass

    with caplog.at_level(logging.WARNING, logger="core_api.admin_user"):
        app = create_app(database_url=db_url)
        with TestClient(app):
            pass

    assert caplog.records == []


def _age_file(path: pathlib.Path, days: float) -> None:
    import os
    import time

    past = time.time() - days * 86400
    os.utime(path, (past, past))


def test_expire_password_file_keeps_a_file_younger_than_five_days(tmp_path):
    from core_api.admin_user import expire_password_file

    f = tmp_path / "initial-admin-password.txt"
    f.write_text("x")
    _age_file(f, 4.9)

    assert expire_password_file(f) is False
    assert f.exists()


def test_expire_password_file_deletes_a_file_five_days_old(tmp_path):
    from core_api.admin_user import expire_password_file

    f = tmp_path / "initial-admin-password.txt"
    f.write_text("x")
    _age_file(f, 5.1)

    assert expire_password_file(f) is True
    assert not f.exists()


def test_expire_password_file_ignores_a_missing_file(tmp_path):
    from core_api.admin_user import expire_password_file

    assert expire_password_file(tmp_path / "initial-admin-password.txt") is False


def test_boot_deletes_an_expired_password_file(app_and_db_url, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    app, db_url = app_and_db_url
    with TestClient(create_app(database_url=db_url)):
        pass
    password_file = _password_file_for(db_url)
    assert password_file.exists()
    _age_file(password_file, 6)

    with TestClient(create_app(database_url=db_url)):
        pass

    assert not password_file.exists()
