from __future__ import annotations

import datetime
import secrets

import jwt
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core_api.db import make_engine, make_session_factory
from core_api.live import LIVE_MAX_AGE_SECONDS, LiveHub
from core_api.models.identity import Site, SiteAccess, User
from core_api.models.sources import Source, SourceKind
from core_api.routers import live as live_router
from core_api.security import create_access_token
from tests.conftest import ENVOY_COLLECTOR_KEY
from tests.test_ingestion import make_energy_source_full

TS = "2026-01-01T10:00:00Z"


@pytest.fixture()
def live_client(app_and_db_url):
    app, db_url = app_and_db_url
    # One portal for the whole test, so pushes and the socket share an event loop.
    with TestClient(app) as client:
        yield client, db_url


def push(client: TestClient, source_id: int, watts: float, ts: str = TS) -> None:
    response = client.post(
        f"/api/v1/sources/{source_id}/live/energy",
        json={"ts": ts, "values": {"production_w": watts}},
        headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY},
    )
    assert response.status_code == 202, response.text


def make_user(db_url: str) -> int:
    session = make_session_factory(make_engine(db_url))()
    user = User(email=f"live-{secrets.token_hex(4)}@example.com", password_hash="x")
    session.add(user)
    session.commit()
    user_id = user.id
    session.close()
    return user_id


def grant_access(db_url: str, site_id: int, user_id: int) -> None:
    session = make_session_factory(make_engine(db_url))()
    session.add(SiteAccess(site_id=site_id, user_id=user_id))
    session.commit()
    session.close()


def add_site_with_source(db_url: str, owner_id: int) -> tuple[int, int]:
    session = make_session_factory(make_engine(db_url))()
    site = Site(owner_user_id=owner_id, name="Other", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config={},
        effective_from=datetime.datetime.now(datetime.timezone.utc),
    )
    session.add(source)
    session.commit()
    ids = site.id, source.id
    session.close()
    return ids


def expect_policy_close(client: TestClient, site_id: int, first_message: str | None) -> None:
    with client.websocket_connect(f"/api/v1/sites/{site_id}/live") as ws:
        if first_message is not None:
            ws.send_text(first_message)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 1008


def test_authorised_socket_gets_current_then_new_values(live_client):
    client, db_url = live_client
    owner_id, site_id, source_id = make_energy_source_full(db_url)
    push(client, source_id, 1500.0)
    token = create_access_token(owner_id)
    with client.websocket_connect(f"/api/v1/sites/{site_id}/live") as ws:
        ws.send_json({"token": token})
        first = ws.receive_json()
        assert first["kind"] == "energy"
        assert first["values"] == {"production_w": 1500.0}
        assert datetime.datetime.fromisoformat(first["ts"]) == datetime.datetime(
            2026, 1, 1, 10, tzinfo=datetime.timezone.utc
        )
        push(client, source_id, 1600.0, ts="2026-01-01T10:00:05Z")
        msg = ws.receive_json()
        assert msg["values"] == {"production_w": 1600.0}
        assert datetime.datetime.fromisoformat(msg["ts"]).second == 5


def test_stale_sample_is_not_sent_to_new_socket(live_client):
    client, db_url = live_client
    owner_id, site_id, source_id = make_energy_source_full(db_url)
    now = [500.0]
    client.app.state.live_hub = LiveHub(clock=lambda: now[0])
    push(client, source_id, 1500.0)
    now[0] += LIVE_MAX_AGE_SECONDS + 1
    token = create_access_token(owner_id)
    with client.websocket_connect(f"/api/v1/sites/{site_id}/live") as ws:
        ws.send_json({"token": token})
        push(client, source_id, 1600.0, ts="2026-01-01T10:00:05Z")
        assert ws.receive_json()["values"] == {"production_w": 1600.0}


def test_other_sites_push_is_not_received(live_client):
    client, db_url = live_client
    owner_id, site_id, source_id = make_energy_source_full(db_url)
    _other_site, other_source = add_site_with_source(db_url, owner_id)
    with client.websocket_connect(f"/api/v1/sites/{site_id}/live") as ws:
        ws.send_json({"token": create_access_token(owner_id)})
        push(client, other_source, 111.0)
        push(client, source_id, 222.0)
        assert ws.receive_json()["values"] == {"production_w": 222.0}


def test_site_access_user_can_connect(live_client):
    client, db_url = live_client
    _owner_id, site_id, source_id = make_energy_source_full(db_url)
    viewer_id = make_user(db_url)
    grant_access(db_url, site_id, viewer_id)
    push(client, source_id, 5.0)
    with client.websocket_connect(f"/api/v1/sites/{site_id}/live") as ws:
        ws.send_json({"token": create_access_token(viewer_id)})
        assert ws.receive_json()["values"] == {"production_w": 5.0}


def test_missing_token_key_closes_1008(live_client):
    client, db_url = live_client
    _owner_id, site_id, _source_id = make_energy_source_full(db_url)
    expect_policy_close(client, site_id, '{"nope": 1}')


def test_non_json_closes_1008(live_client):
    client, db_url = live_client
    _owner_id, site_id, _source_id = make_energy_source_full(db_url)
    expect_policy_close(client, site_id, "not json")


def test_non_object_json_closes_1008(live_client):
    client, db_url = live_client
    _owner_id, site_id, _source_id = make_energy_source_full(db_url)
    expect_policy_close(client, site_id, "[1, 2]")


def test_undecodable_token_closes_1008(live_client):
    client, db_url = live_client
    _owner_id, site_id, _source_id = make_energy_source_full(db_url)
    expect_policy_close(client, site_id, '{"token": "garbage"}')


def test_expired_token_closes_1008(live_client):
    client, db_url = live_client
    owner_id, site_id, _source_id = make_energy_source_full(db_url)
    now = datetime.datetime.now(datetime.timezone.utc)
    expired = jwt.encode(
        {"sub": str(owner_id), "iat": now - datetime.timedelta(hours=2), "exp": now - datetime.timedelta(hours=1)},
        "test-secret",
        algorithm="HS256",
    )
    expect_policy_close(client, site_id, f'{{"token": "{expired}"}}')


def close_reason(client: TestClient, site_id: int, token: str) -> tuple[int, str]:
    with client.websocket_connect(f"/api/v1/sites/{site_id}/live") as ws:
        ws.send_json({"token": token})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    return exc.value.code, exc.value.reason


def test_no_access_and_missing_site_close_identically(live_client):
    client, db_url = live_client
    _owner_id, site_id, _source_id = make_energy_source_full(db_url)
    stranger_token = create_access_token(make_user(db_url))
    no_access = close_reason(client, site_id, stranger_token)
    missing = close_reason(client, site_id + 1000, stranger_token)
    assert no_access[0] == 1008
    assert no_access == missing


def test_unauthenticated_socket_closed_after_timeout(live_client, monkeypatch):
    client, db_url = live_client
    _owner_id, site_id, _source_id = make_energy_source_full(db_url)
    monkeypatch.setattr(live_router, "LIVE_AUTH_TIMEOUT_SECONDS", 0.2)
    expect_policy_close(client, site_id, None)
