from __future__ import annotations

import logging

import pytest

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import SiteAccess, SiteAccessRole, User
from tests.test_admin_users import USERS_URL, _grant, _site, _user
from tests.test_billing import auth

AMBER_KEY = "psk_admin_set_amber_value_987"
ADMIN_LOGGER = "core_api.admin_users"


def _load_user(db_url: str, user_id: int) -> User:
    session = make_session_factory(make_engine(db_url))()
    try:
        user = session.get(User, user_id)
        assert user is not None
        session.expunge(user)
        return user
    finally:
        session.close()


def _grants(db_url: str, user_id: int) -> list[tuple[int, str]]:
    session = make_session_factory(make_engine(db_url))()
    try:
        rows = session.query(SiteAccess).filter(SiteAccess.user_id == user_id).all()
        return [(row.site_id, row.role.value) for row in rows]
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _enable_core_api_loggers(app_and_db_url, monkeypatch):
    """The migration in app_and_db_url runs Alembic's fileConfig, which
    disables every logger that already exists (see test_admin_user.py). The
    no-leak checks below need every core_api logger live to mean anything."""
    for name, candidate in logging.root.manager.loggerDict.items():
        if name.startswith("core_api") and isinstance(candidate, logging.Logger):
            monkeypatch.setattr(candidate, "disabled", False)


@pytest.fixture()
def admin(app_and_db_url) -> int:
    _, db_url = app_and_db_url
    return _user(db_url, "admin@example.com", is_admin=True)


# --- PATCH /{user_id} -------------------------------------------------------


