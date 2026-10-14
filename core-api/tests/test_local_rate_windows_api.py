from __future__ import annotations

import datetime
import secrets

import pytest

from core_api.db import make_engine, make_session_factory
from core_api.models.local_rates import LocalRepriceMarker
from core_api.time_utils import as_utc
from core_api.models.sources import SourceKind
from tests.conftest import PRICING_COLLECTOR_KEY
from tests.test_billing import auth, make_owner_and_site
from tests.test_collector_discovery import make_source
from tests.test_sites import make_user

FLAT = {
    "channel": "buy",
    "label": "Flat",
    "effective_from": "2026-01-01T00:00:00Z",
    "months": list(range(1, 13)),
    "days_of_week": list(range(7)),
    "start_time": "00:00:00",
    "end_time": "00:00:00",
    "rate_c_per_kwh": "32.5",
}


def _window(**overrides) -> dict:
    return {**FLAT, **overrides}


@pytest.fixture()
def local_source(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    response = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "local", "name": "Origin Fixed"},
        headers=auth(owner_id),
    )
    assert response.status_code == 201, response.text
    return {"owner": owner_id, "site": site_id, "source": response.json()["id"], "db_url": db_url}


def _url(ctx, suffix="") -> str:
    return f"/api/v1/sites/{ctx['site']}/sources/{ctx['source']}/rate-windows{suffix}"


def test_create_and_list_a_window(client, local_source):
    created = client.post(_url(local_source), json=_window(), headers=auth(local_source["owner"]))
    assert created.status_code == 201, created.text
    assert created.json()["rate_c_per_kwh"] == "32.5000"
    assert created.json()["effective_from"].endswith("Z") or created.json()["effective_from"].endswith("+00:00")

    listed = client.get(_url(local_source), headers=auth(local_source["owner"]))
    assert [w["label"] for w in listed.json()] == ["Flat"]


def test_adjacent_windows_save_without_an_overlap_error(client, local_source):
    headers = auth(local_source["owner"])
    night = _window(label="Off-peak", end_time="07:00:00", rate_c_per_kwh="18")
    day = _window(label="Peak", start_time="07:00:00", end_time="00:00:00", rate_c_per_kwh="40")
    assert client.post(_url(local_source), json=night, headers=headers).status_code == 201
    assert client.post(_url(local_source), json=day, headers=headers).status_code == 201


def test_an_overlapping_window_is_rejected_and_names_the_clash(client, local_source):
    headers = auth(local_source["owner"])
    client.post(_url(local_source), json=_window(), headers=headers)

    response = client.post(_url(local_source), json=_window(label="Extra", start_time="07:00:00", end_time="09:00:00"), headers=headers)

    assert response.status_code == 422
    assert response.json()["code"] == "rate_window_overlap"
    assert '"Flat"' in response.json()["message"]


def test_the_same_hours_on_another_channel_do_not_clash(client, local_source):
    headers = auth(local_source["owner"])
    client.post(_url(local_source), json=_window(), headers=headers)
    export = _window(channel="sell", label="Feed-in", rate_c_per_kwh="5")
    assert client.post(_url(local_source), json=export, headers=headers).status_code == 201


@pytest.mark.parametrize(
    "bad",
    [
        {"months": [13]},
        {"days_of_week": [7]},
        {"months": []},
        {"start_time": "07:00:30"},
        {"rate_c_per_kwh": "-1"},
        {"effective_to": "2025-01-01T00:00:00Z"},
        {"label": ""},
        {"channel": "demand"},
    ],
)
def test_invalid_windows_are_rejected(client, local_source, bad):
    response = client.post(_url(local_source), json=_window(**bad), headers=auth(local_source["owner"]))
    assert response.status_code == 422


def test_a_time_with_a_timezone_offset_is_rejected(client, local_source):
    # SQLite would drop the offset and store 07:00 as if it were local time.
    response = client.post(
        _url(local_source), json=_window(start_time="07:00:00+10:00"), headers=auth(local_source["owner"])
    )
    assert response.status_code == 422


