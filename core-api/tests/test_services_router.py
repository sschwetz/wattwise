from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core_api.deps import get_db_session
from core_api.errors import install_error_handlers
from core_api.models.service_status import ServiceStatus
from core_api.routers.services import router
from core_api.security import create_access_token
from tests.conftest import ENVOY_COLLECTOR_KEY, PRICING_COLLECTOR_KEY

REPORT = "/api/v1/services/report"


@pytest.fixture()
def client(db):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    return TestClient(app)


def _body(service="local-pricing-collector", commit="abc1234"):
    return {"service": service, "commit": commit}


def test_report_stores_row(client, db):
    r = client.post(REPORT, json=_body(), headers={"X-Collector-Key": PRICING_COLLECTOR_KEY})
    assert r.status_code == 204
    row = db.get(ServiceStatus, "local-pricing-collector")
    assert row is not None and row.commit == "abc1234"


def test_report_accepts_actor_key(client):
    r = client.post(REPORT, json=_body("actron-neo-actor"), headers={"X-Actor-Key": "test-actor-key"})
    assert r.status_code == 204


def test_report_other_service_forbidden(client):
    r = client.post(REPORT, json=_body("actron-neo-actor"), headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    assert r.status_code == 403
    assert r.json()["code"] == "service_not_allowed"


def test_report_unknown_service_forbidden(client):
    r = client.post(REPORT, json=_body("nope"), headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    assert r.status_code == 403


@pytest.mark.parametrize("headers", [{}, {"X-Collector-Key": "wrong"}])
def test_report_bad_key(client, headers):
    r = client.post(REPORT, json=_body(), headers=headers)
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_service_key"


@pytest.mark.parametrize("commit", ["zzz", ""])
def test_report_bad_commit(client, commit):
    r = client.post(REPORT, json=_body(commit=commit), headers={"X-Collector-Key": PRICING_COLLECTOR_KEY})
    assert r.status_code == 422


def test_list_requires_user(client):
    assert client.get("/api/v1/services").status_code == 401


def test_list_returns_seven_rows(client, user):
    token = create_access_token(user.id)
    r = client.get("/api/v1/services", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 7
    assert set(rows[0]) == {"service", "commit", "last_reported_at", "reporting"}
