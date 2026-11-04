from __future__ import annotations

import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core_api.actor_context import ActorContext, WindowOccurrence
from core_api.deps import get_db_session
from core_api.errors import install_error_handlers
from core_api.models import ActorInstance, ActorRun, ActorSchedule, RunKind, RunOutcome
from core_api.models.identity import Site
from core_api.routers import actor as actor_module
from core_api.routers.actor import router
from tests.conftest import ACTOR_KEY, UTC

PROVIDER = "actron_neo"
HEADERS = {"X-Actor-Key": ACTOR_KEY, "X-Actor-Provider": PROVIDER}
CAPS = {"version": 1, "actions": [{"id": "set_mode", "label": "Set mode", "restorable": True}], "readings": []}


@pytest.fixture()
def http(db):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    return TestClient(app)


def _instance(db, site, provider=PROVIDER, **kw) -> ActorInstance:
    i = ActorInstance(site_id=site.id, provider=provider, config=kw.pop("config", {}), **kw)
    db.add(i)
    db.commit()
    return i


def _schedule(db, instance, name="s", enabled=True) -> ActorSchedule:
    s = ActorSchedule(instance_id=instance.id, name=name, enabled=enabled, trigger={"type": "time", "time": "10:00"},
                      conditions=[], action={"id": "set_mode"}, restore={"enabled": True}, retry={})
    db.add(s)
    db.commit()
    return s


def _open_run(db, schedule) -> None:
    db.add(ActorRun(schedule_id=schedule.id, kind=RunKind.ACTION, trigger_at=datetime.datetime.now(UTC),
                    outcome=RunOutcome.OK))
    db.commit()


def _discover(http, provider=PROVIDER, key=ACTOR_KEY):
    return http.get("/api/v1/actor/instances", params={"provider": provider}, headers={"X-Actor-Key": key})


def test_discovery_lists_only_matching_provider(http, db, site):
    mine = _instance(db, site, config={"refresh_token": "r"}, name="AC")
    _instance(db, site, provider="other")
    body = _discover(http).json()
    assert body == [{"instance_id": mine.id, "site_id": site.id, "name": "AC", "timezone": "Australia/Sydney",
                     "restore_only": False, "config": {"refresh_token": "r"}}]


def test_discovery_bad_key_401(http, db, site):
    r = _discover(http, key="nope")
    assert (r.status_code, r.json()["code"]) == (401, "invalid_actor_key")


def test_discovery_omits_disabled_site_and_instance(http, db, site, user):
    _instance(db, site, enabled=False)
    other = Site(owner_user_id=user.id, name="Off", timezone="Australia/Sydney", enabled=False)
    db.add(other)
    db.commit()
    _instance(db, other)
    assert _discover(http).json() == []


def test_discovery_keeps_restore_only_with_unfinished_run(http, db, site):
    i = _instance(db, site, enabled=False)
    _open_run(db, _schedule(db, i))
    site.enabled = False
    db.commit()
    body = _discover(http).json()
    assert [(e["instance_id"], e["restore_only"]) for e in body] == [(i.id, True)]


def test_discovered_config_merges_and_encrypts(http, db, site):
    i = _instance(db, site, config={"a": 1, "b": 2}, auth_state={"old": 1})
    r = http.patch(f"/api/v1/actor/instances/{i.id}/discovered-config", headers=HEADERS,
                   json={"config": {"b": 3, "c": 4}, "auth_state": {"token": "t"}})
    assert r.status_code == 204
    db.refresh(i)
    assert i.config == {"a": 1, "b": 3, "c": 4}
    assert i.auth_state == {"token": "t"}
    raw = db.connection().exec_driver_sql("select config from actor_instances").scalar()
    assert '"a"' not in str(raw)


def test_discovered_config_null_auth_state_clears(http, db, site):
    i = _instance(db, site, auth_state={"old": 1})
    http.patch(f"/api/v1/actor/instances/{i.id}/discovered-config", headers=HEADERS, json={"auth_state": None})
    db.refresh(i)
    assert i.auth_state is None


def test_capabilities_roundtrip_and_invalid_body_422(http, db, site):
    i = _instance(db, site)
    url = f"/api/v1/actor/instances/{i.id}/capabilities"
    assert http.put(url, headers=HEADERS, json=CAPS).status_code == 204
    db.refresh(i)
    assert i.capabilities["actions"][0]["id"] == "set_mode"
    assert i.capabilities_updated_at is not None
    assert http.put(url, headers=HEADERS, json={"version": 2}).status_code == 422


