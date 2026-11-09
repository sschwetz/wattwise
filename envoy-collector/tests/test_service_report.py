from __future__ import annotations

import json
import time

import httpx
import pytest
import respx

from envoy_collector.service_report import report_commit_once, start_commit_reporter

BASE_URL = "http://core-api:8000"
URL = f"{BASE_URL}/api/v1/services/report"


@respx.mock
def test_report_sends_header_and_body_and_returns_true_on_204() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(204))
    assert report_commit_once(BASE_URL, "X-Collector-Key", "k", "envoy-collector", "abc1234") is True
    request = route.calls.last.request
    assert request.headers["X-Collector-Key"] == "k"
    assert json.loads(request.content) == {"service": "envoy-collector", "commit": "abc1234"}


@pytest.mark.parametrize("status", [401, 500])
@respx.mock
def test_report_returns_false_on_error_status(status: int) -> None:
    respx.post(URL).mock(return_value=httpx.Response(status))
    assert report_commit_once(BASE_URL, "X-Collector-Key", "k", "envoy-collector", "abc1234") is False


@respx.mock
def test_report_returns_false_on_connection_error() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    assert report_commit_once(BASE_URL, "X-Collector-Key", "k", "envoy-collector", "abc1234") is False


def _wait_for_calls(route: respx.Route, count: int) -> None:
    deadline = time.monotonic() + 2
    while route.call_count < count and time.monotonic() < deadline:
        time.sleep(0.01)


@respx.mock
def test_reporter_posts_repeatedly_from_a_daemon_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_COMMIT", "0123456789abcdef")
    route = respx.post(URL).mock(return_value=httpx.Response(204))
    thread = start_commit_reporter(BASE_URL, "X-Collector-Key", "k", "envoy-collector", interval_seconds=0.01)
    _wait_for_calls(route, 2)
    assert thread.daemon is True
    assert route.call_count >= 2
    assert json.loads(route.calls[0].request.content)["commit"] == "0123456"


@respx.mock
def test_reporter_sends_unknown_when_git_commit_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    route = respx.post(URL).mock(return_value=httpx.Response(204))
    start_commit_reporter(BASE_URL, "X-Collector-Key", "k", "envoy-collector", interval_seconds=0.01)
    _wait_for_calls(route, 1)
    assert json.loads(route.calls[0].request.content)["commit"] == "unknown"
