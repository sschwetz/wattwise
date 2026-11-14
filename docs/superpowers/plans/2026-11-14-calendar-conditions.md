# Calendar and Demand Window Conditions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Month, Day of week, Week (odd/even) and Demand window rows to the "Only if" list of the schedule editor, usable in the restore conditions too.

**Architecture:** Each condition gets an optional `kind`; a row without one is a reading row, so stored schedules do not change. The actor evaluates the new kinds from `context.now`, `context.timezone` and `context.windows`. The Core API validates them with a tagged union. The webapp's row editor swaps its controls by kind.

**Tech Stack:** Python 3 (dataclasses, pydantic, pytest, ruff, mypy) in `actron-neo-actor` and `core-api`; React, TypeScript, vitest in `webapp`.

**Spec:** `docs/superpowers/specs/2026-11-14-calendar-conditions-design.md`

## Global Constraints

- A condition with no `kind` is a reading row and keeps its stored shape: `reading`, `op`, `value`, `target`, `enabled`. The webapp never sends `kind` for reading rows.
- `months` rows: `op` is `in` or `not_in`; `values` are months 1-12.
- `days` rows: `op` is `in` or `not_in`; `values` are days 0-6 with Monday as 0.
- `week` rows: `parity` is `odd` or `even`.
- `demand_window` rows: `op` is `active` or `not_active`; `window_id` is an int, or null for any window.
- Week number is `floor((days_since_1970-01-01_of_site_local_date + 3) / 7)`. Odd is `week % 2 == 1`. Week 0 is even. Weeks run Monday to Sunday in the site timezone.
- A demand window is active when an occurrence in `context.windows` (matching `window_id` when set) has `start <= now < end`.
- Switched-off rows (`enabled` false) are saved as typed and skipped by validation, as reading rows are now: empty `values` and an unknown `window_id` are accepted on them.
- Month names are `Jan`...`Dec`, day names `Mon`...`Sun`, in reasons and labels.
- Prose in code comments, docs and commit messages follows `.claude/rules/avoid-ai-writing.md`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Commands: `uv run pytest -q`, `uv run ruff check .`, `uv run mypy` in `core-api` and `actron-neo-actor`; `npm run lint`, `npx tsc -b`, `npm test` in `webapp`.

## Review Focus

- A week row on the first Monday after a Sunday that is UTC-Sunday but Sydney-Monday: the site timezone, not UTC, decides. Tested in Task 1.
- The year-end: Sun 2027-01-03 and Mon 2027-01-04 must be an even then an odd week (ISO parity would give two odds). Tested in Task 1.
- A schedule saved before this change loads, saves and evaluates unchanged. Tested in Tasks 1, 2 and 3.
- An unticked months row with nothing selected saves, and the same ticked row is rejected. Tested in Tasks 2 and 3.
- A "demand window is not active" row whose window was removed passes; "is active" fails. Tested in Task 1.

---

### Task 1: Actor evaluates the new condition kinds

**Files:**
- Modify: `actron-neo-actor/actron_neo_actor/models.py` (`Condition`, lines 73-89)
- Modify: `actron-neo-actor/actron_neo_actor/schedule_eval.py` (conditions section, from `_CONTEXT_READINGS` down)
- Test: `actron-neo-actor/tests/test_schedule_eval.py`

**Interfaces:**
- Produces: `Condition` keeps `reading`, `op`, `value`, `target`, `enabled` in that order (so `Condition("outdoor_temp_c", "gt", 35.0)` still works) and gains, after `enabled`: `kind: str = "reading"`, `values: tuple[int, ...] = ()`, `parity: str | None = None`, `window_id: int | None = None`. `reading` defaults to `""`, `op` to `"gt"`, `value` to `0.0`. `Condition.from_json` reads `kind` (absent means `"reading"`), `values` (list to tuple), `parity`, `window_id`.
- Produces: `week_number(day: date) -> int` in `schedule_eval.py`, public, used by tests and by the webapp's mirror function.
- `evaluate_conditions` and `restore_gate_passes` keep their signatures; `early_restore_due` is unchanged.

