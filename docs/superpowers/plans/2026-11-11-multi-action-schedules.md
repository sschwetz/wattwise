# Multi-action schedules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A schedule holds an ordered list of actions, with a per-zone temperature action, per-action results in the run log, and one restore for everything that changed.

**Architecture:** `actor_schedules.action` becomes the list `actions`. The actor builds one snapshot (a list of per-action entries) before it sends any command, runs the actions in order, records each result in a new `steps` column on the run, and sets the run outcome (`ok`, `partial` or `failed`). The editor shows a list of action cards.

**Tech Stack:** Python 3.12 (FastAPI, SQLAlchemy, Alembic, pydantic) in `core-api/`; Python asyncio with `actron-neo-api==0.5.16` in `actron-neo-actor/`; React, TypeScript and vitest in `webapp/`.

**Spec:** [`docs/superpowers/specs/2026-11-11-multi-action-schedules-design.md`](../specs/2026-11-11-multi-action-schedules-design.md)

## Global Constraints

- A schedule has 1 to 8 actions. Core API refuses 0 or 9 with 422 `invalid_schedule`.
- A step's outcome is `ok` or `failed`. A run's outcome is `ok` (all steps ok), `partial` (some ok, some failed) or `failed` (all failed). Only `ok` and `partial` runs owe a restore.
- `partial` is unfinished (holds the unit) everywhere `ok` is, and counts as a failure for the Dashboard tile and the email.
- Zone range comes from the library's `zone.min_temp` and `zone.max_temp`. Hard-code no ±2 or any other offset.
- Core API returns both `actions` and `action` (the first entry) for one release. The actor reads `actions`, then falls back to `action`.
- Restore accepts the old single-entry snapshot `{action, applied, saved}` and the new list form.
- Alembic head is `a1b2c3d4e5f6`; the new migration revises it.
- `actron-neo-api` stays pinned at `0.5.16`.
- Project rules (`CLAUDE.md`): no function over 30 lines, no `any` in TypeScript, group components with more than 3 props, handle errors on async operations, no secrets in plaintext, prose follows `.claude/rules/avoid-ai-writing.md`. Run `/simplify` before presenting.
- Test commands: `cd core-api && uv run pytest -q`, `cd actron-neo-actor && uv run pytest -q`, `cd webapp && npx vitest run`. Also run `uv run ruff check .` and `uv run mypy` in each Python package, and `npm run lint` in `webapp`.

## Review Focus

Each line has a test in the task that owns the code.

1. A run created before the upgrade, with the old single-entry snapshot and an old single-action schedule, still restores (Task 6).
2. A partial run restores only the steps that worked, never the failed ones (Task 5).
3. A zone temperature exactly at `min_temp` or `max_temp` is accepted; one step beyond fails and sends nothing (Task 4).
4. Two actions that set the same field restore to the value from before the run, not the first action's result (Task 5).
5. An actor that restarts after the third of four actions finishes the rest and does not repeat the first three (Task 6).

## File Structure

