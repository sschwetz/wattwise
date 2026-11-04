from __future__ import annotations

import datetime
import decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core_api.actor_failures import current_failures
from core_api.deps import get_current_user, get_db_session
from core_api.errors import install_error_handlers
from core_api.models import ActorInstance, ActorRun, ActorSchedule, RunKind, RunOutcome
from core_api.models.billing import DemandWindow, SiteTariff
from core_api.models.identity import Site, SiteAccess, User
from core_api.routers.actors import router
from tests.conftest import UTC, sydney_local

NOW = datetime.datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
ACTION = {"id": "set_mode", "params": {"mode": "off"}}


def make_instance(db, site, *, last_seen_at=NOW, **kw) -> ActorInstance:
    instance = ActorInstance(site_id=site.id, provider="actron_neo", name="AC", config={"refresh_token": "secret"},
                             last_seen_at=last_seen_at, **kw)
    db.add(instance)
    db.commit()
    return instance


def make_schedule(db, instance, *, restore=None, trigger=None, give_up=15, notify=False) -> ActorSchedule:
    schedule = ActorSchedule(
        instance_id=instance.id, name="Peak off", trigger=trigger or {"type": "time", "time": "10:00"},
        conditions=[], action=ACTION, restore=restore or {"enabled": True, "when": "after_minutes", "minutes": 60},
        retry={"give_up_minutes": give_up}, notify_email=notify,
    )
    db.add(schedule)
    db.commit()
    return schedule


def make_run(db, schedule, trigger_at, outcome, kind=RunKind.ACTION, parent=None) -> ActorRun:
    run = ActorRun(schedule_id=schedule.id, kind=kind, trigger_at=trigger_at, outcome=outcome,
                   finished_at=trigger_at, message="unit did not answer", parent_run_id=parent)
    db.add(run)
    db.commit()
    return run


def kinds(db, site, now=NOW) -> list[str]:
    return [f.kind for f in current_failures(db, site.id, now)]


def test_failed_run_is_current_until_later_ok(db, site):
    schedule = make_schedule(db, make_instance(db, site))
    make_run(db, schedule, NOW - datetime.timedelta(hours=3), RunOutcome.OK, kind=RunKind.TEST)
    make_run(db, schedule, NOW - datetime.timedelta(hours=2), RunOutcome.FAILED)
    [failure] = current_failures(db, site.id, NOW)
    assert (failure.kind, failure.schedule_id, failure.schedule_name) == ("run_failed", schedule.id, "Peak off")
    assert failure.since == NOW - datetime.timedelta(hours=2)
    make_run(db, schedule, NOW - datetime.timedelta(hours=1), RunOutcome.OK, kind=RunKind.TEST)
    assert kinds(db, site) == []


@pytest.mark.parametrize("outcome", [RunOutcome.SKIPPED, RunOutcome.OVERRIDDEN])
def test_failed_run_cleared_by_later_skip_or_override(db, site, outcome):
    schedule = make_schedule(db, make_instance(db, site))
    make_run(db, schedule, NOW - datetime.timedelta(hours=2), RunOutcome.FAILED)
    make_run(db, schedule, NOW - datetime.timedelta(hours=1), outcome)
    assert kinds(db, site) == []


def test_failed_restore_run_is_run_failed(db, site):
    schedule = make_schedule(db, make_instance(db, site))
    action = make_run(db, schedule, NOW - datetime.timedelta(minutes=30), RunOutcome.OK)
    make_run(db, schedule, NOW - datetime.timedelta(minutes=10), RunOutcome.FAILED, kind=RunKind.RESTORE,
             parent=action.id)
    assert kinds(db, site) == ["run_failed"]


def test_overridden_run_is_not_a_failure(db, site):
    schedule = make_schedule(db, make_instance(db, site, last_seen_at=None))
    make_run(db, schedule, NOW - datetime.timedelta(hours=5), RunOutcome.OVERRIDDEN)
    assert kinds(db, site) == []


