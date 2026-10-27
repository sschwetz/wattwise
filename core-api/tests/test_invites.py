from __future__ import annotations

import pytest

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import InviteStatus, SiteAccess, SiteInvite, User
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


def share_site(client, owner_id: int, site_id: int, viewer_id: int, email: str) -> dict:
    """Invite `email` and accept as `viewer_id`. Returns the accepted invite."""
    created = invite(client, owner_id, site_id, email).json()
    accepted = client.post(f"/api/v1/invites/{created['id']}/accept", headers=auth(viewer_id))
    assert accepted.status_code == 200, accepted.text
    return accepted.json()


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
    share_site(client, owner_id, site_id, viewer_id, "viewer@example.com")
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


def unconfirm(db_url: str, user_id: int) -> None:
    session = make_session_factory(make_engine(db_url))()
    session.get(User, user_id).email_confirmed = False
    session.commit()
    session.close()


def access_rows(db_url: str, site_id: int) -> list[SiteAccess]:
    session = make_session_factory(make_engine(db_url))()
    rows = list(session.query(SiteAccess).filter_by(site_id=site_id))
    session.close()
    return rows


def pending(client, user_id: int):
    return client.get("/api/v1/invites/pending", headers=auth(user_id))


def act(client, user_id: int, invite_id: int, action: str):
    return client.post(f"/api/v1/invites/{invite_id}/{action}", headers=auth(user_id))


