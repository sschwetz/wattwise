from __future__ import annotations

import secrets

import pytest

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


def test_create_export_adjustment_window_with_daily_allowance(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff = client.post(
        f"/api/v1/sites/{site_id}/tariffs",
        json={"effective_from": "2026-01-01T00:00:00Z", "service_availability_c_per_day": "80.5"},
        headers=auth(owner_id),
    )
    tariff_id = tariff.json()["id"]
    url = f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/export-adjustments"
    body = {
        "label": "Export fee",
        "months": [1],
        "days_of_week": [0],
        "start_time": "00:00:00",
        "end_time": "23:59:00",
        "adjustment_c_per_kwh": "-3.0",
    }

    with_allowance = client.post(url, json={**body, "daily_allowance_kwh": "10"}, headers=auth(owner_id))
    assert with_allowance.status_code == 201, with_allowance.text
    assert with_allowance.json()["daily_allowance_kwh"] == "10.0000"

    without = client.post(url, json=body, headers=auth(owner_id))
    assert without.status_code == 201, without.text
    assert without.json()["daily_allowance_kwh"] == "0.0000"

    negative = client.post(url, json={**body, "daily_allowance_kwh": "-1"}, headers=auth(owner_id))
    assert negative.status_code == 422


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


CREDIT_PAYLOAD = {
    "label": "Low evening import",
    "months": [1, 2, 3],
    "days_of_week": [0, 1, 2, 3, 4],
    "start_time": "17:00:00",
    "end_time": "21:00:00",
    "max_avg_import_w": "300",
    "credit_c": "50",
}


def _make_tariff(client, owner_id, site_id) -> int:
    response = client.post(
        f"/api/v1/sites/{site_id}/tariffs",
        json={"effective_from": "2026-01-01T00:00:00Z", "service_availability_c_per_day": "80.5"},
        headers=auth(owner_id),
    )
    return response.json()["id"]


def test_create_and_list_conditional_credit(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff_id = _make_tariff(client, owner_id, site_id)
    url = f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/conditional-credits"

    created = client.post(url, json=CREDIT_PAYLOAD, headers=auth(owner_id))
    assert created.status_code == 201, created.text
    assert created.json()["credit_c"] == "50.0000"
    assert created.json()["max_avg_import_w"] == "300.0000"

    listed = client.get(url, headers=auth(owner_id))
    assert [w["label"] for w in listed.json()] == ["Low evening import"]


def test_create_conditional_credit_for_tariff_from_another_site_is_404(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    other_owner_id, other_site_id = make_owner_and_site(db_url)
    tariff_id = _make_tariff(client, owner_id, site_id)

    response = client.post(
        f"/api/v1/sites/{other_site_id}/tariffs/{tariff_id}/conditional-credits",
        json=CREDIT_PAYLOAD,
        headers=auth(other_owner_id),
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "override",
    [
        {"start_time": "21:00:00", "end_time": "17:00:00"},
        {"start_time": "17:00:00", "end_time": "17:00:00"},
        {"days_of_week": []},
        {"months": []},
        {"months": [13]},
        {"days_of_week": [7]},
        {"max_avg_import_w": "0"},
        {"credit_c": "0"},
        {"credit_c": "-5"},
        {"label": ""},
    ],
)
def test_create_conditional_credit_rejects_invalid_input(client, app_and_db_url, override):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff_id = _make_tariff(client, owner_id, site_id)

    response = client.post(
        f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/conditional-credits",
        json={**CREDIT_PAYLOAD, **override},
        headers=auth(owner_id),
    )
    assert response.status_code == 422


@pytest.mark.parametrize("override", [{"start_time": "17:00:30"}, {"end_time": "21:00:00+10:00"}])
def test_create_conditional_credit_rejects_times_that_are_not_whole_minutes(client, app_and_db_url, override):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    tariff_id = _make_tariff(client, owner_id, site_id)

    response = client.post(
        f"/api/v1/sites/{site_id}/tariffs/{tariff_id}/conditional-credits",
        json={**CREDIT_PAYLOAD, **override},
        headers=auth(owner_id),
    )
    assert response.status_code == 422
