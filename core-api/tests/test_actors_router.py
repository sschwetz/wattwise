from __future__ import annotations

import datetime
import decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core_api.actor_schemas import ActorInstanceView, instance_view, shown_auth_state
from core_api.deps import get_current_user, get_db_session
from core_api.errors import install_error_handlers
from core_api.models import ActorInstance, ActorRun, ActorSchedule, RunKind, RunOutcome
from core_api.models.billing import DemandWindow, SiteTariff
from core_api.models.identity import Site, SiteAccess, User
from core_api.routers.actors import router
from tests.conftest import UTC

CAPS = {
    "version": 1,
    "actions": [
        {"id": "set_mode", "label": "Set mode", "restorable": True,
         "params": [{"name": "mode", "label": "Mode", "type": "choice",
                     "options": [{"value": "cool", "label": "Cool"}, {"value": "off", "label": "Off"}]}]},
    ],
    "readings": [],
}
ACTION = {"id": "set_mode", "params": {"mode": "off"}}


class Who:
    user: User


@pytest.fixture()
def who(user):
    holder = Who()
    holder.user = user
    return holder


@pytest.fixture()
def http(db, who):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: who.user
    return TestClient(app)


def _base(site) -> str:
    return f"/api/v1/sites/{site.id}/actors"


def _instance(db, site, caps=CAPS, **kw) -> ActorInstance:
    i = ActorInstance(site_id=site.id, provider="actron_neo", name="AC", config={"refresh_token": "secret-token"},
                      capabilities=caps, **kw)
    db.add(i)
    db.commit()
    return i


def _window(db, site) -> DemandWindow:
    tariff = SiteTariff(site_id=site.id, effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
                        service_availability_c_per_day=decimal.Decimal("100"))
    db.add(tariff)
    db.commit()
    w = DemandWindow(site_tariff_id=tariff.id, label="Peak", months=list(range(1, 13)), days_of_week=list(range(7)),
                     start_time=datetime.time(16), end_time=datetime.time(20),
                     rate_c_per_kw_per_day=decimal.Decimal("50"))
    db.add(w)
    db.commit()
    return w


def _body(instance, **kw) -> dict:
    body = {"instance_id": instance.id, "name": "Peak off", "trigger": {"type": "time", "time": "10:00"},
            "actions": [ACTION], "restore": {"when": "after_minutes", "minutes": 60}}
    body.update(kw)
    return body


def _db_schedule(db, instance, name="s", enabled=True) -> ActorSchedule:
    s = ActorSchedule(instance_id=instance.id, name=name, enabled=enabled, trigger={"type": "time", "time": "10:00"},
                      conditions=[], actions=[ACTION], restore={"enabled": True, "when": "after_minutes", "minutes": 60},
                      retry={})
    db.add(s)
    db.commit()
    return s


def _open_run(db, schedule) -> ActorRun:
    run = ActorRun(schedule_id=schedule.id, kind=RunKind.ACTION, trigger_at=datetime.datetime.now(UTC),
                   outcome=RunOutcome.OK)
    db.add(run)
    db.commit()
    return run


@pytest.fixture()
def viewer(db, site, who):
    v = User(email="viewer@example.com", password_hash="x")
    db.add(v)
    db.commit()
    db.add(SiteAccess(site_id=site.id, user_id=v.id))
    db.commit()
    who.user = v
    return v


def test_viewer_can_read_not_write(http, db, site, viewer):
    inst = _instance(db, site)
    assert http.get(f"{_base(site)}/instances").status_code == 200
    assert http.get(f"{_base(site)}/schedules").status_code == 200
    assert http.post(f"{_base(site)}/instances", json={"provider": "p", "name": "x"}).status_code == 403
    assert http.post(f"{_base(site)}/schedules", json=_body(inst)).status_code == 403
    assert http.delete(f"{_base(site)}/instances/{inst.id}").status_code == 403


def test_config_never_returned(http, db, site):
    inst = _instance(db, site)
    listed = http.get(f"{_base(site)}/instances").json()
    created = http.post(f"{_base(site)}/instances", json={"provider": "actron_neo", "name": "New"}).json()
    patched = http.patch(f"{_base(site)}/instances/{inst.id}", json={"name": "Renamed"}).json()
    for body in (*listed, created, patched):
        assert "config" not in body
    assert "secret-token" not in http.get(f"{_base(site)}/instances").text
    assert created["enabled"] is True and patched["name"] == "Renamed"
    assert set(patched) == {"id", "site_id", "provider", "name", "enabled", "capabilities",
                            "capabilities_updated_at", "latest_readings", "last_seen_at", "auth_state"}


