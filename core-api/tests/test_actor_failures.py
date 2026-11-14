from __future__ import annotations

import datetime
import decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core_api.actor_failures import current_failures, site_log
from core_api.deps import get_current_user, get_db_session
from core_api.errors import install_error_handlers
from core_api.models import ActorInstance, ActorRun, ActorSchedule, RunKind, RunOutcome
from core_api.models.alerts import Alert
from core_api.models.billing import DemandWindow, SiteTariff
from core_api.models.identity import Site, SiteAccess, User
from core_api.models.sources import Source, SourceKind
from core_api.routers.actors import router
from tests.conftest import UTC, sydney_local

NOW = datetime.datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
ACTION = {"id": "set_mode", "params": {"mode": "off"}}
NO_RESTORE = {"enabled": False, "when": "after_minutes", "minutes": 60}


def make_instance(db, site, *, last_seen_at=NOW, **kw) -> ActorInstance:
    instance = ActorInstance(site_id=site.id, provider="actron_neo", name="AC", config={"refresh_token": "secret"},
                             last_seen_at=last_seen_at, **kw)
    db.add(instance)
    db.commit()
    return instance


def make_schedule(db, instance, *, restore=None, trigger=None, give_up=15, notify=False) -> ActorSchedule:
    schedule = ActorSchedule(
        instance_id=instance.id, name="Peak off", trigger=trigger or {"type": "time", "time": "10:00"},
        conditions=[], actions=[ACTION], restore=restore or {"enabled": True, "when": "after_minutes", "minutes": 60},
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


def make_source(db, site, **kw) -> Source:
    source = Source(site_id=site.id, kind=SourceKind.ENERGY, provider="envoy", config={}, effective_from=NOW, **kw)
    db.add(source)
    db.commit()
    return source


def add_alert(db, site, kind, subject_id, code="cert", message="Envoy certificate changed") -> Alert:
    since = NOW - datetime.timedelta(hours=1)
    alert = Alert(site_id=site.id, subject_kind=kind, subject_id=subject_id, code=code, message=message, since=since)
    db.add(alert)
    db.commit()
    return alert


def test_alerts_are_failures_named_for_their_subject_until_cleared(db, site):
    source = make_source(db, site, name="Roof")
    instance = make_instance(db, site)
    add_alert(db, site, "source", source.id)
    add_alert(db, site, "instance", instance.id, code="tick", message="Actron not answering")
    add_alert(db, site, "site", site.id, code="notify_email", message="Email failed")
    found = current_failures(db, site.id, NOW)
    assert [(f.kind, f.subject, f.message) for f in found] == [
        ("alert", "Roof", "Envoy certificate changed"),
        ("alert", "AC", "Actron not answering"),
        ("alert", "Site", "Email failed"),
    ]
    assert all(f.instance_id is None and f.schedule_id is None for f in found)
    db.query(Alert).delete()
    db.commit()
    assert kinds(db, site) == []


def test_alerts_of_a_disabled_source_or_instance_are_left_out(db, site):
    source = make_source(db, site, enabled=False)
    instance = make_instance(db, site, enabled=False)
    add_alert(db, site, "source", source.id)
    add_alert(db, site, "instance", instance.id)
    assert kinds(db, site) == []


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


def test_restore_disabled_is_never_still_off(db, site):
    schedule = make_schedule(db, make_instance(db, site), restore={"enabled": False})
    make_run(db, schedule, NOW - datetime.timedelta(days=3), RunOutcome.OK)
    assert kinds(db, site) == []


def test_conditions_only_restore_is_still_off_after_the_day_cap(db, site):
    restore = {"when": "conditions_only", "early_if": [{"reading": "indoor_temp_c", "op": "lt", "value": 22}]}
    schedule = make_schedule(db, make_instance(db, site), restore=restore, give_up=15)
    trigger_at = NOW - datetime.timedelta(hours=30)
    make_run(db, schedule, trigger_at, RunOutcome.OK)
    cap = trigger_at + datetime.timedelta(hours=24)
    assert kinds(db, site, now=cap + datetime.timedelta(minutes=14)) == []
    [failure] = current_failures(db, site.id, cap + datetime.timedelta(minutes=16))
    assert (failure.kind, failure.since) == ("still_off", cap)


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
        ("run_failed", schedule.id, NOW - datetime.timedelta(hours=1)),
        ("run_failed", other.id, NOW - datetime.timedelta(hours=6)),
    ]
    assert failures[0].message.startswith("The restore failed")


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


def test_failed_restore_is_not_cleared_by_a_later_skip(db, site):
    schedule = make_schedule(db, make_instance(db, site))
    action = make_run(db, schedule, NOW - datetime.timedelta(hours=3), RunOutcome.OK)
    make_run(db, schedule, NOW - datetime.timedelta(hours=2), RunOutcome.FAILED, kind=RunKind.RESTORE,
             parent=action.id)
    make_run(db, schedule, NOW - datetime.timedelta(hours=1), RunOutcome.SKIPPED)
    assert kinds(db, site) == ["run_failed"]


@pytest.mark.parametrize("outcome", [RunOutcome.OK, RunOutcome.OVERRIDDEN])
def test_failed_restore_is_cleared_by_a_later_ok_or_override(db, site, outcome):
    schedule = make_schedule(db, make_instance(db, site))
    action = make_run(db, schedule, NOW - datetime.timedelta(hours=3), RunOutcome.OK)
    make_run(db, schedule, NOW - datetime.timedelta(hours=2), RunOutcome.FAILED, kind=RunKind.RESTORE,
             parent=action.id)
    make_run(db, schedule, NOW - datetime.timedelta(hours=1), outcome)
    assert "run_failed" not in kinds(db, site)


