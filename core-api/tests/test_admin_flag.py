from __future__ import annotations

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text

from core_api.admin_user import ensure_admin_user
from core_api.bootstrap import run_migrations
from core_api.config import get_settings
from core_api.db import make_engine, make_session_factory
from core_api.deps import require_admin
from core_api.models.identity import User
from core_api.security import create_access_token

PREVIOUS_HEAD = "c4e6a8b0d2f3"


def _auth(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _make_user(db_url: str, email: str, is_admin: bool = False) -> int:
    session = make_session_factory(make_engine(db_url))()
    try:
        user = User(email=email, password_hash="x", is_admin=is_admin)
        session.add(user)
        session.commit()
        return user.id
    finally:
        session.close()


def test_me_returns_is_admin_false_by_default(app_and_db_url):
    app, db_url = app_and_db_url
    user_id = _make_user(db_url, "plain@example.com")
    response = TestClient(app).get("/api/v1/users/me", headers=_auth(user_id))
    assert response.status_code == 200
    assert response.json()["is_admin"] is False


def test_migration_sets_lowest_id_user_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    db_url = f"sqlite:///{tmp_path}/migrate.db"
    run_migrations(db_url, revision=PREVIOUS_HEAD)
    engine = make_engine(db_url)
    with engine.begin() as conn:
        for email in ("first@example.com", "second@example.com"):
            conn.execute(
                text("INSERT INTO users (email, password_hash, totp_enabled, email_confirmed) VALUES (:e, 'x', 0, 1)"),
                {"e": email},
            )
    run_migrations(db_url)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT email, is_admin FROM users ORDER BY id")).all()
    assert [(r[0], bool(r[1])) for r in rows] == [("first@example.com", True), ("second@example.com", False)]


def test_bootstrap_admin_is_admin(app_and_db_url):
    _app, db_url = app_and_db_url
    session_factory = make_session_factory(make_engine(db_url))
    ensure_admin_user(session_factory, get_settings(), db_url)
    session = session_factory()
    try:
        user = session.query(User).one()
        assert user.is_admin is True
    finally:
        session.close()


def test_require_admin_rejects_non_admin(app_and_db_url):
    app, db_url = app_and_db_url

    @app.get("/_admin-only")
    def admin_only(user: User = Depends(require_admin)):
        return {"user_id": user.id}

    admin_id = _make_user(db_url, "admin@example.com", is_admin=True)
    plain_id = _make_user(db_url, "plain@example.com")
    client = TestClient(app)

    denied = client.get("/_admin-only", headers=_auth(plain_id))
    assert denied.status_code == 403
    assert denied.json()["code"] == "admin_required"

    allowed = client.get("/_admin-only", headers=_auth(admin_id))
    assert allowed.status_code == 200
    assert allowed.json() == {"user_id": admin_id}