def test_instance_provider_pattern(http, site):
    r = http.post(f"{_base(site)}/instances", json={"provider": "Bad Provider!", "name": "x"})
    assert r.status_code == 422


def test_schedule_validated_against_capabilities(http, db, site):
    inst = _instance(db, site)
    ok = http.post(f"{_base(site)}/schedules", json=_body(inst))
    assert ok.status_code == 201
    stored = db.get(ActorSchedule, ok.json()["id"])
    assert stored.actions == [ACTION] and stored.restore["when"] == "after_minutes"
    bad = http.post(f"{_base(site)}/schedules", json=_body(inst, actions=[{"id": "nope", "params": {}}]))
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid_schedule")
    bad_cond = http.post(f"{_base(site)}/schedules", json=_body(
        inst, restore={"when": "after_minutes", "minutes": 60,
                       "only_if": [{"reading": "mystery", "op": "gt", "value": 1}]}))
    assert (bad_cond.status_code, bad_cond.json()["code"]) == (422, "invalid_schedule")
    site_cond = http.post(f"{_base(site)}/schedules", json=_body(
        inst, conditions=[{"reading": "outdoor_temp_c", "op": "gt", "value": 30}]))
    assert site_cond.status_code == 201


def test_schedule_needs_declared_capabilities(http, db, site):
    inst = _instance(db, site, caps=None)
    r = http.post(f"{_base(site)}/schedules", json=_body(inst))
    assert (r.status_code, r.json()["code"]) == (422, "no_capabilities")


def test_schedule_rejects_foreign_window_id(http, db, site, user):
    inst = _instance(db, site)
    other = Site(owner_user_id=user.id, name="Other", timezone="Australia/Sydney")
    db.add(other)
    db.commit()
    foreign = _window(db, other)
    own = _window(db, site)
    trigger = {"type": "window_start", "window_id": foreign.id}
    r = http.post(f"{_base(site)}/schedules", json=_body(inst, trigger=trigger))
    assert (r.status_code, r.json()["code"]) == (422, "invalid_schedule")
    trigger = {"type": "window_end", "window_id": own.id}
    assert http.post(f"{_base(site)}/schedules", json=_body(inst, trigger=trigger)).status_code == 201
    trigger = {"type": "window_start", "window_id": own.id}
    body = _body(inst, trigger=trigger, restore={"when": "window_end"})
    assert http.post(f"{_base(site)}/schedules", json=body).status_code == 201


def test_patch_schedule_revalidates(http, db, site):
    inst = _instance(db, site)
    sid = http.post(f"{_base(site)}/schedules", json=_body(inst)).json()["id"]
    r = http.patch(f"{_base(site)}/schedules/{sid}", json={"actions": [{"id": "nope", "params": {}}]})
    assert r.status_code == 422
    r = http.patch(f"{_base(site)}/schedules/{sid}", json={"name": "Renamed", "notify_email": True})
    assert r.status_code == 200 and r.json()["name"] == "Renamed" and r.json()["actions"] == [ACTION] and r.json()["action"] == ACTION


OFF = {"reading": "mystery", "op": "gt", "value": 1, "enabled": False}
ON = {"reading": "outdoor_temp_c", "op": "gt", "value": 30}


MONTHS_ROW = {"kind": "months", "op": "in", "values": [12, 1, 2]}
DAYS_ROW = {"kind": "days", "op": "not_in", "values": [5, 6]}
WEEK_ROW = {"kind": "week", "parity": "odd"}
IDLE_ROW = {"kind": "demand_window", "op": "not_active", "window_id": None}


def _post(http, site, inst, **kw):
    return http.post(f"{_base(site)}/schedules", json=_body(inst, **kw))


def test_create_schedule_with_calendar_conditions(http, db, site):
    inst = _instance(db, site)
    r = _post(http, site, inst, conditions=[MONTHS_ROW, DAYS_ROW, WEEK_ROW, IDLE_ROW])
    assert r.status_code == 201
    stored = db.get(ActorSchedule, r.json()["id"]).conditions
    assert stored[0] == {**MONTHS_ROW, "enabled": True}
    assert [c["kind"] for c in stored] == ["months", "days", "week", "demand_window"]


