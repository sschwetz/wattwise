from __future__ import annotations

import datetime
import logging
import re

import pytest
from sqlalchemy import select

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import PasswordResetToken, RecoveryCode, User
from core_api.security import hash_password
from tests.test_admin_users import USERS_URL, _user
from tests.test_billing import auth

ADMIN_LOGGER = "core_api.admin_users"
PASSWORD = "correct-horse-battery"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"
PENDING_SECRET = "PENDINGSECRET234"


@pytest.fixture(autouse=True)
def _enable_core_api_loggers(app_and_db_url, monkeypatch):
    """Alembic's fileConfig disables loggers that already exist (see test_admin_users_write.py)."""
    for name, candidate in logging.root.manager.loggerDict.items():
        if name.startswith("core_api") and isinstance(candidate, logging.Logger):
            monkeypatch.setattr(candidate, "disabled", False)


@pytest.fixture()
def admin(app_and_db_url) -> int:
    _, db_url = app_and_db_url
    return _user(db_url, "admin@example.com", is_admin=True)


def _load(db_url: str, user_id: int) -> User:
    session = make_session_factory(make_engine(db_url))()
    try:
        user = session.get(User, user_id)
        assert user is not None
        session.expunge(user)
        return user
    finally:
        session.close()


def _reset_tokens(db_url: str, user_id: int) -> list[PasswordResetToken]:
    session = make_session_factory(make_engine(db_url))()
    try:
        return list(session.scalars(select(PasswordResetToken).where(PasswordResetToken.user_id == user_id)))
    finally:
        session.close()


# --- POST /{user_id}/password-reset ----------------------------------------


def test_password_reset_emails_the_user_a_working_link(client, app_and_db_url, admin, fake_email_sender):
    _, db_url = app_and_db_url
    target = _user(db_url, "target@example.com", password_hash=hash_password("old-password-1"), email_confirmed=True)
    got = client.post(f"{USERS_URL}/{target}/password-reset", headers=auth(admin))
    assert got.status_code == 204
    assert got.content == b""

    assert len(fake_email_sender.sent) == 1
    to, _subject, body = fake_email_sender.sent[0]
    assert to == "target@example.com"
    match = re.search(r"/reset-password\?token=([^\s]+)", body)
    assert match is not None
    raw = match.group(1)

    rows = _reset_tokens(db_url, target)
    assert len(rows) == 1
    assert rows[0].token_hash != raw
    assert raw not in rows[0].token_hash

    confirm = client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": raw, "new_password": "brand-new-password"}
    )
    assert confirm.status_code == 204
    login = client.post("/api/v1/auth/login", json={"email": "target@example.com", "password": "brand-new-password"})
    assert login.status_code == 200


def test_password_reset_token_expires_in_an_hour(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    target = _user(db_url, "target@example.com")
    client.post(f"{USERS_URL}/{target}/password-reset", headers=auth(admin))
    expires = _reset_tokens(db_url, target)[0].expires_at.replace(tzinfo=datetime.UTC)
    remaining = expires - datetime.datetime.now(datetime.UTC)
    assert datetime.timedelta(minutes=59) < remaining <= datetime.timedelta(hours=1)


def test_password_reset_for_unknown_user_is_404(client, app_and_db_url, admin, fake_email_sender):
    got = client.post(f"{USERS_URL}/9999/password-reset", headers=auth(admin))
    assert got.status_code == 404
    assert got.json()["code"] == "not_found"
    assert fake_email_sender.sent == []


# --- POST /{user_id}/totp/reset --------------------------------------------


def _enrolled(db_url: str) -> int:
    return _user(
        db_url,
        "target@example.com",
        password_hash=hash_password(PASSWORD),
        email_confirmed=True,
        totp_enabled=True,
        totp_secret=TOTP_SECRET,
        pending_totp_secret=PENDING_SECRET,
        last_totp_step=12345,
    )


def test_totp_reset_clears_the_secrets_and_the_flag(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    target = _enrolled(db_url)
    got = client.post(f"{USERS_URL}/{target}/totp/reset", headers=auth(admin))
    assert got.status_code == 200, got.text
    assert got.json()["id"] == target
    assert got.json()["totp_enabled"] is False
    for value in (TOTP_SECRET, PENDING_SECRET, "12345"):
        assert value not in got.text

    user = _load(db_url, target)
    assert user.totp_enabled is False
    assert user.totp_secret is None
    assert user.pending_totp_secret is None
    assert user.last_totp_step is None


def test_totp_reset_drops_the_old_recovery_codes(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    target = _enrolled(db_url)
    session = make_session_factory(make_engine(db_url))()
    session.add(RecoveryCode(user_id=target, code_hash="a" * 64))
    session.commit()
    session.close()

    assert client.post(f"{USERS_URL}/{target}/totp/reset", headers=auth(admin)).status_code == 200
    session = make_session_factory(make_engine(db_url))()
    try:
        assert session.scalars(select(RecoveryCode).where(RecoveryCode.user_id == target)).all() == []
    finally:
        session.close()


def test_login_after_totp_reset_lands_on_the_setup_step(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    target = _enrolled(db_url)
    client.post(f"{USERS_URL}/{target}/totp/reset", headers=auth(admin))

    login = client.post("/api/v1/auth/login", json={"email": "target@example.com", "password": PASSWORD})
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["status"] == "totp_setup_required"
    assert body["setup_token"]

    start = client.post("/api/v1/auth/totp/setup/start", headers={"Authorization": f"Bearer {body['setup_token']}"})
    assert start.status_code == 200, start.text


def test_totp_reset_for_unknown_user_is_404(client, app_and_db_url, admin):
    got = client.post(f"{USERS_URL}/9999/totp/reset", headers=auth(admin))
    assert got.status_code == 404
    assert got.json()["code"] == "not_found"


def test_both_routes_log_one_line_without_values(client, app_and_db_url, admin, caplog, fake_email_sender):
    _, db_url = app_and_db_url
    target = _enrolled(db_url)
    for path, action, status in (("password-reset", "password_reset", 204), ("totp/reset", "reset_totp", 200)):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=ADMIN_LOGGER):
            got = client.post(f"{USERS_URL}/{target}/{path}", headers=auth(admin))
        assert got.status_code == status
        records = [r for r in caplog.records if r.name == ADMIN_LOGGER]
        assert len(records) == 1
        assert records[0].getMessage() == f"admin action: admin_user_id={admin} target_user_id={target} action={action}"
        assert TOTP_SECRET not in caplog.text
        for _to, _subject, body in fake_email_sender.sent:
            match = re.search(r"/reset-password\?token=([^\s]+)", body)
            assert match is not None
            assert match.group(1) not in caplog.text
