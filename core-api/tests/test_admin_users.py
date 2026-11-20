from __future__ import annotations

from sqlalchemy import event

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, SiteAccess, SiteAccessRole, User
from tests.test_billing import auth

AMBER_KEY = "psk_amber_secret_value_123"
PASSWORD_HASH = "hash-value-should-not-leak"
USERS_URL = "/api/v1/admin/users"


def _add(db_url: str, row) -> int:
    session = make_session_factory(make_engine(db_url))()
    try:
        session.add(row)
        session.commit()
        return getattr(row, "id", 0)
    finally:
        session.close()


def _user(db_url: str, email: str, **fields) -> int:
    fields.setdefault("password_hash", "x")
    return _add(db_url, User(email=email, **fields))


def _site(db_url: str, owner_id: int, name: str) -> int:
    return _add(db_url, Site(owner_user_id=owner_id, name=name, timezone="Australia/Sydney"))


def _grant(db_url: str, site_id: int, user_id: int, role: SiteAccessRole) -> None:
    _add(db_url, SiteAccess(site_id=site_id, user_id=user_id, role=role))


def _by_email(body: list[dict]) -> dict[str, dict]:
    return {u["email"]: u for u in body}


def test_list_shows_owner_manager_and_viewer_roles(client, app_and_db_url):
    _, db_url = app_and_db_url
    admin = _user(db_url, "admin@example.com", is_admin=True)
    owner = _user(db_url, "owner@example.com")
    manager = _user(db_url, "manager@example.com")
    viewer = _user(db_url, "viewer@example.com")
    site = _site(db_url, owner, "Home")
    _grant(db_url, site, manager, SiteAccessRole.MANAGER)
    _grant(db_url, site, viewer, SiteAccessRole.VIEWER)

    got = client.get(USERS_URL, headers=auth(admin))
    assert got.status_code == 200, got.text
    ids = [u["id"] for u in got.json()]
    assert ids == sorted(ids)
    users = _by_email(got.json())
    assert users["owner@example.com"]["sites"] == [{"site_id": site, "site_name": "Home", "role": "owner"}]
    assert users["manager@example.com"]["sites"] == [{"site_id": site, "site_name": "Home", "role": "manager"}]
    assert users["viewer@example.com"]["sites"] == [{"site_id": site, "site_name": "Home", "role": "viewer"}]
    assert users["admin@example.com"]["is_admin"] is True


def test_users_with_no_sites_get_an_empty_list(client, app_and_db_url):
    _, db_url = app_and_db_url
    admin = _user(db_url, "admin@example.com", is_admin=True)
    got = client.get(USERS_URL, headers=auth(admin))
    assert got.json()[0]["sites"] == []


def test_sites_are_ordered_by_role_then_name(client, app_and_db_url):
    _, db_url = app_and_db_url
    admin = _user(db_url, "admin@example.com", is_admin=True)
    other = _user(db_url, "other@example.com")
    target = _user(db_url, "target@example.com")
    owned_b = _site(db_url, target, "B owned")
    owned_a = _site(db_url, target, "A owned")
    viewed = _site(db_url, other, "A viewed")
    managed = _site(db_url, other, "Z managed")
    _grant(db_url, viewed, target, SiteAccessRole.VIEWER)
    _grant(db_url, managed, target, SiteAccessRole.MANAGER)

    sites = client.get(f"{USERS_URL}/{target}", headers=auth(admin)).json()["sites"]
    assert [(s["site_id"], s["role"]) for s in sites] == [
        (owned_a, "owner"),
        (owned_b, "owner"),
        (managed, "manager"),
        (viewed, "viewer"),
    ]


