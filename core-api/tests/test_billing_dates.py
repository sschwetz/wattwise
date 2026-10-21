from __future__ import annotations

import pytest

from tests.test_billing import auth, make_owner_and_site

DEMAND = {
    "label": "Peak",
    "months": [1],
    "days_of_week": [0],
    "start_time": "15:00:00",
    "end_time": "21:00:00",
    "rate_c_per_kw_per_day": "45.0",
}
EXPORT = {**{k: DEMAND[k] for k in ("label", "months", "days_of_week", "start_time", "end_time")}, "adjustment_c_per_kwh": "-2"}
CREDIT = {
    **{k: DEMAND[k] for k in ("label", "months", "days_of_week", "start_time", "end_time")},
    "max_avg_import_w": "100",
    "credit_c": "50",
}
WINDOW_KINDS = [("demand-windows", DEMAND), ("export-adjustments", EXPORT), ("conditional-credits", CREDIT)]


@pytest.fixture()
def owner_site(app_and_db_url):
    _, db_url = app_and_db_url
    return make_owner_and_site(db_url)


def _post(client, owner_site, path, body):
    owner_id, site_id = owner_site
    return client.post(f"/api/v1/sites/{site_id}/{path}", json=body, headers=auth(owner_id))


def _patch(client, owner_site, path, body):
    owner_id, site_id = owner_site
    return client.patch(f"/api/v1/sites/{site_id}/{path}", json=body, headers=auth(owner_id))


