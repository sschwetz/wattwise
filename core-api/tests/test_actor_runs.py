from __future__ import annotations

import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core_api.deps import get_db_session, get_email_sender
from core_api.errors import install_error_handlers
from core_api.models import ActorInstance, ActorRun, ActorSchedule, RunOutcome
from core_api.models.alerts import Alert
from core_api.routers.actor import router
from tests.conftest import ACTOR_KEY, UTC

PROVIDER = "actron_neo"
HEADERS = {"X-Actor-Key": ACTOR_KEY, "X-Actor-Provider": PROVIDER}
T0 = datetime.datetime(2026, 11, 5, 1, 0, tzinfo=UTC)


@pytest.fixture()
def http(db, fake_email_sender):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_email_sender] = lambda: fake_email_sender
    return TestClient(app)


def _instance(db, site) -> ActorInstance:
    i = ActorInstance(site_id=site.id, provider=PROVIDER, config={})
    db.add(i)
    db.commit()
    return i


def _schedule(db, instance, name="Cool down", notify=False) -> ActorSchedule:
    s = ActorSchedule(instance_id=instance.id, name=name, enabled=True, trigger={"type": "time", "time": "10:00"},
                      conditions=[], actions=[{"id": "set_mode"}], restore={"enabled": True}, retry={},
                      notify_email=notify)
    db.add(s)
    db.commit()
    return s


@pytest.fixture()
def inst(db, site):
    return _instance(db, site)


def _url(inst, suffix="/runs"):
    return f"/api/v1/actor/instances/{inst.id}{suffix}"


def _create(http, inst, sched, kind="action", trigger_at=T0, **extra):
    body = {"schedule_id": sched.id, "kind": kind, "trigger_at": trigger_at.isoformat(), **extra}
    return http.post(_url(inst), json=body, headers=HEADERS)


def test_create_run_then_duplicate_409(http, db, inst):
    s = _schedule(db, inst)
    r = _create(http, inst, s)
    assert r.status_code == 201
    body = r.json()
    assert (body["outcome"], body["kind"], body["attempts"]) == ("pending", "action", 0)
    assert body["trigger_at"].startswith("2026-11-05T01:00:00")
    assert body["started_at"] is None
    dup = _create(http, inst, s)
    assert (dup.status_code, dup.json()["code"]) == (409, "duplicate_run")


def test_create_rejects_naive_trigger_and_foreign_schedule(http, db, site, inst):
    s = _schedule(db, inst)
    naive = http.post(_url(inst), json={"schedule_id": s.id, "kind": "action", "trigger_at": "2026-11-05T01:00:00"},
                      headers=HEADERS)
    assert naive.status_code == 422
    other = _schedule(db, _instance(db, site))
    r = _create(http, inst, other)
    assert (r.status_code, r.json()["code"]) == (404, "not_found")


def test_restore_run_requires_parent_in_same_schedule(http, db, inst):
    s1, s2 = _schedule(db, inst, "a"), _schedule(db, inst, "b")
    parent = _create(http, inst, s1).json()
    no_parent = _create(http, inst, s1, kind="restore")
    assert (no_parent.status_code, no_parent.json()["code"]) == (422, "invalid_parent")
    wrong = _create(http, inst, s2, kind="restore", parent_run_id=parent["id"])
    assert (wrong.status_code, wrong.json()["code"]) == (422, "invalid_parent")
    ok = _create(http, inst, s1, kind="restore", parent_run_id=parent["id"])
    assert (ok.status_code, ok.json()["parent_run_id"]) == (201, parent["id"])


def test_claim_pending_test_run_twice_second_409(http, db, inst):
    s = _schedule(db, inst)
    run = _create(http, inst, s, kind="test").json()
    first = http.patch(_url(inst, f"/runs/{run['id']}"), json={"outcome": "running"}, headers=HEADERS)
    assert first.status_code == 200
    assert first.json()["started_at"] is not None
    second = http.patch(_url(inst, f"/runs/{run['id']}"), json={"outcome": "running"}, headers=HEADERS)
    assert (second.status_code, second.json()["code"]) == (409, "run_not_pending")


def test_finish_sets_finished_at_and_is_final(http, db, inst):
    s = _schedule(db, inst)
    run = _create(http, inst, s).json()
    path = _url(inst, f"/runs/{run['id']}")
    http.patch(path, json={"outcome": "running"}, headers=HEADERS)
    done = http.patch(path, json={"outcome": "ok", "message": "done", "attempts": 2, "snapshot": {"m": 1}},
                      headers=HEADERS).json()
    assert done["finished_at"] is not None and done["started_at"] is not None
    assert (done["message"], done["attempts"], done["snapshot"]) == ("done", 2, {"m": 1})
    again = http.patch(path, json={"outcome": "failed"}, headers=HEADERS)
    assert (again.status_code, again.json()["code"]) == (409, "run_finished")