def test_reading_condition_is_stored_without_kind(http, db, site):
    inst = _instance(db, site)
    r = _post(http, site, inst, conditions=[ON])
    assert r.status_code == 201
    assert "kind" not in db.get(ActorSchedule, r.json()["id"]).conditions[0]


def test_ticked_months_row_needs_values(http, db, site):
    inst = _instance(db, site)
    empty = {"kind": "months", "op": "in", "values": []}
    assert _post(http, site, inst, conditions=[empty]).status_code == 422
    assert _post(http, site, inst, conditions=[{**empty, "enabled": False}]).status_code == 201


@pytest.mark.parametrize("row", [
    {"kind": "months", "op": "in", "values": [13]},
    {"kind": "months", "op": "in", "values": [0]},
    {"kind": "days", "op": "in", "values": [7]},
    {"kind": "week", "parity": "third"},
    {"kind": "moon"},
])
def test_calendar_condition_ranges_and_kinds_rejected(http, db, site, row):
    inst = _instance(db, site)
    assert _post(http, site, inst, conditions=[row]).status_code == 422


def test_condition_window_must_belong_to_site(http, db, site, user):
    inst = _instance(db, site)
    other = Site(owner_user_id=user.id, name="Other", timezone="Australia/Sydney")
    db.add(other)
    db.commit()
    foreign, own = _window(db, other), _window(db, site)
    row = {"kind": "demand_window", "op": "active", "window_id": foreign.id}
    r = _post(http, site, inst, conditions=[row])
    assert (r.status_code, r.json()["code"]) == (422, "invalid_schedule")
    assert _post(http, site, inst, conditions=[{**row, "window_id": own.id}]).status_code == 201
    assert _post(http, site, inst, conditions=[{**row, "window_id": 999999, "enabled": False}]).status_code == 201


def test_calendar_rows_in_restore_conditions(http, db, site):
    inst = _instance(db, site)
    restore = {"when": "after_minutes", "minutes": 60, "only_if": [WEEK_ROW], "early_if": [DAYS_ROW]}
    assert _post(http, site, inst, restore=restore).status_code == 201


def test_condition_enabled_flag_round_trips(http, db, site):
    inst = _instance(db, site)
    body = _body(inst, conditions=[OFF, ON], restore={"when": "after_minutes", "minutes": 60, "only_if": [OFF],
                                                      "early_if": [OFF, ON]})
    created = http.post(f"{_base(site)}/schedules", json=body)
    assert created.status_code == 201
    assert [c["enabled"] for c in created.json()["conditions"]] == [False, True]
    assert [c["enabled"] for c in created.json()["restore"]["early_if"]] == [False, True]
    [listed] = http.get(f"{_base(site)}/schedules").json()
    assert listed["conditions"] == created.json()["conditions"]
    assert listed["restore"] == created.json()["restore"]
    assert listed["restore"]["only_if"][0]["enabled"] is False
    flipped = {**ON, "enabled": False}
    r = http.patch(f"{_base(site)}/schedules/{listed['id']}", json={"conditions": [flipped]})
    assert r.status_code == 200 and r.json()["conditions"][0]["enabled"] is False
    assert db.get(ActorSchedule, listed["id"]).conditions[0]["enabled"] is False


def test_enabled_condition_with_undeclared_reading_is_422(http, db, site):
    inst = _instance(db, site)
    r = http.post(f"{_base(site)}/schedules", json=_body(inst, conditions=[{**OFF, "enabled": True}]))
    assert (r.status_code, r.json()["code"]) == (422, "invalid_schedule")


def test_legacy_condition_without_flag_is_returned_enabled(http, db, site):
    inst = _instance(db, site)
    s = _db_schedule(db, inst)
    s.conditions = [ON]
    s.restore = {"enabled": True, "when": "after_minutes", "minutes": 60, "only_if": [ON], "early_if": [ON]}
    db.commit()
    [listed] = http.get(f"{_base(site)}/schedules").json()
    assert listed["conditions"] == [{**ON, "enabled": True}]
    assert listed["restore"]["only_if"] == listed["restore"]["early_if"] == [{**ON, "enabled": True}]
    assert db.get(ActorSchedule, s.id).conditions == [ON]


