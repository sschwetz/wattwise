from __future__ import annotations

import pytest

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import InviteStatus, SiteAccess, SiteInvite
from tests.test_sites import auth, make_user


def make_site(client, owner_id: int, name: str = "Home") -> int:
    resp = client.post("/api/v1/sites", json={"name": name, "timezone": "UTC"}, headers=auth(owner_id))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def invite(client, owner_id: int, site_id: int, email: str):
    return client.post(f"/api/v1/sites/{site_id}/invites", json={"email": email}, headers=auth(owner_id))


def set_status(db_url: str, invite_id: int, status: InviteStatus) -> None:
    session = make_session_factory(make_engine(db_url))()
    row = session.get(SiteInvite, invite_id)
    row.status = status
    session.commit()
    session.close()


def test_invite_to_unregistered_email_sends_email_and_is_invited(client, app_and_db_url, fake_email_sender):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    site_id = make_site(client, owner_id, "Beach House")
    resp = invite(client, owner_id, site_id, "new@example.com")
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "invited"
    assert len(fake_email_sender.sent) == 1
    to, subject, _body = fake_email_sender.sent[0]
    assert to == "new@example.com"
    assert "Beach House" in subject


def test_invite_response_and_email_same_for_registered_and_unregistered(client, app_and_db_url, fake_email_sender):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    make_user(db_url, "known@example.com")
    site_id = make_site(client, owner_id)
    known = invite(client, owner_id, site_id, "known@example.com")
    unknown = invite(client, owner_id, site_id, "unknown@example.com")
    assert known.status_code == unknown.status_code == 201
    assert known.json().keys() == unknown.json().keys()
    assert known.json()["status"] == unknown.json()["status"]
    (_, subject_a, body_a), (_, subject_b, body_b) = fake_email_sender.sent
    assert subject_a == subject_b
    assert body_a == body_b


def test_email_is_lowercased(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    site_id = make_site(client, owner_id)
    resp = invite(client, owner_id, site_id, "Bob@Example.com")
    assert resp.json()["email"] == "bob@example.com"
    listed = client.get(f"/api/v1/sites/{site_id}/invites", headers=auth(owner_id)).json()
    assert listed[0]["email"] == "bob@example.com"


def test_cannot_invite_self(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    site_id = make_site(client, owner_id)
    assert invite(client, owner_id, site_id, "Owner@Example.com").status_code == 400


@pytest.mark.parametrize("status", [InviteStatus.INVITED, InviteStatus.ACCEPTED])
def test_duplicate_invite_returns_409_already_invited(client, app_and_db_url, status):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    site_id = make_site(client, owner_id)
    first = invite(client, owner_id, site_id, "bob@example.com").json()
    set_status(db_url, first["id"], status)
    again = invite(client, owner_id, site_id, "BOB@example.com")
    assert again.status_code == 409
    assert again.json()["code"] == "already_invited"


@pytest.mark.parametrize("status", [InviteStatus.REVOKED, InviteStatus.DECLINED])
def test_final_invite_allows_a_fresh_one(client, app_and_db_url, status):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    site_id = make_site(client, owner_id)
    first = invite(client, owner_id, site_id, "bob@example.com").json()
    set_status(db_url, first["id"], status)
    again = invite(client, owner_id, site_id, "bob@example.com")
    assert again.status_code == 201
    assert again.json()["id"] != first["id"]


def test_pending_invite_cap_is_20(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    site_id = make_site(client, owner_id)
    for i in range(20):
        assert invite(client, owner_id, site_id, f"p{i}@example.com").status_code == 201
    over = invite(client, owner_id, site_id, "p20@example.com")
    assert over.status_code == 409
    assert over.json()["code"] == "invite_limit"


def test_viewer_cannot_create_invite(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    viewer_id = make_user(db_url, "viewer@example.com")
    site_id = make_site(client, owner_id)
    client.post(f"/api/v1/sites/{site_id}/access", json={"user_id": viewer_id}, headers=auth(owner_id))
    assert invite(client, viewer_id, site_id, "x@example.com").status_code == 403


def test_owner_lists_all_invites_viewer_lists_only_own(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    viewer_id = make_user(db_url, "viewer@example.com")
    site_id = make_site(client, owner_id)
    other = invite(client, owner_id, site_id, "other@example.com").json()
    mine = invite(client, owner_id, site_id, "viewer@example.com").json()

    session = make_session_factory(make_engine(db_url))()
    row = session.get(SiteInvite, mine["id"])
    row.status = InviteStatus.ACCEPTED
    row.invitee_user_id = viewer_id
    session.add(SiteAccess(site_id=site_id, user_id=viewer_id))
    session.commit()
    session.close()

    owner_list = client.get(f"/api/v1/sites/{site_id}/invites", headers=auth(owner_id)).json()
    assert [i["id"] for i in owner_list] == [mine["id"], other["id"]]
    viewer_list = client.get(f"/api/v1/sites/{site_id}/invites", headers=auth(viewer_id)).json()
    assert [i["id"] for i in viewer_list] == [mine["id"]]
    assert viewer_list[0]["status"] == "accepted"


def test_email_failure_does_not_fail_the_request(client, app_and_db_url, fake_email_sender, monkeypatch):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    site_id = make_site(client, owner_id)

    def boom(**_kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(fake_email_sender, "send", boom)
    assert invite(client, owner_id, site_id, "bob@example.com").status_code == 201