def test_run_of_other_instance_is_404(http, db, site, inst):
    other = _instance(db, site)
    run = _create(http, inst, _schedule(db, inst)).json()
    r = http.patch(_url(other, f"/runs/{run['id']}"), json={"outcome": "running"}, headers=HEADERS)
    assert r.status_code == 404


def _fail(http, inst, s, message="device offline"):
    run = _create(http, inst, s).json()
    return http.patch(_url(inst, f"/runs/{run['id']}"), json={"outcome": "failed", "message": message},
                      headers=HEADERS)


def test_failed_run_emails_owner_when_notify_on(http, db, inst, user, site, fake_email_sender):
    s = _schedule(db, inst, notify=True)
    path_resp = _fail(http, inst, s)
    assert path_resp.status_code == 200
    assert len(fake_email_sender.sent) == 1
    to, subject, body = fake_email_sender.sent[0]
    assert to == user.email
    assert "Cool down" in subject
    assert "device offline" in body and site.name in body
    repeat = http.patch(_url(inst, f"/runs/{path_resp.json()['id']}"), json={"outcome": "failed"}, headers=HEADERS)
    assert repeat.status_code == 409
    assert len(fake_email_sender.sent) == 1


def test_unsent_notification_email_raises_a_site_alert_until_one_sends(http, db, inst, site, fake_email_sender, monkeypatch):
    s = _schedule(db, inst, notify=True)
    monkeypatch.setattr(fake_email_sender, "send", lambda **kw: (_ for _ in ()).throw(RuntimeError("relay down")))
    _fail(http, inst, s)
    assert [(a.subject_kind, a.subject_id, a.code) for a in db.query(Alert).all()] == [("site", site.id, "notify_email")]
    monkeypatch.undo()
    _fail(http, inst, _schedule(db, inst, notify=True))
    assert db.query(Alert).count() == 0


def test_failed_run_no_email_when_notify_off(http, db, inst, fake_email_sender):
    _fail(http, inst, _schedule(db, inst, notify=False))
    assert fake_email_sender.sent == []


def test_unfinished_listing_includes_action_without_restore(http, db, inst):
    s = _schedule(db, inst)
    open_run = _create(http, inst, s).json()
    closed = _create(http, inst, s, trigger_at=T0 + datetime.timedelta(hours=1)).json()
    rest = _create(http, inst, s, kind="restore", parent_run_id=closed["id"]).json()
    http.patch(_url(inst, f"/runs/{rest['id']}"), json={"outcome": "ok"}, headers=HEADERS)
    r = http.get(_url(inst), params={"unfinished": "true"}, headers=HEADERS)
    assert [x["id"] for x in r.json()] == [open_run["id"]]


def test_pending_test_listing(http, db, inst):
    s = _schedule(db, inst)
    t = _create(http, inst, s, kind="test").json()
    _create(http, inst, s)
    r = http.get(_url(inst), params={"pending_test": "true"}, headers=HEADERS)
    assert [x["id"] for x in r.json()] == [t["id"]]


def test_since_listing_returns_all_kinds_ordered(http, db, inst):
    s = _schedule(db, inst)
    late = _create(http, inst, s, trigger_at=T0 + datetime.timedelta(hours=2)).json()
    test = _create(http, inst, s, kind="test", trigger_at=T0 + datetime.timedelta(hours=1)).json()
    _create(http, inst, s, trigger_at=T0 - datetime.timedelta(hours=1))
    r = http.get(_url(inst), params={"since": T0.isoformat()}, headers=HEADERS)
    assert [x["id"] for x in r.json()] == [test["id"], late["id"]]


@pytest.mark.parametrize("params", [{}, {"unfinished": "true", "pending_test": "true"},
                                     {"since": T0.isoformat(), "unfinished": "true"}])
def test_listing_needs_exactly_one_filter(http, inst, params):
    r = http.get(_url(inst), params=params, headers=HEADERS)
    assert r.status_code == 422


def test_duplicate_detected_across_utc_offsets(http, db, inst):
    s = _schedule(db, inst)
    assert _create(http, inst, s).status_code == 201
    same_instant = datetime.datetime(2026, 11, 5, 11, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=10)))
    dup = _create(http, inst, s, trigger_at=same_instant)
    assert (dup.status_code, dup.json()["code"]) == (409, "duplicate_run")