def test_delete_refused_while_unfinished_run(http, db, site):
    inst = _instance(db, site)
    sched = _db_schedule(db, inst)
    _open_run(db, sched)
    for path in (f"/schedules/{sched.id}", f"/instances/{inst.id}"):
        r = http.delete(f"{_base(site)}{path}")
        assert (r.status_code, r.json()["code"]) == (409, "unit_held_off")


def test_delete_succeeds_when_idle(http, db, site):
    inst = _instance(db, site)
    sched = _db_schedule(db, inst)
    assert http.delete(f"{_base(site)}/schedules/{sched.id}").status_code == 204
    assert http.delete(f"{_base(site)}/instances/{inst.id}").status_code == 204


def test_test_run_created_pending(http, db, site):
    inst = _instance(db, site)
    sched = _db_schedule(db, inst)
    before = datetime.datetime.now(UTC)
    r = http.post(f"{_base(site)}/schedules/{sched.id}/test")
    assert r.status_code == 201
    body = r.json()
    assert (body["kind"], body["outcome"], body["schedule_id"]) == ("test", "pending", sched.id)
    assert datetime.datetime.fromisoformat(body["trigger_at"]) >= before


def test_test_run_refused_for_disabled_instance_or_no_capabilities(http, db, site):
    off = _instance(db, site, enabled=False)
    r = http.post(f"{_base(site)}/schedules/{_db_schedule(db, off).id}/test")
    assert (r.status_code, r.json()["code"]) == (409, "instance_disabled")
    bare = _instance(db, site, caps=None)
    r = http.post(f"{_base(site)}/schedules/{_db_schedule(db, bare).id}/test")
    assert (r.status_code, r.json()["code"]) == (409, "no_capabilities")


def test_disable_schedule_with_unfinished_run_keeps_run(http, db, site):
    inst = _instance(db, site)
    sched = _db_schedule(db, inst)
    run = _open_run(db, sched)
    assert http.patch(f"{_base(site)}/schedules/{sched.id}", json={"enabled": False}).json()["enabled"] is False
    assert http.patch(f"{_base(site)}/instances/{inst.id}", json={"enabled": False}).json()["enabled"] is False
    db.expire_all()
    assert db.get(ActorRun, run.id).outcome == RunOutcome.OK


def test_runs_listing_newest_first_with_schedule_name(http, db, site):
    inst = _instance(db, site)
    sched = _db_schedule(db, inst, name="Evening")
    now = datetime.datetime.now(UTC)
    for minutes in (1, 2, 3):
        db.add(ActorRun(schedule_id=sched.id, kind=RunKind.ACTION, trigger_at=now + datetime.timedelta(minutes=minutes),
                        outcome=RunOutcome.OK))
    db.commit()
    runs = http.get(f"{_base(site)}/runs", params={"limit": 2}).json()
    assert len(runs) == 2 and runs[0]["trigger_at"] > runs[1]["trigger_at"]
    assert runs[0]["schedule_name"] == "Evening"
    assert http.get(f"{_base(site)}/runs", params={"limit": 201}).status_code == 422
    per = http.get(f"{_base(site)}/schedules/{sched.id}/runs").json()
    assert len(per) == 3 and per[0]["trigger_at"] > per[2]["trigger_at"]


def test_cross_site_ids_are_404(http, db, site, user):
    other = Site(owner_user_id=user.id, name="Other", timezone="Australia/Sydney")
    db.add(other)
    db.commit()
    inst = _instance(db, other)
    sched = _db_schedule(db, inst)
    base = _base(site)
    assert http.patch(f"{base}/instances/{inst.id}", json={"name": "x"}).status_code == 404
    assert http.delete(f"{base}/instances/{inst.id}").status_code == 404
    assert http.patch(f"{base}/schedules/{sched.id}", json={"name": "x"}).status_code == 404
    assert http.delete(f"{base}/schedules/{sched.id}").status_code == 404
    assert http.get(f"{base}/schedules/{sched.id}/runs").status_code == 404
    assert http.post(f"{base}/schedules/{sched.id}/test").status_code == 404
    assert http.post(f"{base}/schedules", json=_body(inst)).status_code == 404


