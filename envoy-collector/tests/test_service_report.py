from __future__ import annotations

import json
import logging
import threading
import time

import httpx
import pytest
import respx

from envoy_collector.service_report import report_commit_once, start_commit_reporter

BASE_URL = "http://core-api:8000"
URL = f"{BASE_URL}/api/v1/services/report"


@respx.mock
def test_report_sends_header_and_body_and_returns_none_on_204() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(204))
    assert report_commit_once(BASE_URL, "X-Collector-Key", "k", "envoy-collector", "abc1234") is None
    request = route.calls.last.request
    assert request.headers["X-Collector-Key"] == "k"
    assert json.loads(request.content) == {"service": "envoy-collector", "commit": "abc1234"}


@pytest.mark.parametrize("status", [401, 500])
@respx.mock
def test_report_returns_reason_on_error_status(status: int) -> None:
    respx.post(URL).mock(return_value=httpx.Response(status))
    assert report_commit_once(BASE_URL, "X-Collector-Key", "k", "envoy-collector", "abc1234") is not None


@respx.mock
def test_report_returns_reason_on_connection_error() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    assert report_commit_once(BASE_URL, "X-Collector-Key", "k", "envoy-collector", "abc1234") is not None


def _wait_for_calls(route: respx.Route, count: int) -> None:
    deadline = time.monotonic() + 2
    while route.call_count < count and time.monotonic() < deadline:
        time.sleep(0.01)


@respx.mock
def test_reporter_posts_repeatedly_from_a_daemon_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_COMMIT", "0123456789abcdef")
    route = respx.post(URL).mock(return_value=httpx.Response(204))
    stop = threading.Event()
    thread = start_commit_reporter(BASE_URL, "X-Collector-Key", "k", "envoy-collector", 0.01, stop)
    _wait_for_calls(route, 2)
    stop.set()
    thread.join()
    assert thread.daemon is True
    assert route.call_count >= 2
    assert json.loads(route.calls[0].request.content)["commit"] == "0123456"


@respx.mock
def test_reporter_sends_unknown_when_git_commit_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    route = respx.post(URL).mock(return_value=httpx.Response(204))
    stop = threading.Event()
    thread = start_commit_reporter(BASE_URL, "X-Collector-Key", "k", "envoy-collector", 0.01, stop)
    _wait_for_calls(route, 1)
    stop.set()
    thread.join()
    assert json.loads(route.calls[0].request.content)["commit"] == "unknown"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0123456\n", "0123456"), (" ABCDEF1234", "abcdef1"), ("HEAD", "unknown"), ("abc", "unknown")],
)
@respx.mock
def test_reporter_normalises_or_rejects_git_commit(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str, expected: str
) -> None:
    monkeypatch.setenv("GIT_COMMIT", raw)
    route = respx.post(URL).mock(return_value=httpx.Response(204))
    stop = threading.Event()
    with caplog.at_level(logging.WARNING):
        thread = start_commit_reporter(BASE_URL, "X-Collector-Key", "k", "envoy-collector", 0.01, stop)
        _wait_for_calls(route, 1)
        stop.set()
        thread.join()
    assert json.loads(route.calls[0].request.content)["commit"] == expected
    assert ("is not a hex commit" in caplog.text) == (expected == "unknown")


@respx.mock
def test_reporter_warning_names_the_http_status(caplog: pytest.LogCaptureFixture) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(422))
    stop = threading.Event()
    with caplog.at_level(logging.WARNING):
        thread = start_commit_reporter(BASE_URL, "X-Collector-Key", "k", "envoy-collector", 0.01, stop)
        _wait_for_calls(route, 2)
        stop.set()
        thread.join()
    assert caplog.text.count("Could not report commit") == 1
    assert "HTTP 422" in caplog.text