def test_next_local_time_on_the_dst_end_day_takes_the_first_pass():
    from core_api.actor_failures import next_local_time
    from core_api.time_utils import site_tz

    tz = site_tz("Australia/Sydney")
    after = datetime.datetime(2026, 4, 4, 14, 0, tzinfo=UTC)  # 01:00 AEDT on 5 April
    first = next_local_time(after, "02:30", tz)
    assert first == datetime.datetime(2026, 4, 4, 15, 30, tzinfo=UTC)  # 02:30 AEDT
    second = next_local_time(first + datetime.timedelta(minutes=1), "02:30", tz)
    assert second == datetime.datetime(2026, 4, 4, 16, 30, tzinfo=UTC)  # 02:30 AEST, the repeated hour


def test_partial_run_shows_in_failures_until_later_ok(db, site):
    schedule = make_schedule(db, make_instance(db, site))
    make_run(db, schedule, NOW - datetime.timedelta(hours=2), RunOutcome.PARTIAL)
    [failure] = [f for f in current_failures(db, site.id, NOW) if f.kind == "run_failed"]
    assert failure.message == "The action partly failed: unit did not answer"
    make_run(db, schedule, NOW - datetime.timedelta(hours=1), RunOutcome.OK, kind=RunKind.TEST)
    assert "run_failed" not in kinds(db, site)


def add_listed_alert(db, site, code, *, severity="failure", since=None, cleared_at=None, sticky=False) -> Alert:
    alert = Alert(site_id=site.id, subject_kind="site", subject_id=site.id, code=code, message=code, severity=severity,
                  since=since or NOW - datetime.timedelta(hours=2), cleared_at=cleared_at, sticky=sticky)
    db.add(alert)
    db.commit()
    return alert


def test_cleared_alert_is_listed_for_an_hour(db, site):
    add_listed_alert(db, site, "recent", cleared_at=NOW - datetime.timedelta(minutes=59))
    add_listed_alert(db, site, "old", cleared_at=NOW - datetime.timedelta(minutes=61))
    [item] = current_failures(db, site.id, NOW)
    assert item.message == "recent"
    assert item.cleared_at == NOW - datetime.timedelta(minutes=59)


def test_alert_cleared_exactly_an_hour_ago_is_not_listed(db, site):
    add_listed_alert(db, site, "edge", cleared_at=NOW - datetime.timedelta(minutes=60))
    assert current_failures(db, site.id, NOW) == []


def test_alert_failure_carries_severity_id_and_sticky(db, site):
    alert = add_listed_alert(db, site, "kept", severity="notice", sticky=True)
    [item] = current_failures(db, site.id, NOW)
    assert (item.severity, item.alert_id, item.sticky, item.cleared_at) == ("notice", alert.id, True, None)


def test_partial_run_failure_has_severity_partial(db, site):
    schedule = make_schedule(db, make_instance(db, site))
    make_run(db, schedule, NOW - datetime.timedelta(hours=2), RunOutcome.PARTIAL)
    [partial] = [f for f in current_failures(db, site.id, NOW) if f.kind == "run_failed"]
    assert (partial.severity, partial.alert_id) == ("partial", None)
    other = make_schedule(db, make_instance(db, site))
    make_run(db, other, NOW - datetime.timedelta(hours=1), RunOutcome.FAILED)
    by_schedule = {f.schedule_id: f for f in current_failures(db, site.id, NOW) if f.kind == "run_failed"}
    assert (by_schedule[other.id].severity, by_schedule[other.id].alert_id) == ("failure", None)


def test_ordering(db, site):
    hour = datetime.timedelta(hours=1)
    add_listed_alert(db, site, "notice", severity="notice", since=NOW - hour)
    add_listed_alert(db, site, "partial", severity="partial", since=NOW - hour)
    add_listed_alert(db, site, "failure_old", since=NOW - 3 * hour)
    add_listed_alert(db, site, "failure_new", since=NOW - 2 * hour)
    add_listed_alert(db, site, "cleared", since=NOW - 5 * hour, cleared_at=NOW - datetime.timedelta(minutes=5))
    assert [f.message for f in current_failures(db, site.id, NOW)] == [
        "failure_new", "failure_old", "partial", "notice", "cleared",
    ]


def test_site_log_lists_ok_action_runs_of_the_last_day_as_notices(db, site):
    schedule = make_schedule(db, make_instance(db, site), restore=NO_RESTORE)
    make_run(db, schedule, NOW - datetime.timedelta(hours=2), RunOutcome.OK)
    make_run(db, schedule, NOW - datetime.timedelta(hours=25), RunOutcome.OK)
    make_run(db, schedule, NOW - datetime.timedelta(hours=1), RunOutcome.OK, kind=RunKind.RESTORE)
    log = site_log(db, site.id, NOW)
    assert [(f.kind, f.severity, f.schedule_name) for f in log] == [("run_ok", "notice", "Peak off")]
    assert log[0].since == NOW - datetime.timedelta(hours=2)


def test_site_log_puts_failures_before_ok_runs(db, site):
    schedule = make_schedule(db, make_instance(db, site), restore=NO_RESTORE)
    make_run(db, schedule, NOW - datetime.timedelta(hours=3), RunOutcome.OK)
    other = make_schedule(db, db.get(ActorInstance, schedule.instance_id), restore=NO_RESTORE)
    make_run(db, other, NOW - datetime.timedelta(hours=4), RunOutcome.FAILED)
    assert [f.kind for f in site_log(db, site.id, NOW)] == ["run_failed", "run_ok"]