- [ ] **Step 1: Write the failing tests** in `test_schedule_eval.py`. A helper `at_sydney(y, m, d, h=12)` returns the UTC instant for that Sydney local time (use `ZoneInfo(SYDNEY)`). Tests:
  - `test_week_number_epoch_anchor`: `week_number(date(1970,1,1)) == 0`, `date(1970,1,4) == 0`, `date(1970,1,5) == 1`, `date(2026,11,9) == date(2026,11,15) == 2967`, `date(2026,11,16) == 2968`.
  - `test_week_row_parity_and_reason`: with `Condition(kind="week", parity="odd")` and now Sat 2026-11-14 Sydney, passes; with `parity="even"`, `reason == "week is odd, not even"`.
  - `test_week_row_uses_site_timezone`: now `utc(2026,11,15,14)` (Sun UTC, Mon 01:00 Sydney) fails `parity="odd"` and passes `parity="even"`.
  - `test_week_alternates_across_year_end`: Sun 2027-01-03 passes `even`; Mon 2027-01-04 passes `odd`.
  - `test_months_row`: `Condition(kind="months", op="in", values=(12,1,2))` with now in July: `reason == "month Jul is not in Dec, Jan, Feb"`; in January passes. `op="not_in", values=(7,8)` in July: `reason == "month Jul is in Jul, Aug"`.
  - `test_days_row`: `kind="days", op="in", values=(0,1,2,3,4)` on Sat 2026-11-14: `reason == "day Sat is not in Mon, Tue, Wed, Thu, Fri"`; `op="not_in", values=(5,6)` on Sat: `reason == "day Sat is in Sat, Sun"`. Day number is `local_date.weekday()`.
  - `test_demand_window_row`: a `WindowOccurrence(7, "Peak", start, end)` around now. `op="not_active"` fails with `reason == "demand window Peak is active"`; `op="active"` passes; `window_id=8` with `active` fails with `reason == "no demand window is active"` and with `not_active` passes; no windows plus `not_active` passes; `now == end` is not active, `now == start` is active.
  - `test_calendar_rows_ignore_stale_and_missing_readings`: a week row passes with `stale={"outdoor_temp_c"}` and empty readings.
  - `test_restore_gate_evaluates_calendar_rows`: `restore_gate_passes([Condition(kind="week", parity="even")], {}, set(), ctx)` is False on an odd week (a missing reading passes the gate, a failing calendar row does not).
  - `test_switched_off_calendar_row_is_ignored`: `enabled=False` passes.
  - `test_condition_from_json_old_and_new`: a dict with no `kind` gives `kind == "reading"`; `{"kind":"months","op":"in","values":[12,1],"enabled":True}` gives `values == (12, 1)`.

- [ ] **Step 2: Run to verify failure**

Run (in `actron-neo-actor`): `uv run pytest tests/test_schedule_eval.py -q`
Expected: new tests FAIL (unknown keyword `kind`, `week_number` missing).

- [ ] **Step 3: Extend `Condition`** as specified under Interfaces. `from_json` keeps its `enabled` bool check and the "missing enabled counts as on" rule; reading rows still need `reading`, `op`, `value` keys, other kinds do not.

- [ ] **Step 4: Implement in `schedule_eval.py`:**
  - `week_number(day: date) -> int`: the formula in Global Constraints.
  - `_local_now(context: Context) -> datetime`: `context.now.astimezone(ZoneInfo(context.timezone))`.
  - `_calendar_result(cond: Condition, context: Context) -> ConditionResult | None`: handles `months`, `days`, `week`, `demand_window`; returns `None` for `reading`. Reason strings are exactly those in Step 1. A set of month and day name tuples lives at module level.
  - `evaluate_conditions`: in the loop, a non-reading kind goes through `_calendar_result` and a failed result is returned at once. An unknown kind fails with `unsupported condition: {kind}`.
  - `restore_gate_passes`: a non-reading kind that does not pass returns False.
  - Keep each new function under 30 lines.

- [ ] **Step 5: Run the actor tests, ruff and mypy**

Run (in `actron-neo-actor`): `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add actron-neo-actor
git commit -m "feat(actor): evaluate month, day, week and demand window conditions"
```

