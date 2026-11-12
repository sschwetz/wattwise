import datetime
import json

import httpx
import pytest
import respx

from actron_neo_actor.core_api_client import CoreApiClient, CoreApiConflict, CoreApiError
from actron_neo_actor.models import Run

BASE = "http://core"
UTC = datetime.UTC

RUN_JSON = {
    "id": 7,
    "schedule_id": 3,
    "kind": "action",
    "trigger_at": "2026-11-05T01:00:00Z",
    "started_at": None,
    "finished_at": "2026-11-05T01:00:09+00:00",
    "outcome": "ok",
    "message": None,
    "attempts": 1,
    "parent_run_id": None,
    "snapshot": {"mode": "COOL"},
}

# Mirrors test_schedule_response_shape_is_pinned in core-api/tests/test_actor_router.py:
# the same keys as GET /actor/instances/{id}/schedules (no instance_id).
SCHEDULE_JSON = {
    "id": 3,
    "name": "Pre-cool",
    "enabled": True,
    "trigger": {"type": "time", "time": "14:00", "days_of_week": [0, 1]},
    "conditions": [{"reading": "outdoor_temp_c", "op": "gt", "value": 30.0, "target": None}],
    "action": {"id": "set_mode", "params": {"mode": "COOL", "zones": ["a"]}},
    "restore": {
        "enabled": True, "when": "after_minutes", "minutes": 30, "time": None,
        "only_if": [{"reading": "indoor_temp_c", "op": "lt", "value": 20.0, "target": None, "enabled": False}],
        "early_if": [],
    },
    "retry": {"max_attempts": 3, "interval_seconds": 30, "give_up_minutes": 15},
    "notify_email": True,
    "created_at": "2026-11-01T00:00:00Z",
}


@pytest.fixture
def client() -> CoreApiClient:
    return CoreApiClient(BASE, "secret-key")


@respx.mock
def test_headers_carry_key_and_provider(client):
    route = respx.get(f"{BASE}/api/v1/actor/instances", params={"provider": "actron_neo"}).respond(json=[])
    assert client.list_instances() == []
    request = route.calls.last.request
    assert request.headers["X-Actor-Key"] == "secret-key"
    assert request.headers["X-Actor-Provider"] == "actron_neo"


@respx.mock
def test_list_instances_parses_fields(client):
    respx.get(f"{BASE}/api/v1/actor/instances").respond(
        json=[{"instance_id": 1, "site_id": 2, "name": None, "timezone": "Australia/Sydney",
               "restore_only": True, "config": {"refresh_token": "x"}}]
    )
    [instance] = client.list_instances()
    assert (instance.instance_id, instance.timezone, instance.restore_only) == (1, "Australia/Sydney", True)
    assert instance.config == {"refresh_token": "x"}
    assert "refresh_token" not in repr(instance)


@respx.mock
def test_create_run_409_raises_conflict(client):
    respx.post(f"{BASE}/api/v1/actor/instances/1/runs").respond(
        409, json={"code": "duplicate_run", "message": "dup", "detail": None}
    )
    with pytest.raises(CoreApiConflict) as caught:
        client.create_run(1, 3, "action", datetime.datetime(2026, 11, 5, tzinfo=UTC))
    assert caught.value.code == "duplicate_run"
    assert isinstance(caught.value, CoreApiError)


@respx.mock
def test_non_200_raises_core_api_error_with_body(client):
    respx.get(f"{BASE}/api/v1/actor/instances/1/schedules").respond(500, text="boom")
    with pytest.raises(CoreApiError, match="500.*boom"):
        client.get_schedules(1)


@respx.mock
def test_malformed_body_raises_core_api_error(client):
    respx.get(f"{BASE}/api/v1/actor/instances/1/context").respond(200, text="not json")
    with pytest.raises(CoreApiError):
        client.get_context(1)


@respx.mock
def test_missing_field_raises_core_api_error(client):
    respx.get(f"{BASE}/api/v1/actor/instances/1/context").respond(json={"now": "2026-11-05T00:00:00Z"})
    with pytest.raises(CoreApiError):
        client.get_context(1)


@respx.mock
def test_transport_failure_raises_core_api_error(client):
    respx.get(f"{BASE}/api/v1/actor/instances").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(CoreApiError, match="Failed to reach"):
        client.list_instances()


@respx.mock
def test_naive_datetime_in_body_is_rejected(client):
    respx.get(f"{BASE}/api/v1/actor/instances/1/runs").respond(json=[{**RUN_JSON, "trigger_at": "2026-11-05T01:00:00"}])
    with pytest.raises(CoreApiError):
        client.list_unfinished_runs(1)


@respx.mock
def test_get_schedules_parses_nested_specs(client):
    respx.get(f"{BASE}/api/v1/actor/instances/1/schedules").respond(json=[SCHEDULE_JSON])
    [schedule] = client.get_schedules(1)
    assert schedule.instance_id == 1
    assert schedule.trigger.type == "time" and schedule.trigger.days_of_week == [0, 1]
    assert schedule.conditions[0].op == "gt"
    assert schedule.conditions[0].enabled is True
    assert schedule.restore.only_if[0].enabled is False
    assert schedule.actions[0].params["zones"] == ["a"]
    assert schedule.restore.when == "after_minutes" and schedule.restore.minutes == 30
    assert schedule.retry.give_up_minutes == 15
    assert schedule.created_at == datetime.datetime(2026, 11, 1, tzinfo=UTC)


