"""The real CoreApiClient against the shared fixtures in contracts/actor-api (see its README).

Core API's test_actor_contract.py checks that its routes answer with bodies
of the same shape, so a change on either side that breaks the other fails."""

import datetime
import json
from pathlib import Path
from typing import Any

import respx
from actron_neo_api import ActronAirStatus

from actron_neo_actor.capabilities import build_capabilities
from actron_neo_actor.core_api_client import CoreApiClient
from actron_neo_actor.device_state import state_from_status
from tests.fakes import START, raw_state

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts" / "actor-api"
BASE = "http://core/api/v1/actor"


def fixture(name: str) -> Any:
    return json.loads((CONTRACTS / name).read_text())


def _kind(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    return {str: "string", dict: "object", list: "array", type(None): "null"}[type(value)]


def assert_same_shape(actual: object, expected: object, where: str = "$") -> None:
    """The same check as core-api/tests/test_actor_contract.py: keys and JSON value types, not values."""
    assert _kind(actual) == _kind(expected), f"{where}: {_kind(actual)} is not {_kind(expected)}"
    if isinstance(actual, dict) and isinstance(expected, dict):
        assert set(actual) == set(expected), f"{where}: keys {sorted(actual)} are not {sorted(expected)}"
        for key in expected:
            assert_same_shape(actual[key], expected[key], f"{where}.{key}")
    elif isinstance(actual, list) and isinstance(expected, list) and expected:
        if len(actual) == len(expected):
            pairs = list(zip(actual, expected, strict=True))
        else:
            pairs = [(a, expected[0]) for a in actual]
        for i, (a, e) in enumerate(pairs):
            assert_same_shape(a, e, f"{where}[{i}]")


def client() -> CoreApiClient:
    return CoreApiClient("http://core", "k")


def status() -> ActronAirStatus:
    return ActronAirStatus.model_validate({"isOnline": True, "lastKnownState": raw_state()})


@respx.mock
def test_instances_list_entry_parses():
    body = fixture("instances_list_entry.json")
    respx.get(f"{BASE}/instances").respond(json=[body])
    [instance] = client().list_instances()
    assert (instance.instance_id, instance.timezone, instance.restore_only) == (1, "Australia/Sydney", False)


@respx.mock
def test_schedules_list_entry_parses():
    respx.get(f"{BASE}/instances/1/schedules").respond(json=[fixture("schedules_list_entry.json")])
    [schedule] = client().get_schedules(1)
    assert (schedule.trigger.days_of_week, schedule.actions[0].id) == ([0, 1, 2, 3, 4], "set_zones")
    assert schedule.restore.early_if[0].target == "Family"
    assert schedule.created_at == datetime.datetime(2026, 11, 1, tzinfo=datetime.UTC)


@respx.mock
def test_context_parses():
    respx.get(f"{BASE}/instances/1/context").respond(json=fixture("context.json"))
    context = client().get_context(1)
    assert (context.outdoor_temp_c, context.price_c_per_kwh) == (31.5, 42.1)
    assert context.windows[0].window_id == 7


@respx.mock
def test_run_created_parses():
    respx.post(f"{BASE}/instances/1/runs").respond(201, json=fixture("run_created.json"))
    run = client().create_run(1, 1, "action", START, snapshot={"is_on": True, "mode": "COOL"})
    assert (run.id, run.outcome, run.started_at, run.snapshot) == (12, "pending", None, {"is_on": True, "mode": "COOL"})


@respx.mock
def test_run_list_item_parses():
    respx.get(f"{BASE}/instances/1/runs").respond(json=[fixture("run_list_item.json")])
    [run] = client().list_recent_runs(1, START)
    assert (run.kind, run.parent_run_id, run.message) == ("restore", 12, "restored power")
    assert run.finished_at == datetime.datetime(2026, 11, 5, 7, 0, 20, tzinfo=datetime.UTC)


@respx.mock
def test_capabilities_request_has_the_contract_shape():
    route = respx.put(f"{BASE}/instances/1/capabilities").respond(204)
    client().put_capabilities(1, build_capabilities(status()))
    assert_same_shape(json.loads(route.calls.last.request.content), fixture("capabilities_request.json"))


@respx.mock
def test_readings_request_has_the_contract_shape():
    route = respx.put(f"{BASE}/instances/1/readings").respond(204)
    client().put_readings(1, state_from_status(status(), START).to_payload())
    assert_same_shape(json.loads(route.calls.last.request.content), fixture("readings_request.json"))


@respx.mock
def test_schedule_reads_actions_list():
    body = fixture("schedules_list_entry.json")
    body["actions"].append({"id": "set_mode", "params": {"mode": "COOL"}})
    respx.get(f"{BASE}/instances/1/schedules").respond(json=[body])
    [schedule] = client().get_schedules(1)
    assert [a.id for a in schedule.actions] == ["set_zones", "set_mode"]
    assert schedule.actions[0].id == "set_zones"


@respx.mock
def test_schedule_falls_back_to_single_action():
    body = fixture("schedules_list_entry.json")
    del body["actions"]
    respx.get(f"{BASE}/instances/1/schedules").respond(json=[body])
    [schedule] = client().get_schedules(1)
    assert [a.id for a in schedule.actions] == ["set_zones"]


@respx.mock
def test_run_parses_steps_and_old_runs_without_them():
    step = {"index": 0, "action_id": "set_zones", "params": {}, "outcome": "ok", "message": None, "attempts": 1}
    body = {**fixture("run_list_item.json"), "steps": [step]}
    old = {k: v for k, v in fixture("run_list_item.json").items() if k != "steps"}
    respx.get(f"{BASE}/instances/1/runs").respond(json=[body, old, fixture("run_created.json")])
    with_steps, without, null = client().list_recent_runs(1, START)
    assert (with_steps.steps, without.steps, null.steps) == ([step], None, None)
