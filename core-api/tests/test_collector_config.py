from __future__ import annotations

import secrets
from datetime import UTC, datetime

from core_api.db import make_engine, make_session_factory
from core_api.models.alerts import Alert
from core_api.models.identity import Site, User
from core_api.models.sources import Source, SourceKind
from tests.conftest import ENVOY_COLLECTOR_KEY, PRICING_COLLECTOR_KEY, WEATHER_COLLECTOR_KEY


def make_energy_source(db_url: str, config: dict | None = None) -> int:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email=f"cc-owner-{secrets.token_hex(4)}@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.ENERGY,
        provider="envoy",
        config=config or {"host": "envoy.local", "token": "abc"},
        effective_from=datetime.now(UTC),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id


def test_get_source_config(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url, {"host": "envoy.local", "token": "abc"})
    response = client.get(
        f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    )
    assert response.status_code == 200, response.text
    assert response.json()["config"] == {"host": "envoy.local", "token": "abc"}


def test_get_source_config_wrong_key_rejected(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    response = client.get(
        f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": "wrong"}
    )
    assert response.status_code == 401


def test_get_source_config_rejects_a_different_kinds_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    response = client.get(
        f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": WEATHER_COLLECTOR_KEY}
    )
    assert response.status_code == 401


def test_report_discovered_config_merges_serial_num(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url, {"host": "envoy.local", "token": "abc"})
    patch = client.patch(
        f"/api/v1/sources/{source_id}/discovered-config",
        json={"serial_num": "123456789012"},
        headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY},
    )
    assert patch.status_code == 204, patch.text

    get = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    assert get.json()["config"] == {"host": "envoy.local", "token": "abc", "serial_num": "123456789012"}


def test_report_discovered_config_merges_cert_fingerprint(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url, {"host": "envoy.local", "token": "abc"})
    patch = client.patch(
        f"/api/v1/sources/{source_id}/discovered-config",
        json={"cert_fingerprint": "aa:bb:cc"},
        headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY},
    )
    assert patch.status_code == 204, patch.text

    get = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    assert get.json()["config"] == {"host": "envoy.local", "token": "abc", "cert_fingerprint": "aa:bb:cc"}


def test_report_discovered_config_can_merge_both_fields_at_once(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url, {"host": "envoy.local", "token": "abc"})
    patch = client.patch(
        f"/api/v1/sources/{source_id}/discovered-config",
        json={"serial_num": "123456789012", "cert_fingerprint": "aa:bb:cc"},
        headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY},
    )
    assert patch.status_code == 204, patch.text

    get = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    assert get.json()["config"] == {
        "host": "envoy.local",
        "token": "abc",
        "serial_num": "123456789012",
        "cert_fingerprint": "aa:bb:cc",
    }


def test_report_discovered_config_merges_token(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url, {"host": "envoy.local", "token": "old", "username": "u@example.com"})
    patch = client.patch(
        f"/api/v1/sources/{source_id}/discovered-config",
        json={"token": "new"},
        headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY},
    )
    assert patch.status_code == 204, patch.text

    get = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    assert get.json()["config"] == {"host": "envoy.local", "token": "new", "username": "u@example.com"}


def test_report_discovered_config_rejects_extra_fields(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url, {"host": "envoy.local", "token": "abc"})
    patch = client.patch(
        f"/api/v1/sources/{source_id}/discovered-config",
        json={"serial_num": "123456789012", "host": "evil.example.com"},
        headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY},
    )
    assert patch.status_code == 422

    get = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    assert get.json()["config"] == {"host": "envoy.local", "token": "abc"}