def test_pending_lists_invites_for_confirmed_email_case_insensitively(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    bob_id = make_user(db_url, "Bob@Example.com")
    site_id = make_site(client, owner_id, "Beach House")
    invite(client, owner_id, site_id, "BOB@example.com")
    resp = pending(client, bob_id)
    assert resp.status_code == 200, resp.text
    (item,) = resp.json()
    assert item["site_id"] == site_id
    assert item["site_name"] == "Beach House"
    assert item["inviter_email"] == "owner@example.com"
    assert {"id", "created_at"} <= item.keys()


def test_pending_hidden_when_email_not_confirmed(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    bob_id = make_user(db_url, "bob@example.com")
    site_id = make_site(client, owner_id)
    invite(client, owner_id, site_id, "bob@example.com")
    unconfirm(db_url, bob_id)
    assert pending(client, bob_id).json() == []


@pytest.mark.parametrize("status", [InviteStatus.ACCEPTED, InviteStatus.DECLINED, InviteStatus.REVOKED])
def test_pending_excludes_non_invited_statuses(client, app_and_db_url, status):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    bob_id = make_user(db_url, "bob@example.com")
    site_id = make_site(client, owner_id)
    invite_id = invite(client, owner_id, site_id, "bob@example.com").json()["id"]
    set_status(db_url, invite_id, status)
    assert pending(client, bob_id).json() == []


def test_accept_creates_site_access_and_sets_accepted_at_and_invitee(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    bob_id = make_user(db_url, "bob@example.com")
    site_id = make_site(client, owner_id)
    invite_id = invite(client, owner_id, site_id, "bob@example.com").json()["id"]
    resp = act(client, bob_id, invite_id, "accept")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["accepted_at"] is not None
    sites = client.get("/api/v1/sites", headers=auth(bob_id)).json()
    assert site_id in [s["id"] for s in sites]
    session = make_session_factory(make_engine(db_url))()
    assert session.get(SiteInvite, invite_id).invitee_user_id == bob_id
    session.close()


def test_accept_twice_returns_409_and_one_access_row(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    bob_id = make_user(db_url, "bob@example.com")
    site_id = make_site(client, owner_id)
    invite_id = invite(client, owner_id, site_id, "bob@example.com").json()["id"]
    assert act(client, bob_id, invite_id, "accept").status_code == 200
    assert act(client, bob_id, invite_id, "accept").status_code == 409
    assert len(access_rows(db_url, site_id)) == 1


def test_accept_someone_elses_invite_returns_404(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    make_user(db_url, "bob@example.com")
    eve_id = make_user(db_url, "eve@example.com")
    site_id = make_site(client, owner_id)
    invite_id = invite(client, owner_id, site_id, "bob@example.com").json()["id"]
    assert act(client, eve_id, invite_id, "accept").status_code == 404
    assert act(client, eve_id, invite_id, "decline").status_code == 404
    assert act(client, eve_id, 99999, "accept").status_code == 404
    assert access_rows(db_url, site_id) == []


def test_accept_with_unconfirmed_email_returns_404(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    bob_id = make_user(db_url, "bob@example.com")
    site_id = make_site(client, owner_id)
    invite_id = invite(client, owner_id, site_id, "bob@example.com").json()["id"]
    unconfirm(db_url, bob_id)
    assert act(client, bob_id, invite_id, "accept").status_code == 404


def test_decline_marks_declined_with_timestamp_and_creates_no_access(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    bob_id = make_user(db_url, "bob@example.com")
    site_id = make_site(client, owner_id)
    invite_id = invite(client, owner_id, site_id, "bob@example.com").json()["id"]
    resp = act(client, bob_id, invite_id, "decline")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "declined"
    assert resp.json()["declined_at"] is not None
    assert access_rows(db_url, site_id) == []


def test_decline_after_accept_returns_409(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    bob_id = make_user(db_url, "bob@example.com")
    site_id = make_site(client, owner_id)
    invite_id = invite(client, owner_id, site_id, "bob@example.com").json()["id"]
    assert act(client, bob_id, invite_id, "accept").status_code == 200
    assert act(client, bob_id, invite_id, "decline").status_code == 409


def revoke(client, user_id: int, site_id: int, invite_id: int):
    return client.post(f"/api/v1/sites/{site_id}/invites/{invite_id}/revoke", headers=auth(user_id))


def load_invite(db_url: str, invite_id: int) -> SiteInvite:
    session = make_session_factory(make_engine(db_url))()
    row = session.get(SiteInvite, invite_id)
    session.expunge(row)
    session.close()
    return row


def site_ids(client, user_id: int) -> list[int]:
    return [s["id"] for s in client.get("/api/v1/sites", headers=auth(user_id)).json()]


@pytest.mark.parametrize("status", [InviteStatus.REVOKED, InviteStatus.DECLINED])
def test_accepting_a_final_invite_returns_409_and_grants_nothing(client, app_and_db_url, status):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    bob_id = make_user(db_url, "bob@example.com")
    site_id = make_site(client, owner_id)
    created = invite(client, owner_id, site_id, "bob@example.com").json()
    set_status(db_url, created["id"], status)
    resp = act(client, bob_id, created["id"], "accept")
    assert resp.status_code == 409
    assert access_rows(db_url, site_id) == []


def test_owner_revokes_pending_invite(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    site_id = make_site(client, owner_id)
    created = invite(client, owner_id, site_id, "bob@example.com").json()
    resp = revoke(client, owner_id, site_id, created["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "revoked"
    assert resp.json()["revoked_at"] is not None
    assert load_invite(db_url, created["id"]).revoked_by_user_id == owner_id


def test_owner_revokes_accepted_invite_removes_access(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    viewer_id = make_user(db_url, "viewer@example.com")
    site_id = make_site(client, owner_id)
    accepted = share_site(client, owner_id, site_id, viewer_id, "viewer@example.com")
    assert site_id in site_ids(client, viewer_id)
    assert revoke(client, owner_id, site_id, accepted["id"]).status_code == 200
    assert site_id not in site_ids(client, viewer_id)
    assert access_rows(db_url, site_id) == []
    row = load_invite(db_url, accepted["id"])
    assert row.status == InviteStatus.REVOKED
    assert row.revoked_by_user_id == owner_id


def test_viewer_revokes_own_accepted_invite(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    viewer_id = make_user(db_url, "viewer@example.com")
    site_id = make_site(client, owner_id)
    accepted = share_site(client, owner_id, site_id, viewer_id, "viewer@example.com")
    resp = revoke(client, viewer_id, site_id, accepted["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "revoked"
    assert load_invite(db_url, accepted["id"]).revoked_by_user_id == viewer_id
    assert site_id not in site_ids(client, viewer_id)
    assert access_rows(db_url, site_id) == []


def test_viewer_cannot_revoke_pending_invite_or_anyone_elses(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    viewer_id = make_user(db_url, "viewer@example.com")
    other_id = make_user(db_url, "other@example.com")
    site_id = make_site(client, owner_id)
    mine = share_site(client, owner_id, site_id, viewer_id, "viewer@example.com")
    theirs = share_site(client, owner_id, site_id, other_id, "other@example.com")
    pending_invite = invite(client, owner_id, site_id, "late@example.com").json()
    assert revoke(client, viewer_id, site_id, theirs["id"]).status_code in (403, 404)
    assert revoke(client, viewer_id, site_id, pending_invite["id"]).status_code in (403, 404)
    assert load_invite(db_url, theirs["id"]).status == InviteStatus.ACCEPTED
    assert load_invite(db_url, pending_invite["id"]).status == InviteStatus.INVITED
    assert load_invite(db_url, mine["id"]).status == InviteStatus.ACCEPTED


def test_stranger_cannot_revoke(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    stranger_id = make_user(db_url, "stranger@example.com")
    site_id = make_site(client, owner_id)
    created = invite(client, owner_id, site_id, "bob@example.com").json()
    assert revoke(client, stranger_id, site_id, created["id"]).status_code == 404


def test_revoke_invite_from_another_site_returns_404(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    site_a = make_site(client, owner_id, "A")
    site_b = make_site(client, owner_id, "B")
    created = invite(client, owner_id, site_a, "bob@example.com").json()
    assert revoke(client, owner_id, site_b, created["id"]).status_code == 404
    assert load_invite(db_url, created["id"]).status == InviteStatus.INVITED


@pytest.mark.parametrize("status", [InviteStatus.DECLINED, InviteStatus.REVOKED])
def test_revoke_declined_or_revoked_returns_409(client, app_and_db_url, status):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    site_id = make_site(client, owner_id)
    created = invite(client, owner_id, site_id, "bob@example.com").json()
    set_status(db_url, created["id"], status)
    assert revoke(client, owner_id, site_id, created["id"]).status_code == 409
    assert load_invite(db_url, created["id"]).status == status


def test_reinvite_after_revoke_creates_new_row_and_old_row_unchanged(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    site_id = make_site(client, owner_id)
    first = invite(client, owner_id, site_id, "bob@example.com").json()
    revoke(client, owner_id, site_id, first["id"])
    before = load_invite(db_url, first["id"])
    second = invite(client, owner_id, site_id, "bob@example.com")
    assert second.status_code == 201
    assert second.json()["id"] != first["id"]
    after = load_invite(db_url, first["id"])
    assert (after.status, after.revoked_at, after.revoked_by_user_id) == (
        before.status,
        before.revoked_at,
        before.revoked_by_user_id,
    )


def test_no_endpoint_deletes_an_invite(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    site_id = make_site(client, owner_id)
    created = invite(client, owner_id, site_id, "bob@example.com").json()
    resp = client.delete(f"/api/v1/sites/{site_id}/invites/{created['id']}", headers=auth(owner_id))
    assert resp.status_code == 405
    assert load_invite(db_url, created["id"]) is not None


def test_old_access_routes_are_gone(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id = make_user(db_url, "owner@example.com")
    site_id = make_site(client, owner_id)
    headers = auth(owner_id)
    assert client.post(f"/api/v1/sites/{site_id}/access", json={"user_id": owner_id}, headers=headers).status_code in (404, 405)
    assert client.get(f"/api/v1/sites/{site_id}/access", headers=headers).status_code in (404, 405)
    assert client.delete(f"/api/v1/sites/{site_id}/access/{owner_id}", headers=headers).status_code in (404, 405)
