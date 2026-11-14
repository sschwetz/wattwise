"""An in-memory stand-in for the synchronous CoreApiClient.

Runs follow the Core API rules in core_api/routers/actor.py and actor_runs.py:
create makes a `pending` row and refuses a duplicate (schedule, kind,
trigger_at) with 409 `duplicate_run`; `running` claims only a pending run;
a finished run changes only from `ok` to `overridden`; the unfinished listing
skips schedules whose restore is off.
"""

from __future__ import annotations

import dataclasses
import datetime
import threading
from collections.abc import Callable

from actron_neo_actor.core_api_client import CoreApiConflict, CoreApiError
from actron_neo_actor.models import Context, Instance, Run, Schedule

TERMINAL = {"ok", "partial", "skipped", "failed", "overridden"}
CLOSING_RESTORE = {"ok", "skipped", "failed"}


class FakeCore:
    def __init__(self, clock: Callable[[], datetime.datetime]) -> None:
        self.clock = clock
        self.instances: list[Instance] = []
        self.schedules: dict[int, list[Schedule]] = {}
        self.contexts: dict[int, Context] = {}
        self.runs: dict[int, Run] = {}
        self.capabilities: list[tuple[int, dict]] = []
        self.readings: list[tuple[int, dict]] = []
        self.discovered: list[tuple[int, dict]] = []
        self.errors: dict[str, Exception] = {}  # method name -> error to raise
        self.instance_errors: dict[int, Exception] = {}  # instance id -> error from every per-instance call
        self.alerts: dict[tuple[int, str], str] = {}  # (instance id, code) -> message of the alerts now showing
        self.alert_options: dict[tuple[int, str], tuple[str, bool]] = {}  # (severity, sticky) of the last set per alert
        self.alert_calls: list[tuple[str, int, str]] = []  # ("set" or "clear", instance id, code) per call
        self.threads: set[int] = set()
        self.updates: list[tuple[int, str, dict | None]] = []  # (run id, outcome, snapshot) per update_run
        self._next_id = 1

    # Helpers for tests

    def add_run(self, **fields: object) -> Run:
        base = {"started_at": None, "finished_at": None, "message": None, "attempts": 0, "parent_run_id": None,
                "snapshot": None, "steps": None, "outcome": "pending"}
        run = Run(id=self._next_id, **{**base, **fields})  # type: ignore[arg-type]
        self._next_id += 1
        self.runs[run.id] = run
        return run

    def of(self, kind: str, schedule_id: int | None = None) -> list[Run]:
        return [r for r in self.runs.values() if r.kind == kind and schedule_id in (None, r.schedule_id)]

    def _enter(self, name: str, instance_id: int | None = None) -> None:
        self.threads.add(threading.get_ident())
        if name in self.errors:
            raise self.errors[name]
        if instance_id is not None and instance_id in self.instance_errors:
            raise self.instance_errors[instance_id]

    def _schedule(self, schedule_id: int) -> Schedule | None:
        return next((s for ss in self.schedules.values() for s in ss if s.id == schedule_id), None)

    # Client surface

    def list_instances(self) -> list[Instance]:
        self._enter("list_instances")
        return list(self.instances)

    def patch_discovered(self, instance_id: int, config: dict | None = None, auth_state: object = None) -> None:
        self._enter("patch_discovered", instance_id)
        self.discovered.append((instance_id, {"config": config, "auth_state": auth_state}))

    def set_alert(
        self, instance_id: int, code: str, message: str, severity: str = "failure", sticky: bool = False
    ) -> None:
        self.alert_calls.append(("set", instance_id, code))
        self.alerts[(instance_id, code)] = message
        self.alert_options[(instance_id, code)] = (severity, sticky)

    def clear_alert(self, instance_id: int, code: str) -> None:
        self.alert_calls.append(("clear", instance_id, code))
        self.alerts.pop((instance_id, code), None)

    def put_capabilities(self, instance_id: int, capabilities: dict) -> None:
        self._enter("put_capabilities", instance_id)
        self.capabilities.append((instance_id, capabilities))

    def get_schedules(self, instance_id: int) -> list[Schedule]:
        self._enter("get_schedules", instance_id)
        return list(self.schedules.get(instance_id, []))

    def get_context(self, instance_id: int) -> Context:
        self._enter("get_context", instance_id)
        return dataclasses.replace(self.contexts[instance_id], now=self.clock())

    def put_readings(self, instance_id: int, state: dict) -> None:
        self._enter("put_readings", instance_id)
        self.readings.append((instance_id, state))

    def create_run(
        self,
        instance_id: int,
        schedule_id: int,
        kind: str,
        trigger_at: datetime.datetime,
        parent_run_id: int | None = None,
        snapshot: dict | None = None,
    ) -> Run:
        self._enter("create_run", instance_id)
        if any((r.schedule_id, r.kind, r.trigger_at) == (schedule_id, kind, trigger_at) for r in self.runs.values()):
            raise CoreApiConflict("Failed to create run: 409", "duplicate_run")
        if kind == "restore":
            parent = self.runs.get(parent_run_id or -1)
            if parent is None or parent.kind != "action" or parent.schedule_id != schedule_id:
                raise CoreApiError("Failed to create run: 422 invalid_parent")
        return self.add_run(schedule_id=schedule_id, kind=kind, trigger_at=trigger_at, parent_run_id=parent_run_id,
                            snapshot=snapshot)

    def update_run(
        self,
        instance_id: int,
        run_id: int,
        outcome: str,
        message: str | None = None,
        attempts: int | None = None,
        snapshot: dict | None = None,
        steps: list[dict] | None = None,
    ) -> Run:
        self._enter("update_run", instance_id)
        self.updates.append((run_id, outcome, snapshot))
        run = self.runs[run_id]
        override = run.outcome == "ok" and outcome == "overridden"
        if run.outcome in TERMINAL and not override:
            raise CoreApiConflict("Failed to update run: 409", "run_finished")
        if outcome == "running" and run.outcome != "pending":
            raise CoreApiConflict("Failed to update run: 409", "run_not_pending")
        changes: dict = {"outcome": outcome}
        given = {"message": message, "attempts": attempts, "snapshot": snapshot, "steps": steps}
        changes.update({k: v for k, v in given.items() if v is not None})
        if outcome == "running":
            changes["started_at"] = self.clock()
        elif not override:
            changes["finished_at"] = self.clock()
        self.runs[run_id] = dataclasses.replace(run, **changes)
        return self.runs[run_id]

    def _instance_runs(self, instance_id: int) -> list[Run]:
        ids = {s.id for s in self.schedules.get(instance_id, [])}
        return sorted((r for r in self.runs.values() if r.schedule_id in ids), key=lambda r: (r.trigger_at, r.id))

    def list_unfinished_runs(self, instance_id: int) -> list[Run]:
        self._enter("list_unfinished_runs", instance_id)
        found = []
        for run in sorted(self._instance_runs(instance_id), key=lambda r: r.id):
            schedule = self._schedule(run.schedule_id)
            closed = any(r.parent_run_id == run.id and r.kind == "restore" and r.outcome in CLOSING_RESTORE
                         for r in self.runs.values())
            if run.kind == "action" and run.outcome in ("pending", "running", "ok", "partial") and not closed:
                if schedule is not None and schedule.restore.enabled:
                    found.append(run)
        return found

    def list_pending_test_runs(self, instance_id: int) -> list[Run]:
        self._enter("list_pending_test_runs", instance_id)
        return [r for r in self._instance_runs(instance_id) if r.kind == "test" and r.outcome == "pending"]

    def list_recent_runs(self, instance_id: int, since: datetime.datetime) -> list[Run]:
        self._enter("list_recent_runs", instance_id)
        return [r for r in self._instance_runs(instance_id) if r.trigger_at >= since]
