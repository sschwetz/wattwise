"""The actor API responses match the shared fixtures in contracts/actor-api (see its README).

Only keys and JSON value types are compared, not values. The actor's client
tests parse the same files."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core_api.actor_context import ActorContext, WindowOccurrence
from core_api.actor_schemas import Capabilities
from core_api.deps import get_current_user, get_db_session, get_email_sender
from core_api.errors import install_error_handlers
from core_api.models import ActorInstance
from core_api.routers import actor as actor_module
from core_api.routers import actors as owner_routes
from tests.conftest import ACTOR_KEY, UTC

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts" / "actor-api"
HEADERS = {"X-Actor-Key": ACTOR_KEY, "X-Actor-Provider": "actron_neo"}


def fixture(name: str):
    return json.loads((CONTRACTS / name).read_text())


def _kind(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    return {str: "string", dict: "object", list: "array", type(None): "null"}[type(value)]


def assert_same_shape(actual: object, expected: object, where: str = "$") -> None:
    assert _kind(actual) == _kind(expected), f"{where}: {_kind(actual)} is not {_kind(expected)}"
    if isinstance(expected, dict):
        assert set(actual) == set(expected), f"{where}: keys {sorted(actual)} are not {sorted(expected)}"
        for key in expected:
            assert_same_shape(actual[key], expected[key], f"{where}.{key}")
    elif isinstance(expected, list) and expected:
        pairs = zip(actual, expected, strict=True) if len(actual) == len(expected) else ((a, expected[0]) for a in actual)
        for i, (a, e) in enumerate(pairs):
            assert_same_shape(a, e, f"{where}[{i}]")


@pytest.fixture()
def http(db, user, fake_email_sender):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(actor_module.router, prefix="/api/v1")
    app.include_router(owner_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_email_sender] = lambda: fake_email_sender
    return TestClient(app)


@pytest.fixture()
def inst(db, site, http) -> ActorInstance:
    instance = ActorInstance(site_id=site.id, provider="actron_neo", name="Lounge AC",
                             config={"refresh_token": "placeholder-token"})
    db.add(instance)
    db.commit()
    r = http.put(_url(instance, "/capabilities"), headers=HEADERS, json=fixture("capabilities_request.json"))
    assert r.status_code == 204
    return instance


def _url(instance: ActorInstance, suffix: str) -> str:
    return f"/api/v1/actor/instances/{instance.id}{suffix}"


def _schedule_id(http, site, inst) -> int:
    body = {k: v for k, v in fixture("schedules_list_entry.json").items() if k not in ("id", "created_at")}
    r = http.post(f"/api/v1/sites/{site.id}/actors/schedules", json={**body, "instance_id": inst.id})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_capabilities_request_is_stored_with_defaults_filled_in(http, db, site, inst):
    [listed] = http.get(f"/api/v1/sites/{site.id}/actors/instances").json()
    expected = Capabilities.model_validate(fixture("capabilities_request.json")).model_dump(mode="json")
    assert listed["capabilities"] == expected


def test_readings_request_is_accepted(http, db, inst):
    body = fixture("readings_request.json")
    assert http.put(_url(inst, "/readings"), headers=HEADERS, json=body).status_code == 204
    db.refresh(inst)
    assert inst.latest_readings == body["state"]


def test_instances_list_entry(http, inst):
    r = http.get("/api/v1/actor/instances", params={"provider": "actron_neo"}, headers=HEADERS)
    [entry] = r.json()
    assert_same_shape(entry, fixture("instances_list_entry.json"))


def test_schedules_list_entry(http, site, inst):
    _schedule_id(http, site, inst)
    [entry] = http.get(_url(inst, "/schedules"), headers=HEADERS).json()
    assert_same_shape(entry, fixture("schedules_list_entry.json"))


def test_context(http, inst, monkeypatch):
    now = datetime.datetime(2026, 11, 5, 4, 0, tzinfo=UTC)
    window = WindowOccurrence(7, "Peak", now + datetime.timedelta(hours=1), now + datetime.timedelta(hours=5))
    context = ActorContext(now, "Australia/Sydney", 31.5, 64.0, 42.1, [window])
    monkeypatch.setattr(actor_module, "build_context", lambda _db, _site, _now: context)
    body = http.get(_url(inst, "/context"), headers=HEADERS).json()
    assert_same_shape(body, fixture("context.json"))


def _create_run(http, inst, schedule_id: int, kind: str, **extra) -> dict:
    payload = {"schedule_id": schedule_id, "kind": kind, "trigger_at": "2026-11-05T05:00:00+00:00", **extra}
    r = http.post(_url(inst, "/runs"), headers=HEADERS, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_run_created(http, site, inst):
    snapshot = fixture("run_created.json")["snapshot"]
    created = _create_run(http, inst, _schedule_id(http, site, inst), "action", snapshot=snapshot)
    assert_same_shape(created, fixture("run_created.json"))


def test_run_list_item(http, site, inst):
    schedule_id = _schedule_id(http, site, inst)
    action = _create_run(http, inst, schedule_id, "action", snapshot={"is_on": True})
    restore = _create_run(http, inst, schedule_id, "restore", parent_run_id=action["id"])
    for outcome in ("running", "ok"):
        update = {"outcome": outcome, "message": "restored power", "attempts": 1}
        assert http.patch(_url(inst, f"/runs/{restore['id']}"), headers=HEADERS, json=update).status_code == 200
    runs = http.get(_url(inst, "/runs"), headers=HEADERS, params={"since": "2026-11-05T00:00:00+00:00"}).json()
    [listed] = [r for r in runs if r["kind"] == "restore"]
    assert_same_shape(listed, fixture("run_list_item.json"))