def _tariff(client, owner_site, start="2026-01-01T00:00:00Z", end=None) -> int:
    body = {"effective_from": start, "service_availability_c_per_day": "80"}
    if end:
        body["effective_to"] = end
    response = _post(client, owner_site, "tariffs", body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_tariff_end_not_after_start_is_rejected(client, owner_site):
    body = {"effective_from": "2026-07-01T00:00:00Z", "effective_to": "2026-07-01T00:00:00Z", "service_availability_c_per_day": "80"}

    assert _post(client, owner_site, "tariffs", body).status_code == 422


def test_overlapping_tariff_is_rejected(client, owner_site):
    _tariff(client, owner_site, end="2026-07-01T00:00:00Z")

    response = _post(
        client, owner_site, "tariffs", {"effective_from": "2026-06-30T00:00:00Z", "service_availability_c_per_day": "90"}
    )

    assert response.status_code == 422


def test_tariff_starting_where_the_last_ends_is_accepted(client, owner_site):
    _tariff(client, owner_site, end="2026-07-01T00:00:00Z")

    response = _post(
        client, owner_site, "tariffs", {"effective_from": "2026-07-01T00:00:00Z", "service_availability_c_per_day": "90"}
    )

    assert response.status_code == 201, response.text


def test_ending_a_tariff_before_it_starts_is_rejected(client, owner_site):
    tariff_id = _tariff(client, owner_site)

    response = _patch(client, owner_site, f"tariffs/{tariff_id}", {"effective_to": "2025-12-31T00:00:00Z"})

    assert response.status_code == 422


def test_extending_a_tariff_into_the_next_one_is_rejected(client, owner_site):
    first = _tariff(client, owner_site, end="2026-07-01T00:00:00Z")
    _tariff(client, owner_site, start="2026-07-01T00:00:00Z")

    response = _patch(client, owner_site, f"tariffs/{first}", {"effective_to": "2026-08-01T00:00:00Z"})

    assert response.status_code == 422


def test_overlapping_billing_cycle_is_rejected(client, owner_site):
    cycle = {"effective_from": "2026-01-01T00:00:00Z", "cycle_months": 1, "anchor_day": 1}
    assert _post(client, owner_site, "billing-cycle", cycle).status_code == 201

    assert _post(client, owner_site, "billing-cycle", {**cycle, "effective_from": "2026-03-01T00:00:00Z"}).status_code == 422


def test_billing_cycle_starting_where_the_last_ends_is_accepted(client, owner_site):
    cycle = {"effective_from": "2026-01-01T00:00:00Z", "cycle_months": 1, "anchor_day": 1}
    first = _post(client, owner_site, "billing-cycle", cycle).json()["id"]
    assert _patch(client, owner_site, f"billing-cycle/{first}", {"effective_to": "2026-07-01T00:00:00Z"}).status_code == 200

    response = _post(client, owner_site, "billing-cycle", {**cycle, "effective_from": "2026-07-01T00:00:00Z"})

    assert response.status_code == 201, response.text


@pytest.mark.parametrize("path,body", WINDOW_KINDS)
def test_window_without_dates_takes_the_tariffs_start(client, owner_site, path, body):
    tariff_id = _tariff(client, owner_site, start="2026-03-01T00:00:00Z")

    response = _post(client, owner_site, f"tariffs/{tariff_id}/{path}", body)

    assert response.status_code == 201, response.text
    assert response.json()["effective_from"].startswith("2026-03-01T00:00:00")
    assert response.json()["effective_to"] is None


@pytest.mark.parametrize("path,body", WINDOW_KINDS)
def test_window_starting_before_its_tariff_is_rejected(client, owner_site, path, body):
    tariff_id = _tariff(client, owner_site, start="2026-03-01T00:00:00Z")

    response = _post(
        client, owner_site, f"tariffs/{tariff_id}/{path}", {**body, "effective_from": "2026-02-28T00:00:00Z"}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("path,body", WINDOW_KINDS)
def test_window_ending_after_its_tariff_is_rejected(client, owner_site, path, body):
    tariff_id = _tariff(client, owner_site, end="2026-07-01T00:00:00Z")

    response = _post(client, owner_site, f"tariffs/{tariff_id}/{path}", {**body, "effective_to": "2026-07-02T00:00:00Z"})

    assert response.status_code == 422


@pytest.mark.parametrize("path,body", WINDOW_KINDS)
def test_open_ended_window_under_an_ended_tariff_is_accepted(client, owner_site, path, body):
    tariff_id = _tariff(client, owner_site, end="2026-07-01T00:00:00Z")

    assert _post(client, owner_site, f"tariffs/{tariff_id}/{path}", body).status_code == 201


@pytest.mark.parametrize("path,body", WINDOW_KINDS)
def test_window_ending_before_it_starts_is_rejected(client, owner_site, path, body):
    tariff_id = _tariff(client, owner_site)
    dates = {"effective_from": "2026-05-01T00:00:00Z", "effective_to": "2026-04-01T00:00:00Z"}

    assert _post(client, owner_site, f"tariffs/{tariff_id}/{path}", {**body, **dates}).status_code == 422


@pytest.mark.parametrize("path,body", WINDOW_KINDS)
def test_window_can_be_ended(client, owner_site, path, body):
    tariff_id = _tariff(client, owner_site)
    window_id = _post(client, owner_site, f"tariffs/{tariff_id}/{path}", body).json()["id"]

    response = _patch(client, owner_site, f"tariffs/{tariff_id}/{path}/{window_id}", {"effective_to": "2026-07-01T00:00:00Z"})

    assert response.status_code == 200, response.text
    assert response.json()["effective_to"].startswith("2026-07-01T00:00:00")


@pytest.mark.parametrize("path,body", WINDOW_KINDS)
def test_ending_a_window_before_it_starts_is_rejected(client, owner_site, path, body):
    tariff_id = _tariff(client, owner_site)
    window_id = _post(client, owner_site, f"tariffs/{tariff_id}/{path}", body).json()["id"]

    response = _patch(client, owner_site, f"tariffs/{tariff_id}/{path}/{window_id}", {"effective_to": "2025-01-01T00:00:00Z"})

    assert response.status_code == 422


def test_ending_a_window_of_another_tariff_is_404(client, owner_site):
    first, second = _tariff(client, owner_site, end="2026-07-01T00:00:00Z"), _tariff(client, owner_site, start="2026-07-01T00:00:00Z")
    window_id = _post(client, owner_site, f"tariffs/{first}/demand-windows", DEMAND).json()["id"]

    response = _patch(client, owner_site, f"tariffs/{second}/demand-windows/{window_id}", {"effective_to": "2026-08-01T00:00:00Z"})

    assert response.status_code == 404


def test_adding_a_demand_window_into_a_finalized_period_clears_its_stored_charge(client, owner_site, app_and_db_url):
    import datetime

    from core_api.db import make_engine, make_session_factory
    from core_api.models.billing import DemandCharge, DemandInterval

    _, db_url = app_and_db_url
    tariff_id = _tariff(client, owner_site)
    slot = datetime.datetime(2026, 3, 10, tzinfo=datetime.timezone.utc)
    session = make_session_factory(make_engine(db_url))()
    session.add(DemandInterval(site_id=owner_site[1], interval_start=slot, interval_end=slot + datetime.timedelta(minutes=5), rolling_sum_w=1.0))
    session.flush()
    session.add(
        DemandCharge(
            site_id=owner_site[1],
            billing_period_start=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc),
            billing_period_end=datetime.datetime(2026, 4, 1, tzinfo=datetime.timezone.utc),
            peak_interval_start=slot,
            peak_avg_import_w=1.0,
            rate_applied="45.0",
            charge_amount="100",
            is_estimated=False,
        )
    )
    session.commit()

    response = _post(client, owner_site, f"tariffs/{tariff_id}/demand-windows", {**DEMAND, "effective_from": "2026-03-15T00:00:00Z"})

    assert response.status_code == 201, response.text
    assert session.query(DemandCharge).count() == 0
    session.close()