def test_email_change_is_lowercased_and_keeps_confirmation_state(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    target = _user(db_url, "old@example.com", email_confirmed=False)
    got = client.patch(f"{USERS_URL}/{target}", json={"email": "New.Address@Example.COM"}, headers=auth(admin))
    assert got.status_code == 200, got.text
    assert got.json()["email"] == "new.address@example.com"
    assert got.json()["email_confirmed"] is False
    assert _load_user(db_url, target).email == "new.address@example.com"


def test_duplicate_email_is_409_case_insensitive(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    _user(db_url, "Taken@Example.com")
    target = _user(db_url, "target@example.com")
    got = client.patch(f"{USERS_URL}/{target}", json={"email": "taken@example.com"}, headers=auth(admin))
    assert got.status_code == 409
    assert got.json()["code"] == "email_taken"
    assert _load_user(db_url, target).email == "target@example.com"


def test_setting_a_user_to_their_own_email_is_not_a_conflict(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    target = _user(db_url, "Mixed@example.com")
    got = client.patch(f"{USERS_URL}/{target}", json={"email": "mixed@example.com"}, headers=auth(admin))
    assert got.status_code == 200, got.text
    assert got.json()["email"] == "mixed@example.com"


@pytest.mark.parametrize("body", [{"email": "not-an-email"}, {"email": None}, {"is_admin": None}])
def test_invalid_or_null_fields_are_422(client, app_and_db_url, admin, body):
    _, db_url = app_and_db_url
    target = _user(db_url, "target@example.com")
    got = client.patch(f"{USERS_URL}/{target}", json=body, headers=auth(admin))
    assert got.status_code == 422


def test_display_name_is_set_and_cleared(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    target = _user(db_url, "target@example.com")
    url = f"{USERS_URL}/{target}"
    set_ = client.patch(url, json={"display_name": "  Sam  "}, headers=auth(admin))
    assert set_.status_code == 200, set_.text
    assert set_.json()["display_name"] == "Sam"

    untouched = client.patch(url, json={}, headers=auth(admin))
    assert untouched.json()["display_name"] == "Sam"

    cleared = client.patch(url, json={"display_name": None}, headers=auth(admin))
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["display_name"] is None
    assert _load_user(db_url, target).display_name is None


def test_blank_display_name_is_422(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    target = _user(db_url, "target@example.com")
    got = client.patch(f"{USERS_URL}/{target}", json={"display_name": "   "}, headers=auth(admin))
    assert got.status_code == 422


def test_is_admin_can_be_granted(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    target = _user(db_url, "target@example.com")
    got = client.patch(f"{USERS_URL}/{target}", json={"is_admin": True}, headers=auth(admin))
    assert got.status_code == 200, got.text
    assert got.json()["is_admin"] is True
    assert _load_user(db_url, target).is_admin is True


def test_clearing_the_only_admin_is_409_last_admin(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    got = client.patch(f"{USERS_URL}/{admin}", json={"is_admin": False}, headers=auth(admin))
    assert got.status_code == 409
    assert got.json()["code"] == "last_admin"
    assert _load_user(db_url, admin).is_admin is True


def test_with_two_admins_the_first_demotion_succeeds_and_the_second_is_409(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    second = _user(db_url, "second@example.com", is_admin=True)
    first = client.patch(f"{USERS_URL}/{admin}", json={"is_admin": False}, headers=auth(second))
    assert first.status_code == 200, first.text
    assert first.json()["is_admin"] is False

    last = client.patch(f"{USERS_URL}/{second}", json={"is_admin": False}, headers=auth(second))
    assert last.status_code == 409
    assert last.json()["code"] == "last_admin"
    assert _load_user(db_url, second).is_admin is True


def test_a_failed_demotion_does_not_apply_other_fields(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    got = client.patch(
        f"{USERS_URL}/{admin}",
        json={"is_admin": False, "display_name": "Changed"},
        headers=auth(admin),
    )
    assert got.status_code == 409
    assert _load_user(db_url, admin).display_name is None


def test_clearing_the_flag_of_a_non_admin_is_not_a_conflict(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    target = _user(db_url, "target@example.com")
    got = client.patch(f"{USERS_URL}/{target}", json={"is_admin": False}, headers=auth(admin))
    assert got.status_code == 200, got.text
    assert got.json()["is_admin"] is False


def test_patch_unknown_user_is_404(client, admin):
    got = client.patch(f"{USERS_URL}/9999", json={"display_name": "X"}, headers=auth(admin))
    assert got.status_code == 404
    assert got.json()["code"] == "not_found"


# --- avatar and Amber key ---------------------------------------------------


def test_admin_removes_a_users_avatar(client, app_and_db_url, admin, tmp_path):
    _, db_url = app_and_db_url
    target = _user(db_url, "target@example.com", avatar_content_type="image/png")
    avatar_file = tmp_path / "avatars" / str(target)
    avatar_file.parent.mkdir(parents=True, exist_ok=True)
    avatar_file.write_bytes(b"png-bytes")

    got = client.delete(f"{USERS_URL}/{target}/avatar", headers=auth(admin))
    assert got.status_code == 200, got.text
    assert got.json()["has_avatar"] is False
    assert not avatar_file.exists()
    assert _load_user(db_url, target).avatar_content_type is None


def test_setting_the_amber_key_never_returns_or_logs_it(client, app_and_db_url, admin, caplog):
    _, db_url = app_and_db_url
    target = _user(db_url, "target@example.com")
    with caplog.at_level(logging.DEBUG):
        got = client.put(f"{USERS_URL}/{target}/amber-key", json={"amber_api_key": AMBER_KEY}, headers=auth(admin))
    assert got.status_code == 200, got.text
    assert got.json()["has_amber_api_key"] is True
    assert AMBER_KEY not in got.text
    assert AMBER_KEY not in caplog.text
    assert all(AMBER_KEY not in str(r.args) for r in caplog.records)
    assert _load_user(db_url, target).amber_api_key == AMBER_KEY


def test_empty_amber_key_is_422(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    target = _user(db_url, "target@example.com")
    got = client.put(f"{USERS_URL}/{target}/amber-key", json={"amber_api_key": ""}, headers=auth(admin))
    assert got.status_code == 422


def test_clearing_the_amber_key(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    target = _user(db_url, "target@example.com", amber_api_key=AMBER_KEY)
    got = client.delete(f"{USERS_URL}/{target}/amber-key", headers=auth(admin))
    assert got.status_code == 200, got.text
    assert got.json()["has_amber_api_key"] is False
    assert _load_user(db_url, target).amber_api_key is None


# --- site access ------------------------------------------------------------


def test_granting_manager_then_viewer_changes_the_row(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    owner = _user(db_url, "owner@example.com")
    target = _user(db_url, "target@example.com")
    site = _site(db_url, owner, "Home")
    url = f"{USERS_URL}/{target}/sites/{site}"

    managed = client.put(url, json={"role": "manager"}, headers=auth(admin))
    assert managed.status_code == 200, managed.text
    assert managed.json()["sites"] == [{"site_id": site, "site_name": "Home", "role": "manager"}]

    viewed = client.put(url, json={"role": "viewer"}, headers=auth(admin))
    assert viewed.status_code == 200, viewed.text
    assert viewed.json()["sites"] == [{"site_id": site, "site_name": "Home", "role": "viewer"}]
    assert _grants(db_url, target) == [(site, "viewer")]


@pytest.mark.parametrize("role", ["owner", "admin", ""])
def test_granting_an_unknown_role_is_422(client, app_and_db_url, admin, role):
    _, db_url = app_and_db_url
    owner = _user(db_url, "owner@example.com")
    target = _user(db_url, "target@example.com")
    site = _site(db_url, owner, "Home")
    got = client.put(f"{USERS_URL}/{target}/sites/{site}", json={"role": role}, headers=auth(admin))
    assert got.status_code == 422


def test_granting_a_role_on_the_users_own_site_is_409(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    owner = _user(db_url, "owner@example.com")
    site = _site(db_url, owner, "Home")
    got = client.put(f"{USERS_URL}/{owner}/sites/{site}", json={"role": "viewer"}, headers=auth(admin))
    assert got.status_code == 409
    assert got.json()["code"] == "is_owner"
    assert _grants(db_url, owner) == []


def test_grant_for_unknown_user_or_site_is_404(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    owner = _user(db_url, "owner@example.com")
    site = _site(db_url, owner, "Home")
    for url in (f"{USERS_URL}/9999/sites/{site}", f"{USERS_URL}/{owner}/sites/9999"):
        got = client.put(url, json={"role": "viewer"}, headers=auth(admin))
        assert got.status_code == 404, url
        assert got.json()["code"] == "not_found"
        assert client.delete(url, headers=auth(admin)).json()["code"] == "not_found"


def test_removing_a_grant(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    owner = _user(db_url, "owner@example.com")
    target = _user(db_url, "target@example.com")
    site = _site(db_url, owner, "Home")
    _grant(db_url, site, target, SiteAccessRole.MANAGER)

    got = client.delete(f"{USERS_URL}/{target}/sites/{site}", headers=auth(admin))
    assert got.status_code == 200, got.text
    assert got.json()["sites"] == []
    assert _grants(db_url, target) == []


def test_removing_a_missing_grant_is_404(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    owner = _user(db_url, "owner@example.com")
    target = _user(db_url, "target@example.com")
    site = _site(db_url, owner, "Home")
    got = client.delete(f"{USERS_URL}/{target}/sites/{site}", headers=auth(admin))
    assert got.status_code == 404
    assert got.json()["code"] == "not_found"


def test_removing_the_owner_is_409(client, app_and_db_url, admin):
    _, db_url = app_and_db_url
    owner = _user(db_url, "owner@example.com")
    site = _site(db_url, owner, "Home")
    got = client.delete(f"{USERS_URL}/{owner}/sites/{site}", headers=auth(admin))
    assert got.status_code == 409
    assert got.json()["code"] == "is_owner"


# --- logging ----------------------------------------------------------------


def _write_calls(target: int, site: int) -> list[tuple[str, str, dict | None, str]]:
    base = f"{USERS_URL}/{target}"
    return [
        ("PATCH", base, {"display_name": "Secret Name", "email": "secret.new@example.com"}, "update_user"),
        ("PATCH", base, {"is_admin": True}, "update_user"),
        ("DELETE", f"{base}/avatar", None, "remove_avatar"),
        ("PUT", f"{base}/amber-key", {"amber_api_key": AMBER_KEY}, "set_amber_api_key"),
        ("DELETE", f"{base}/amber-key", None, "clear_amber_api_key"),
        ("PUT", f"{base}/sites/{site}", {"role": "manager"}, "grant_site_access"),
        ("DELETE", f"{base}/sites/{site}", None, "remove_site_access"),
    ]


def test_each_write_logs_one_line_with_ids_and_action_only(client, app_and_db_url, admin, caplog):
    _, db_url = app_and_db_url
    owner = _user(db_url, "owner@example.com")
    target = _user(db_url, "target@example.com")
    site = _site(db_url, owner, "Home")

    for method, url, body, action in _write_calls(target, site):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=ADMIN_LOGGER):
            got = client.request(method, url, json=body, headers=auth(admin))
        assert got.status_code == 200, (url, got.text)
        records = [r for r in caplog.records if r.name == ADMIN_LOGGER]
        assert len(records) == 1, (method, url)
        message = records[0].getMessage()
        assert message == f"admin action: admin_user_id={admin} target_user_id={target} action={action}"
        for value in ("Secret Name", "secret.new@example.com", "target@example.com", AMBER_KEY):
            assert value not in caplog.text