def _finished_pair(db, schedule) -> None:
    """An action run closed by a restore child, so the unit is not held off."""
    now = datetime.datetime.now(UTC)
    action = ActorRun(schedule_id=schedule.id, kind=RunKind.ACTION, trigger_at=now, outcome=RunOutcome.OK)
    db.add(action)
    db.commit()
    db.add(ActorRun(schedule_id=schedule.id, kind=RunKind.RESTORE, trigger_at=now, outcome=RunOutcome.OK,
                    parent_run_id=action.id))
    db.commit()


def test_delete_schedule_with_restore_child_run(http, db, site):
    sched = _db_schedule(db, _instance(db, site))
    _finished_pair(db, sched)
    assert http.delete(f"{_base(site)}/schedules/{sched.id}").status_code == 204
    assert db.query(ActorRun).count() == 0


def test_delete_instance_with_restore_child_run(http, db, site):
    inst = _instance(db, site)
    _finished_pair(db, _db_schedule(db, inst))
    assert http.delete(f"{_base(site)}/instances/{inst.id}").status_code == 204
    assert db.query(ActorRun).count() == 0


def test_schedule_delete_ignores_sibling_unfinished_run(http, db, site):
    inst = _instance(db, site)
    idle = _db_schedule(db, inst, name="idle")
    busy = _db_schedule(db, inst, name="busy")
    _open_run(db, busy)
    assert http.delete(f"{_base(site)}/schedules/{idle.id}").status_code == 204
    r = http.delete(f"{_base(site)}/schedules/{busy.id}")
    assert (r.status_code, r.json()["code"]) == (409, "unit_held_off")


def _reset_url(site, inst) -> str:
    return f"{_base(site)}/instances/{inst.id}/sign-in/reset"


def _stored_config(db, inst) -> dict:
    db.expire_all()
    return db.get(ActorInstance, inst.id).config


def test_sign_in_reset_viewer_forbidden(http, db, site, viewer):
    inst = _instance(db, site)
    assert http.post(_reset_url(site, inst)).status_code == 403
    assert _stored_config(db, inst)["refresh_token"] == "secret-token"


def test_sign_in_reset_other_site_is_404(http, db, site, user):
    other = Site(owner_user_id=user.id, name="Other", timezone="Australia/Sydney")
    db.add(other)
    db.commit()
    inst = _instance(db, other)
    assert http.post(_reset_url(site, inst)).status_code == 404
    assert _stored_config(db, inst)["refresh_token"] == "secret-token"


def test_sign_in_reset_removes_only_the_token(http, db, site):
    inst = _instance(db, site, auth_state={"needs_sign_in": True})
    inst.config = {"refresh_token": "secret-token", "device_id": "unit-1"}
    db.commit()
    r = http.post(_reset_url(site, inst))
    assert r.status_code == 200
    assert "config" not in r.json() and r.json()["auth_state"] is None
    db.expire_all()
    stored = db.get(ActorInstance, inst.id)
    assert stored.config == {"device_id": "unit-1"}
    assert stored.auth_state is None
    raw = str(db.connection().exec_driver_sql("select config from actor_instances").scalar())
    assert "secret-token" not in raw and "device_id" not in raw


def test_sign_in_reset_is_idempotent(http, db, site):
    inst = _instance(db, site, auth_state={"needs_sign_in": True})
    assert http.post(_reset_url(site, inst)).status_code == 200
    again = http.post(_reset_url(site, inst))
    assert again.status_code == 200 and again.json()["auth_state"] is None
    assert "refresh_token" not in _stored_config(db, inst)


# Editing a schedule that holds the unit off


CAPS_TWO = {**CAPS, "actions": [*CAPS["actions"], {"id": "fan_only", "label": "Fan only", "params": []}]}


def _held_schedule(http, db, site):
    inst = _instance(db, site, caps=CAPS_TWO)
    created = http.post(f"{_base(site)}/schedules", json=_body(inst, restore={"when": "after_minutes", "minutes": 60}))
    sched = db.get(ActorSchedule, created.json()["id"])
    _open_run(db, sched)
    return sched, created.json()


HELD_CHANGES = [
    {"trigger": {"type": "time", "time": "11:00"}},
    {"actions": [{"id": "fan_only", "params": {}}]},
    {"restore": {"when": "after_minutes", "minutes": 30}},
]


