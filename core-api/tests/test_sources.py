from __future__ import annotations

import datetime
import decimal

import sqlalchemy as sa

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, User
from core_api.models.local_rates import LocalRateWindow, LocalRepriceMarker
from core_api.models.pricing import PriceChannel, PriceReading
from core_api.security import create_access_token


def make_owner_and_site(db_url: str) -> tuple[int, int]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email="src-owner@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="UTC")
    session.add(site)
    session.commit()
    ids = (owner.id, site.id)
    session.close()
    return ids


def auth(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_create_source_does_not_return_a_collector_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    response = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {"host": "envoy.local"}},
        headers=auth(owner_id),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "collector_api_key" not in body
    assert body["kind"] == "energy"
    assert body["provider"] == "envoy"
    assert body["enabled"] is True


def test_create_source_accepts_an_optional_name(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    response = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {}, "name": "Garage Envoy"},
        headers=auth(owner_id),
    )
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Garage Envoy"


def test_create_source_without_name_defaults_to_null(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    response = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {}},
        headers=auth(owner_id),
    )
    assert response.status_code == 201, response.text
    assert response.json()["name"] is None


def test_patch_source_updates_name(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {}, "name": "Garage Envoy"},
        headers=auth(owner_id),
    ).json()["id"]

    patch = client.patch(
        f"/api/v1/sites/{site_id}/sources/{source_id}",
        json={"name": "Main House Envoy"},
        headers=auth(owner_id),
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["name"] == "Main House Envoy"


def test_delete_source_with_no_readings_succeeds(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {}},
        headers=auth(owner_id),
    ).json()["id"]

    response = client.delete(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert response.status_code == 204, response.text

    get = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert get.status_code == 404


def test_delete_source_with_readings_rejected(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {}},
        headers=auth(owner_id),
    ).json()["id"]

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.readings import EnergyMetric, EnergyReading

    session.add(
        EnergyReading(
            source_id=source_id,
            ts=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            metric=EnergyMetric.PRODUCTION_W,
            value=100.0,
        )
    )
    session.commit()
    session.close()

    response = client.delete(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert response.status_code == 409, response.text

    get = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert get.status_code == 200


def test_delete_nonexistent_source_returns_404(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    response = client.delete(f"/api/v1/sites/{site_id}/sources/999999", headers=auth(owner_id))
    assert response.status_code == 404


def test_second_weather_source_for_same_site_rejected(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    first = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "weather", "provider": "weatherlink_live", "config": {}},
        headers=auth(owner_id),
    )
    assert first.status_code == 201
    second = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "weather", "provider": "weatherlink_live", "config": {}},
        headers=auth(owner_id),
    )
    assert second.status_code == 409


def test_disable_source(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {}},
        headers=auth(owner_id),
    ).json()["id"]

    patch = client.patch(
        f"/api/v1/sites/{site_id}/sources/{source_id}", json={"enabled": False}, headers=auth(owner_id)
    )
    assert patch.status_code == 200
    assert patch.json()["enabled"] is False


def test_patch_config_clears_pinned_cert_fingerprint_when_host_changes(client, app_and_db_url):
    """envoy-collector pins a TLS certificate fingerprint to a source's host
    (trust-on-first-use) and refuses to talk to a host that doesn't match the
    pin. If the owner repoints a source at a different device (new local IP,
    replaced hardware), the stale pin for the OLD host must not survive the
    host change, or the collector will mismatch-reject the legitimate new
    device forever instead of re-pinning it."""
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={
            "kind": "energy",
            "provider": "envoy",
            "config": {"host": "old-envoy.local", "token": "abc", "cert_fingerprint": "old-pin", "serial_num": "111"},
        },
        headers=auth(owner_id),
    ).json()["id"]

    patch = client.patch(
        f"/api/v1/sites/{site_id}/sources/{source_id}",
        json={"config": {"host": "new-envoy.local"}},
        headers=auth(owner_id),
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["config"] == {"host": "new-envoy.local", "token": "abc"}


def _create_envoy_source(client, db_url, config):
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": config},
        headers=auth(owner_id),
    ).json()["id"]
    return owner_id, site_id, source_id


