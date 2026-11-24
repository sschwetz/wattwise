# Admin Schedule Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin retunes the core-api background job timings from a new Schedule tab in Admin Settings, with no rebuild or restart.

**Architecture:** A registry in code defines the editable jobs and their defaults and limits. A `job_schedules` table stores overrides only. `build_scheduler` applies overrides at startup; admin endpoints write the table and call `reschedule_job` on the running APScheduler. The webapp adds one tab that follows the Email tab's hook and component pattern.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, APScheduler (`BackgroundScheduler`), pytest; React, TanStack Query, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-23-job-schedules-design.md`

## Global Constraints

- Two timing kinds only: `interval` (every N minutes) and `daily` (HH:MM). No cron expressions.
- Editable jobs: `demand_interval`, `demand_charge`, `retention_rollup`, `actor_watchdog`, `mail_outbox_cleanup`, `service_status`. `expire_admin_password_file` is not editable and not in the registry.
- A job with no row in `job_schedules` uses its default from code.
- `max_instances=1` and `coalesce=True` stay on every job.
- Admin endpoints use `require_admin` and log with `admin_users.log_admin_action(admin, None, action)`; never log values.
- Server limits are enforced with a 422 whose message names the failed limit.
- No `any` types in TypeScript; functions stay under 30 lines.
- Prose follows `.claude/rules/avoid-ai-writing.md`.
- Docs are updated in the same change (Task 5), and the `documentation` agent runs before handover.

## Review Focus

- A daily time of `24:00` or `7:5`, or an interval of `0`, `-5` or `1.5`, returns 422 and leaves the stored row and the running trigger unchanged.
- Saving a job that is mid-run does not start a second run (`max_instances=1` still holds after `reschedule_job`).
- `PUT` with a `kind` that does not match the job's registry kind (an interval on a daily job) returns 422.
- A job id not in the registry (including `expire_admin_password_file`) returns 404 on `PUT` and `DELETE`.
- `GET` with `SCHEDULER_ENABLED=false` returns timings with `next_run: null` and `scheduler_running: false`, and `PUT` still stores the row.

## File Structure

- Create `core-api/core_api/job_registry.py`: `JobSpec`, `JOBS`, `validate_timing`.
- Create `core-api/core_api/models/job_schedules.py`: `JobSchedule` model.
- Create `core-api/alembic/versions/b2d4f6a8c0e1_add_job_schedules.py`: migration.
- Create `core-api/core_api/admin_jobs.py`: read, save and reset logic.
- Create `core-api/core_api/admin_jobs_schemas.py`: pydantic request and response models.
- Create `core-api/core_api/routers/admin_jobs.py`: routes.
- Modify `core-api/core_api/scheduler.py`, `core-api/core_api/main.py`, `core-api/core_api/models/__init__.py`.
- Create `webapp/src/admin/useAdminJobs.ts`, `webapp/src/admin/ScheduleTab.tsx`, `webapp/src/admin/JobRow.tsx`, `webapp/src/admin/ScheduleTab.test.tsx`.
- Modify `webapp/src/admin/tabs.ts`, `webapp/src/admin/AdminSettingsPage.tsx`.
- Docs: `docs/core-api.md`, `docs/operations.md`, `docs/webapp.md`, `docs/data-model.md`, `docs/README.md`, new `webapp/src/docs/14-admin-schedule.md`, `webapp/src/docs/index.md`, `webapp/src/docs/11-glossary.md`.

---

### Task 1: Registry, model and migration

**Files:**
- Create: `core-api/core_api/job_registry.py`, `core-api/core_api/models/job_schedules.py`, `core-api/alembic/versions/b2d4f6a8c0e1_add_job_schedules.py`
- Modify: `core-api/core_api/models/__init__.py`
- Test: `core-api/tests/test_job_registry.py`

**Interfaces:**
- Produces:
  - `JobSpec` frozen dataclass: `id: str`, `label: str`, `kind: Literal["interval","daily"]`, `default_minutes: int | None`, `default_hour: int | None`, `default_minute: int | None`, `min_minutes: int | None`.
  - `JOBS: dict[str, JobSpec]` keyed by job id.
  - `Timing` frozen dataclass: `kind: str`, `interval_minutes: int | None`, `hour: int | None`, `minute: int | None`.
  - `default_timing(spec: JobSpec) -> Timing`.
  - `validate_timing(spec: JobSpec, timing: Timing) -> None`; raises `AppError(422, "invalid_job_schedule", message)`.
  - `JobSchedule` model, table `job_schedules`: `job_id` String(64) primary key, `kind` String(16), `interval_minutes` Integer nullable, `hour` Integer nullable, `minute` Integer nullable, `updated_at` DateTime(timezone=True).