def test_owner_with_an_access_row_for_the_same_site_appears_once(client, app_and_db_url):
    _, db_url = app_and_db_url
    admin = _user(db_url, "admin@example.com", is_admin=True)
    owner = _user(db_url, "owner@example.com")
    site = _site(db_url, owner, "Home")
    _grant(db_url, site, owner, SiteAccessRole.VIEWER)

    listed = _by_email(client.get(USERS_URL, headers=auth(admin)).json())
    assert listed["owner@example.com"]["sites"] == [{"site_id": site, "site_name": "Home", "role": "owner"}]
    single = client.get(f"{USERS_URL}/{owner}", headers=auth(admin)).json()
    assert single["sites"] == listed["owner@example.com"]["sites"]


def test_flags_are_reported(client, app_and_db_url):
    _, db_url = app_and_db_url
    admin = _user(db_url, "admin@example.com", is_admin=True)
    target = _user(
        db_url,
        "flags@example.com",
        display_name="Flags",
        avatar_content_type="image/png",
        totp_enabled=True,
        email_confirmed=False,
        amber_api_key=AMBER_KEY,
    )
    got = client.get(f"{USERS_URL}/{target}", headers=auth(admin)).json()
    assert got == {
        "id": target,
        "email": "flags@example.com",
        "display_name": "Flags",
        "is_admin": False,
        "has_avatar": True,
        "totp_enabled": True,
        "email_confirmed": False,
        "has_amber_api_key": True,
        "sites": [],
    }


def test_no_secret_names_or_values_in_the_response(client, app_and_db_url):
    _, db_url = app_and_db_url
    admin = _user(db_url, "admin@example.com", is_admin=True)
    target = _user(
        db_url,
        "secret@example.com",
        password_hash=PASSWORD_HASH,
        amber_api_key=AMBER_KEY,
        totp_secret="TOTPSECRETVALUE",
        pending_totp_secret="PENDINGTOTPVALUE",
        last_totp_step=12345,
    )
    for response in (
        client.get(USERS_URL, headers=auth(admin)),
        client.get(f"{USERS_URL}/{target}", headers=auth(admin)),
    ):
        assert response.status_code == 200
        text = response.text
        for name in ("password_hash", "amber_api_key", "totp_secret", "pending_totp_secret", "last_totp_step"):
            assert f'"{name}"' not in text  # quoted: "has_amber_api_key" is allowed
        for value in (PASSWORD_HASH, AMBER_KEY, "TOTPSECRETVALUE", "PENDINGTOTPVALUE", "12345"):
            assert value not in text


def test_non_admin_gets_403(client, app_and_db_url):
    _, db_url = app_and_db_url
    plain = _user(db_url, "plain@example.com")
    for url in (USERS_URL, f"{USERS_URL}/{plain}"):
        got = client.get(url, headers=auth(plain))
        assert got.status_code == 403
        assert got.json()["code"] == "admin_required"


def test_no_token_gets_401(client, app_and_db_url):
    _, db_url = app_and_db_url
    plain = _user(db_url, "plain@example.com")
    for url in (USERS_URL, f"{USERS_URL}/{plain}"):
        assert client.get(url).status_code == 401


def test_unknown_user_is_404(client, app_and_db_url):
    _, db_url = app_and_db_url
    admin = _user(db_url, "admin@example.com", is_admin=True)
    got = client.get(f"{USERS_URL}/9999", headers=auth(admin))
    assert got.status_code == 404
    assert got.json()["code"] == "not_found"


def _count_list_queries(client, app, admin: int) -> int:
    engine = app.state.session_factory.kw["bind"]
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert client.get(USERS_URL, headers=auth(admin)).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return len(statements)


def test_list_does_not_query_per_user(client, app_and_db_url):
    app, db_url = app_and_db_url
    admin = _user(db_url, "admin@example.com", is_admin=True)
    first = _user(db_url, "u1@example.com")
    _site(db_url, first, "S1")
    small = _count_list_queries(client, app, admin)

    for i in range(2, 6):
        uid = _user(db_url, f"u{i}@example.com")
        site = _site(db_url, uid, f"S{i}")
        _grant(db_url, site, first, SiteAccessRole.VIEWER)
    large = _count_list_queries(client, app, admin)
    assert large == small
