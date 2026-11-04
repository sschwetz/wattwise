from __future__ import annotations

import datetime
import decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
            "action": ACTION}
    body.update(kw)
    return body


def _db_schedule(db, instance, name="s", enabled=True) -> ActorSchedule:
    s = ActorSchedule(instance_id=instance.id, name=name, enabled=enabled, trigger={"type": "time", "time": "10:00"},
                      conditions=[], action=ACTION, restore={"enabled": True}, retry={})
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
    assert stored.action == ACTION and stored.restore["when"] == "window_end"
    bad = http.post(f"{_base(site)}/schedules", json=_body(inst, action={"id": "nope", "params": {}}))
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid_schedule")
    bad_cond = http.post(f"{_base(site)}/schedules", json=_body(
        inst, restore={"only_if": [{"reading": "mystery", "op": "gt", "value": 1}]}))
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


def test_patch_schedule_revalidates(http, db, site):
    inst = _instance(db, site)
    sid = http.post(f"{_base(site)}/schedules", json=_body(inst)).json()["id"]
    r = http.patch(f"{_base(site)}/schedules/{sid}", json={"action": {"id": "nope", "params": {}}})
    assert r.status_code == 422
    r = http.patch(f"{_base(site)}/schedules/{sid}", json={"name": "Renamed", "notify_email": True})
    assert r.status_code == 200 and r.json()["name"] == "Renamed" and r.json()["action"] == ACTION


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
