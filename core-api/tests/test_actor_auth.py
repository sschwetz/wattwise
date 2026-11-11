from __future__ import annotations

import datetime

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from core_api.deps import get_actor_instance, get_db_session
from core_api.errors import install_error_handlers
from core_api.models import ActorInstance, ActorRun, ActorSchedule, RunKind, RunOutcome
from tests.conftest import ACTOR_KEY

PROVIDER = "actron_neo"


@pytest.fixture()
def instance(db, site) -> ActorInstance:
    i = ActorInstance(site_id=site.id, provider=PROVIDER, config={})
    db.add(i)
    db.commit()
    return i


@pytest.fixture()
def http(db):
    app = FastAPI()
    install_error_handlers(app)
    app.dependency_overrides[get_db_session] = lambda: db

    @app.get("/probe/{instance_id}")
    def probe(inst: ActorInstance = Depends(get_actor_instance)) -> dict:
        return {"id": inst.id}

    return TestClient(app)


def _get(http, instance_id, key=ACTOR_KEY, provider=PROVIDER):
    headers = {"X-Actor-Key": key.encode("utf-8"), "X-Actor-Provider": provider}
    return http.get(f"/probe/{instance_id}", headers=headers)


def test_valid_credentials_pass(http, instance):
    assert _get(http, instance.id).json() == {"id": instance.id}


def test_wrong_key_401(http, instance):
    r = _get(http, instance.id, key="nope")
    assert (r.status_code, r.json()["code"]) == (401, "invalid_actor_key")


def test_unknown_instance_401(http):
    assert _get(http, 999).status_code == 401


def test_provider_mismatch_401(http, instance):
    r = _get(http, instance.id, provider="other")
    assert (r.status_code, r.json()["code"]) == (401, "invalid_actor_key")


def test_non_ascii_key_401(http, instance):
    assert _get(http, instance.id, key="kéy").status_code == 401


def test_disabled_instance_403(http, db, instance):
    instance.enabled = False
    db.commit()
    r = _get(http, instance.id)
    assert (r.status_code, r.json()["code"]) == (403, "instance_disabled")


def test_disabled_site_403(http, db, instance, site):
    site.enabled = False
    db.commit()
    r = _get(http, instance.id)
    assert (r.status_code, r.json()["code"]) == (403, "site_disabled")


def test_disabled_instance_with_unfinished_action_run_allowed(http, db, instance, site):
    s = ActorSchedule(instance_id=instance.id, name="s", trigger={}, conditions=[], actions=[{}], restore={}, retry={})
    db.add(s)
    db.flush()
    db.add(ActorRun(schedule_id=s.id, kind=RunKind.ACTION, outcome=RunOutcome.OK,
                    trigger_at=datetime.datetime(2026, 11, 5, tzinfo=datetime.UTC)))
    instance.enabled = False
    site.enabled = False
    db.commit()
    assert _get(http, instance.id).status_code == 200