@pytest.mark.parametrize("change", HELD_CHANGES)
def test_patch_refused_while_held_off(http, db, site, change):
    sched, _ = _held_schedule(http, db, site)
    r = http.patch(f"{_base(site)}/schedules/{sched.id}", json=change)
    assert (r.status_code, r.json()["code"]) == (409, "unit_held_off")
    assert r.json()["message"]


@pytest.mark.parametrize("change", HELD_CHANGES)
def test_patch_accepted_when_nothing_held_off(http, db, site, change):
    inst = _instance(db, site, caps=CAPS_TWO)
    sid = http.post(f"{_base(site)}/schedules", json=_body(inst)).json()["id"]
    assert http.patch(f"{_base(site)}/schedules/{sid}", json=change).status_code == 200


@pytest.mark.parametrize("change", [
    {"name": "Renamed"},
    {"enabled": False},
    {"retry": {"max_attempts": 5}},
    {"conditions": [ON]},
    {"notify_email": True},
])
def test_patch_other_fields_accepted_while_held_off(http, db, site, change):
    sched, _ = _held_schedule(http, db, site)
    assert http.patch(f"{_base(site)}/schedules/{sched.id}", json=change).status_code == 200


def test_full_body_patch_with_unchanged_spec_accepted_while_held_off(http, db, site):
    sched, body = _held_schedule(http, db, site)
    full = {k: v for k, v in body.items() if k not in ("id", "instance_id", "action")}
    full["name"] = "Renamed"
    r = http.patch(f"{_base(site)}/schedules/{sched.id}", json=full)
    assert r.status_code == 200 and r.json()["name"] == "Renamed"


def test_patch_held_off_by_a_sibling_schedule_is_allowed(http, db, site):
    inst = _instance(db, site, caps=CAPS_TWO)
    _open_run(db, _db_schedule(db, inst, name="busy"))
    sid = http.post(f"{_base(site)}/schedules", json=_body(inst)).json()["id"]
    assert http.patch(f"{_base(site)}/schedules/{sid}", json=HELD_CHANGES[1]).status_code == 200


# Restore at the window end


@pytest.mark.parametrize("trigger", [
    {"type": "time", "time": "10:00"},
    {"type": "price_above", "c_per_kwh": 40},
    "window_end",
])
def test_window_end_restore_needs_a_window_start_trigger(http, db, site, trigger):
    inst = _instance(db, site)
    if trigger == "window_end":
        trigger = {"type": "window_end", "window_id": _window(db, site).id}
    r = http.post(f"{_base(site)}/schedules", json=_body(inst, trigger=trigger, restore={"when": "window_end"}))
    assert (r.status_code, r.json()["code"]) == (422, "invalid_schedule")
    sid = http.post(f"{_base(site)}/schedules", json=_body(inst, trigger=trigger)).json()["id"]
    r = http.patch(f"{_base(site)}/schedules/{sid}", json={"restore": {"when": "window_end"}})
    assert (r.status_code, r.json()["code"]) == (422, "invalid_schedule")
    assert "window" in r.json()["message"]


def test_window_end_restore_with_window_start_trigger_accepted(http, db, site):
    inst = _instance(db, site)
    trigger = {"type": "window_start", "window_id": _window(db, site).id}
    r = http.post(f"{_base(site)}/schedules", json=_body(inst, trigger=trigger, restore={"when": "window_end"}))
    assert r.status_code == 201


def test_window_end_restore_ignored_when_restore_is_off(http, db, site):
    inst = _instance(db, site)
    r = http.post(f"{_base(site)}/schedules", json=_body(inst, restore={"enabled": False, "when": "window_end"}))
    assert r.status_code == 201


# Sign-in reset while the unit is held off


def test_sign_in_reset_allowed_when_sign_in_lost_while_held_off(http, db, site):
    inst = _instance(db, site, auth_state={"needs_sign_in": True})
    inst.config = {"refresh_token": "secret-token", "device_id": "unit-1"}
    db.commit()
    _open_run(db, _db_schedule(db, inst))
    r = http.post(_reset_url(site, inst))
    assert r.status_code == 200 and r.json()["auth_state"] is None
    db.expire_all()
    stored = db.get(ActorInstance, inst.id)
    assert stored.config == {"device_id": "unit-1"}
    assert stored.auth_state is None