- [ ] **Step 1: Write failing tests in `tests/test_job_registry.py`**
  - `test_registry_defaults_match_current_schedule`: `JOBS["demand_interval"].default_minutes == 5`; `JOBS["actor_watchdog"].default_minutes == 1`; `JOBS["service_status"].default_minutes == 1`; `JOBS["demand_charge"]` default is `00:10`; `retention_rollup` `00:20`; `mail_outbox_cleanup` `00:30`; `"expire_admin_password_file" not in JOBS`.
  - `test_validate_timing_rejects`: parametrized over `interval_minutes` of `0`, `-5`; `hour=24`; `minute=60`; an interval timing on a daily job; a daily timing on an interval job. Each raises `AppError` with status 422 and code `invalid_job_schedule`.
  - `test_validate_timing_accepts_default`: `validate_timing(spec, default_timing(spec))` passes for every job.
  - `test_job_schedule_row_round_trips` (uses `app_and_db_url` and a session as in `tests/test_admin_mail.py`): insert and read back a `JobSchedule`.

- [ ] **Step 2: Run `cd core-api && uv run pytest tests/test_job_registry.py -v`.** Expected: FAIL, module missing.

- [ ] **Step 3: Implement the registry, model and migration.** `min_minutes` is 1 for `demand_interval`, `actor_watchdog`, `service_status`; `None` for daily jobs. Add `JobSchedule` to `models/__init__.py` so Alembic autogenerate and the test DB create the table. The migration's `down_revision` is `a7c9e1b3d5f2` (current head).

- [ ] **Step 4: Run the tests again.** Expected: PASS. Also run `uv run pytest tests/test_migrations*.py -v` if present, to confirm a single head.

- [ ] **Step 5: Commit** `feat(core-api): add job registry and job_schedules table`.

---

### Task 2: Scheduler applies overrides and reschedules live

