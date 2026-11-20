from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from core_api.bootstrap import run_migrations
from core_api.db import make_engine, make_session_factory
from core_api.models.actors import ActorInstance
from tests.test_billing import auth, make_owner_and_site
from tests.test_invites import share_site
from tests.test_sites import make_user

LOCAL_SOURCE = {"kind": "pricing", "provider": "local", "name": "Origin Fixed"}
FLAT_WINDOW = {
    "channel": "buy",
    "label": "Flat",
    "effective_from": "2026-01-01T00:00:00Z",
    "months": list(range(1, 13)),
    "days_of_week": list(range(7)),
    "start_time": "00:00:00",
    "end_time": "00:00:00",
    "rate_c_per_kwh": "32.5",
}
PENDING_SIGN_IN = {"user_code": "QZXW-7731", "verification_uri": "https://example.com/device"}


def _grant(db_url: str, site_id: int, user_id: int, role: str) -> None:
    """A site_access row written in SQL, so the role column's check constraint is what decides."""
    with make_engine(db_url).begin() as conn:
        conn.execute(
            text("INSERT INTO site_access (site_id, user_id, role) VALUES (:s, :u, :r)"),
            {"s": site_id, "u": user_id, "r": role},
        )


@pytest.fixture()
def roles(app_and_db_url):
    """A site with its owner, a manager, a viewer and a user with no access."""
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    manager_id = make_user(db_url, "manager@example.com")
    viewer_id = make_user(db_url, "viewer@example.com")
    stranger_id = make_user(db_url, "stranger@example.com")
    _grant(db_url, site_id, manager_id, "manager")
    _grant(db_url, site_id, viewer_id, "viewer")
    return {
        "db_url": db_url,
        "site": site_id,
        "owner": owner_id,
        "manager": manager_id,
        "viewer": viewer_id,
        "stranger": stranger_id,
    }


def _site_url(roles, suffix: str = "") -> str:
    return f"/api/v1/sites/{roles['site']}{suffix}"


def test_my_role_is_manager_for_a_manager(client, roles):
    got = client.get(_site_url(roles), headers=auth(roles["manager"]))
    assert got.status_code == 200
    assert got.json()["my_role"] == "manager"
    listed = client.get("/api/v1/sites", headers=auth(roles["manager"])).json()
    assert [s["my_role"] for s in listed] == ["manager"]


def test_manager_can_patch_the_site_and_a_viewer_cannot(client, roles):
    patched = client.patch(_site_url(roles), json={"name": "Renamed"}, headers=auth(roles["manager"]))
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Renamed"
    assert patched.json()["my_role"] == "manager"

    denied = client.patch(_site_url(roles), json={"name": "Nope"}, headers=auth(roles["viewer"]))
    assert denied.status_code == 403
    assert denied.json()["code"] == "forbidden"


def test_a_user_with_no_access_gets_not_found_on_a_manager_route(client, roles):
    response = client.patch(_site_url(roles), json={"name": "Nope"}, headers=auth(roles["stranger"]))
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_manager_can_create_a_source_and_a_viewer_cannot(client, roles):
    body = {"kind": "energy", "provider": "envoy", "config": {"host": "envoy.local"}}
    created = client.post(_site_url(roles, "/sources"), json=body, headers=auth(roles["manager"]))
    assert created.status_code == 201, created.text

    denied = client.post(_site_url(roles, "/sources"), json=body, headers=auth(roles["viewer"]))
    assert denied.status_code == 403
    assert denied.json()["code"] == "forbidden"


def test_manager_sees_source_credentials_and_a_viewer_does_not(client, roles):
    body = {"kind": "energy", "provider": "envoy", "config": {"host": "envoy.local", "token": "device-token"}}
    source_id = client.post(_site_url(roles, "/sources"), json=body, headers=auth(roles["owner"])).json()["id"]

    as_manager = client.get(_site_url(roles, f"/sources/{source_id}"), headers=auth(roles["manager"]))
    as_viewer = client.get(_site_url(roles, f"/sources/{source_id}"), headers=auth(roles["viewer"]))
    assert "token" in as_manager.json()["config"]
    assert "token" not in as_viewer.json()["config"]


def test_manager_can_read_and_write_billing_and_a_viewer_cannot_write(client, roles):
    body = {"effective_from": "2026-01-01T00:00:00Z", "cycle_months": 3, "anchor_day": 18}
    created = client.post(_site_url(roles, "/billing-cycle"), json=body, headers=auth(roles["manager"]))
    assert created.status_code == 201, created.text

    read = client.get(_site_url(roles, "/billing-cycle"), headers=auth(roles["manager"]))
    assert read.status_code == 200
    assert len(read.json()) == 1

    denied = client.post(_site_url(roles, "/billing-cycle"), json=body, headers=auth(roles["viewer"]))
    assert denied.status_code == 403
    assert denied.json()["code"] == "forbidden"


def test_manager_can_add_a_local_rate_window(client, roles):
    source = client.post(_site_url(roles, "/sources"), json=LOCAL_SOURCE, headers=auth(roles["manager"]))
    assert source.status_code == 201, source.text
    url = _site_url(roles, f"/sources/{source.json()['id']}/rate-windows")

    created = client.post(url, json=FLAT_WINDOW, headers=auth(roles["manager"]))
    assert created.status_code == 201, created.text

    denied = client.post(url, json={**FLAT_WINDOW, "label": "Other"}, headers=auth(roles["viewer"]))
    assert denied.status_code == 403
    assert denied.json()["code"] == "forbidden"