def test_patch_config_keeps_a_serial_num_entered_together_with_a_host_change(client, app_and_db_url):
    """The owner can type the serial number by hand (the collector may not be
    able to discover it yet). A new serial sent in the same save as a new host
    is the owner's value for the new device, not the stale one to clear."""
    _, db_url = app_and_db_url
    owner_id, site_id, source_id = _create_envoy_source(
        client, db_url, {"host": "old.local", "cert_fingerprint": "old-pin", "serial_num": "111"}
    )
    patch = client.patch(
        f"/api/v1/sites/{site_id}/sources/{source_id}",
        json={"config": {"host": "new.local", "serial_num": "222"}},
        headers=auth(owner_id),
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["config"] == {"host": "new.local", "serial_num": "222"}


def test_patch_config_still_clears_the_old_serial_num_when_the_form_resends_it(client, app_and_db_url):
    """The webapp form resends the stored serial_num on every save. If the host
    changed and the serial did not, it is still the old device's serial."""
    _, db_url = app_and_db_url
    owner_id, site_id, source_id = _create_envoy_source(
        client, db_url, {"host": "old.local", "cert_fingerprint": "old-pin", "serial_num": "111"}
    )
    patch = client.patch(
        f"/api/v1/sites/{site_id}/sources/{source_id}",
        json={"config": {"host": "new.local", "serial_num": "111"}},
        headers=auth(owner_id),
    )
    assert patch.json()["config"] == {"host": "new.local"}


def test_patch_config_sets_serial_num_without_a_host_change(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id, source_id = _create_envoy_source(client, db_url, {"host": "envoy.local"})
    patch = client.patch(
        f"/api/v1/sites/{site_id}/sources/{source_id}",
        json={"config": {"host": "envoy.local", "serial_num": "333"}},
        headers=auth(owner_id),
    )
    assert patch.json()["config"] == {"host": "envoy.local", "serial_num": "333"}


def test_patch_config_keeps_pinned_cert_fingerprint_when_host_is_unchanged(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={
            "kind": "energy",
            "provider": "envoy",
            "config": {"host": "envoy.local", "token": "abc", "cert_fingerprint": "pin", "serial_num": "111"},
        },
        headers=auth(owner_id),
    ).json()["id"]

    patch = client.patch(
        f"/api/v1/sites/{site_id}/sources/{source_id}",
        json={"config": {"token": "new-token"}},
        headers=auth(owner_id),
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["config"] == {
        "host": "envoy.local",
        "token": "new-token",
        "cert_fingerprint": "pin",
        "serial_num": "111",
    }


def test_patch_config_merges_without_wiping_other_keys(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {"host": "envoy.local", "token": "old-token"}},
        headers=auth(owner_id),
    ).json()["id"]

    patch = client.patch(
        f"/api/v1/sites/{site_id}/sources/{source_id}",
        json={"config": {"token": "new-token"}},
        headers=auth(owner_id),
    )
    assert patch.status_code == 200, patch.text

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    from core_api.models.sources import Source

    source = session.get(Source, source_id)
    session.close()
    assert source.config == {"host": "envoy.local", "token": "new-token"}


def test_renewal_url_present_for_envoy_with_serial_num(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {"serial_num": "123456789012"}},
        headers=auth(owner_id),
    ).json()["id"]

    response = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert response.status_code == 200
    assert response.json()["renewal_url"] == (
        "https://enlighten.enphaseenergy.com/entrez-auth-token?serial_num=123456789012"
    )


def test_renewal_url_absent_without_serial_num(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {}},
        headers=auth(owner_id),
    ).json()["id"]

    response = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert response.json()["renewal_url"] is None


def test_get_source_includes_raw_config(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {"host": "envoy.local", "token": "abc"}},
        headers=auth(owner_id),
    ).json()["id"]

    response = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert response.status_code == 200
    assert response.json()["config"] == {"host": "envoy.local", "token": "abc"}


