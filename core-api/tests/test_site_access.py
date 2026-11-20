from __future__ import annotations

import pytest
from sqlalchemy import text

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import InviteStatus, SiteAccess, SiteInvite
from tests.test_invites import make_site, share_site
from tests.test_manager_role import _grant
from tests.test_sites import auth, make_user


@pytest.fixture()
def people(client, app_and_db_url):
    """A site with its owner, a manager, two viewers and a user with no access."""
    _, db_url = app_and_db_url
    owner = make_user(db_url, "owner@example.com")
    site = make_site(client, owner)
    manager = make_user(db_url, "manager@example.com")
    viewer_b = make_user(db_url, "b-viewer@example.com")
    viewer_a = make_user(db_url, "a-viewer@example.com")
    stranger = make_user(db_url, "stranger@example.com")
    _grant(db_url, site, manager, "manager")
    _grant(db_url, site, viewer_b, "viewer")
    _grant(db_url, site, viewer_a, "viewer")
    return {
        "db_url": db_url,
        "site": site,
        "owner": owner,
        "manager": manager,
        "viewer": viewer_a,
        "viewer_b": viewer_b,
        "stranger": stranger,
    }


def _url(people, user_id: int | None = None) -> str:
    base = f"/api/v1/sites/{people['site']}/access"
    return base if user_id is None else f"{base}/{user_id}"


def _grant_row(db_url: str, site_id: int, user_id: int) -> SiteAccess | None:
    session = make_session_factory(make_engine(db_url))()
    try:
        return session.get(SiteAccess, (site_id, user_id))
    finally:
        session.close()


@pytest.mark.parametrize("caller", ["owner", "manager"])
def test_owner_and_manager_list_people_owner_first_then_by_role_and_email(client, people, caller):
    got = client.get(_url(people), headers=auth(people[caller]))
    assert got.status_code == 200
    assert [(p["email"], p["role"]) for p in got.json()] == [
        ("owner@example.com", "owner"),
        ("manager@example.com", "manager"),
        ("a-viewer@example.com", "viewer"),
        ("b-viewer@example.com", "viewer"),
    ]
    assert got.json()[0] == {
        "user_id": people["owner"],
        "email": "owner@example.com",
        "display_name": None,
        "role": "owner",
    }


def test_a_viewer_cannot_list_people(client, people):
    got = client.get(_url(people), headers=auth(people["viewer"]))
    assert (got.status_code, got.json()["code"]) == (403, "forbidden")
    assert "manager@example.com" not in got.text


def test_a_stranger_gets_404_listing(client, people):
    got = client.get(_url(people), headers=auth(people["stranger"]))
    assert (got.status_code, got.json()["code"]) == (404, "not_found")


def test_the_owner_removes_a_viewer(client, people):
    got = client.delete(_url(people, people["viewer"]), headers=auth(people["owner"]))
    assert got.status_code == 204
    assert _grant_row(people["db_url"], people["site"], people["viewer"]) is None
    assert _grant_row(people["db_url"], people["site"], people["viewer_b"]) is not None


def test_removing_a_manager_revokes_their_accepted_invite(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner = make_user(db_url, "owner@example.com")
    site = make_site(client, owner)
    member = make_user(db_url, "member@example.com")
    accepted = share_site(client, owner, site, member, "member@example.com")
    with make_engine(db_url).begin() as conn:
        conn.execute(text("UPDATE site_access SET role = 'manager' WHERE user_id = :u"), {"u": member})

    got = client.delete(f"/api/v1/sites/{site}/access/{member}", headers=auth(owner))

    assert got.status_code == 204
    assert _grant_row(db_url, site, member) is None
    session = make_session_factory(make_engine(db_url))()
    invite = session.get(SiteInvite, accepted["id"])
    assert (invite.status, invite.revoked_by_user_id) == (InviteStatus.REVOKED, owner)
    assert invite.revoked_at is not None
    session.close()


def test_the_owner_cannot_remove_themselves(client, people):
    got = client.delete(_url(people, people["owner"]), headers=auth(people["owner"]))
    assert (got.status_code, got.json()["code"]) == (409, "is_owner")


def test_removing_someone_without_a_grant_is_404(client, people):
    got = client.delete(_url(people, people["stranger"]), headers=auth(people["owner"]))
    assert (got.status_code, got.json()["code"]) == (404, "not_found")


@pytest.mark.parametrize(("caller", "code"), [("manager", 403), ("viewer", 403), ("stranger", 404)])
def test_only_the_owner_removes_access(client, people, caller, code):
    got = client.delete(_url(people, people["viewer_b"]), headers=auth(people[caller]))
    assert got.status_code == code
    assert _grant_row(people["db_url"], people["site"], people["viewer_b"]) is not None
