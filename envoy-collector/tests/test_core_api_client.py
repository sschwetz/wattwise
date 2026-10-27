from __future__ import annotations

import datetime
import json

import httpx
import pytest
import respx

from envoy_collector.core_api_client import (
    CoreApiError,
    fetch_collector_sources,
    fetch_source_config,
    push_energy_readings,
    push_live_sample,
    report_discovered_config,
)

BASE_URL = "http://localhost:8000"


@respx.mock
def test_fetch_source_config_sends_collector_key_header():
    route = respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(
        return_value=httpx.Response(200, json={"config": {"host": "envoy.local", "token": "abc"}})
    )
    result = fetch_source_config(BASE_URL, 1, "my-key")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    assert result == {"host": "envoy.local", "token": "abc"}


@respx.mock
def test_fetch_source_config_raises_on_non_200():
    respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(return_value=httpx.Response(401))
    with pytest.raises(CoreApiError):
        fetch_source_config(BASE_URL, 1, "wrong-key")


@respx.mock
def test_report_discovered_config_sends_serial_num():
    route = respx.patch(f"{BASE_URL}/api/v1/sources/1/discovered-config").mock(
        return_value=httpx.Response(204)
    )
    report_discovered_config(BASE_URL, 1, "my-key", serial_num="123456789012")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    import json as _json

    assert _json.loads(route.calls[0].request.content) == {"serial_num": "123456789012"}


@respx.mock
def test_report_discovered_config_sends_cert_fingerprint():
    route = respx.patch(f"{BASE_URL}/api/v1/sources/1/discovered-config").mock(
        return_value=httpx.Response(204)
    )
    report_discovered_config(BASE_URL, 1, "my-key", cert_fingerprint="aa:bb:cc")
    import json as _json

    assert _json.loads(route.calls[0].request.content) == {"cert_fingerprint": "aa:bb:cc"}


@respx.mock
def test_report_discovered_config_can_send_both_fields_at_once():
    route = respx.patch(f"{BASE_URL}/api/v1/sources/1/discovered-config").mock(
        return_value=httpx.Response(204)
    )
    report_discovered_config(BASE_URL, 1, "my-key", serial_num="123456789012", cert_fingerprint="aa:bb:cc")
    import json as _json

    assert _json.loads(route.calls[0].request.content) == {
        "serial_num": "123456789012",
        "cert_fingerprint": "aa:bb:cc",
    }


@respx.mock
def test_report_discovered_config_raises_on_non_204():
    respx.patch(f"{BASE_URL}/api/v1/sources/1/discovered-config").mock(
        return_value=httpx.Response(422)
    )
    with pytest.raises(CoreApiError):
        report_discovered_config(BASE_URL, 1, "my-key", serial_num="x")


@respx.mock
def test_push_energy_readings_sends_batch_as_iso_timestamps():
    route = respx.post(f"{BASE_URL}/api/v1/sources/1/readings/energy").mock(
        return_value=httpx.Response(202, json={"ingested": 1})
    )
    ts = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=datetime.UTC)
    push_energy_readings(BASE_URL, 1, "my-key", [{"ts": ts, "metric": "production_w", "value": 100.0}])

    import json as _json

    body = _json.loads(route.calls[0].request.content)
    assert body == {"readings": [{"ts": "2026-09-29T12:00:00+00:00", "metric": "production_w", "value": 100.0}]}


@respx.mock
def test_push_energy_readings_waits_longer_than_core_apis_lock_wait():
    # Core API waits up to 30s for a held SQLite write lock, so a shorter
    # client timeout abandons pushes the server is about to complete.
    route = respx.post(f"{BASE_URL}/api/v1/sources/1/readings/energy").mock(
        return_value=httpx.Response(202, json={"ingested": 0})
    )
    push_energy_readings(BASE_URL, 1, "my-key", [])

    assert route.calls[0].request.extensions["timeout"]["read"] > 30


@respx.mock
def test_push_energy_readings_raises_on_non_202():
    respx.post(f"{BASE_URL}/api/v1/sources/1/readings/energy").mock(return_value=httpx.Response(422))
    with pytest.raises(CoreApiError):
        push_energy_readings(BASE_URL, 1, "my-key", [])