@pytest.mark.parametrize("auth_state", [None, {"user_code": "ABCD", "verification_uri": "https://example.com"},
                                        {"needs_sign_in": False}])
def test_sign_in_reset_refused_while_held_off_unless_sign_in_lost(http, db, site, auth_state):
    inst = _instance(db, site, auth_state=auth_state)
    _open_run(db, _db_schedule(db, inst))
    r = http.post(_reset_url(site, inst))
    assert (r.status_code, r.json()["code"]) == (409, "unit_held_off")
    assert _stored_config(db, inst)["refresh_token"] == "secret-token"


def test_delete_still_refused_when_sign_in_lost_while_held_off(http, db, site):
    inst = _instance(db, site, auth_state={"needs_sign_in": True})
    _open_run(db, _db_schedule(db, inst))
    r = http.delete(f"{_base(site)}/instances/{inst.id}")
    assert (r.status_code, r.json()["code"]) == (409, "unit_held_off")


# Test now refusals


def test_test_run_refused_for_disabled_site(http, db, site):
    sched = _db_schedule(db, _instance(db, site))
    site.enabled = False
    db.commit()
    r = http.post(f"{_base(site)}/schedules/{sched.id}/test")
    assert (r.status_code, r.json()["code"]) == (409, "site_disabled")


def test_test_run_refused_for_disabled_schedule(http, db, site):
    sched = _db_schedule(db, _instance(db, site), enabled=False)
    r = http.post(f"{_base(site)}/schedules/{sched.id}/test")
    assert (r.status_code, r.json()["code"]) == (409, "schedule_disabled")


@pytest.mark.parametrize("outcome", [RunOutcome.PENDING, RunOutcome.RUNNING])
def test_test_run_refused_while_one_is_in_progress(http, db, site, outcome):
    sched = _db_schedule(db, _instance(db, site))
    db.add(ActorRun(schedule_id=sched.id, kind=RunKind.TEST, trigger_at=datetime.datetime.now(UTC), outcome=outcome))
    db.commit()
    r = http.post(f"{_base(site)}/schedules/{sched.id}/test")
    assert (r.status_code, r.json()["code"]) == (409, "test_in_progress")


def test_test_run_allowed_after_the_last_one_finished(http, db, site):
    sched = _db_schedule(db, _instance(db, site))
    db.add(ActorRun(schedule_id=sched.id, kind=RunKind.TEST, trigger_at=datetime.datetime.now(UTC),
                    outcome=RunOutcome.OK))
    db.commit()
    assert http.post(f"{_base(site)}/schedules/{sched.id}/test").status_code == 201


def test_time_trigger_needs_a_day(http, db, site):
    inst = _instance(db, site)
    no_days = {"type": "time", "time": "10:00", "days_of_week": []}
    assert http.post(f"{_base(site)}/schedules", json=_body(inst, trigger=no_days)).status_code == 422
    sid = http.post(f"{_base(site)}/schedules", json=_body(inst)).json()["id"]
    r = http.patch(f"{_base(site)}/schedules/{sid}", json={"trigger": no_days})
    assert (r.status_code, r.json()["code"]) == (422, "invalid_schedule")


CODE = "QZXW-7731"
LINK = "https://example.com/device?code=QZXW-7731"
PENDING = {"user_code": CODE, "verification_uri": LINK, "expires_at": "2026-11-05T03:00:00+00:00"}


def test_owner_sees_the_sign_in_code(http, db, site):
    _instance(db, site, auth_state=PENDING)
    assert http.get(f"{_base(site)}/instances").json()[0]["auth_state"] == PENDING


def test_viewer_sees_signing_in_without_the_code_or_link(http, db, site, viewer):
    _instance(db, site, auth_state=PENDING)
    r = http.get(f"{_base(site)}/instances")
    assert r.json()[0]["auth_state"] == {"signing_in": True}
    for secret in (CODE, LINK, "example.com", "user_code", "verification_uri", "expires_at"):
        assert secret not in r.text


def test_viewer_sees_needs_sign_in(http, db, site, viewer):
    _instance(db, site, auth_state={"needs_sign_in": True})
    assert http.get(f"{_base(site)}/instances").json()[0]["auth_state"] == {"needs_sign_in": True}


