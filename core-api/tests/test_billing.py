from __future__ import annotations

import secrets

from core_api.db import make_engine, make_session_factory
from core_api.models.identity import Site, User
from core_api.security import create_access_token


def make_owner_and_site(db_url: str) -> tuple[int, int]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    owner = User(email=f"bill-owner-{secrets.token_hex(4)}@example.com", password_hash="x")
    session.add(owner)
    session.commit()
    site = Site(owner_user_id=owner.id, name="Home", timezone="Australia/Sydney")
    session.add(site)
    session.commit()
    ids = (owner.id, site.id)
    session.close()
    return ids


def auth(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_create_tariff_and_demand_window(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff = client.post(
        f"/api/v1/sites/{site_id}/tariffs",
        json={"effective_from": "2026-01-01T00:00:00Z", "service_availability_c_per_day": "80.5"},
        headers=auth(owner_id),
    )
    assert tariff.status_code == 201, tariff.text
    tariff_id = tariff.json()["id"]

    window = client.post(
        f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/demand-windows",
        json={
            "label": "Summer peak",
            "months": [12, 1, 2],
            "days_of_week": [0, 1, 2, 3, 4],
            "start_time": "15:00:00",
            "end_time": "21:00:00",
            "rate_c_per_kw_per_day": "45.0",
            "measurement_interval_minutes": 30,
        },
        headers=auth(owner_id),
    )
    assert window.status_code == 201, window.text

    listed = client.get(f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/demand-windows", headers=auth(owner_id))
    assert len(listed.json()) == 1


def test_create_export_adjustment_window(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff = client.post(
        f"/api/v1/sites/{site_id}/tariffs",
        json={"effective_from": "2026-01-01T00:00:00Z", "service_availability_c_per_day": "80.5"},
        headers=auth(owner_id),
    )
    tariff_id = tariff.json()["id"]

    window = client.post(
        f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/export-adjustments",
        json={
            "label": "Solar soak incentive",
            "months": [11, 12, 1, 2],
            "days_of_week": [0, 1, 2, 3, 4, 5, 6],
            "start_time": "10:00:00",
            "end_time": "15:00:00",
            "adjustment_c_per_kwh": "5.0",
        },
        headers=auth(owner_id),
    )
    assert window.status_code == 201, window.text
    assert window.json()["adjustment_c_per_kwh"] == "5.0000"

    listed = client.get(f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/export-adjustments", headers=auth(owner_id))
    assert len(listed.json()) == 1


def test_create_export_adjustment_window_for_tariff_from_another_site_is_404(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    other_owner_id, other_site_id = make_owner_and_site(db_url)
    tariff = client.post(
        f"/api/v1/sites/{site_id}/tariffs",
        json={"effective_from": "2026-01-01T00:00:00Z", "service_availability_c_per_day": "80.5"},
        headers=auth(owner_id),
    )
    tariff_id = tariff.json()["id"]

    window = client.post(
        f"/api/v1/sites/{other_site_id}/tariffs/{tariff_id}/export-adjustments",
        json={
            "label": "Solar soak incentive",
            "months": [1],
            "days_of_week": [0],
            "start_time": "10:00:00",
            "end_time": "15:00:00",
            "adjustment_c_per_kwh": "5.0",
        },
        headers=auth(other_owner_id),
    )
    assert window.status_code == 404


def test_create_tariff_with_monthly_fee(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff = client.post(
        f"/api/v1/sites/{site_id}/tariffs",
        json={
            "effective_from": "2026-01-01T00:00:00Z",
            "service_availability_c_per_day": "80.5",
            "monthly_fee_c": "2500.0",
        },
        headers=auth(owner_id),
    )
    assert tariff.status_code == 201, tariff.text
    assert tariff.json()["monthly_fee_c"] == "2500.0000"


def test_create_monthly_fee_discount_window(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff = client.post(
        f"/api/v1/sites/{site_id}/tariffs",
        json={
            "effective_from": "2026-01-01T00:00:00Z",
            "service_availability_c_per_day": "80.5",
            "monthly_fee_c": "2500.0",
        },
        headers=auth(owner_id),
    )
    tariff_id = tariff.json()["id"]

    window = client.post(
        f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/monthly-fee-discounts",
        json={
            "label": "New customer promo",
            "starts_at": "2026-01-01T00:00:00Z",
            "ends_at": "2026-04-01T00:00:00Z",
            "discounted_fee_c": "0.0",
        },
        headers=auth(owner_id),
    )
    assert window.status_code == 201, window.text
    assert window.json()["discounted_fee_c"] == "0.0000"

    listed = client.get(f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/monthly-fee-discounts", headers=auth(owner_id))
    assert len(listed.json()) == 1


def test_create_monthly_fee_discount_window_for_tariff_from_another_site_is_404(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    other_owner_id, other_site_id = make_owner_and_site(db_url)
    tariff = client.post(
        f"/api/v1/sites/{site_id}/tariffs",
        json={"effective_from": "2026-01-01T00:00:00Z", "service_availability_c_per_day": "80.5"},
        headers=auth(owner_id),
    )
    tariff_id = tariff.json()["id"]

    window = client.post(
        f"/api/v1/sites/{other_site_id}/tariffs/{tariff_id}/monthly-fee-discounts",
        json={
            "label": "New customer promo",
            "starts_at": "2026-01-01T00:00:00Z",
            "ends_at": "2026-04-01T00:00:00Z",
            "discounted_fee_c": "0.0",
        },
        headers=auth(other_owner_id),
    )
    assert window.status_code == 404


def test_create_and_get_billing_cycle(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    create = client.post(
        f"/api/v1/sites/{site_id}/billing-cycle",
        json={"effective_from": "2026-01-01T00:00:00Z", "cycle_months": 3, "anchor_day": 18},
        headers=auth(owner_id),
    )
    assert create.status_code == 201, create.text

    invalid = client.post(
        f"/api/v1/sites/{site_id}/billing-cycle",
        json={"effective_from": "2026-01-01T00:00:00Z", "cycle_months": 0, "anchor_day": 18},
        headers=auth(owner_id),
    )
    assert invalid.status_code == 422


def test_close_tariff_effective_to(client, app_and_db_url):
    """Finding 9/I9: PATCH must be able to close a SiteTariff's effective_to,
    the delete-equivalent for this effective-dated entity."""
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff = client.post(
        f"/api/v1/sites/{site_id}/tariffs",
        json={"effective_from": "2026-01-01T00:00:00Z", "service_availability_c_per_day": "80.5"},
        headers=auth(owner_id),
    )
    tariff_id = tariff.json()["id"]

    patch = client.patch(
        f"/api/v1/sites/{site_id}/tariffs/{tariff_id}",
        json={"effective_to": "2026-06-01T00:00:00Z"},
        headers=auth(owner_id),
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["effective_to"] == "2026-06-01T00:00:00Z"


def test_close_tariff_from_another_site_is_404(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    other_owner_id, other_site_id = make_owner_and_site(db_url)
    tariff = client.post(
        f"/api/v1/sites/{site_id}/tariffs",
        json={"effective_from": "2026-01-01T00:00:00Z", "service_availability_c_per_day": "80.5"},
        headers=auth(owner_id),
    )
    tariff_id = tariff.json()["id"]

    patch = client.patch(
        f"/api/v1/sites/{other_site_id}/tariffs/{tariff_id}",
        json={"effective_to": "2026-06-01T00:00:00Z"},
        headers=auth(other_owner_id),
    )
    assert patch.status_code == 404


def test_close_billing_cycle_effective_to(client, app_and_db_url):
    """Finding 9/I9: PATCH must be able to close a BillingCycle's
    effective_to, the delete-equivalent for this effective-dated entity."""
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    cycle = client.post(
        f"/api/v1/sites/{site_id}/billing-cycle",
        json={"effective_from": "2026-01-01T00:00:00Z", "cycle_months": 3, "anchor_day": 18},
        headers=auth(owner_id),
    )
    cycle_id = cycle.json()["id"]

    patch = client.patch(
        f"/api/v1/sites/{site_id}/billing-cycle/{cycle_id}",
        json={"effective_to": "2026-06-01T00:00:00Z"},
        headers=auth(owner_id),
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["effective_to"] == "2026-06-01T00:00:00Z"


def test_close_billing_cycle_from_another_site_is_404(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    other_owner_id, other_site_id = make_owner_and_site(db_url)
    cycle = client.post(
        f"/api/v1/sites/{site_id}/billing-cycle",
        json={"effective_from": "2026-01-01T00:00:00Z", "cycle_months": 3, "anchor_day": 18},
        headers=auth(owner_id),
    )
    cycle_id = cycle.json()["id"]

    patch = client.patch(
        f"/api/v1/sites/{other_site_id}/billing-cycle/{cycle_id}",
        json={"effective_to": "2026-06-01T00:00:00Z"},
        headers=auth(other_owner_id),
    )
    assert patch.status_code == 404