def make_pricing_source(db_url: str, amber_api_key: str | None, config: dict | None = None) -> int:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email=f"cc-amber-owner-{secrets.token_hex(4)}@example.com", password_hash="x", amber_api_key=amber_api_key)
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    source = Source(
        site_id=site.id,
        kind=SourceKind.PRICING,
        provider="amber",
        config=config or {"amber_site_id": "example-site-id"},
        effective_from=datetime.now(UTC),
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()
    return source_id


def test_get_source_config_merges_owner_amber_key_for_amber_provider(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url, amber_api_key="psk_owner_token")
    response = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": PRICING_COLLECTOR_KEY})
    assert response.status_code == 200, response.text
    assert response.json()["config"] == {"amber_site_id": "example-site-id", "amber_api_key": "psk_owner_token"}


def test_get_source_config_does_not_merge_key_for_non_amber_provider(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url, {"host": "envoy.local", "token": "abc"})
    response = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    assert response.status_code == 200, response.text
    assert response.json()["config"] == {"host": "envoy.local", "token": "abc"}
    assert "amber_api_key" not in response.json()["config"]


def test_get_source_config_merges_null_when_owner_has_no_amber_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_pricing_source(db_url, amber_api_key=None)
    response = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": PRICING_COLLECTOR_KEY})
    assert response.status_code == 200, response.text
    assert response.json()["config"]["amber_api_key"] is None


def test_report_discovered_config_merges_cert_expires_at_as_an_iso_string(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url, {"host": "envoy.local", "token": "abc"})
    patch = client.patch(
        f"/api/v1/sources/{source_id}/discovered-config",
        json={"cert_fingerprint": "aa:bb:cc", "cert_expires_at": "2027-03-01T10:00:00+10:00"},
        headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY},
    )
    assert patch.status_code == 204, patch.text

    get = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    config = get.json()["config"]
    assert config["cert_fingerprint"] == "aa:bb:cc"
    assert config["cert_expires_at"] == "2027-03-01T10:00:00+10:00"


def test_report_discovered_config_rejects_a_cert_expires_at_without_a_timezone(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url, {"host": "envoy.local", "token": "abc"})
    patch = client.patch(
        f"/api/v1/sources/{source_id}/discovered-config",
        json={"cert_expires_at": "2027-03-01T10:00:00"},
        headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY},
    )
    assert patch.status_code == 422


def test_report_discovered_config_merges_cert_serial(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url, {"host": "envoy.local", "token": "abc"})
    patch = client.patch(
        f"/api/v1/sources/{source_id}/discovered-config",
        json={"cert_serial": "322103FC7603C350"},
        headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY},
    )
    assert patch.status_code == 204, patch.text

    get = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    assert get.json()["config"]["cert_serial"] == "322103FC7603C350"


def _stored_alerts(db_url: str, source_id: int) -> dict[str, tuple[str, datetime]]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    rows = session.query(Alert).filter_by(subject_kind="source", subject_id=source_id).all()
    result = {row.code: (row.message, row.since) for row in rows}
    session.close()
    return result


def test_put_alert_sets_message_and_keeps_the_first_start_time(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    url = f"/api/v1/sources/{source_id}/alerts/cert"
    headers = {"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    first = client.put(url, json={"message": "cert changed"}, headers=headers)
    assert first.status_code == 204, first.text
    _, since = _stored_alerts(db_url, source_id)["cert"]
    second = client.put(url, json={"message": "still changed"}, headers=headers)
    assert second.status_code == 204, second.text
    message, since_after = _stored_alerts(db_url, source_id)["cert"]
    assert message == "still changed"
    assert since_after == since


def test_alerts_with_different_codes_coexist_and_clear_independently(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    headers = {"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    for code in ("cert", "token"):
        client.put(f"/api/v1/sources/{source_id}/alerts/{code}", json={"message": code}, headers=headers)
    response = client.delete(f"/api/v1/sources/{source_id}/alerts/cert", headers=headers)
    assert response.status_code == 204, response.text
    assert list(_stored_alerts(db_url, source_id)) == ["token"]
    assert client.delete(f"/api/v1/sources/{source_id}/alerts/cert", headers=headers).status_code == 204


def test_alert_rejects_a_wrong_collector_key_an_empty_message_and_a_bad_code(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    url = f"/api/v1/sources/{source_id}/alerts/cert"
    wrong = client.put(url, json={"message": "x"}, headers={"X-Collector-Key": "wrong"})
    assert wrong.status_code == 401
    headers = {"X-Collector-Key": ENVOY_COLLECTOR_KEY}
    assert client.put(url, json={"message": ""}, headers=headers).status_code == 422
    bad_code = client.put(f"/api/v1/sources/{source_id}/alerts/Bad-Code", json={"message": "x"}, headers=headers)
    assert bad_code.status_code == 422


def test_discovered_config_accepts_firmware(client, app_and_db_url):
    _, db_url = app_and_db_url
    source_id = make_energy_source(db_url)
    patch = client.patch(
        f"/api/v1/sources/{source_id}/discovered-config",
        json={"firmware": "D8.3.5226"},
        headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY},
    )
    assert patch.status_code == 204, patch.text
    get = client.get(f"/api/v1/sources/{source_id}/config", headers={"X-Collector-Key": ENVOY_COLLECTOR_KEY})
    assert get.json()["config"]["firmware"] == "D8.3.5226"