def test_still_off_after_give_up_window(db, site):
    schedule = make_schedule(db, make_instance(db, site), give_up=15)
    trigger_at = NOW - datetime.timedelta(hours=2)
    make_run(db, schedule, trigger_at, RunOutcome.OK)
    restore_at = trigger_at + datetime.timedelta(minutes=60)
    assert kinds(db, site, now=restore_at + datetime.timedelta(minutes=14)) == []
    [failure] = current_failures(db, site.id, restore_at + datetime.timedelta(minutes=16))
    assert (failure.kind, failure.since) == ("still_off", restore_at)


def test_still_off_at_time_uses_site_clock(db, site):
    schedule = make_schedule(db, make_instance(db, site), restore={"when": "at_time", "time": "18:30"}, give_up=15)
    make_run(db, schedule, sydney_local(2026, 11, 5, 16), RunOutcome.OK)
    restore_at = sydney_local(2026, 11, 5, 18, 30)
    assert kinds(db, site, now=restore_at + datetime.timedelta(minutes=14)) == []
    [failure] = current_failures(db, site.id, restore_at + datetime.timedelta(minutes=16))
    assert (failure.kind, failure.since) == ("still_off", restore_at)


def test_still_off_window_end_uses_occurrence_end(db, site):
    tariff = SiteTariff(site_id=site.id, effective_from=datetime.datetime(2020, 1, 1, tzinfo=UTC),
                        service_availability_c_per_day=decimal.Decimal("100"))
    db.add(tariff)
    db.commit()
    window = DemandWindow(site_tariff_id=tariff.id, label="Peak", months=list(range(1, 13)),
                          days_of_week=list(range(7)), start_time=datetime.time(16), end_time=datetime.time(20),
                          rate_c_per_kw_per_day=decimal.Decimal("50"))
    db.add(window)
    db.commit()
    instance = make_instance(db, site, last_seen_at=sydney_local(2026, 11, 5, 21))
    schedule = make_schedule(db, instance, restore={"when": "window_end"},
                             trigger={"type": "window_start", "window_id": window.id}, give_up=15)
    make_run(db, schedule, sydney_local(2026, 11, 5, 16), RunOutcome.OK)
    restore_at = sydney_local(2026, 11, 5, 20)
    assert kinds(db, site, now=restore_at + datetime.timedelta(minutes=14)) == []
    [failure] = current_failures(db, site.id, restore_at + datetime.timedelta(minutes=16))
    assert (failure.kind, failure.since) == ("still_off", restore_at)


def test_still_off_window_end_without_occurrence_falls_back_to_a_day(db, site):
    schedule = make_schedule(db, make_instance(db, site), restore={"when": "window_end"}, give_up=15)
    trigger_at = NOW - datetime.timedelta(hours=30)
    make_run(db, schedule, trigger_at, RunOutcome.OK)
    assert kinds(db, site, now=trigger_at + datetime.timedelta(hours=24, minutes=14)) == []
    assert kinds(db, site, now=trigger_at + datetime.timedelta(hours=24, minutes=16)) == ["still_off"]


@pytest.mark.parametrize("restore", [{"when": "conditions_only"}, {"enabled": False}])
def test_no_time_based_restore_is_never_still_off(db, site, restore):
    schedule = make_schedule(db, make_instance(db, site), restore=restore)
    make_run(db, schedule, NOW - datetime.timedelta(days=3), RunOutcome.OK)
    assert kinds(db, site) == []


def test_actor_silent_with_unfinished_run(db, site):
    instance = make_instance(db, site, last_seen_at=NOW - datetime.timedelta(minutes=6))
    schedule = make_schedule(db, instance)
    make_run(db, schedule, NOW - datetime.timedelta(minutes=10), RunOutcome.OK)
    [failure] = current_failures(db, site.id, NOW)
    assert (failure.kind, failure.since) == ("actor_silent", NOW - datetime.timedelta(minutes=6))
    instance.last_seen_at = NOW - datetime.timedelta(minutes=4)
    db.commit()
    assert kinds(db, site) == []
    instance.last_seen_at = None
    db.commit()
    assert kinds(db, site) == ["actor_silent"]


def test_restore_disabled_run_with_silent_actor_is_not_a_failure(db, site):
    schedule = make_schedule(db, make_instance(db, site, last_seen_at=None), restore={"enabled": False})
    make_run(db, schedule, NOW - datetime.timedelta(hours=1), RunOutcome.OK)
    assert kinds(db, site) == []


