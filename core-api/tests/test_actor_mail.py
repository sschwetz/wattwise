from __future__ import annotations

import datetime
import re

import pytest
import sqlalchemy as sa
from fastapi.routing import iter_route_contexts

from core_api.db import make_engine, make_session_factory
from core_api.models.mail import EmailOutbox, MailProvider, OutboxStatus
from tests.conftest import ACTOR_KEY, MAIL_ACTOR_KEY

MAIL_URL = "/api/v1/actor/mail"
HEADERS = {"X-Actor-Key": MAIL_ACTOR_KEY, "X-Actor-Provider": "mail"}
SETTINGS = {"api_key": "dummy-api-key-value"}
BODY = "Reset your password: https://example.test/reset?token=dummy-token-value"
FIELDS = [{"name": "api_key", "label": "API key", "secret": True, "required": True}]


@pytest.fixture
def session(app_and_db_url):
    _, db_url = app_and_db_url
    db = make_session_factory(make_engine(db_url))()
    yield db
    db.close()


def _declaration(provider: str = "smtp2go", label: str = "SMTP2GO", fields: list | None = None) -> dict:
    return {"provider": provider, "label": label, "fields": FIELDS if fields is None else fields}


def _put_providers(client, body):
    return client.put(f"{MAIL_URL}/providers", json=body, headers=HEADERS)


def _outbox_row(session, **fields) -> int:
    fields.setdefault("to_address", "a@example.com")
    fields.setdefault("subject", "Reset")
    fields.setdefault("body", BODY)
    fields.setdefault("next_attempt_at", datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1))
    row = EmailOutbox(**fields)
    session.add(row)
    session.commit()
    return row.id


def _reload(session, model, key):
    session.expire_all()
    return session.get(model, key)


# --- PUT /providers ---


def test_declare_creates_a_provider_row(client, session):
    assert _put_providers(client, [_declaration()]).status_code == 204
    row = _reload(session, MailProvider, "smtp2go")
    assert row.label == "SMTP2GO"
    assert row.fields == FIELDS
    assert row.settings is None
    assert row.is_active is False
    assert row.declared_at is not None and row.updated_at is not None


def test_a_second_declare_updates_label_and_fields_but_not_settings_or_active(client, session):
    _put_providers(client, [_declaration()])
    row = _reload(session, MailProvider, "smtp2go")
    row.settings = SETTINGS
    row.is_active = True
    old_stamp = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    row.declared_at = old_stamp
    row.updated_at = old_stamp
    session.commit()

    new_fields = [*FIELDS, {"name": "sender", "label": "Sender", "secret": False, "required": False}]
    assert _put_providers(client, [_declaration(label="SMTP2GO v2", fields=new_fields)]).status_code == 204

    row = _reload(session, MailProvider, "smtp2go")
    assert row.label == "SMTP2GO v2"
    assert row.fields == new_fields
    assert row.settings == SETTINGS
    assert row.is_active is True
    assert row.declared_at.year > 2020
    assert row.updated_at.year > 2020


def test_declare_keeps_providers_the_actor_no_longer_lists(client, session):
    _put_providers(client, [_declaration("smtp2go"), _declaration("other", "Other")])
    _put_providers(client, [_declaration("smtp2go")])
    session.expire_all()
    assert {r.provider for r in session.query(MailProvider)} == {"smtp2go", "other"}


@pytest.mark.parametrize(
    "body",
    [
        [_declaration(), _declaration()],
        [_declaration(fields=[FIELDS[0], FIELDS[0]])],
        [_declaration(provider="SMTP2GO")],
        [_declaration(provider="a" * 41)],
        [_declaration(fields=[{**FIELDS[0], "name": "api-key"}])],
        [{**_declaration(), "extra": 1}],
        [_declaration(fields=[{**FIELDS[0], "extra": 1}])],
        [_declaration(f"p{i}") for i in range(21)],
        [_declaration(fields=[{**FIELDS[0], "name": f"f{i}"} for i in range(31)])],
    ],
    ids=[
        "duplicate-provider",
        "duplicate-field",
        "uppercase-provider",
        "long-provider",
        "bad-field-name",
        "extra-provider-key",
        "extra-field-key",
        "too-many-providers",
        "too-many-fields",
    ],
)
def test_bad_declarations_are_422(client, session, body):
    got = _put_providers(client, body)
    assert got.status_code == 422
    assert got.json()["code"] == "validation_error"
    session.expire_all()
    assert session.query(MailProvider).count() == 0


def test_the_limits_themselves_are_accepted(client):
    many_fields = [{**FIELDS[0], "name": f"f{i}"} for i in range(30)]
    body = [_declaration(f"p{i}", fields=many_fields) for i in range(20)]
    assert _put_providers(client, body).status_code == 204


# --- GET /active ---


def test_active_returns_204_when_no_provider_is_active(client):
    _put_providers(client, [_declaration()])
    got = client.get(f"{MAIL_URL}/active", headers=HEADERS)
    assert got.status_code == 204
    assert got.content == b""


def test_active_returns_the_decrypted_settings(client, session):
    session.add(MailProvider(provider="smtp2go", label="SMTP2GO", fields=FIELDS, settings=SETTINGS, is_active=True))
    session.commit()
    got = client.get(f"{MAIL_URL}/active", headers=HEADERS)
    assert got.status_code == 200
    assert got.json() == {"provider": "smtp2go", "settings": SETTINGS}


