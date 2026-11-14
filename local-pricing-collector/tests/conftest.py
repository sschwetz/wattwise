from __future__ import annotations

import pytest

from local_pricing_collector import alerts


@pytest.fixture(autouse=True)
def alert_calls(monkeypatch: pytest.MonkeyPatch) -> list:
    """Records the alerts sent to Core API instead of sending them."""
    calls: list = []
    monkeypatch.setattr(
        alerts,
        "set_source_alert",
        lambda url, sid, key, code, message, severity="failure", sticky=False: calls.append(
            ("set", code, message, severity, sticky)
        ),
    )
    monkeypatch.setattr(alerts, "clear_source_alert", lambda url, sid, key, code: calls.append(("clear", code)))
    alerts.reset()
    return calls