def test_patch_replaces_a_window_and_ignores_itself_in_the_overlap_check(client, local_source):
    headers = auth(local_source["owner"])
    window_id = client.post(_url(local_source), json=_window(), headers=headers).json()["id"]

    response = client.patch(_url(local_source, f"/{window_id}"), json=_window(rate_c_per_kwh="35"), headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["rate_c_per_kwh"] == "35.0000"


def test_patch_that_would_overlap_another_window_is_rejected(client, local_source):
    headers = auth(local_source["owner"])
    client.post(_url(local_source), json=_window(end_time="07:00:00"), headers=headers)
    day_id = client.post(
        _url(local_source), json=_window(label="Day", start_time="07:00:00", end_time="00:00:00"), headers=headers
    ).json()["id"]

    response = client.patch(_url(local_source, f"/{day_id}"), json=_window(label="Day", start_time="06:00:00"), headers=headers)

    assert response.status_code == 422


def test_delete_a_window(client, local_source):
    headers = auth(local_source["owner"])
    window_id = client.post(_url(local_source), json=_window(), headers=headers).json()["id"]

    assert client.delete(_url(local_source, f"/{window_id}"), headers=headers).status_code == 204
    assert client.get(_url(local_source), headers=headers).json() == []


def test_windows_cannot_be_added_to_a_non_local_source(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    amber = client.post(
        f"/api/v1/sites/{site_id}/sources", json={"kind": "pricing", "provider": "amber"}, headers=auth(owner_id)
    ).json()

    response = client.post(
        f"/api/v1/sites/{site_id}/sources/{amber['id']}/rate-windows", json=_window(), headers=auth(owner_id)
    )

    assert response.status_code == 422
    assert response.json()["code"] == "not_a_local_source"


def test_only_the_site_owner_can_add_windows(client, local_source, app_and_db_url):
    _, db_url = app_and_db_url
    other_owner, _ = make_owner_and_site(db_url)
    response = client.post(_url(local_source), json=_window(), headers=auth(other_owner))
    assert response.status_code == 403


def test_a_viewer_can_list_windows_but_not_change_them(client, local_source):
    owner_headers = auth(local_source["owner"])
    window_id = client.post(_url(local_source), json=_window(), headers=owner_headers).json()["id"]
    viewer_id = make_user(local_source["db_url"], f"viewer-{secrets.token_hex(4)}@example.com")
    grant = client.post(
        f"/api/v1/sites/{local_source['site']}/access", json={"user_id": viewer_id}, headers=owner_headers
    )
    assert grant.status_code == 201, grant.text
    viewer = auth(viewer_id)

    assert client.get(_url(local_source), headers=viewer).status_code == 200
    assert client.post(_url(local_source), json=_window(), headers=viewer).status_code == 403
    assert client.patch(_url(local_source, f"/{window_id}"), json=_window(), headers=viewer).status_code == 403
    assert client.delete(_url(local_source, f"/{window_id}"), headers=viewer).status_code == 403


def test_another_sites_source_is_not_found_under_this_site(client, local_source):
    other_owner, other_site = make_owner_and_site(local_source["db_url"])
    other_source = client.post(
        f"/api/v1/sites/{other_site}/sources",
        json={"kind": "pricing", "provider": "local", "name": "Theirs"},
        headers=auth(other_owner),
    ).json()["id"]

    response = client.get(
        f"/api/v1/sites/{local_source['site']}/sources/{other_source}/rate-windows",
        headers=auth(local_source["owner"]),
    )

    assert response.status_code == 404


def test_a_local_source_needs_a_name(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    for body in ({"kind": "pricing", "provider": "local"}, {"kind": "pricing", "provider": "local", "name": "  "}):
        response = client.post(f"/api/v1/sites/{site_id}/sources", json=body, headers=auth(owner_id))
        assert response.status_code == 422


def test_the_local_provider_is_only_for_pricing_sources(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    response = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "weather", "provider": "local", "name": "x"},
        headers=auth(owner_id),
    )
    assert response.status_code == 422


def test_renaming_a_local_source_to_a_blank_name_is_rejected(client, local_source):
    response = client.patch(
        f"/api/v1/sites/{local_source['site']}/sources/{local_source['source']}",
        json={"name": "   "},
        headers=auth(local_source["owner"]),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "name_invalid"


def _rename(client, ctx, name):
    return client.patch(
        f"/api/v1/sites/{ctx['site']}/sources/{ctx['source']}", json={"name": name}, headers=auth(ctx["owner"])
    )


def test_renaming_a_local_source_to_a_too_long_name_is_rejected(client, local_source):
    response = _rename(client, local_source, "x" * 101)
    assert response.status_code == 422
    assert response.json()["code"] == "name_invalid"


def test_renaming_a_local_source_to_a_100_character_name_stores_it_stripped(client, local_source):
    name = "x" * 100
    response = _rename(client, local_source, f"  {name}  ")
    assert response.status_code == 200, response.text
    assert response.json()["name"] == name


def test_local_config_ignores_extra_source_config_keys(client, app_and_db_url):
    _, db_url = app_and_db_url
    owner_id, site_id = make_owner_and_site(db_url)
    source_id = client.post(
        f"/api/v1/sites/{site_id}/sources",
        json={"kind": "pricing", "provider": "local", "name": "Origin", "config": {"foo": 1}},
        headers=auth(owner_id),
    ).json()["id"]

    response = client.get(
        f"/api/v1/sources/{source_id}/config",
        headers={"X-Collector-Key": PRICING_COLLECTOR_KEY, "X-Collector-Provider": "local"},
    )

    assert response.json()["config"] == {"timezone": "Australia/Sydney", "rate_windows": []}


def test_config_for_a_local_source_with_no_windows_is_empty(client, local_source):
    response = client.get(
        f"/api/v1/sources/{local_source['source']}/config",
        headers={"X-Collector-Key": PRICING_COLLECTOR_KEY, "X-Collector-Provider": "local"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["config"] == {"timezone": "Australia/Sydney", "rate_windows": []}


def test_config_for_a_local_source_lists_its_windows(client, local_source):
    client.post(_url(local_source), json=_window(), headers=auth(local_source["owner"]))

    response = client.get(
        f"/api/v1/sources/{local_source['source']}/config",
        headers={"X-Collector-Key": PRICING_COLLECTOR_KEY, "X-Collector-Provider": "local"},
    )

    assert response.json()["config"]["rate_windows"] == [
        {
            "channel": "buy",
            "effective_from": "2026-01-01T00:00:00+00:00",
            "effective_to": None,
            "months": list(range(1, 13)),
            "days_of_week": list(range(7)),
            "start_time": "00:00:00",
            "end_time": "00:00:00",
            "rate_c_per_kwh": "32.5000",
        }
    ]


def test_amber_config_has_no_rate_windows(client, app_and_db_url):
    _, db_url = app_and_db_url
    amber_id, _ = make_source(db_url, SourceKind.PRICING, "amber")
    response = client.get(f"/api/v1/sources/{amber_id}/config", headers={"X-Collector-Key": PRICING_COLLECTOR_KEY})
    assert "rate_windows" not in response.json()["config"]


UTC = datetime.timezone.utc


def _marker(ctx):
    session = make_session_factory(make_engine(ctx["db_url"]))()
    try:
        row = session.get(LocalRepriceMarker, ctx["source"])
        return (as_utc(row.reprice_from), row.version) if row else None
    finally:
        session.close()


def _drop_marker(ctx):
    session = make_session_factory(make_engine(ctx["db_url"]))()
    try:
        row = session.get(LocalRepriceMarker, ctx["source"])
        if row:
            session.delete(row)
            session.commit()
    finally:
        session.close()


def _create(client, ctx, **overrides) -> int:
    response = client.post(_url(ctx), json=_window(**overrides), headers=auth(ctx["owner"]))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _replace(client, ctx, window_id, **overrides):
    response = client.patch(_url(ctx, f"/{window_id}"), json=_window(**overrides), headers=auth(ctx["owner"]))
    assert response.status_code == 200, response.text


def test_a_new_source_has_no_marker(local_source):
    assert _marker(local_source) is None


def test_creating_a_window_marks_from_its_start(client, local_source):
    _create(client, local_source)
    assert _marker(local_source) == (datetime.datetime(2026, 1, 1, tzinfo=UTC), 1)


def test_a_later_change_keeps_the_earlier_start_and_raises_the_version(client, local_source):
    _create(client, local_source)
    _create(client, local_source, channel="sell", effective_from="2026-03-01T00:00:00Z")
    assert _marker(local_source) == (datetime.datetime(2026, 1, 1, tzinfo=UTC), 2)


def test_a_change_that_starts_earlier_lowers_the_start(client, local_source):
    _create(client, local_source, effective_from="2026-03-01T00:00:00Z")
    _create(client, local_source, channel="sell", effective_from="2026-01-01T00:00:00Z")
    assert _marker(local_source) == (datetime.datetime(2026, 1, 1, tzinfo=UTC), 2)


def test_changing_a_rate_marks_from_the_window_start(client, local_source):
    window_id = _create(client, local_source)
    _drop_marker(local_source)

    _replace(client, local_source, window_id, rate_c_per_kwh="35")

    assert _marker(local_source) == (datetime.datetime(2026, 1, 1, tzinfo=UTC), 1)


def test_ending_a_window_marks_the_moment_it_ends(client, local_source):
    window_id = _create(client, local_source)
    _drop_marker(local_source)

    _replace(client, local_source, window_id, effective_to="2026-06-15T10:00:00Z")

    assert _marker(local_source) == (datetime.datetime(2026, 6, 15, 10, 0, tzinfo=UTC), 1)


def test_end_then_add_from_now_marks_only_from_now(client, local_source):
    window_id = _create(client, local_source, effective_from="2025-01-01T00:00:00Z")
    _drop_marker(local_source)

    _replace(client, local_source, window_id, effective_from="2025-01-01T00:00:00Z", effective_to="2026-06-15T10:00:00Z")
    _create(client, local_source, effective_from="2026-06-15T10:00:01Z", rate_c_per_kwh="35")

    assert _marker(local_source) == (datetime.datetime(2026, 6, 15, 10, 0, tzinfo=UTC), 2)


def test_renaming_a_window_marks_nothing(client, local_source):
    window_id = _create(client, local_source)
    _drop_marker(local_source)

    _replace(client, local_source, window_id, label="Renamed")

    assert _marker(local_source) is None


def test_deleting_a_window_marks_from_its_start(client, local_source):
    window_id = _create(client, local_source, effective_from="2026-02-01T00:00:00Z")
    _drop_marker(local_source)

    response = client.delete(_url(local_source, f"/{window_id}"), headers=auth(local_source["owner"]))

    assert response.status_code == 204
    assert _marker(local_source) == (datetime.datetime(2026, 2, 1, tzinfo=UTC), 1)


def test_a_rejected_change_marks_nothing(client, local_source):
    _create(client, local_source)
    _drop_marker(local_source)

    overlap = client.post(
        _url(local_source), json=_window(label="Clash", start_time="07:00:00", end_time="09:00:00"), headers=auth(local_source["owner"])
    )

    assert overlap.status_code == 422
    assert _marker(local_source) is None


CONFIG_HEADERS = {"X-Collector-Key": PRICING_COLLECTOR_KEY, "X-Collector-Provider": "local"}


def _config(client, ctx) -> dict:
    return client.get(f"/api/v1/sources/{ctx['source']}/config", headers=CONFIG_HEADERS).json()["config"]


def _clear(client, ctx, version, headers=None):
    return client.post(
        f"/api/v1/sources/{ctx['source']}/prices/reprice-marker/clear",
        json={"version": version},
        headers=headers or CONFIG_HEADERS,
    )


def test_config_has_no_reprice_key_without_a_marker(client, local_source):
    assert "reprice" not in _config(client, local_source)


def test_config_carries_the_marker_as_utc_iso_and_a_version(client, local_source):
    _create(client, local_source, effective_from="2026-02-01T00:00:00Z")

    assert _config(client, local_source)["reprice"] == {"from": "2026-02-01T00:00:00+00:00", "version": 1}


def test_clearing_with_the_matching_version_removes_the_marker(client, local_source):
    _create(client, local_source)

    response = _clear(client, local_source, 1)

    assert response.status_code == 204
    assert "reprice" not in _config(client, local_source)


def test_clearing_with_a_stale_version_keeps_the_marker(client, local_source):
    _create(client, local_source)
    _create(client, local_source, channel="sell")  # version is now 2

    response = _clear(client, local_source, 1)

    assert response.status_code == 204
    assert _config(client, local_source)["reprice"]["version"] == 2


def test_clearing_when_there_is_no_marker_is_a_no_op(client, local_source):
    assert _clear(client, local_source, 1).status_code == 204


def test_clearing_needs_the_collector_key(client, local_source):
    assert _clear(client, local_source, 1, headers={"X-Collector-Key": "wrong"}).status_code == 401


def test_clearing_rejects_a_provider_header_for_another_provider(client, local_source):
    headers = {"X-Collector-Key": PRICING_COLLECTOR_KEY, "X-Collector-Provider": "amber"}
    assert _clear(client, local_source, 1, headers=headers).status_code == 401


def test_an_amber_config_never_has_a_reprice_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    amber_id, _ = make_source(db_url, SourceKind.PRICING, "amber")
    response = client.get(f"/api/v1/sources/{amber_id}/config", headers={"X-Collector-Key": PRICING_COLLECTOR_KEY})
    assert "reprice" not in response.json()["config"]