def test_active_with_no_saved_settings_returns_an_empty_dict(client, session):
    session.add(MailProvider(provider="smtp2go", label="SMTP2GO", fields=FIELDS, is_active=True))
    session.commit()
    assert client.get(f"{MAIL_URL}/active", headers=HEADERS).json() == {"provider": "smtp2go", "settings": {}}


# --- outbox ---


def _claim(client, limit=10):
    return client.post(f"{MAIL_URL}/outbox/claim", params={"limit": limit}, headers=HEADERS)


def test_claim_returns_decrypted_bodies_and_a_second_claim_returns_none(client, session):
    row_id = _outbox_row(session)
    got = _claim(client)
    assert got.status_code == 200
    assert got.json() == [{"id": row_id, "to_address": "a@example.com", "subject": "Reset", "body": BODY}]
    assert _reload(session, EmailOutbox, row_id).status is OutboxStatus.SENDING
    raw = session.execute(sa.text("SELECT body FROM email_outbox")).scalar()
    assert "dummy-token-value" not in raw
    assert _claim(client).json() == []


@pytest.mark.parametrize("limit", [0, 51, -1])
def test_claim_limit_outside_1_to_50_is_422(client, limit):
    assert _claim(client, limit).status_code == 422


def test_claim_without_a_limit_is_422(client):
    assert client.post(f"{MAIL_URL}/outbox/claim", headers=HEADERS).status_code == 422


def test_claim_honours_the_limit(client, session):
    for _ in range(3):
        _outbox_row(session)
    assert len(_claim(client, 2).json()) == 2


def test_patch_sent_clears_the_body(client, session):
    row_id = _outbox_row(session)
    _claim(client)
    got = client.patch(f"{MAIL_URL}/outbox/{row_id}", json={"outcome": "sent"}, headers=HEADERS)
    assert got.status_code == 204
    row = _reload(session, EmailOutbox, row_id)
    assert row.status is OutboxStatus.SENT
    assert row.body == ""


def test_patch_failed_schedules_a_retry(client, session):
    row_id = _outbox_row(session)
    _claim(client)
    got = client.patch(f"{MAIL_URL}/outbox/{row_id}", json={"outcome": "failed", "error": "smtp 451"}, headers=HEADERS)
    assert got.status_code == 204
    row = _reload(session, EmailOutbox, row_id)
    assert row.status is OutboxStatus.PENDING
    assert row.last_error == "smtp 451"


def test_patch_a_row_that_is_not_sending_is_409(client, session):
    row_id = _outbox_row(session)
    got = client.patch(f"{MAIL_URL}/outbox/{row_id}", json={"outcome": "sent"}, headers=HEADERS)
    assert got.status_code == 409
    assert got.json()["code"] == "not_sending"
    assert _reload(session, EmailOutbox, row_id).body == BODY


def test_patch_an_unknown_row_is_404(client):
    got = client.patch(f"{MAIL_URL}/outbox/9999", json={"outcome": "sent"}, headers=HEADERS)
    assert got.status_code == 404
    assert got.json()["code"] == "not_found"


@pytest.mark.parametrize("body", [{"outcome": "bounced"}, {"outcome": "sent", "extra": 1}, {}])
def test_patch_bad_body_is_422(client, session, body):
    row_id = _outbox_row(session)
    _claim(client)
    assert client.patch(f"{MAIL_URL}/outbox/{row_id}", json=body, headers=HEADERS).status_code == 422


# --- auth ---


def _mail_routes(app) -> list[tuple[str, str]]:
    """Every (method, path) on the mail actor router, read from the app so a
    route added later is covered without editing this test."""
    routes = []
    for route in iter_route_contexts(app.routes):
        if route.path and route.methods and route.path.startswith(MAIL_URL):
            routes.extend((method, route.path) for method in sorted(route.methods))
    return routes


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Actor-Key": "wrong-key", "X-Actor-Provider": "mail"},
        {"X-Actor-Key": ACTOR_KEY, "X-Actor-Provider": "mail"},
        {"X-Actor-Key": MAIL_ACTOR_KEY, "X-Actor-Provider": "smtp2go"},
        {"X-Actor-Key": MAIL_ACTOR_KEY},
        {"X-Actor-Provider": "mail"},
        {},
    ],
    ids=["wrong-key", "device-actor-key", "wrong-provider", "missing-provider", "missing-key", "no-headers"],
)
def test_every_route_refuses_a_bad_actor(client, app_and_db_url, headers):
    app, _ = app_and_db_url
    routes = _mail_routes(app)
    assert {"GET", "PATCH", "PUT", "POST"} <= {method for method, _ in routes}
    for method, path in routes:
        url = path.replace("{id}", "1")
        assert not re.search(r"\{\w+\}", url), f"unfilled path parameter in {path}"
        got = client.request(method, url, headers=headers)
        assert got.status_code == 401, (method, path)
        body = got.json()
        assert body["code"] == "invalid_actor_key"
        assert "provider" not in body["message"].lower() and "header" not in body["message"].lower()


def test_the_mail_key_cannot_list_device_actor_instances(client):
    url = "/api/v1/actor/instances?provider=actron_neo"
    device = client.get(url, headers={"X-Actor-Key": ACTOR_KEY, "X-Actor-Provider": "actron_neo"})
    assert device.status_code == 200
    got = client.get(url, headers={"X-Actor-Key": MAIL_ACTOR_KEY, "X-Actor-Provider": "actron_neo"})
    assert (got.status_code, got.json()["code"]) == (401, "invalid_actor_key")