**Files:**
- Modify: `core-api/core_api/scheduler.py`
- Test: `core-api/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `JOBS`, `Timing`, `default_timing`, `JobSchedule` from Task 1.
- Produces in `scheduler.py`:
  - `trigger_for(timing: Timing) -> IntervalTrigger | CronTrigger`.
  - `load_timings(db: Session) -> dict[str, Timing]`: the effective timing per job id (override if a row exists, else default).
  - `apply_timing(scheduler: BackgroundScheduler, job_id: str, timing: Timing) -> None`: calls `scheduler.reschedule_job(job_id, trigger=trigger_for(timing))`.
  - `LAST_RUNS: dict[str, datetime.datetime]`, updated by `_run_with_session` with the job id after each run finishes (success or failure).
- `_run_with_session` gains a `job_id: str` first argument; existing `args=[...]` lists change to match.

- [ ] **Step 1: Write failing tests in `tests/test_scheduler.py`**
  - `test_build_scheduler_uses_stored_override`: insert `JobSchedule(job_id="demand_interval", kind="interval", interval_minutes=10)`, start the app with `SCHEDULER_ENABLED=true`, assert `scheduler.get_job("demand_interval").trigger.interval == datetime.timedelta(minutes=10)`.
  - `test_build_scheduler_uses_default_without_row`: same job has `timedelta(minutes=5)`.
  - `test_apply_timing_reschedules_running_job`: call `apply_timing(scheduler, "actor_watchdog", Timing("interval", 3, None, None))`, assert the trigger interval is 3 minutes and `max_instances == 1`, `coalesce is True`.
  - `test_apply_timing_daily`: `Timing("daily", None, 4, 15)` gives a `CronTrigger` whose `hour` field is `4` and `minute` field is `15`.
  - `test_last_run_recorded_even_when_job_raises`: run a job via `job.func(*job.args)` with the job function monkeypatched to raise; `LAST_RUNS["demand_interval"]` is set.

- [ ] **Step 2: Run the new tests.** Expected: FAIL.

- [ ] **Step 3: Implement.** Replace the hard-coded triggers in `build_scheduler` with a loop over `JOBS` using `load_timings` (open one session from `session_factory` for the read). Keep `service_status`'s `next_run_time=now` and `misfire_grace_time=30`; keep `expire_admin_password_file` as is. Record `LAST_RUNS[job_id]` in a `finally` block in `_run_with_session`. Update the existing job-id assertions only if ids change (they must not).

- [ ] **Step 4: Run `uv run pytest tests/test_scheduler.py tests/jobs -v`.** Expected: PASS.

- [ ] **Step 5: Commit** `feat(core-api): load job timings from the database and reschedule live`.

---

### Task 3: Admin endpoints

**Files:**
- Create: `core-api/core_api/admin_jobs.py`, `core-api/core_api/admin_jobs_schemas.py`, `core-api/core_api/routers/admin_jobs.py`
- Modify: `core-api/core_api/main.py` (include the router with `prefix=api_prefix`)
- Test: `core-api/tests/test_admin_jobs.py`

**Interfaces:**
- Consumes: Task 1 registry and model, Task 2 `load_timings`, `apply_timing`, `LAST_RUNS`.
- Produces routes under `/api/v1/admin/jobs`:
  - `GET ""` returns `JobListOut {timezone: str, scheduler_running: bool, jobs: list[JobOut]}`.
  - `PUT "/{job_id}"` with `TimingIn {kind, interval_minutes?, hour?, minute?}` returns `JobOut`.
  - `DELETE "/{job_id}"` returns `JobOut` (the job after the reset).
  - `JobOut {id, label, kind, timing: TimingOut, default: TimingOut, is_default: bool, min_minutes: int | null, next_run: datetime | null, last_run: datetime | null}`; `TimingOut {interval_minutes, hour, minute}`.
- `timezone` is `str(scheduler.timezone)` when the scheduler exists, else `"UTC"`.
- Logging actions: `save_job_schedule`, `reset_job_schedule`.

- [ ] **Step 1: Write failing tests in `tests/test_admin_jobs.py`** (use the `admin` fixture and `auth` helper as in `test_admin_mail.py`; start the app with `SCHEDULER_ENABLED=true` and `with TestClient(app)` for tests that touch the live scheduler):
  - `test_get_lists_all_registry_jobs_with_defaults`: six jobs, `is_default` true, `next_run` set when the scheduler runs.
  - `test_put_stores_row_and_reschedules`: `PUT demand_interval` with `{"kind":"interval","interval_minutes":10}` returns 200, `is_default` false; the live trigger is 10 minutes; a row exists.
  - `test_put_daily_job`: `PUT demand_charge` with `{"kind":"daily","hour":1,"minute":5}`.
  - `test_delete_resets_to_default`: after a PUT, `DELETE` returns `is_default` true, removes the row and restores the 5-minute trigger.
  - `test_put_invalid_returns_422_and_changes_nothing`: parametrized over the Review Focus cases (interval `0`, `-5`, `1.5`; `hour` 24; `minute` 60; wrong kind for the job; below `min_minutes`); no row stored and the trigger unchanged.
  - `test_unknown_job_is_404`: `PUT` and `DELETE` on `nope` and on `expire_admin_password_file`.
  - `test_non_admin_is_403_and_anonymous_is_401` for all three routes.
  - `test_scheduler_disabled_still_stores_row`: `SCHEDULER_ENABLED=false`; `GET` shows `scheduler_running: false` and `next_run: null`; `PUT` returns 200 and stores the row.
  - `test_admin_action_log_has_no_values` (caplog): the log line contains the action name and no interval digits beyond the ids already logged.

- [ ] **Step 2: Run the tests.** Expected: FAIL, 404 on every route.

- [ ] **Step 3: Implement.** Logic in `admin_jobs.py` (`list_jobs(db, scheduler)`, `save_timing(db, scheduler, job_id, payload)`, `reset_timing(db, scheduler, job_id)`, `get_spec_or_404(job_id)`); the router stays thin like `routers/admin_mail.py`. Read the scheduler from `request.app.state.scheduler`, which may be `None`. Write the row, commit, then call `apply_timing` only if the scheduler is not `None`. Validate with `validate_timing` before any write. Pydantic `TimingIn` uses strict ints so `1.5` fails at the schema and returns 422.

- [ ] **Step 4: Run `uv run pytest tests/test_admin_jobs.py -v`, then the whole suite `uv run pytest`.** Expected: PASS.

- [ ] **Step 5: Commit** `feat(core-api): admin endpoints to read, save and reset job schedules`.

---

### Task 4: Webapp Schedule tab

**Files:**
- Create: `webapp/src/admin/useAdminJobs.ts`, `webapp/src/admin/ScheduleTab.tsx`, `webapp/src/admin/JobRow.tsx`
- Modify: `webapp/src/admin/tabs.ts` (add `{ id: "schedule", label: "Schedule" }`), `webapp/src/admin/AdminSettingsPage.tsx` (render `<ScheduleTab />` for `tab === "schedule"`)
- Test: `webapp/src/admin/ScheduleTab.test.tsx`; extend `AdminSettingsPage.test.tsx` for the new tab link.

**Interfaces:**
- Consumes: the Task 3 JSON shapes.
- Produces in `useAdminJobs.ts`: types `JobTiming`, `AdminJob`, `AdminJobList` matching `JobOut` and `JobListOut`; `useAdminJobs()` (query, key `["admin","jobs"]`); `useSaveJob(jobId)` and `useResetJob(jobId)` (mutations that invalidate the query). Follow `useAdminMail.ts`.
- `JobRow({ job }: { job: AdminJob })` is one row: label, an interval number input or an `HH:MM` time input depending on `kind`, **Save**, **Reset to default** (hidden when `is_default`), next run and last run text.

- [ ] **Step 1: Write failing tests in `ScheduleTab.test.tsx`**, mocking `apiFetch` as `EmailTab.test.tsx` does:
  - lists every job with its timing and the timezone label for daily jobs.
  - editing an interval and clicking Save sends `PUT /api/v1/admin/jobs/demand_interval` with `{kind:"interval", interval_minutes: 10}`.
  - editing a daily time sends `{kind:"daily", hour:1, minute:5}`.
  - Reset to default shows only when `is_default` is false and sends `DELETE`.
  - a 422 response shows the server message in the row and keeps the typed value.
  - when `scheduler_running` is false, a line says the scheduler is off and changes apply after it is enabled.
  - a load failure shows `QueryError` with a retry, like the Email tab.

- [ ] **Step 2: Run `cd webapp && npx vitest run src/admin`.** Expected: FAIL.

- [ ] **Step 3: Implement** with the shared classes in `adminUi.ts` (`sectionClass`, `headingClass`, `mutedClass`). Keep each component under 30 lines by splitting the timing input out of `JobRow` if needed.

- [ ] **Step 4: Run `npx vitest run`, `npx tsc --noEmit` and the lint script in `webapp/package.json`.** Expected: PASS.

- [ ] **Step 5: Commit** `feat(webapp): admin Schedule tab for background jobs`.

---

### Task 5: Documentation

**Files:** see File Structure, Docs line.

- [ ] **Step 1: Run the `documentation` agent** with the spec path and the list of changed files. It edits the maintainer pages; confirm it covers: `docs/core-api.md` (rewrite the background-jobs paragraph at the line on `scheduler.py`, the files table, the endpoint list), `docs/data-model.md` (`job_schedules`), `docs/operations.md` (retune without restart; the single-process note), `docs/webapp.md` (the tab), `docs/README.md` if it lists pages.
- [ ] **Step 2: Write `webapp/src/docs/14-admin-schedule.md`** in the style of `13-admin-email.md`: what each job does, interval and daily editing, the timezone shown, Reset to default, what happens when the scheduler is off. Link it from `index.md` and add glossary entries for "job" and "interval" in `11-glossary.md`; update the tab list wherever the guide names the admin tabs.
- [ ] **Step 3: Screenshots.** If `scripts/docs-screenshots` has a per-chapter config, add the new tab; otherwise state in the handover that no image was added.
- [ ] **Step 4: Run the repo's link and anchor check** (see `docs/development.md` for the command). Expected: no broken links.
- [ ] **Step 5: Commit** `docs: describe the admin Schedule tab`.

---

## Final steps

- Run `/simplify`, then the full `uv run pytest` and `npx vitest run`.
- Resolve the open question from the spec: confirm the scheduler's time zone (`BackgroundScheduler()` uses the container's local zone) and that the tab shows `timezone` next to daily times.

## Self-review notes

- Spec coverage: registry, table, startup load, API, live reschedule, last run in memory, scheduler-disabled behavior, tab, docs, tests each map to Tasks 1 to 5.
- `service_status` is editable at 1 minute minimum; its `REPORT_INTERVAL_SECONDS` is 60 and stays the code default. The About dialog's staleness window (`STALE_AFTER`, 3 times that value) does not change, so a longer interval will make the dialog show stale status. Task 3's endpoint docs and the user chapter say so.
