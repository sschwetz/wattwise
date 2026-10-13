from __future__ import annotations

import secrets
from datetime import datetime, timezone

from core_api.db import make_engine, make_session_factory
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
        effective_from=datetime.now(timezone.utc),
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
        effective_from=datetime.now(timezone.utc),
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