def test_list_sources_includes_raw_config(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "amber", "config": {"amber_site_id": "site-123"}},
        headers=auth(owner_id),
    )

    response = client.get(f"/api/v1/sites/{site_id}/sources", headers=auth(owner_id))
    assert response.status_code == 200
    assert response.json()[0]["config"] == {"amber_site_id": "site-123"}


def _grant_viewer_access(db_url: str, site_id: int, email: str) -> int:
    from core_api.models.identity import SiteAccess

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    viewer = User(email=email, password_hash="x")
    session.add(viewer)
    session.commit()
    session.add(SiteAccess(site_id=site_id, user_id=viewer.id))
    session.commit()
    viewer_id = viewer.id
    session.close()
    return viewer_id


def test_get_source_redacts_the_token_for_a_non_owner_viewer(client, app_and_db_url):
    """A read-only site collaborator can see that an envoy source exists, but
    must not be able to read its local-API token: that token can reach the
    owner's device directly, well beyond what a read-only grant should expose."""
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {"host": "envoy.local", "token": "abc"}},
        headers=auth(owner_id),
    ).json()["id"]
    viewer_id = _grant_viewer_access(db_url, site_id, "viewer-src@example.com")

    response = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(viewer_id))
    assert response.status_code == 200
    assert response.json()["config"] == {"host": "envoy.local"}

    # The owner still sees the real token.
    owner_view = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert owner_view.json()["config"] == {"host": "envoy.local", "token": "abc"}


def test_list_sources_redacts_the_token_for_a_non_owner_viewer(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": {"host": "envoy.local", "token": "abc"}},
        headers=auth(owner_id),
    )
    viewer_id = _grant_viewer_access(db_url, site_id, "viewer-list@example.com")

    response = client.get(f"/api/v1/sites/{site_id}/sources", headers=auth(viewer_id))
    assert response.status_code == 200
    assert response.json()[0]["config"] == {"host": "envoy.local"}


def test_get_source_redacts_enlighten_credentials_for_a_non_owner_viewer(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    config = {"host": "envoy.local", "token": "abc", "username": "u@example.com", "password": "pw"}
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "energy", "provider": "envoy", "config": config},
        headers=auth(owner_id),
    ).json()["id"]
    viewer_id = _grant_viewer_access(db_url, site_id, "viewer-creds@example.com")

    viewer_view = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(viewer_id))
    assert viewer_view.json()["config"] == {"host": "envoy.local"}

    owner_view = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert owner_view.json()["config"] == config


def test_source_config_never_includes_amber_api_key(client, app_and_db_url):
    """The Amber API key lives on User.amber_api_key and is injected into the
    collector-facing config view only at serve time (core_api.routers.
    collector_config.get_source_config) — it must never appear in the raw
    Source.config a logged-in user can read, even for an amber source."""
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "amber", "config": {"amber_site_id": "site-123"}},
        headers=auth(owner_id),
    ).json()["id"]

    response = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert "amber_api_key" not in response.json()["config"]


def test_renewal_url_absent_for_non_envoy_provider(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "weather", "provider": "weatherlink_live", "config": {"serial_num": "123"}},
        headers=auth(owner_id),
    ).json()["id"]

    response = client.get(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))
    assert response.json()["renewal_url"] is None


def test_second_pricing_source_rejected_while_first_is_still_active(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    first = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "amber", "config": {"amber_site_id": "site-1"}},
        headers=auth(owner_id),
    )
    assert first.status_code == 201

    second = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "amber", "config": {"amber_site_id": "site-2"}},
        headers=auth(owner_id),
    )
    assert second.status_code == 409


def test_second_pricing_source_allowed_once_first_is_closed(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    first_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "amber", "config": {"amber_site_id": "site-1"}},
        headers=auth(owner_id),
    ).json()["id"]

    close = client.patch(
        f"/api/v1/sites/{site_id}/sources/{first_id}",
        json={"effective_to": "2020-01-01T00:00:00Z"},
        headers=auth(owner_id),
    )
    assert close.status_code == 200

    second = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "amber", "config": {"amber_site_id": "site-2"}},
        headers=auth(owner_id),
    )
    assert second.status_code == 201