| File | Change |
|---|---|
| `core-api/alembic/versions/<new>_multi_action_schedules.py` | Create. Adds `actions`, `steps`, `partial`; copies `action` into `actions`. |
| `core-api/core_api/models/actors.py` | `ActorSchedule.actions`, `ActorRun.steps`, `RunOutcome.PARTIAL`. |
| `core-api/core_api/actor_schemas.py` | `actions` on create, patch, view and actor output; `steps` on run update and output. |
| `core-api/core_api/actor_schedule_validation.py` | Validate every action. |
| `core-api/core_api/actor_runs.py`, `actor_failures.py`, `routers/actors.py`, `routers/actor.py` | Treat `partial` like `ok` for holding the unit, like `failed` for alerts and email. |
| `contracts/actor-api/schedules_list_entry.json`, `capabilities_request.json` | `actions`, `set_zone_temperature`. |
| `actron-neo-actor/actron_neo_actor/models.py`, `core_api_client.py`, `async_core.py` | `Schedule.actions`, `Run.steps`, `steps` on run updates. |
| `actron-neo-actor/actron_neo_actor/device_state.py`, `capabilities.py`, `device_snapshot.py`, `device.py` | Zone setpoints and ranges, `set_zone_temperature`. |
| `actron-neo-actor/actron_neo_actor/snapshots.py` | Create. Multi-action snapshot, ordering, filtering. |
| `actron-neo-actor/actron_neo_actor/actions.py`, `restores.py`, `resume.py`, `test_runs.py`, `unit_checks.py` | Run, restore and resume a list. |
| `webapp/src/actors/types.ts`, `scheduleForm.ts`, `scheduleValidation.ts`, `scheduleDirty.ts`, `serverRules.testHelper.ts` | `actions: ActionForm[]`. |
| `webapp/src/actors/ActionStep.tsx`, `ActionCard.tsx` (create), `RestoreStep.tsx` | Action list UI. |
| `webapp/src/actors/runFormat.ts`, `RunHistory.tsx`, `ScheduleDetail.tsx`, `ScheduleList.tsx`, `FailuresTile.tsx` | Steps, `partial` chip, all actions shown. |
| `docs/actors.md`, `docs/data-model.md`, `docs/superpowers/specs/2026-11-04-actors-design.md` | Update. |

---

### Task 1: Core API stores and validates a list of actions

**Files:**
- Create: `core-api/alembic/versions/<new>_multi_action_schedules.py`
- Modify: `core-api/core_api/models/actors.py`, `core-api/core_api/actor_schemas.py`, `core-api/core_api/actor_schedule_validation.py`, `core-api/core_api/routers/actors.py` (`_SCHEDULE_FIELDS`, `_HELD_FIELDS`), `core-api/core_api/routers/actor.py` (`ActorScheduleOut`), `contracts/actor-api/schedules_list_entry.json`
- Test: `core-api/tests/test_actor_models.py`, `test_actors_router.py`, `test_actor_router.py`, `test_actor_contract.py`

**Interfaces:**
- Produces: `ScheduleCreate.actions: list[ActionSpec]` (`min_length=1`, `max_length=8`); `ScheduleView.actions: list[dict]` and `.action: dict`; `ActorScheduleOut.actions` and `.action`; `ActorSchedule.actions` JSON column (list of `{id, params}`); `ActorSchedule.action` removed from the model, with `action` computed on the output schemas as `actions[0]`.

