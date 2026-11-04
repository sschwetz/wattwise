from __future__ import annotations

import datetime

import pytest

from core_api.actor_runs import has_unfinished_action_run, unfinished_action_runs
from core_api.models import ActorInstance, ActorRun, ActorSchedule, RunKind, RunOutcome

T0 = datetime.datetime(2026, 11, 5, 17, 0, tzinfo=datetime.UTC)


@pytest.fixture()
def schedule(db, site) -> ActorSchedule:
    instance = ActorInstance(site_id=site.id, provider="actron_neo", config={})
    db.add(instance)
    db.flush()
    s = ActorSchedule(instance_id=instance.id, name="s", trigger={}, conditions=[], action={}, restore={}, retry={})
    db.add(s)
    db.commit()
    return s


def _add(db, schedule, kind, outcome, parent=None, minute=0) -> ActorRun:
    run = ActorRun(schedule_id=schedule.id, kind=kind, outcome=outcome, parent_run_id=parent,
                   trigger_at=T0 + datetime.timedelta(minutes=minute))
    db.add(run)
    db.commit()
    return run


@pytest.mark.parametrize("outcome", [RunOutcome.PENDING, RunOutcome.RUNNING, RunOutcome.OK])
def test_action_without_restore_is_unfinished(db, schedule, outcome):
    run = _add(db, schedule, RunKind.ACTION, outcome)
    assert [r.id for r in unfinished_action_runs(db, schedule.instance_id)] == [run.id]
    assert has_unfinished_action_run(db, schedule.instance_id)


@pytest.mark.parametrize("outcome", [RunOutcome.OVERRIDDEN, RunOutcome.SKIPPED, RunOutcome.FAILED])
def test_dead_action_is_not_unfinished(db, schedule, outcome):
    _add(db, schedule, RunKind.ACTION, outcome)
    assert not has_unfinished_action_run(db, schedule.instance_id)


@pytest.mark.parametrize("outcome", [RunOutcome.OK, RunOutcome.SKIPPED, RunOutcome.FAILED])
def test_finished_restore_closes_action(db, schedule, outcome):
    action = _add(db, schedule, RunKind.ACTION, RunOutcome.OK)
    _add(db, schedule, RunKind.RESTORE, outcome, parent=action.id)
    assert unfinished_action_runs(db, schedule.instance_id) == []


@pytest.mark.parametrize("outcome", [RunOutcome.PENDING, RunOutcome.RUNNING])
def test_open_restore_leaves_action_unfinished(db, schedule, outcome):
    action = _add(db, schedule, RunKind.ACTION, RunOutcome.OK)
    _add(db, schedule, RunKind.RESTORE, outcome, parent=action.id)
    assert has_unfinished_action_run(db, schedule.instance_id)


def test_other_instance_runs_ignored(db, schedule, site):
    _add(db, schedule, RunKind.ACTION, RunOutcome.OK)
    other = ActorInstance(site_id=site.id, provider="actron_neo", config={})
    db.add(other)
    db.commit()
    assert not has_unfinished_action_run(db, other.id)