def test_lost_terminal_race_changes_nothing_and_sends_no_email(http, db, inst, fake_email_sender, monkeypatch):
    from sqlalchemy import update
    from sqlalchemy.orm.attributes import set_committed_value

    from core_api.routers import actor as actor_module

    s = _schedule(db, inst, notify=True)
    run = _create(http, inst, s).json()
    finished = datetime.datetime(2026, 11, 5, 2, 0, tzinfo=UTC)
    real = actor_module._instance_run

    def racing(db_, instance, run_id):
        found = real(db_, instance, run_id)
        # Another request finishes the run after this handler has read it.
        db_.execute(update(ActorRun).where(ActorRun.id == run_id).values(
            outcome=RunOutcome.FAILED, message="winner", snapshot={"w": 1}, finished_at=finished,
        ).execution_options(synchronize_session=False))
        db_.commit()
        # The handler still holds the state it read before the other request won.
        set_committed_value(found, "outcome", RunOutcome.PENDING)
        return found

    monkeypatch.setattr(actor_module, "_instance_run", racing)
    r = http.patch(_url(inst, f"/runs/{run['id']}"),
                   json={"outcome": "failed", "message": "loser", "snapshot": {"l": 1}}, headers=HEADERS)
    assert (r.status_code, r.json()["code"]) == (409, "run_finished")
    assert fake_email_sender.sent == []
    db.expire_all()
    row = db.get(ActorRun, run["id"])
    assert (row.message, row.snapshot) == ("winner", {"w": 1})
    assert row.finished_at.replace(tzinfo=UTC) == finished


def _finished(http, inst, sched, outcome="ok"):
    run = _create(http, inst, sched).json()
    path = _url(inst, f"/runs/{run['id']}")
    http.patch(path, json={"outcome": "running"}, headers=HEADERS)
    return path, http.patch(path, json={"outcome": outcome}, headers=HEADERS).json()