def test_update_rejects_extending_effective_to_into_another_pricing_sources_range(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    first_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "amber", "config": {"amber_site_id": "site-1"}},
        headers=auth(owner_id),
    ).json()["id"]
    client.patch(
        f"/api/v1/sites/{site_id}/sources/{first_id}",
        json={"effective_to": "2020-01-01T00:00:00Z"},
        headers=auth(owner_id),
    )
    client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "amber", "config": {"amber_site_id": "site-2"}},
        headers=auth(owner_id),
    )

    response = client.patch(
        f"/api/v1/sites/{site_id}/sources/{first_id}",
        json={"effective_to": "2099-01-01T00:00:00Z"},
        headers=auth(owner_id),
    )
    assert response.status_code == 409


def test_patch_config_clears_the_cert_expiry_and_serial_when_host_changes(client, app_and_db_url):
    """The expiry belongs to the pinned certificate, so it goes with the pin."""
    _, db_url = app_and_db_url
    owner_id, site_id, source_id = _create_envoy_source(
        client,
        db_url,
        {
            "host": "old.local",
            "cert_fingerprint": "old-pin",
            "cert_expires_at": "2027-01-01T00:00:00+00:00",
            "cert_serial": "322103FC",
        },
    )
    patch = client.patch(
        f"/api/v1/sites/{site_id}/sources/{source_id}",
        json={"config": {"host": "new.local"}},
        headers=auth(owner_id),
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["config"] == {"host": "new.local"}


def create_pricing_source(client, site_id: int, owner_id: int) -> int:
    return client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "amber", "config": {}},
        headers=auth(owner_id),
    ).json()["id"]


def add_rows(db_url: str, *rows: object) -> None:
    session = make_session_factory(make_engine(db_url))()
    try:
        session.add_all(rows)
        session.commit()
    finally:
        session.close()


def flat_window(source_id: int) -> LocalRateWindow:
    return LocalRateWindow(
        source_id=source_id, channel=PriceChannel.BUY, label="Flat",
        effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        months=[1], days_of_week=[0], start_time=datetime.time(0, 0), end_time=datetime.time(0, 0),
        rate_c_per_kwh=decimal.Decimal("30"),
    )


def count_windows(db_url: str) -> int:
    session = make_session_factory(make_engine(db_url))()
    try:
        return session.execute(sa.select(sa.func.count()).select_from(LocalRateWindow)).scalar_one()
    finally:
        session.close()


def test_delete_pricing_source_also_deletes_its_rate_windows(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = create_pricing_source(client, site_id, owner_id)
    add_rows(db_url, flat_window(source_id))

    response = client.delete(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))

    assert response.status_code == 204, response.text
    assert count_windows(db_url) == 0


def test_delete_pricing_source_with_readings_keeps_its_rate_windows(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = create_pricing_source(client, site_id, owner_id)
    reading = PriceReading(
        pricing_source_id=source_id,
        ts=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        channel=PriceChannel.BUY,
        value=decimal.Decimal("30"),
    )
    add_rows(db_url, flat_window(source_id), reading)

    response = client.delete(f"/api/v1/sites/{site_id}/sources/{source_id}", headers=auth(owner_id))

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "source_has_readings"
    assert count_windows(db_url) == 1


def test_deleting_a_source_deletes_its_reprice_marker(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "local", "name": "Fixed"},
        headers=auth(owner_id),
    ).json()
    client.post(
        f"/api/v1/sites/{site_id}/sources/{source['id']}/rate-windows",
        json={
            "channel": "buy", "label": "Flat", "effective_from": "2026-01-01T00:00:00Z",
            "months": list(range(1, 13)), "days_of_week": list(range(7)),
            "start_time": "00:00:00", "end_time": "00:00:00", "rate_c_per_kwh": "30",
        },
        headers=auth(owner_id),
    )

    response = client.delete(f"/api/v1/sites/{site_id}/sources/{source['id']}", headers=auth(owner_id))

    assert response.status_code == 204, response.text
    session = make_session_factory(make_engine(db_url))()
    try:
        assert session.query(LocalRepriceMarker).count() == 0
    finally:
        session.close()
