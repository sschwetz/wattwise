from __future__ import annotations

import datetime

import httpx
import pytest

from core_api.config import get_settings
from core_api.jobs import service_status_job
from core_api.jobs.service_status_job import fetch_webapp_commit, run_service_status_job
from core_api.models.service_status import ServiceStatus
from core_api.service_status import record_report

NOW = datetime.datetime(2026, 11, 9, 1, 0, tzinfo=datetime.UTC)


def test_job_writes_core_api_row(db):
    run_service_status_job(db, NOW, fetch_version=lambda _url: None)
    row = db.get(ServiceStatus, "core-api")
    assert row is not None and row.commit == get_settings().git_commit


def test_job_writes_webapp_row(db):
    run_service_status_job(db, NOW, fetch_version=lambda _url: "abc1234")
    row = db.get(ServiceStatus, "webapp")
    assert row is not None and row.commit == "abc1234"


def test_job_skips_webapp_when_fetch_fails(db):
    record_report(db, "webapp", "1111111", NOW - datetime.timedelta(minutes=1))
    run_service_status_job(db, NOW, fetch_version=lambda _url: None)
    row = db.get(ServiceStatus, "webapp")
    assert row is not None and row.commit == "1111111"


def _patch_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(service_status_job, "_http_client", lambda: httpx.Client(transport=transport, timeout=5))


def test_fetch_returns_commit(monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"commit": "abc1234"})

    _patch_transport(monkeypatch, handler)
    assert fetch_webapp_commit("http://webapp") == "abc1234"
    assert seen == ["http://webapp/version"]


def _timeout(request):
    raise httpx.ConnectTimeout("slow", request=request)


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(500, json={"commit": "abc1234"}),
        lambda r: httpx.Response(200, content=b"not json"),
        lambda r: httpx.Response(200, json={}),
        lambda r: httpx.Response(200, json={"commit": "NOT HEX!"}),
        lambda r: httpx.Response(200, json={"commit": 5}),
        _timeout,
    ],
)
def test_fetch_returns_none_on_bad_response(monkeypatch, handler):
    _patch_transport(monkeypatch, handler)
    assert fetch_webapp_commit("http://webapp") is None
