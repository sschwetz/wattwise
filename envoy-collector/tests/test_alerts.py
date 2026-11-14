from __future__ import annotations

import pytest

from envoy_collector import alerts
from envoy_collector.config import Settings
from envoy_collector.core_api_client import CoreApiError

SETTINGS = Settings(
    core_api_url="http://localhost:8000", collector_key="key", poll_interval_seconds=60, discovery_refresh_seconds=300
)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    calls: list[tuple] = []

    def _set(url, sid, key, code, message, severity="failure", sticky=False):
        calls.append((code, message, severity, sticky))

    monkeypatch.setattr(alerts, "set_source_alert", _set)
    alerts.reset()
    return calls


def test_raise_alert_resends_when_severity_changes(sent):
    alerts.raise_alert(SETTINGS, 1, "meters", "msg", severity="partial")
    alerts.raise_alert(SETTINGS, 1, "meters", "msg", severity="partial")
    alerts.raise_alert(SETTINGS, 1, "meters", "msg", severity="failure")
    assert [c[2] for c in sent] == ["partial", "failure"]


def test_sticky_notice_is_not_deduplicated(sent):
    alerts.raise_alert(SETTINGS, 1, "repinned", "msg", severity="notice", sticky=True)
    alerts.raise_alert(SETTINGS, 1, "repinned", "msg", severity="notice", sticky=True)
    assert len(sent) == 2


def test_sticky_notice_is_kept_and_retried_when_core_api_is_down(monkeypatch, sent):
    outcomes = [CoreApiError("down")]

    def _set(url, sid, key, code, message, severity="failure", sticky=False):
        if outcomes:
            raise outcomes.pop(0)
        sent.append((code, message, severity, sticky))

    monkeypatch.setattr(alerts, "set_source_alert", _set)
    alerts.raise_alert(SETTINGS, 1, "repinned", "msg", severity="notice", sticky=True)
    assert sent == []
    alerts.retry_pending(SETTINGS)
    assert sent == [("repinned", "msg", "notice", True)]
    alerts.retry_pending(SETTINGS)
    assert len(sent) == 1


def test_non_sticky_alert_is_not_kept_for_retry(monkeypatch, sent):
    def _fail(*args, **kwargs):
        raise CoreApiError("down")

    monkeypatch.setattr(alerts, "set_source_alert", _fail)
    alerts.raise_alert(SETTINGS, 1, "meters", "msg")
    monkeypatch.setattr(alerts, "set_source_alert", lambda *a, **k: sent.append(a))
    alerts.retry_pending(SETTINGS)
    assert sent == []