---

### Task 2: Core API accepts, validates and documents the new kinds

**Files:**
- Modify: `core-api/core_api/actor_schemas.py` (`ConditionSpec`, line 76; `_conditions_with_flag` stays)
- Modify: `core-api/core_api/actor_capabilities.py` (`validate_conditions_against`, line 88)
- Modify: `core-api/core_api/actor_schedule_validation.py` (`validate_schedule`, line 60; add a window check next to `_check_window_belongs_to_site`)
- Modify: `contracts/actor-api/schedules_list_entry.json` and `contracts/actor-api/README.md` (condition shapes)
- Test: `core-api/tests/test_actors_router.py`, `core-api/tests/test_actor_contract.py`, and the actor's `tests/test_actor_models.py` pattern if the contract test parses examples with the actor model

**Interfaces:**
- Produces in `actor_schemas.py`: `ReadingCondition` (today's fields, no `kind`), `MonthsCondition`, `DaysCondition`, `WeekCondition`, `DemandWindowCondition`, each `_Strict` with `kind: Literal[...]` except `ReadingCondition`, plus `enabled: bool = True`. `ConditionSpec` becomes an `Annotated` union of the five using a callable `Discriminator` that tags a dict or model by its `kind` (missing means reading). Fields per Global Constraints.
- `MonthsCondition.values` and `DaysCondition.values` are `list[int]`; each element is range-checked always, and "at least one" is checked only when `enabled`.
- Produces: `check_condition_windows(db: Session, site_id: int, conditions: Iterable[ConditionSpec]) -> None` in `actor_schedule_validation.py`: for each enabled `DemandWindowCondition` with a `window_id`, raise `AppError(422, "invalid_schedule", f"Window {id} is not a demand window of this site")` unless the same query as `_check_window_belongs_to_site` finds it. Called from `validate_schedule` on the same condition list it already builds.
- `validate_conditions_against` runs `_check_condition` only for enabled `ReadingCondition` rows and skips other kinds.

- [ ] **Step 1: Write the failing tests** in `test_actors_router.py`, using its `_body` and `_base` helpers:
  - `test_create_schedule_with_calendar_conditions`: POST with conditions `[{"kind":"months","op":"in","values":[12,1,2],"enabled":True}, {"kind":"days","op":"not_in","values":[5,6]}, {"kind":"week","parity":"odd"}, {"kind":"demand_window","op":"not_active","window_id":None}]` returns 201, and `db.get(ActorSchedule, id).conditions[0]` equals the posted months dict plus `enabled`.
  - `test_reading_condition_is_stored_without_kind`: the existing `ON` reading row stored has no `kind` key.
  - `test_ticked_months_row_needs_values`: `{"kind":"months","op":"in","values":[]}` enabled gives 422; with `"enabled": False` gives 201.
  - `test_month_and_day_ranges`: `values:[13]` and `days values:[7]` give 422.
  - `test_condition_window_must_belong_to_site`: an enabled demand_window row with a window id of another site gives 422 `invalid_schedule`; one of this site gives 201; a disabled one with an unknown id gives 201.
  - `test_same_kinds_in_restore_conditions`: a week row in `restore.only_if` and a days row in `restore.early_if` save.
  - `test_unknown_kind_rejected`: `{"kind":"moon"}` gives 422.
  - In `test_actor_contract.py`, make the example in `schedules_list_entry.json` include one row of each new kind (months in `conditions`, week and demand_window in `restore.only_if`, days in `restore.early_if`) and assert the existing parse of that file still passes through both `ScheduleOut` and the actor's `Schedule.from_json`.

- [ ] **Step 2: Run to verify failure**

Run (in `core-api`): `uv run pytest tests/test_actors_router.py tests/test_actor_contract.py -q`
Expected: new tests FAIL.

- [ ] **Step 3: Implement the five models and the tagged `ConditionSpec`** in `actor_schemas.py` as under Interfaces. Replace `isinstance`-free attribute access elsewhere (`cond.reading`) with the narrowing in `actor_capabilities.py`.

- [ ] **Step 4: Implement `check_condition_windows` and wire it into `validate_schedule`; make `validate_conditions_against` skip non-reading kinds.**

- [ ] **Step 5: Update the contract example and README** with one row per kind and a short table of the fields, matching Global Constraints.

- [ ] **Step 6: Run the Core API suite, ruff and mypy**

Run (in `core-api`): `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: all pass, including the router and contract tests that existed before.

- [ ] **Step 7: Commit**

```bash
git add core-api contracts
git commit -m "feat(core-api): accept month, day, week and demand window conditions"
```

---

### Task 3: Webapp form model, payload and validation

**Files:**
- Modify: `webapp/src/actors/types.ts` (`ConditionSpec`, line 49)
- Modify: `webapp/src/actors/scheduleForm.ts` (`ConditionRow`, `addCondition`, `conditionRows`, `conditionsPayload`, `conditionAccepted`)
- Modify: `webapp/src/actors/scheduleValidation.ts` (`rowError`, `rowErrors`)
- Modify: `webapp/src/actors/scheduleDirty.ts` if the row strip needs the new fields (it strips only `key`, so it likely does not)
- Create: `webapp/src/actors/calendarConditions.ts` (pure helpers)
- Test: `webapp/src/actors/scheduleForm.test.ts`, `webapp/src/actors/scheduleDirty.test.ts`, new `webapp/src/actors/calendarConditions.test.ts`

**Interfaces:**
- Produces in `types.ts`: `ConditionSpec` is a union: the existing shape (with optional `kind?: undefined`), `{ kind: "months"; op: "in" | "not_in"; values: number[]; enabled?: boolean }`, `{ kind: "days"; ... }` likewise, `{ kind: "week"; parity: "odd" | "even"; enabled?: boolean }`, `{ kind: "demand_window"; op: "active" | "not_active"; window_id: number | null; enabled?: boolean }`.
- Produces in `scheduleForm.ts`: `ConditionRow` is a union, each member with `key: number; use: boolean; kind: ...`: `kind: "reading"` (today's fields), `"months"` and `"days"` (`op: "in" | "not_in"; values: number[]`), `"week"` (`parity: "odd" | "even"`), `"demand_window"` (`op: "active" | "not_active"; windowId: string`, empty string means any). Export `type ConditionKind = ConditionRow["kind"]`.
- Produces in `calendarConditions.ts`:
  - `weekNumber(year: number, month: number, day: number): number` (month 1-12), the same formula as the actor.
  - `currentWeekParity(now: Date, timezone: string): "odd" | "even"`: the site-local date of `now`, then `weekNumber`. Local date from `Intl.DateTimeFormat("en-CA", { timeZone, ... })` as `dayKey` in `runFormat.ts` does.
  - `changeKind(row: ConditionRow, kind: ConditionKind, defaults: { readings: ReadingChoice[]; parity: "odd" | "even" }): ConditionRow`: keeps `key` and `use`; reading kind uses `pointAtReading(..., defaults.readings[0])`; months and days start with `op: "in"`, `values: []`; week starts at `defaults.parity`; demand window starts `op: "not_active"`, `windowId: ""`.
- `addCondition(rows, readings)` is unchanged in behavior and now sets `kind: "reading"`.

- [ ] **Step 1: Write the failing tests:**
  - `calendarConditions.test.ts`: `weekNumber(1970,1,1) === 0`; `weekNumber(1970,1,5) === 1`; `weekNumber(2026,11,14) === 2967`; `weekNumber(2027,1,3) === 2974` and `(2027,1,4) === 2975`; `currentWeekParity(new Date("2026-11-14T02:00:00Z"), "Australia/Sydney") === "odd"`; `currentWeekParity(new Date("2026-11-15T14:00:00Z"), "Australia/Sydney") === "even"` (Sydney Monday); `changeKind` cases for each kind, including that `key` and `use` survive.
  - `scheduleForm.test.ts`: round trip through `fromSchedule` then `toPayload` for one saved row of each kind gives back the same specs (months `values` sorted ascending in the payload); a reading row in the payload has no `kind` key; an unticked months row with `values: []` is sent as `{ kind: "months", op: "in", values: [], enabled: false }`; a ticked demand window row with `windowId: ""` sends `window_id: null`; a ticked months row with no values is dropped from the payload as a ticked number row with no number is today; `conditionAccepted` returns true for non-reading kinds.
  - `scheduleValidation` cases, in `scheduleForm.test.ts` beside the existing `validateForm` ones: a ticked months or days row with no values gives an error under `conditions.<key>` reading `Pick at least one month.` and `Pick at least one day.`; an unticked one gives none; a ticked demand window row whose `windowId` is not in the supplied windows list gives `This demand window is no longer available. Pick another.`. `validateForm` takes the windows as a new third argument `windows: DemandWindow[]`; update its existing callers.
  - `scheduleDirty.test.ts`: changing a months row's `values` marks the form dirty; changing only a row `key` does not.

- [ ] **Step 2: Run to verify failure**

Run (in `webapp`): `npm test -- src/actors/calendarConditions.test.ts src/actors/scheduleForm.test.ts src/actors/scheduleDirty.test.ts`
Expected: FAIL (module and exports missing).

- [ ] **Step 3: Implement the types, `calendarConditions.ts`, and the changes to `conditionRows`, `conditionsPayload`, `conditionAccepted`, `rowError`.** `rowError` switches on `row.kind`; the reading branch is today's code. Existing test fixtures that build `ConditionRow` literals gain `kind: "reading"`.

- [ ] **Step 4: Run lint, types and tests**

Run (in `webapp`): `npm run lint && npx tsc -b && npm test`
Expected: pass. Components still compile because Task 4 has not changed `ConditionsEditor`; if `tsc` flags the editor reading `row.reading` on the union, narrow it with `row.kind === "reading"` in the smallest way that keeps behavior (Task 4 replaces it).

- [ ] **Step 5: Commit**

```bash
git add webapp/src/actors
git commit -m "feat(webapp): model month, day, week and demand window condition rows"
```

---

### Task 4: Webapp row editor controls

**Files:**
- Modify: `webapp/src/actors/ConditionsEditor.tsx`
- Modify: `webapp/src/actors/ScheduleEditor.tsx` (`EditorSetup`, `OnlyIfStep`)
- Modify: `webapp/src/actors/RestoreStep.tsx` (`RestoreConditions`)
- Modify: `webapp/src/actors/ScheduleDetail.tsx` (line 90, where `setup` is built) and the new-schedule page that builds `EditorSetup` (`webapp/src/pages/SchedulePage.tsx` or wherever `ScheduleEditor` is given `setup`; find with `grep -rn "<ScheduleEditor" webapp/src`)
- Modify: `webapp/src/actors/editorModel.ts` if `EditorModel` needs the windows and timezone
- Test: `webapp/src/actors/ScheduleEditor.test.tsx`, `webapp/src/actors/ScheduleDetail.test.tsx`

**Interfaces:**
- Consumes: `ConditionRow`, `ConditionKind`, `changeKind`, `currentWeekParity`, `windowLabel` (from `demandWindows.ts`), `SHORT_MONTHS` and `SHORT_DAYS` (from `../sources/rateWindowUi`).
- Produces: `EditorSetup` gains `timezone: string`. `ConditionsModel` gains `calendar: { windows: DemandWindow[]; timezone: string }`. `EditorModel` gains the same two values so both steps can fill `calendar`.
- Row UI, by kind, after the tick box and the kind select (labels are what tests find by role and label):
  - The first select has `aria-label="Reading {n}"` today. It lists the readings, then an `<optgroup label="Calendar">` with options Month, Day of week, Week and Demand window. Picking one calls `changeKind`. Its value is the reading id for reading rows and the kind otherwise.
  - Month: `aria-label="Month comparison {n}"` select (`is` / `is not`), then 12 toggle buttons named by `SHORT_MONTHS`, `aria-pressed` set; same for Day with `aria-label="Day comparison {n}"` and `SHORT_DAYS` (Mon first, matching value 0).
  - Week: text "is" then `aria-label="Week parity {n}"` select with Odd and Even.
  - Demand window: `aria-label="Window state {n}"` select (`is` / `is not` followed by the word "active"), then `aria-label="Window {n}"` select with "Any demand window" first then each window by `windowLabel`, and a stale id shown as `{id} (no longer available)` like `ReadingSelect` does.
- Adding a Week row (kind change or "Add condition" then picking Week) starts at `currentWeekParity(new Date(), timezone)`.

- [ ] **Step 1: Write the failing tests** in `ScheduleEditor.test.tsx` (use its existing render helper and a fixed `setup.timezone = "Australia/Sydney"` and fake timers set to Sat 2026-11-14):
  - choosing "Month" in `Reading 1` shows 12 toggle buttons, pressing Dec, Jan and Feb then saving sends `{ kind: "months", op: "in", values: [1, 2, 12], enabled: true }`.
  - choosing "Week" shows the Week parity select preset to Odd; saving sends `{ kind: "week", parity: "odd", enabled: true }`; with fake time on Mon 2026-11-16 it presets to Even.
  - choosing "Demand window" and "is not" sends `{ kind: "demand_window", op: "not_active", window_id: null, enabled: true }`; selecting a listed window sends its id.
  - a ticked Month row with nothing pressed blocks the save and shows `Pick at least one month.`
  - the summer example: three rows (Month Dec-Feb, outdoor above 30, demand window is not active) save in one payload in that order.
  - a saved schedule with a `days` row opens with the right buttons pressed.
  - the restore step's "Only restore if" also offers the Calendar group.
  - in `ScheduleDetail.test.tsx`, the editor receives the site's timezone (a Week row opened there presets by that zone).

- [ ] **Step 2: Run to verify failure**

Run (in `webapp`): `npm test -- src/actors/ScheduleEditor.test.tsx src/actors/ScheduleDetail.test.tsx`
Expected: new tests FAIL.

- [ ] **Step 3: Implement the editor.** Split `ConditionLine` so each kind is its own small component (`MonthsControls`, `DaysControls`, `WeekControls`, `WindowControls`, plus today's reading controls) and keep every function under 30 lines. Group props into the existing model objects, not new positional props. Add `timezone` to `EditorSetup` and pass it where the editor is built; the site's timezone comes from `useSites()` as in `FailuresTile.tsx:49`. Update the hint text under the step to `Every ticked condition must be true, or the run is skipped.` (unchanged) and make `validateForm` receive `setup.windows`.

- [ ] **Step 4: Run lint, types and the whole webapp suite**

Run (in `webapp`): `npm run lint && npx tsc -b && npm test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add webapp
git commit -m "feat(webapp): pick months, days, week parity and demand window in Only if"
```

---

### Task 5: Docs, cross-check and final review

**Files:**
- Modify: user docs under `webapp/src/docs` or `docs/` that describe the "Only if" step (find with `grep -rn -i "only if" webapp/src/docs docs README.md`)

- [ ] **Step 1: Update the docs** for the "Only if" step and the restore conditions: the four new rows, that weeks run Monday to Sunday and alternate from 1 January 1970 (so setting odd or even needs no date), and that all of it uses the site timezone. Use the `documentation` agent type if available.

- [ ] **Step 2: Cross-check the week rule** across the actor, webapp and docs: run the dates in `test_week_number_epoch_anchor` and `calendarConditions.test.ts` and confirm both give 2967 for Sat 2026-11-14.

- [ ] **Step 3: Run every suite once**

Run: `cd actron-neo-actor && uv run pytest -q && uv run ruff check . && uv run mypy`, then the same in `core-api`, then `cd webapp && npm run lint && npx tsc -b && npm test`
Expected: all pass.

- [ ] **Step 4: Run `/simplify`** on the branch diff, then check the CLAUDE.md review list: functions over 30 lines, duplicated logic (month and day toggle groups should share one component), `any` types, components with more than 3 props, async error handling. Fix what it finds and rerun Step 3.

- [ ] **Step 5: Commit**

```bash
git add -A docs webapp/src/docs
git commit -m "docs: describe calendar and demand window conditions"
```