@respx.mock
def test_push_energy_readings_error_carries_the_status_code():
    respx.post(f"{BASE_URL}/api/v1/sources/1/readings/energy").mock(
        return_value=httpx.Response(403, json={"code": "site_disabled"})
    )
    with pytest.raises(CoreApiError) as exc_info:
        push_energy_readings(BASE_URL, 1, "my-key", [])
    assert exc_info.value.status_code == 403


@respx.mock
def test_transport_failure_has_no_status_code():
    respx.post(f"{BASE_URL}/api/v1/sources/1/readings/energy").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError) as exc_info:
        push_energy_readings(BASE_URL, 1, "my-key", [])
    assert exc_info.value.status_code is None


@respx.mock
def test_fetch_collector_sources_sends_kind_and_collector_key():
    route = respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(
        return_value=httpx.Response(
            200, json=[{"source_id": 1, "site_id": 10, "config": {"host": "envoy.local"}}]
        )
    )
    result = fetch_collector_sources(BASE_URL, "my-key")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    assert route.calls[0].request.url.params["kind"] == "energy"
    assert result == [{"source_id": 1, "site_id": 10, "config": {"host": "envoy.local"}}]


@respx.mock
def test_fetch_collector_sources_raises_on_non_200():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(return_value=httpx.Response(401))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "wrong-key")


@respx.mock
def test_fetch_collector_sources_raises_core_api_error_on_connection_failure():
    respx.get(f"{BASE_URL}/api/v1/collector/sources").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        fetch_collector_sources(BASE_URL, "my-key")


@respx.mock
def test_fetch_source_config_raises_core_api_error_on_connection_failure():
    respx.get(f"{BASE_URL}/api/v1/sources/1/config").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        fetch_source_config(BASE_URL, 1, "my-key")


@respx.mock
def test_report_discovered_config_raises_core_api_error_on_connection_failure():
    respx.patch(f"{BASE_URL}/api/v1/sources/1/discovered-config").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        report_discovered_config(BASE_URL, 1, "my-key", serial_num="x")


@respx.mock
def test_push_energy_readings_raises_core_api_error_on_connection_failure():
    respx.post(f"{BASE_URL}/api/v1/sources/1/readings/energy").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(CoreApiError):
        push_energy_readings(BASE_URL, 1, "my-key", [])


@respx.mock
def test_report_discovered_config_sends_token():
    route = respx.patch(f"{BASE_URL}/api/v1/sources/1/discovered-config").mock(return_value=httpx.Response(204))
    report_discovered_config(BASE_URL, 1, "my-key", token="new-token")
    assert route.calls[0].request.headers["X-Collector-Key"] == "my-key"
    assert json.loads(route.calls[0].request.content) == {"token": "new-token"}


@respx.mock
def test_report_discovered_config_sends_cert_expires_at():
    route = respx.patch(f"{BASE_URL}/api/v1/sources/1/discovered-config").mock(
        return_value=httpx.Response(204)
    )
    report_discovered_config(BASE_URL, 1, "my-key", cert_expires_at="2027-01-01T00:00:00+00:00")
    import json as _json

    assert _json.loads(route.calls[0].request.content) == {"cert_expires_at": "2027-01-01T00:00:00+00:00"}


@respx.mock
def test_report_discovered_config_sends_cert_serial():
    route = respx.patch(f"{BASE_URL}/api/v1/sources/1/discovered-config").mock(
        return_value=httpx.Response(204)
    )
    report_discovered_config(BASE_URL, 1, "my-key", cert_serial="322103FC")
    import json as _json

    assert _json.loads(route.calls[0].request.content) == {"cert_serial": "322103FC"}


@respx.mock
def test_push_live_sample_posts_values_with_key_and_short_timeout():
    route = respx.post(f"{BASE_URL}/api/v1/sources/1/live/energy").mock(return_value=httpx.Response(202))
    ts = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=datetime.UTC)
    push_live_sample(BASE_URL, 1, "my-key", ts, {"production_w": 100.0})

    request = route.calls[0].request
    assert request.headers["X-Collector-Key"] == "my-key"
    assert json.loads(request.content) == {"ts": "2026-09-29T12:00:00+00:00", "values": {"production_w": 100.0}}
    assert request.extensions["timeout"]["read"] == 2


@respx.mock
def test_push_live_sample_raises_on_non_202():
    respx.post(f"{BASE_URL}/api/v1/sources/1/live/energy").mock(return_value=httpx.Response(500))
    with pytest.raises(CoreApiError):
        push_live_sample(BASE_URL, 1, "my-key", datetime.datetime.now(datetime.UTC), {})