@respx.mock
def test_non_bool_condition_flag_is_a_malformed_body(client):
    bad = {**SCHEDULE_JSON, "conditions": [{**SCHEDULE_JSON["conditions"][0], "enabled": "no"}]}
    respx.get(f"{BASE}/api/v1/actor/instances/1/schedules").respond(json=[bad])
    with pytest.raises(CoreApiError, match="Unexpected"):
        client.get_schedules(1)


@respx.mock
def test_get_context_parses_windows(client):
    respx.get(f"{BASE}/api/v1/actor/instances/1/context").respond(json={
        "now": "2026-11-05T00:00:00Z", "timezone": "Australia/Sydney", "outdoor_temp_c": 31.5,
        "battery_soc_pct": None, "price_c_per_kwh": 12.0,
        "windows": [{"window_id": 4, "label": "Peak", "start": "2026-11-05T05:00:00Z", "end": "2026-11-05T09:00:00+00:00"}],
    })
    context = client.get_context(1)
    assert context.battery_soc_pct is None and context.outdoor_temp_c == 31.5
    assert context.windows[0].start == datetime.datetime(2026, 11, 5, 5, tzinfo=UTC)


@respx.mock
def test_patch_discovered_omits_unset_auth_state(client):
    route = respx.patch(f"{BASE}/api/v1/actor/instances/1/discovered-config").respond(204)
    client.patch_discovered(1, config={"device_id": "d"})
    assert json.loads(route.calls.last.request.content) == {"config": {"device_id": "d"}}


@respx.mock
def test_patch_discovered_sends_explicit_null_auth_state(client):
    route = respx.patch(f"{BASE}/api/v1/actor/instances/1/discovered-config").respond(204)
    client.patch_discovered(1, auth_state=None)
    assert json.loads(route.calls.last.request.content) == {"auth_state": None}


@respx.mock
def test_put_capabilities_and_readings_send_bodies(client):
    caps = respx.put(f"{BASE}/api/v1/actor/instances/1/capabilities").respond(204)
    readings = respx.put(f"{BASE}/api/v1/actor/instances/1/readings").respond(204)
    client.put_capabilities(1, {"version": 1})
    client.put_readings(1, {"room_temp_c": 22})
    assert json.loads(caps.calls.last.request.content) == {"version": 1}
    assert json.loads(readings.calls.last.request.content) == {"state": {"room_temp_c": 22}}


@respx.mock
def test_create_run_sends_utc_iso_and_returns_run(client):
    route = respx.post(f"{BASE}/api/v1/actor/instances/1/runs").respond(201, json=RUN_JSON)
    run = client.create_run(1, 3, "action", datetime.datetime(2026, 11, 5, 12, tzinfo=datetime.timezone(datetime.timedelta(hours=11))))
    assert json.loads(route.calls.last.request.content) == {
        "schedule_id": 3, "kind": "action", "trigger_at": "2026-11-05T01:00:00+00:00",
        "parent_run_id": None, "snapshot": None,
    }
    assert isinstance(run, Run) and run.id == 7 and run.outcome == "ok"
    assert run.trigger_at == datetime.datetime(2026, 11, 5, 1, tzinfo=UTC)


def test_create_run_rejects_naive_trigger_at(client):
    with pytest.raises(ValueError):
        client.create_run(1, 3, "action", datetime.datetime(2026, 11, 5))


@respx.mock
def test_update_run_sends_only_given_fields(client):
    route = respx.patch(f"{BASE}/api/v1/actor/instances/1/runs/7").respond(json=RUN_JSON)
    client.update_run(1, 7, "running")
    assert json.loads(route.calls.last.request.content) == {"outcome": "running"}
    client.update_run(1, 7, "failed", message="x", attempts=2, snapshot={"a": 1})
    assert json.loads(route.calls.last.request.content) == {
        "outcome": "failed", "message": "x", "attempts": 2, "snapshot": {"a": 1},
    }


@respx.mock
def test_update_run_sends_steps_only_when_given(client):
    route = respx.patch(f"{BASE}/api/v1/actor/instances/1/runs/7").respond(json=RUN_JSON)
    client.update_run(1, 7, "ok")
    assert "steps" not in json.loads(route.calls.last.request.content)
    steps = [{"index": 0, "action_id": "set_mode", "params": {}, "outcome": "ok", "message": None, "attempts": 1}]
    client.update_run(1, 7, "partial", steps=steps)
    assert json.loads(route.calls.last.request.content) == {"outcome": "partial", "steps": steps}


@respx.mock
def test_update_run_409_raises_conflict(client):
    respx.patch(f"{BASE}/api/v1/actor/instances/1/runs/7").respond(409, json={"code": "run_finished", "message": "m"})
    with pytest.raises(CoreApiConflict) as caught:
        client.update_run(1, 7, "ok")
    assert caught.value.code == "run_finished"


@respx.mock
def test_run_listing_filters(client):
    route = respx.get(f"{BASE}/api/v1/actor/instances/1/runs").respond(json=[RUN_JSON])
    client.list_unfinished_runs(1)
    assert route.calls.last.request.url.params["unfinished"] == "true"
    client.list_pending_test_runs(1)
    assert route.calls.last.request.url.params["pending_test"] == "true"
    runs = client.list_recent_runs(1, datetime.datetime(2026, 11, 4, tzinfo=UTC))
    assert route.calls.last.request.url.params["since"] == "2026-11-04T00:00:00+00:00"
    assert runs[0].snapshot == {"mode": "COOL"}
