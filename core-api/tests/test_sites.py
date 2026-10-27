from __future__ import annotations

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import User
from core_api.security import create_access_token


def make_user(db_url: str, email: str) -> int:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = User(email=email, password_hash="x")
    session.add(user)
    session.commit()
    user_id = user.id
    session.close()
    return user_id


def auth(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_create_and_get_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    create = client.post(
        "/api/v1/sites", json={"name": "Home", "timezone": "Australia/Sydney"}, headers=auth(owner_id)
    )
    assert create.status_code == 201, create.text
    site_id = create.json()["id"]

    get = client.get(f"/api/v1/sites/{site_id}", headers=auth(owner_id))
    assert get.status_code == 200
    assert get.json()["name"] == "Home"


def test_list_sites_returns_only_accessible_sites(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner2@example.com")
    stranger_id = make_user(db_url, "stranger2@example.com")
    client.post("/api/v1/sites", json={"name": "Home", "timezone": "UTC"}, headers=auth(owner_id))

    mine = client.get("/api/v1/sites", headers=auth(owner_id)).json()
    theirs = client.get("/api/v1/sites", headers=auth(stranger_id)).json()
    assert len(mine) == 1
    assert theirs == []


def test_invited_viewer_reads_site_but_cannot_invite_and_loses_access_on_revoke(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner3@example.com")
    viewer_id = make_user(db_url, "viewer3@example.com")
    site_id = client.post(
        "/api/v1/sites", json={"name": "Home", "timezone": "UTC"}, headers=auth(owner_id)
    ).json()["id"]

    created = client.post(
        f"/api/v1/sites/{site_id}/invites", json={"email": "viewer3@example.com"}, headers=auth(owner_id)
    )
    assert created.status_code == 201
    assert client.get(f"/api/v1/sites/{site_id}", headers=auth(viewer_id)).status_code == 404
    invite_id = created.json()["id"]
    assert client.post(f"/api/v1/invites/{invite_id}/accept", headers=auth(viewer_id)).status_code == 200

    assert client.get(f"/api/v1/sites/{site_id}", headers=auth(viewer_id)).status_code == 200
    assert client.post(
        f"/api/v1/sites/{site_id}/invites", json={"email": "x@example.com"}, headers=auth(viewer_id)
    ).status_code == 403

    revoke = client.post(f"/api/v1/sites/{site_id}/invites/{invite_id}/revoke", headers=auth(owner_id))
    assert revoke.status_code == 200
    assert client.get(f"/api/v1/sites/{site_id}", headers=auth(viewer_id)).status_code == 404


def test_owner_can_patch_site_name_and_timezone(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner5@example.com")
    site_id = client.post(
        "/api/v1/sites", json={"name": "Home", "timezone": "Australia/Sydney"}, headers=auth(owner_id)
    ).json()["id"]

    response = client.patch(
        f"/api/v1/sites/{site_id}",
        json={"name": "New Name", "timezone": "Australia/Perth"},
        headers=auth(owner_id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "New Name"
    assert body["timezone"] == "Australia/Perth"


def test_patch_site_allows_partial_update(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner6@example.com")
    site_id = client.post(
        "/api/v1/sites", json={"name": "Home", "timezone": "Australia/Sydney"}, headers=auth(owner_id)
    ).json()["id"]

    response = client.patch(
        f"/api/v1/sites/{site_id}", json={"name": "Only Name"}, headers=auth(owner_id)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Only Name"
    assert body["timezone"] == "Australia/Sydney"


def test_non_owner_cannot_patch_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner7@example.com")
    stranger_id = make_user(db_url, "stranger7@example.com")
    site_id = client.post(
        "/api/v1/sites", json={"name": "Home", "timezone": "Australia/Sydney"}, headers=auth(owner_id)
    ).json()["id"]

    response = client.patch(
        f"/api/v1/sites/{site_id}", json={"name": "Hijacked"}, headers=auth(stranger_id)
    )
    assert response.status_code == 404


def _create_site(client, owner_id: int) -> int:
    return client.post(
        "/api/v1/sites", json={"name": "Home", "timezone": "UTC"}, headers=auth(owner_id)
    ).json()["id"]


def test_new_site_is_enabled(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "enabled-owner@example.com")
    site_id = _create_site(client, owner_id)
    got = client.get(f"/api/v1/sites/{site_id}", headers=auth(owner_id))
    assert got.json()["enabled"] is True


def test_owner_can_disable_and_reenable_site(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "toggle-owner@example.com")
    site_id = _create_site(client, owner_id)
    off = client.patch(f"/api/v1/sites/{site_id}", json={"enabled": False}, headers=auth(owner_id))
    assert off.status_code == 200
    assert off.json()["enabled"] is False
    on = client.patch(f"/api/v1/sites/{site_id}", json={"enabled": True}, headers=auth(owner_id))
    assert on.status_code == 200
    assert on.json()["enabled"] is True


def test_viewer_cannot_change_enabled(client, app_and_db_url):
    from core_api.models.identity import SiteAccess

    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "enabled-owner2@example.com")
    viewer_id = make_user(db_url, "enabled-viewer2@example.com")
    site_id = _create_site(client, owner_id)
    session = make_session_factory(make_engine(db_url))()
    session.add(SiteAccess(site_id=site_id, user_id=viewer_id))
    session.commit()
    session.close()
    res = client.patch(f"/api/v1/sites/{site_id}", json={"enabled": False}, headers=auth(viewer_id))
    assert res.status_code == 403


def test_list_sites_includes_disabled_sites_with_flag(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "list-owner@example.com")
    site_id = _create_site(client, owner_id)
    client.patch(f"/api/v1/sites/{site_id}", json={"enabled": False}, headers=auth(owner_id))
    sites = client.get("/api/v1/sites", headers=auth(owner_id)).json()
    assert [(s["id"], s["enabled"]) for s in sites] == [(site_id, False)]
