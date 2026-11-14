from __future__ import annotations

import httpx
import respx

from local_pricing_collector import alerts
from local_pricing_collector.config import Settings
from local_pricing_collector.core_api_client import set_source_alert

BASE_URL = "http://localhost:8000"


@respx.mock
def test_raise_alert_sends_severity_and_sticky():
    route = respx.put(f"{BASE_URL}/api/v1/sources/1/alerts/pricing").mock(return_value=httpx.Response(204))
    set_source_alert(BASE_URL, 1, "key", "pricing", "msg", severity="partial", sticky=True)
    assert route.calls[0].request.read() == b'{"message":"msg","severity":"partial","sticky":true}'


def test_raise_alert_resends_when_severity_changes(alert_calls):
    settings = Settings(core_api_url=BASE_URL, collector_key="key")
    alerts.raise_alert(settings, 1, "pricing", "msg", severity="partial")
    alerts.raise_alert(settings, 1, "pricing", "msg", severity="partial")
    alerts.raise_alert(settings, 1, "pricing", "msg", severity="failure")
    assert [c[3] for c in alert_calls] == ["partial", "failure"]