- [ ] **Step 1: Write failing tests.** `test_create_schedule_with_two_actions`, `test_create_schedule_rejects_zero_and_nine_actions` (422 `invalid_schedule`), `test_create_schedule_rejects_one_invalid_action_of_three` (error names the failing entry's index), `test_patch_actions_replaces_whole_list`, `test_patch_actions_refused_while_held_off` (409 `unit_held_off`), `test_actor_schedules_return_actions_and_first_action`, and a migration test that a row with `action={"id":"system_off","params":{}}` becomes `actions=[{"id":"system_off","params":{}}]`.
- [ ] **Step 2: Run** `uv run pytest tests/test_actors_router.py tests/test_actor_router.py tests/test_actor_models.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement the migration** revising `a1b2c3d4e5f6`: add `actions` JSON, copy `[action]` into it, drop `action`; add `steps` JSON nullable to `actor_runs`; rebuild `ck_actor_runs_outcome` with `partial`. Downgrade copies `actions[0]` back. Follow how `895863e431c6_add_actors.py` writes the check constraint.
- [ ] **Step 4: Implement the model, schema and validation changes.** `validate_schedule(db, site_id, instance, schedule)` loops `validate_action_against` over `schedule.actions` and prefixes any error with `actions[i]`. Keep the error code `invalid_schedule`.
- [ ] **Step 5: Update `contracts/actor-api/schedules_list_entry.json`** to show `actions` and `action`, and keep `test_actor_contract.py` green.
- [ ] **Step 6: Run** `uv run pytest -q && uv run ruff check . && uv run mypy`. Expected: PASS.
- [ ] **Step 7: Commit** `feat(core-api): store a list of actions per schedule`.

### Task 2: Core API handles the `partial` outcome and run steps

**Files:**
- Modify: `core-api/core_api/actor_runs.py` (`_OPEN_ACTION_OUTCOMES`), `core-api/core_api/actor_failures.py`, `core-api/core_api/routers/actor.py` (`_TERMINAL`, `_transition_run`, `update_actor_run` email), `core-api/core_api/actor_schemas.py` (`RunUpdate.steps`, `RunOut.steps`), `core-api/core_api/models/actors.py`
- Test: `core-api/tests/test_actor_runs.py`, `test_actor_runs_helpers.py`, `test_actor_failures.py`, `test_actor_router.py`

**Interfaces:**
- Consumes: `RunOutcome.PARTIAL` and `ActorRun.steps` from Task 1.
- Produces: `RunUpdate.steps: list[StepResult] | None` where `StepResult` is `{index: int, action_id: str, params: dict, outcome: Literal["ok","failed"], message: str | None, attempts: int}`; `RunOut.steps`. `partial` is terminal, and the `ok` to `overridden` change stays the only change allowed on a finished run.

- [ ] **Step 1: Write failing tests.** `test_partial_run_is_unfinished_until_restored`; `test_partial_run_blocks_delete_with_unit_held_off`; `test_partial_run_shows_in_failures_until_later_ok`; `test_partial_run_emails_when_notify_email`; `test_run_update_stores_steps`; `test_partial_cannot_return_to_pending`.
- [ ] **Step 2: Run** `uv run pytest tests/test_actor_runs.py tests/test_actor_failures.py tests/test_actor_router.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `_OPEN_ACTION_OUTCOMES` gains `PARTIAL`. The failure query and the update email treat `FAILED` and `PARTIAL` alike; the failure text for a partial run reads `The action partly failed`. `_transition_run` writes `steps` with `message`, `attempts` and `snapshot` unless overriding.
- [ ] **Step 4: Run** the full Core API checks as in Task 1 Step 6. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core-api): add the partial run outcome and per-step results`.

### Task 3: The actor reads `actions` and writes `steps`

**Files:**
- Modify: `actron-neo-actor/actron_neo_actor/models.py`, `core_api_client.py` (`update_run`), `async_core.py` (`update_run`), `executor.py` (`update`)
- Test: `actron-neo-actor/tests/test_contract.py`, `test_core_api_client.py`, `fake_core.py`

**Interfaces:**
- Produces: `Schedule.actions: list[ActionSpec]` (parsed from `actions`, else `[action]`); `Run.steps: list[dict] | None`; `update_run(instance_id, run_id, outcome, message, attempts, snapshot, steps=None)` on `CoreApiClient` and `AsyncCore`; `executor.update(env, run, outcome, message=None, *, attempts=None, snapshot=None, steps=None) -> Run | None`. `fake_core.py` accepts and stores `steps`.

- [ ] **Step 1: Write failing tests.** `test_schedule_reads_actions_list`, `test_schedule_falls_back_to_single_action`, `test_update_run_sends_steps_only_when_given`, `test_run_parses_steps_and_old_runs_without_them`.
- [ ] **Step 2: Run** `uv run pytest tests/test_contract.py tests/test_core_api_client.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the signatures above. Keep `Schedule.action` as a property returning `actions[0]` until Task 6 removes its last caller.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && uv run mypy`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(actor): read the actions list and report steps`.

### Task 4: `set_zone_temperature` with a range check

**Files:**
- Modify: `actron-neo-actor/actron_neo_actor/device_state.py` (`DeviceState`), `capabilities.py`, `device_snapshot.py`, `device.py` (`_dispatch`), `contracts/actor-api/capabilities_request.json`
- Test: `actron-neo-actor/tests/test_device.py`, `test_contract.py`

