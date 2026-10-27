from __future__ import annotations

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from core_api.bootstrap import run_migrations
from core_api.db import make_engine, make_session_factory
from core_api.deps import get_current_user, get_source_by_collector_key, require_site_access, require_site_owner
from core_api.errors import AppError
from core_api.main import create_app
from core_api.models.identity import Site, SiteAccess, SiteAccessRole, User
from core_api.models.sources import Source, SourceKind
from core_api.security import create_access_token, create_scoped_token
from tests.conftest import ENVOY_COLLECTOR_KEY


@pytest.fixture()
def wired_app(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    db_url = f"sqlite:///{tmp_path}/deps_test.db"
    run_migrations(db_url)
    app = create_app(database_url=db_url)

    @app.get("/whoami")
    def whoami(user: User = Depends(get_current_user)):
        return {"user_id": user.id}

    @app.get("/sites/{site_id}/peek")
    def peek(site: Site = Depends(require_site_access)):
        return {"site_id": site.id}

    @app.get("/sites/{site_id}/owner-only")
    def owner_only(site: Site = Depends(require_site_owner)):
        return {"site_id": site.id}

    return app, db_url


def seed(db_url: str) -> dict:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="owner@example.com", password_hash="x")
    viewer = User(email="viewer@example.com", password_hash="x")
    stranger = User(email="stranger@example.com", password_hash="x")
    session.add_all([owner, viewer, stranger])
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    session.add(SiteAccess(site_id=site.id, user_id=viewer.id, role=SiteAccessRole.VIEWER))
    session.commit()
    ids = {"owner": owner.id, "viewer": viewer.id, "stranger": stranger.id, "site": site.id}
    session.close()
    return ids


def auth(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_get_current_user_requires_valid_token(wired_app):
    app, _ = wired_app
    client = TestClient(app)
    assert client.get("/whoami").status_code in (401, 403)  # no header at all


def test_get_current_user_accepts_valid_token(wired_app):
    app, db_url = wired_app
    ids = seed(db_url)
    client = TestClient(app)
    response = client.get("/whoami", headers=auth(ids["owner"]))
    assert response.status_code == 200
    assert response.json() == {"user_id": ids["owner"]}


def test_require_site_access_allows_owner_and_viewer_denies_stranger(wired_app):
    app, db_url = wired_app
    ids = seed(db_url)
    client = TestClient(app)
    assert client.get(f"/sites/{ids['site']}/peek", headers=auth(ids["owner"])).status_code == 200
    assert client.get(f"/sites/{ids['site']}/peek", headers=auth(ids["viewer"])).status_code == 200
    assert client.get(f"/sites/{ids['site']}/peek", headers=auth(ids["stranger"])).status_code == 404


def test_require_site_owner_denies_viewer(wired_app):
    app, db_url = wired_app
    ids = seed(db_url)
    client = TestClient(app)
    assert client.get(f"/sites/{ids['site']}/owner-only", headers=auth(ids["owner"])).status_code == 200
    assert client.get(f"/sites/{ids['site']}/owner-only", headers=auth(ids["viewer"])).status_code == 403


def test_collector_key_auth(wired_app):
    app, db_url = wired_app

    @app.get("/sources/{source_id}/ping")
    def ping(source: Source = Depends(get_source_by_collector_key)):
        return {"source_id": source.id}

    ids = seed(db_url)
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    import datetime

    source = Source(
        site_id=ids["site"],
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        effective_from=datetime.datetime.now(datetime.UTC),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()

    client = TestClient(app)
    ok = client.get(f"/sources/{source_id}/ping", headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    assert ok.status_code == 200

    bad = client.get(f"/sources/{source_id}/ping", headers={"X-Collector-Key": "wrong-key"})
    assert bad.status_code == 401

    missing = client.get(f"/sources/{source_id}/ping")
    assert missing.status_code in (401, 422)

    # Non-ASCII header value, sent as the raw latin-1 bytes Starlette decodes headers
    # as -- this used to crash compare_digest with a TypeError (500) instead of a
    # clean 401.
    non_ascii = client.get(
        f"/sources/{source_id}/ping", headers={"X-Collector-Key": "café".encode("latin-1")}
    )
    assert non_ascii.status_code == 401


def test_require_setup_token_rejects_mfa_scoped_token(db, user):
    from fastapi.security import HTTPAuthorizationCredentials

    from core_api.deps import require_setup_token

    token, _jti = create_scoped_token(user.id, "mfa")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(AppError) as exc_info:
        require_setup_token(credentials=creds, db=db)
    assert exc_info.value.status_code == 401


def test_require_mfa_token_rejects_setup_scoped_token(db, user):
    from fastapi.security import HTTPAuthorizationCredentials

    from core_api.deps import require_mfa_token

    token, _jti = create_scoped_token(user.id, "totp_setup")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(AppError) as exc_info:
        require_mfa_token(credentials=creds, db=db)
    assert exc_info.value.status_code == 401


def test_require_setup_token_rejects_a_normal_access_token(db, user):
    from fastapi.security import HTTPAuthorizationCredentials

    from core_api.deps import require_setup_token
    from core_api.security import create_access_token

    token = create_access_token(user.id)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(AppError) as exc_info:
        require_setup_token(credentials=creds, db=db)
    assert exc_info.value.status_code == 401


def test_require_mfa_token_rejects_a_normal_access_token(db, user):
    from fastapi.security import HTTPAuthorizationCredentials

    from core_api.deps import require_mfa_token
    from core_api.security import create_access_token

    token = create_access_token(user.id)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(AppError) as exc_info:
        require_mfa_token(credentials=creds, db=db)
    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_a_scoped_token(db, user):
    from fastapi.security import HTTPAuthorizationCredentials

    from core_api.deps import get_current_user

    token, _jti = create_scoped_token(user.id, "mfa")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(AppError) as exc_info:
        get_current_user(credentials=creds, db=db)
    assert exc_info.value.status_code == 401