def test_one_run_failed_per_schedule_dated_by_the_latest_failure(db, site):
    schedule = make_schedule(db, make_instance(db, site))
    make_run(db, schedule, NOW - datetime.timedelta(hours=5), RunOutcome.FAILED)
    make_run(db, schedule, NOW - datetime.timedelta(hours=4), RunOutcome.OK, kind=RunKind.TEST)
    action = make_run(db, schedule, NOW - datetime.timedelta(hours=3), RunOutcome.FAILED)
    make_run(db, schedule, NOW - datetime.timedelta(hours=2), RunOutcome.FAILED, kind=RunKind.TEST)
    make_run(db, schedule, NOW - datetime.timedelta(hours=1), RunOutcome.FAILED, kind=RunKind.RESTORE,
             parent=action.id)
    other = make_schedule(db, make_instance(db, site))
    make_run(db, other, NOW - datetime.timedelta(hours=6), RunOutcome.FAILED)
    failures = current_failures(db, site.id, NOW)
    assert [(f.kind, f.schedule_id, f.since) for f in failures] == [
        ("run_failed", other.id, NOW - datetime.timedelta(hours=6)),
        ("run_failed", schedule.id, NOW - datetime.timedelta(hours=1)),
    ]
    assert failures[1].message.startswith("The restore failed")


def test_silent_actor_without_unfinished_run_is_not_a_failure(db, site):
    schedule = make_schedule(db, make_instance(db, site, last_seen_at=None))
    action = make_run(db, schedule, NOW - datetime.timedelta(minutes=30), RunOutcome.OK)
    make_run(db, schedule, NOW - datetime.timedelta(minutes=5), RunOutcome.OK, kind=RunKind.RESTORE, parent=action.id)
    assert kinds(db, site) == []


def test_needs_sign_in_listed(db, site):
    instance = make_instance(db, site, auth_state={"needs_sign_in": True})
    make_instance(db, site, auth_state={"user_code": "ABCD", "verification_uri": "https://example.com"})
    [failure] = current_failures(db, site.id, NOW)
    assert (failure.kind, failure.instance_id, failure.schedule_id, failure.schedule_name) == (
        "needs_sign_in", instance.id, None, None)


def test_disabled_instance_failures_still_show(db, site):
    schedule = make_schedule(db, make_instance(db, site, enabled=False))
    make_run(db, schedule, NOW - datetime.timedelta(hours=1), RunOutcome.FAILED)
    assert kinds(db, site) == ["run_failed"]


def test_other_site_failures_not_listed(db, site, user):
    other = Site(owner_user_id=user.id, name="Other", timezone="Australia/Sydney")
    db.add(other)
    db.commit()
    make_instance(db, other, auth_state={"needs_sign_in": True})
    assert kinds(db, site) == []


# Endpoint


@pytest.fixture()
def viewer_http(db, site):
    viewer = User(email="viewer@example.com", password_hash="x")
    db.add(viewer)
    db.commit()
    db.add(SiteAccess(site_id=site.id, user_id=viewer.id))
    db.commit()
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: viewer
    return TestClient(app)


def test_failures_endpoint_viewer_can_read(viewer_http, db, site):
    schedule = make_schedule(db, make_instance(db, site))
    finished = datetime.datetime.now(UTC) - datetime.timedelta(minutes=3)
    make_run(db, schedule, finished, RunOutcome.FAILED)
    response = viewer_http.get(f"/api/v1/sites/{site.id}/actors/failures")
    assert response.status_code == 200
    [item] = response.json()
    assert item["kind"] == "run_failed"
    assert item["schedule_id"] == schedule.id
    assert item["schedule_name"] == "Peak off"
    assert item["message"]
    since = datetime.datetime.fromisoformat(item["since"])
    assert since == finished
    assert since.utcoffset() == datetime.timedelta(0)


def test_failures_endpoint_hidden_from_strangers(db, site):
    stranger = User(email="stranger@example.com", password_hash="x")
    db.add(stranger)
    db.commit()
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: stranger
    assert TestClient(app).get(f"/api/v1/sites/{site.id}/actors/failures").status_code == 404