def test_instance_view_redacts_for_anyone_but_the_owner(db, site, user):
    viewer = User(email="other@example.com", password_hash="x")
    db.add(viewer)
    db.commit()
    inst = _instance(db, site, auth_state=PENDING)
    assert instance_view(inst, site, user).auth_state == PENDING
    shown = instance_view(inst, site, viewer).model_dump_json()
    assert CODE not in shown and LINK not in shown and '"signing_in":true' in shown
    inst.auth_state = None
    assert instance_view(inst, site, viewer).auth_state is None


@pytest.mark.parametrize("stored", ["QZXW-7731", ["QZXW-7731"], 7])
def test_shown_auth_state_hides_a_malformed_value_from_a_viewer(stored):
    assert shown_auth_state(stored, is_owner=False) == {"signing_in": True}


def test_instance_view_cannot_be_built_from_the_row_directly(db, site):
    inst = _instance(db, site, auth_state=PENDING)
    with pytest.raises(ValidationError):
        ActorInstanceView.model_validate(inst)


def test_create_schedule_with_two_actions(http, db, site):
    inst = _instance(db, site, caps=CAPS_TWO)
    second = {"id": "fan_only", "params": {}}
    r = http.post(f"{_base(site)}/schedules", json=_body(inst, actions=[ACTION, second]))
    assert r.status_code == 201
    assert r.json()["actions"] == [ACTION, second]
    assert r.json()["action"] == ACTION
    assert db.get(ActorSchedule, r.json()["id"]).actions == [ACTION, second]


@pytest.mark.parametrize("count", [0, 9])
def test_create_schedule_rejects_zero_and_nine_actions(http, db, site, count):
    inst = _instance(db, site)
    r = http.post(f"{_base(site)}/schedules", json=_body(inst, actions=[ACTION] * count))
    assert (r.status_code, r.json()["code"]) == (422, "invalid_schedule")


def test_create_schedule_rejects_one_invalid_action_of_three(http, db, site):
    inst = _instance(db, site, caps=CAPS_TWO)
    actions = [ACTION, {"id": "fan_only", "params": {}}, {"id": "nope", "params": {}}]
    r = http.post(f"{_base(site)}/schedules", json=_body(inst, actions=actions))
    assert (r.status_code, r.json()["code"]) == (422, "invalid_schedule")
    assert "actions[2]" in r.json()["message"]
    actions[1] = {"id": "fan_only", "params": {"speed": 1}}
    actions[2] = ACTION
    r = http.post(f"{_base(site)}/schedules", json=_body(inst, actions=actions))
    assert (r.status_code, r.json()["code"]) == (422, "invalid_schedule")
    assert "actions[1]" in r.json()["message"]


def test_patch_actions_replaces_whole_list(http, db, site):
    inst = _instance(db, site, caps=CAPS_TWO)
    two = [ACTION, {"id": "fan_only", "params": {}}]
    sid = http.post(f"{_base(site)}/schedules", json=_body(inst, actions=two)).json()["id"]
    only = [{"id": "fan_only", "params": {}}]
    r = http.patch(f"{_base(site)}/schedules/{sid}", json={"actions": only})
    assert r.status_code == 200
    assert r.json()["actions"] == only and r.json()["action"] == only[0]
    assert db.get(ActorSchedule, sid).actions == only


def test_patch_actions_refused_while_held_off(http, db, site):
    sched, _ = _held_schedule(http, db, site)
    r = http.patch(f"{_base(site)}/schedules/{sched.id}", json={"actions": [ACTION, {"id": "fan_only", "params": {}}]})
    assert (r.status_code, r.json()["code"]) == (409, "unit_held_off")


def test_partial_run_blocks_delete_with_unit_held_off(http, db, site):
    inst = _instance(db, site)
    sched = _db_schedule(db, inst)
    run = _open_run(db, sched)
    run.outcome = RunOutcome.PARTIAL
    run.steps = [{"index": 0, "action_id": "a", "params": {}, "outcome": "failed", "message": None, "attempts": 1}]
    db.commit()
    for path in (f"/schedules/{sched.id}", f"/instances/{inst.id}"):
        r = http.delete(f"{_base(site)}{path}")
        assert (r.status_code, r.json()["code"]) == (409, "unit_held_off")
    [listed] = http.get(f"{_base(site)}/runs").json()
    assert listed["outcome"] == "partial" and listed["steps"] == run.steps