def test_ok_run_can_become_overridden_and_keeps_finished_at(http, db, inst):
    path, done = _finished(http, inst, _schedule(db, inst))
    r = http.patch(path, json={"outcome": "overridden", "message": "owner changed the unit"}, headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert (body["outcome"], body["message"]) == ("overridden", "owner changed the unit")
    assert body["finished_at"] == done["finished_at"]


def test_ok_run_cannot_become_failed(http, db, inst):
    path, _ = _finished(http, inst, _schedule(db, inst))
    r = http.patch(path, json={"outcome": "failed"}, headers=HEADERS)
    assert (r.status_code, r.json()["code"]) == (409, "run_finished")


@pytest.mark.parametrize("outcome", ["ok", "overridden", "failed"])
def test_overridden_run_is_final(http, db, inst, outcome):
    path, _ = _finished(http, inst, _schedule(db, inst))
    http.patch(path, json={"outcome": "overridden"}, headers=HEADERS)
    r = http.patch(path, json={"outcome": outcome}, headers=HEADERS)
    assert (r.status_code, r.json()["code"]) == (409, "run_finished")


@pytest.mark.parametrize("outcome", ["skipped", "failed"])
def test_only_ok_runs_can_become_overridden(http, db, inst, outcome):
    path, _ = _finished(http, inst, _schedule(db, inst), outcome)
    r = http.patch(path, json={"outcome": "overridden"}, headers=HEADERS)
    assert (r.status_code, r.json()["code"]) == (409, "run_finished")


def test_lost_override_race_is_409(http, db, inst, monkeypatch):
    from sqlalchemy import update
    from sqlalchemy.orm.attributes import set_committed_value

    from core_api.routers import actor as actor_module

    path, done = _finished(http, inst, _schedule(db, inst))
    real = actor_module._instance_run

    def racing(db_, instance, run_id):
        found = real(db_, instance, run_id)
        # Another request overrides the run after this handler has read it as ok.
        db_.execute(update(ActorRun).where(ActorRun.id == run_id).values(
            outcome=RunOutcome.OVERRIDDEN, message="winner").execution_options(synchronize_session=False))
        db_.commit()
        set_committed_value(found, "outcome", RunOutcome.OK)
        return found

    monkeypatch.setattr(actor_module, "_instance_run", racing)
    r = http.patch(path, json={"outcome": "overridden", "message": "loser"}, headers=HEADERS)
    assert (r.status_code, r.json()["code"]) == (409, "run_finished")
    db.expire_all()
    assert db.get(ActorRun, done["id"]).message == "winner"


@pytest.mark.parametrize("kind", ["action", "test"])
def test_parent_run_id_only_for_restore_runs(http, db, inst, kind):
    s = _schedule(db, inst)
    parent = _create(http, inst, s, trigger_at=T0 - datetime.timedelta(hours=1)).json()
    r = _create(http, inst, s, kind=kind, parent_run_id=parent["id"])
    assert (r.status_code, r.json()["code"]) == (422, "invalid_parent")


def _finished_of_kind(http, db, inst, kind):
    s = _schedule(db, inst)
    if kind == "restore":
        parent = _create(http, inst, s, trigger_at=T0 - datetime.timedelta(hours=1)).json()
        run = _create(http, inst, s, kind="restore", parent_run_id=parent["id"]).json()
    else:
        run = _create(http, inst, s, kind=kind).json()
    path = _url(inst, f"/runs/{run['id']}")
    http.patch(path, json={"outcome": "running"}, headers=HEADERS)
    http.patch(path, json={"outcome": "ok"}, headers=HEADERS)
    return path


@pytest.mark.parametrize("kind", ["restore", "test"])
def test_only_action_runs_can_become_overridden(http, db, inst, kind):
    path = _finished_of_kind(http, db, inst, kind)
    r = http.patch(path, json={"outcome": "overridden"}, headers=HEADERS)
    assert (r.status_code, r.json()["code"]) == (409, "run_finished")


def test_override_keeps_snapshot_and_attempts(http, db, inst):
    s = _schedule(db, inst)
    run = _create(http, inst, s, snapshot={"mode": "COOL"}).json()
    path = _url(inst, f"/runs/{run['id']}")
    http.patch(path, json={"outcome": "running"}, headers=HEADERS)
    http.patch(path, json={"outcome": "ok", "attempts": 2}, headers=HEADERS)
    r = http.patch(path, json={"outcome": "overridden", "message": "owner changed the unit", "attempts": 9,
                               "snapshot": {"mode": "HEAT"}}, headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert (body["outcome"], body["attempts"], body["snapshot"]) == ("overridden", 2, {"mode": "COOL"})


STEPS = [
    {"index": 0, "action_id": "set_mode", "params": {"mode": "off"}, "outcome": "ok", "message": None, "attempts": 1},
    {"index": 1, "action_id": "set_fan", "params": {}, "outcome": "failed", "message": "no reply", "attempts": 3},
]


def _finish_partial(http, inst, s, **extra):
    run = _create(http, inst, s).json()
    path = _url(inst, f"/runs/{run['id']}")
    http.patch(path, json={"outcome": "running"}, headers=HEADERS)
    return path, http.patch(path, json={"outcome": "partial", "message": "fan failed", **extra}, headers=HEADERS)


def test_partial_run_is_unfinished_until_restored(http, db, inst):
    s = _schedule(db, inst)
    _, done = _finish_partial(http, inst, s)
    assert done.json()["outcome"] == "partial"
    listed = http.get(_url(inst), params={"unfinished": "true"}, headers=HEADERS).json()
    assert [x["id"] for x in listed] == [done.json()["id"]]
    rest = _create(http, inst, s, kind="restore", parent_run_id=done.json()["id"]).json()
    http.patch(_url(inst, f"/runs/{rest['id']}"), json={"outcome": "ok"}, headers=HEADERS)
    assert http.get(_url(inst), params={"unfinished": "true"}, headers=HEADERS).json() == []


def test_partial_run_emails_when_notify_email(http, db, inst, user, fake_email_sender):
    _finish_partial(http, inst, _schedule(db, inst, notify=True))
    assert len(fake_email_sender.sent) == 1
    assert fake_email_sender.sent[0][0] == user.email


def test_run_update_stores_steps(http, db, inst):
    _, done = _finish_partial(http, inst, _schedule(db, inst), steps=STEPS)
    assert done.json()["steps"] == STEPS
    db.expire_all()
    assert db.get(ActorRun, done.json()["id"]).steps == STEPS


def test_run_update_rejects_bad_step(http, db, inst):
    run = _create(http, inst, _schedule(db, inst)).json()
    bad = [{**STEPS[0], "outcome": "skipped"}]
    r = http.patch(_url(inst, f"/runs/{run['id']}"), json={"outcome": "failed", "steps": bad}, headers=HEADERS)
    assert r.status_code == 422


def test_partial_cannot_return_to_pending(http, db, inst):
    path, _ = _finish_partial(http, inst, _schedule(db, inst))
    r = http.patch(path, json={"outcome": "pending"}, headers=HEADERS)
    assert (r.status_code, r.json()["code"]) == (409, "run_finished")


@pytest.mark.parametrize("outcome", ["ok", "failed", "overridden"])
def test_partial_run_is_never_changed(http, db, inst, outcome):
    path, _ = _finish_partial(http, inst, _schedule(db, inst))
    r = http.patch(path, json={"outcome": outcome}, headers=HEADERS)
    assert (r.status_code, r.json()["code"]) == (409, "run_finished")