def test_manager_can_create_an_actor_instance(client, roles):
    created = client.post(
        _site_url(roles, "/actors/instances"),
        json={"provider": "actron_neo", "name": "Aircon"},
        headers=auth(roles["manager"]),
    )
    assert created.status_code == 201, created.text

    denied = client.post(
        _site_url(roles, "/actors/instances"),
        json={"provider": "actron_neo", "name": "Other"},
        headers=auth(roles["viewer"]),
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "forbidden"


def test_manager_sees_the_actor_sign_in_code_and_a_viewer_does_not(client, roles):
    instance_id = client.post(
        _site_url(roles, "/actors/instances"),
        json={"provider": "actron_neo", "name": "Aircon"},
        headers=auth(roles["owner"]),
    ).json()["id"]
    session = make_session_factory(make_engine(roles["db_url"]))()
    try:
        session.get(ActorInstance, instance_id).auth_state = PENDING_SIGN_IN
        session.commit()
    finally:
        session.close()

    as_manager = client.get(_site_url(roles, "/actors/instances"), headers=auth(roles["manager"])).json()
    as_viewer = client.get(_site_url(roles, "/actors/instances"), headers=auth(roles["viewer"])).json()
    assert as_manager[0]["auth_state"] == PENDING_SIGN_IN
    assert as_viewer[0]["auth_state"] == {"signing_in": True}


def test_manager_can_create_list_and_revoke_viewer_invites(client, roles):
    by_owner = client.post(
        _site_url(roles, "/invites"), json={"email": "from-owner@example.com"}, headers=auth(roles["owner"])
    )
    assert by_owner.status_code == 201, by_owner.text
    by_manager = client.post(
        _site_url(roles, "/invites"), json={"email": "from-manager@example.com"}, headers=auth(roles["manager"])
    )
    assert by_manager.status_code == 201, by_manager.text

    listed = client.get(_site_url(roles, "/invites"), headers=auth(roles["manager"]))
    assert {i["email"] for i in listed.json()} == {"from-owner@example.com", "from-manager@example.com"}

    revoked = client.post(_site_url(roles, f"/invites/{by_owner.json()['id']}/revoke"), headers=auth(roles["manager"]))
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"


def test_viewer_cannot_create_an_invite(client, roles):
    denied = client.post(
        _site_url(roles, "/invites"), json={"email": "someone@example.com"}, headers=auth(roles["viewer"])
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "forbidden"


def test_manager_cannot_invite_the_site_owner(client, roles):
    owner_email = client.get("/api/v1/users/me", headers=auth(roles["owner"])).json()["email"]
    denied = client.post(_site_url(roles, "/invites"), json={"email": owner_email}, headers=auth(roles["manager"]))
    assert denied.status_code == 400
    assert denied.json()["code"] == "cannot_invite_owner"


def _promote(db_url: str, site_id: int, user_id: int) -> None:
    with make_engine(db_url).begin() as conn:
        conn.execute(
            text("UPDATE site_access SET role = 'manager' WHERE site_id = :s AND user_id = :u"),
            {"s": site_id, "u": user_id},
        )


def test_revoking_an_invite_that_removes_a_manager_is_owner_only(client, roles):
    """Revoking an accepted invite deletes the invitee's grant. When an admin has since made
    that grant a manager grant, only the owner may remove it."""
    other_id = make_user(roles["db_url"], "other-manager@example.com")
    accepted = share_site(client, roles["owner"], roles["site"], other_id, "other-manager@example.com")
    _promote(roles["db_url"], roles["site"], other_id)
    revoke_url = _site_url(roles, f"/invites/{accepted['id']}/revoke")

    denied = client.post(revoke_url, headers=auth(roles["manager"]))
    assert denied.status_code == 403
    assert denied.json()["code"] == "forbidden"
    assert client.get(_site_url(roles), headers=auth(other_id)).json()["my_role"] == "manager"

    allowed = client.post(revoke_url, headers=auth(roles["owner"]))
    assert allowed.status_code == 200, allowed.text
    assert client.get(_site_url(roles), headers=auth(other_id)).status_code == 404


def test_a_promoted_invitee_can_still_revoke_their_own_invite(client, roles):
    other_id = make_user(roles["db_url"], "leaving@example.com")
    accepted = share_site(client, roles["owner"], roles["site"], other_id, "leaving@example.com")
    _promote(roles["db_url"], roles["site"], other_id)

    left = client.post(_site_url(roles, f"/invites/{accepted['id']}/revoke"), headers=auth(other_id))
    assert left.status_code == 200, left.text
    assert client.get(_site_url(roles), headers=auth(other_id)).status_code == 404


def test_migration_accepts_manager_and_rejects_other_roles(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    db_url = f"sqlite:///{tmp_path}/migrate.db"
    run_migrations(db_url)
    engine = make_engine(db_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (email, password_hash, totp_enabled, email_confirmed, is_admin) "
                "VALUES ('o@example.com', 'x', 0, 1, 0), ('m@example.com', 'x', 0, 1, 0)"
            )
        )
        conn.execute(text("INSERT INTO sites (owner_user_id, name, timezone, enabled) VALUES (1, 'Home', 'UTC', 1)"))
    _grant(db_url, 1, 2, "manager")
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE site_access SET role = 'admin' WHERE site_id = 1 AND user_id = 2"))