**Interfaces:**
- Produces: `DeviceState.zone_setpoints_c: dict[str, float]` and `DeviceState.zone_ranges_c: dict[str, tuple[float, float]]` (title to the library's `zone.current_setpoint` and `(zone.min_temp, zone.max_temp)`), included in `to_payload()`; action `set_zone_temperature` with params `zone` (choice from zone titles) and `temperature_c` (number, `min` and `max` the unit-wide limits), `restorable: true`; `check_zone_range(zone: str, value: float, state: DeviceState) -> None` raising `DeviceError`.

- [ ] **Step 1: Write failing tests.** `test_zone_temperature_inside_range_sends_command`; `test_zone_temperature_at_min_and_max_is_accepted`; `test_zone_temperature_just_outside_range_fails_and_sends_nothing` (message `Study 28.0°C is outside 20.0–24.0°C (the unit is set to 22.0°C)`); `test_zone_temperature_in_fan_and_off_mode_fails_with_library_text`; `test_unknown_zone_fails`; `test_capabilities_declare_set_zone_temperature`.
- [ ] **Step 2: Run** `uv run pytest tests/test_device.py tests/test_contract.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `_dispatch` for the new action calls `check_zone_range`, then the zone's `set_temperature`. The range check runs before any command, because the library clamps silently. Snapshot entry: `applied {"zone": title, "setpoint_c": float}`, `saved {"zone": title, "cool_setpoint_c": float, "heat_setpoint_c": float}`. `matches_action` compares the zone's current setpoint with `setpoint_c` within `SETPOINT_TOLERANCE_C`. Restore reports the field as `zone_temp:<title>`.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && uv run mypy`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(actor): add set_zone_temperature with a range check`.

### Task 5: One snapshot for many actions, one restore

**Files:**
- Create: `actron-neo-actor/actron_neo_actor/snapshots.py`
- Modify: `actron-neo-actor/actron_neo_actor/device.py` (`snapshot_for`, `restore`), `device_snapshot.py` (`plan_restore`), `unit_checks.py` (`owner_changed`)
- Test: `actron-neo-actor/tests/test_snapshots.py` (create), `test_device.py`

**Interfaces:**
- Consumes: Task 4 entries.
- Produces: `snapshot_for_all(actions: list[ActionSpec], state: DeviceState) -> dict` returning `{"actions": [{"action","applied","saved"}, ...]}`; `restorable(snapshot: dict | None, steps: list[dict] | None) -> dict | None` keeping only entries whose step is `ok` (a legacy single-entry snapshot passes through unchanged); `plan_restore(snapshot, current)` accepts both shapes and orders main-unit fields before zone fields; `owner_changed(device, actions, snapshot, state) -> bool`, true only when every `ok` action no longer shows.

- [ ] **Step 1: Write failing tests.** `test_snapshot_lists_one_entry_per_action`; `test_restorable_drops_failed_steps`; `test_restorable_passes_legacy_snapshot_through`; `test_two_actions_on_one_field_restore_the_value_from_before_the_run`; `test_restore_puts_main_setpoint_back_before_zone_setpoints`; `test_restore_leaves_a_field_the_owner_changed`; `test_owner_changed_needs_every_ok_action_gone`.
- [ ] **Step 2: Run** `uv run pytest tests/test_snapshots.py tests/test_device.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `snapshot_for_all` computes every entry from the state before the run. When two entries save the same field, `plan_restore` uses the earlier entry's `saved` value and the later entry's `applied` value for the owner-changed check.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && uv run mypy`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(actor): snapshot and restore a list of actions`.

### Task 6: The actor runs, restores and resumes a list

**Files:**
- Modify: `actron-neo-actor/actron_neo_actor/actions.py` (`_apply`, `claim_and_apply`), `restores.py` (`run_restores`, `_check_override`, `_resume`), `resume.py` (`_carry_on`), `test_runs.py`, `models.py` (remove `Schedule.action`)
- Test: `actron-neo-actor/tests/test_worker.py`, `worker_support.py`, `fakes.py`

**Interfaces:**
- Consumes: Tasks 3 and 5.
- Produces: `run_actions(env, run, schedule, ...) -> tuple[Run, list[StepResult], str] | None` (the claimed run, the steps, the outcome); the final `update` sends `outcome`, `message`, `attempts` (sum over steps), `snapshot` (on the claim) and `steps`. Message for a partial run: `2 of 3 ran. Failed: <step message>; <step message>`. A restore is eligible when the parent run is `ok` or `partial`.

- [ ] **Step 1: Write failing tests.** `test_all_actions_ok_gives_ok`; `test_middle_action_fails_and_the_rest_still_run` (outcome `partial`, three steps, message names the failure); `test_all_actions_fail_gives_failed_and_no_restore`; `test_snapshot_is_saved_with_the_claim_before_any_command`; `test_give_up_marks_unstarted_actions_failed_with_gave_up`; `test_partial_run_restores_only_ok_steps`; `test_legacy_run_with_single_snapshot_restores` (Review Focus 1); `test_restart_after_third_of_four_actions_finishes_the_rest` (Review Focus 5); `test_test_now_runs_every_action`.
- [ ] **Step 2: Run** `uv run pytest tests/test_worker.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Per-action retry uses `apply_with_retries` with the shared `started` time, so `give_up_minutes` spans the run. On resume, `_carry_on` checks each `ok` or unrecorded step with `shows_action` and re-applies only those the unit does not show. Keep each function under 30 lines by moving step bookkeeping into `snapshots.py`-style helpers in `actions.py`.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check . && uv run mypy`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(actor): run a schedule's actions in order and restore them together`.

### Task 7: Web app form model for a list of actions

**Files:**
- Modify: `webapp/src/actors/types.ts`, `scheduleForm.ts`, `scheduleValidation.ts`, `scheduleDirty.ts`, `serverRules.testHelper.ts`
- Test: `webapp/src/actors/scheduleForm.test.ts`, `scheduleDirty.test.ts`

**Interfaces:**
- Produces: `ActorSchedule.actions: ActionSpec[]`, `ScheduleBody.actions`, `RunOutcome` gains `"partial"`, `ActorRun.steps: RunStep[] | null` with `RunStep = { index: number; action_id: string; params: Record<string, ActionParamValue>; outcome: "ok" | "failed"; message: string | null; attempts: number }`; `ActionForm = { key: number; actionId: string; params: Record<string, FormParam> }`; `ScheduleFormState.actions: ActionForm[]` replacing `actionId` and `params`; `addAction(form, capabilities): ScheduleFormState`, `removeAction(form, key: number)`, `moveAction(form, key: number, by: -1 | 1)`, `resetActionParams(form, key, action)`; `validateForm` errors keyed `actions.<key>.action` and `actions.<key>.param.<name>`.

- [ ] **Step 1: Write failing tests.** `test_form_from_schedule_builds_one_card_per_action`; `test_payload_sends_actions_in_order`; `test_add_remove_move_keep_stable_keys`; `test_cannot_remove_the_last_action`; `test_cannot_add_a_ninth_action`; `test_validation_reports_the_card_with_the_bad_param`; `test_dirty_check_sees_a_reordered_list`.
- [ ] **Step 2: Run** `cd webapp && npx vitest run src/actors/scheduleForm.test.ts src/actors/scheduleDirty.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** the signatures above. Keep `serverRules.testHelper.ts` in step with Core API: it validates each entry and refuses 0 or more than 8.
- [ ] **Step 4: Run** `npx vitest run && npm run lint && npx tsc --noEmit`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(webapp): hold a list of actions in the schedule form`.

### Task 8: Action list in the editor

**Files:**
- Create: `webapp/src/actors/ActionCard.tsx`
- Modify: `webapp/src/actors/ActionStep.tsx`, `RestoreStep.tsx` (restorable only if every action is), `ParamField.tsx` (zone range hint)
- Test: `webapp/src/actors/ScheduleEditor.test.tsx`

**Interfaces:**
- Consumes: Task 7 helpers.
- Produces: `ActionCard({ model, card }: { model: EditorModel; card: ActionForm })` with an action picker, its params, remove, move up and move down; an **Add action** button under the list; a hint under a `set_zone_temperature` card showing `Allowed now: <min>–<max>°C` from `zone_ranges_c` in the latest readings, and the line `Order matters: an earlier temperature change moves this range.`

- [ ] **Step 1: Write failing tests.** `adds a second action card`, `removes a card but not the last`, `moves a card down and the payload order follows`, `shows the error on the card it belongs to`, `shows the allowed range hint for a zone temperature card`, `hides Add action at eight cards`.
- [ ] **Step 2: Run** `npx vitest run src/actors/ScheduleEditor.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** `ActionCard` and `ActionStep` (the step keeps title "Do this"). Group `ActionCard` props into `{ model, card }`.
- [ ] **Step 4: Run** `npx vitest run && npm run lint && npx tsc --noEmit`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(webapp): add, remove and reorder actions in the editor`.

### Task 9: Run history, detail and list show every action

**Files:**
- Modify: `webapp/src/actors/runFormat.ts` (`OUTCOME_LABEL`, `OUTCOME_TONE`, `DEFAULT_MESSAGE`), `RunHistory.tsx` (line 62), `ScheduleDetail.tsx`, `ScheduleList.tsx`, `FailuresTile.tsx`
- Test: `webapp/src/actors/runFormat.test.ts`, `RunHistory.test.tsx`, `ScheduleDetail.test.tsx`, `ScheduleList.test.tsx`, `FailuresTile.test.tsx`

**Interfaces:**
- Consumes: `RunStep` and `partial` from Task 7.
- Produces: `outcomeLabel("partial")` is `Partly done`; `stepLines(run: ActorRun, labelFor: (id: string) => string): { text: string; tone: "ok" | "failed" }[]` with failures first; the history shows `labelFor` names for all actions of the schedule.

- [ ] **Step 1: Write failing tests.** `partial has its own chip and tone`; `history lists one line per step, failures first`; `history copes with a run that has no steps`; `detail and list name every action`; `failures tile includes a partial run`.
- [ ] **Step 2: Run** `npx vitest run src/actors`. Expected: FAIL.
- [ ] **Step 3: Implement** the helpers and the views.
- [ ] **Step 4: Run** `npx vitest run && npm run lint && npx tsc --noEmit`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(webapp): show per-action results and the partial outcome`.

### Task 10: Docs

**Files:**
- Modify: `docs/actors.md` (schedule, restore, runs, failure tables), `docs/data-model.md` (`actor_schedules`, `actor_runs`, run outcomes, snapshot), `docs/superpowers/specs/2026-11-04-actors-design.md` (remove the non-goal "Several actions in one schedule")
- Test: none

- [ ] **Step 1: Update the three files** to match the spec's wording: the list of actions, `set_zone_temperature` and its range rule, the `partial` outcome, the `steps` column, the rollout `action` field, and the two snapshot shapes.
- [ ] **Step 2: Run** `grep -rn "one action" docs/ | head` and fix any line that still says a schedule has one action.
- [ ] **Step 3: Commit** `docs: describe schedules with several actions`.

---

## Self-review

- **Spec coverage:** list of 1 to 8 (Tasks 1, 7, 8); `set_zone_temperature` and range (4); per-action results and continuing after a failure (6); `partial` (2, 6); one restore, ordering, same-field rule (5); legacy snapshot and rollout field (1, 3, 5, 6); editor (8); history (9); docs (10). Per-zone fan stays out, as the spec says.
- **Types:** `StepResult` (Core API), `Run.steps` (actor) and `RunStep` (web app) use the same six fields. `snapshot_for_all`, `restorable` and `owner_changed` are defined in Task 5 and used in Task 6 with the same signatures.
- **Left to the builder:** the migration's SQLite and Postgres details, the exact step-message wording, and the card layout.