def test_schedules_exclude_disabled_unless_unfinished_run(http, db, site):
    i = _instance(db, site)
    on = _schedule(db, i, "on")
    _schedule(db, i, "off", enabled=False)
    held = _schedule(db, i, "held", enabled=False)
    _open_run(db, held)
    r = http.get(f"/api/v1/actor/instances/{i.id}/schedules", headers=HEADERS)
    assert [s["id"] for s in r.json()] == [on.id, held.id]
    assert r.json()[0]["action"] == {"id": "set_mode"}


def test_context_matches_build_context(http, db, site, monkeypatch):
    seen = {}
    ctx = ActorContext(now=datetime.datetime(2026, 1, 1, tzinfo=UTC), timezone="Australia/Sydney",
                       outdoor_temp_c=21.5, battery_soc_pct=None, price_c_per_kwh=30.0,
                       windows=[WindowOccurrence(7, "Peak", datetime.datetime(2026, 1, 1, 6),
                                                 datetime.datetime(2026, 1, 1, 8))])

    def fake(db_, site_, now):
        seen.update(site=site_.id, now=now)
        return ctx

    monkeypatch.setattr(actor_module, "build_context", fake)
    i = _instance(db, site)
    before = datetime.datetime.now(UTC)
    body = http.get(f"/api/v1/actor/instances/{i.id}/context", headers=HEADERS).json()
    assert seen["site"] == site.id
    assert before <= seen["now"] <= datetime.datetime.now(UTC)
    assert body["now"] == "2026-01-01T00:00:00Z"
    assert body["windows"] == [{"window_id": 7, "label": "Peak", "start": "2026-01-01T06:00:00Z",
                                "end": "2026-01-01T08:00:00Z"}]
    assert (body["outdoor_temp_c"], body["battery_soc_pct"], body["price_c_per_kwh"]) == (21.5, None, 30.0)


def test_readings_sets_last_seen(http, db, site):
    i = _instance(db, site)
    r = http.put(f"/api/v1/actor/instances/{i.id}/readings", headers=HEADERS, json={"state": {"temp": 21}})
    assert r.status_code == 204
    db.refresh(i)
    assert i.latest_readings == {"temp": 21}
    assert i.last_seen_at is not None


@pytest.mark.parametrize("method,suffix,body", [
    ("patch", "discovered-config", {"config": {}}),
    ("put", "capabilities", CAPS),
    ("get", "schedules", None),
    ("get", "context", None),
    ("put", "readings", {"state": {}}),
])
def test_provider_mismatch_401_on_every_route(http, db, site, method, suffix, body):
    i = _instance(db, site)
    headers = {**HEADERS, "X-Actor-Provider": "other"}
    r = http.request(method, f"/api/v1/actor/instances/{i.id}/{suffix}", headers=headers, json=body)
    assert (r.status_code, r.json()["code"]) == (401, "invalid_actor_key")


def test_discovered_config_without_auth_state_keeps_it(http, db, site):
    i = _instance(db, site, config={"a": 1}, auth_state={"keep": "me"})
    r = http.patch(f"/api/v1/actor/instances/{i.id}/discovered-config", headers=HEADERS, json={"config": {"b": 2}})
    assert r.status_code == 204
    db.refresh(i)
    assert i.auth_state == {"keep": "me"}
    assert i.config == {"a": 1, "b": 2}


_INSTANCE_ROUTES = [
    ("put", "readings", {"state": {"t": 1}}),
    ("put", "capabilities", CAPS),
    ("get", "schedules", None),
    ("get", "context", None),
]


@pytest.mark.parametrize("method,suffix,body", _INSTANCE_ROUTES)
def test_disabled_instance_with_unfinished_run_still_served(http, db, site, method, suffix, body):
    i = _instance(db, site, enabled=False)
    _open_run(db, _schedule(db, i))
    r = http.request(method, f"/api/v1/actor/instances/{i.id}/{suffix}", headers=HEADERS, json=body)
    assert r.status_code in (200, 204)


@pytest.mark.parametrize("method,suffix,body", _INSTANCE_ROUTES)
def test_disabled_instance_without_run_403(http, db, site, method, suffix, body):
    i = _instance(db, site, enabled=False)
    r = http.request(method, f"/api/v1/actor/instances/{i.id}/{suffix}", headers=HEADERS, json=body)
    assert (r.status_code, r.json()["code"]) == (403, "instance_disabled")
