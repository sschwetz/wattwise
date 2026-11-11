from __future__ import annotations

import datetime
import json

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from core_api.bootstrap import run_migrations
from core_api.db import make_engine
from core_api.models import ActorInstance, ActorRun, ActorSchedule, RunKind, RunOutcome

TRIGGER_AT = datetime.datetime(2026, 11, 5, 17, 0, tzinfo=datetime.UTC)
REFRESH_TOKEN = "refresh-token-abc123"


def _schedule(db, site) -> ActorSchedule:
    instance = ActorInstance(site_id=site.id, provider="actron_neo", name="Aircon",
                             config={"refresh_token": REFRESH_TOKEN})
    db.add(instance)
    db.flush()
    schedule = ActorSchedule(instance_id=instance.id, name="Peak off", trigger={"type": "time"},
                             conditions=[], actions=[{"id": "system_off"}], restore={"to": "previous"},
                             retry={"attempts": 3, "seconds": 30, "give_up_minutes": 10})
    db.add(schedule)
    db.commit()
    return schedule


def _run(schedule: ActorSchedule, kind: RunKind = RunKind.ACTION) -> ActorRun:
    return ActorRun(schedule_id=schedule.id, kind=kind, trigger_at=TRIGGER_AT, outcome=RunOutcome.PENDING)


def test_config_is_encrypted_at_rest(db, site):
    _schedule(db, site)
    raw = db.execute(sa.text("SELECT config FROM actor_instances")).scalar()
    assert REFRESH_TOKEN not in raw
    db.expire_all()
    assert db.query(ActorInstance).one().config == {"refresh_token": REFRESH_TOKEN}


def test_auth_state_is_encrypted_at_rest(db, site):
    code = {"user_code": "QZXW-7731", "verification_uri": "https://example.com/device?code=QZXW-7731",
            "expires_at": "2026-11-05T03:00:00+00:00"}
    db.add(ActorInstance(site_id=site.id, provider="actron_neo", config={}, auth_state=code))
    db.commit()
    raw = db.execute(sa.text("SELECT auth_state FROM actor_instances")).scalar()
    assert "QZXW-7731" not in raw and "example.com" not in raw
    db.expire_all()
    assert db.query(ActorInstance).one().auth_state == code


def test_duplicate_run_key_rejected(db, site):
    schedule = _schedule(db, site)
    db.add(_run(schedule))
    db.commit()
    db.add(_run(schedule))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(_run(schedule, RunKind.RESTORE))
    db.commit()


def test_run_outcome_check_constraint(db, site):
    schedule = _schedule(db, site)
    insert = sa.text("INSERT INTO actor_runs (schedule_id, kind, trigger_at, outcome, attempts) "
                     "VALUES (:s, 'action', :t, :o, 0)")
    with pytest.raises(IntegrityError):
        db.execute(insert, {"s": schedule.id, "t": TRIGGER_AT, "o": "exploded"})
    db.rollback()


def test_migration_turns_action_into_actions(tmp_path):
    db_url = f"sqlite:///{tmp_path}/migrate.db"
    run_migrations(db_url, "a1b2c3d4e5f6")
    engine = make_engine(db_url)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO users (id, email, password_hash, email_confirmed, created_at) "
                             "VALUES (1, 'o@example.com', 'h', 1, CURRENT_TIMESTAMP)"))
        conn.execute(sa.text("INSERT INTO sites (id, owner_user_id, name, timezone, enabled) "
                             "VALUES (1, 1, 'Home', 'Australia/Sydney', 1)"))
        conn.execute(sa.text("INSERT INTO actor_instances (id, site_id, provider, enabled, config) "
                             "VALUES (1, 1, 'actron_neo', 1, 'x')"))
        conn.execute(sa.text(
            "INSERT INTO actor_schedules (id, instance_id, name, enabled, trigger, conditions, action, restore, "
            "retry, notify_email, created_at) VALUES (1, 1, 's', 1, '{}', '[]', :a, '{}', '{}', 0, CURRENT_TIMESTAMP)"),
            {"a": json.dumps({"id": "system_off", "params": {}})})
    run_migrations(db_url)
    with engine.connect() as conn:
        stored = conn.execute(sa.text("SELECT actions FROM actor_schedules WHERE id = 1")).scalar_one()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("actor_schedules")}
    assert json.loads(stored) == [{"id": "system_off", "params": {}}]
    assert "action" not in columns


def test_run_stores_partial_outcome_and_steps(db, site):
    run = _run(_schedule(db, site))
    run.outcome = RunOutcome.PARTIAL
    run.steps = [{"id": "system_off", "outcome": "ok"}]
    db.add(run)
    db.commit()
    db.refresh(run)
    assert run.outcome is RunOutcome.PARTIAL and run.steps == [{"id": "system_off", "outcome": "ok"}]
