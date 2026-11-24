# Admin Schedule tab for background jobs

## Intent

An admin retunes when the core-api background jobs run from Admin Settings, with no rebuild or restart. Today the timings are fixed in `core-api/core_api/scheduler.py`.

## Goals

- A **Schedule** tab in Admin Settings lists each job with its current timing, next run and last run.
- An admin edits a job's timing. The change applies to the running scheduler at once and survives a restart.
- An admin resets a job to its default from code.

## Non-goals

- Editing the actor-run site schedules. The actor polls those every 30 seconds; they are not APScheduler jobs.
- Full cron expressions.
- Adding, removing or disabling jobs.
- Editing `expire_admin_password_file`, which is a security timer and stays fixed.

## Anti-goals

- A bad value that stops a job or floods the database. Values are validated on the server against per-job limits.
- Two sources of truth for a timing. The code default is the fallback; the table holds only overrides.

## Approaches

1. **Table plus live reschedule (chosen).** Overrides in a `job_schedules` table; saving calls `reschedule_job` on the running scheduler.
2. Table read at startup only. Simpler, but a change needs a restart, which is what the admin wants to avoid.
3. Environment variables. No UI, and a change needs a compose edit and a restart.

## Design

- **Registry.** `scheduler.py` defines each editable job once: id, default timing, kind, and limits. `build_scheduler` and the API both read this registry. Kinds: `interval` (every N minutes) or `daily` (HH:MM).
- **Table `job_schedules`.** `job_id` (primary key), `kind`, `interval_minutes` (nullable), `hour` and `minute` (nullable), `updated_at`. A job with no row uses its default. Alembic migration included.
- **Startup.** `build_scheduler` loads rows and builds each trigger from the override, falling back to the default.
- **API.** Admin-only endpoints under the existing admin router: list jobs (timing, default, limits, next run, last run), `PUT` a job's timing, `DELETE` to reset. Validation returns 422 with the limit that failed. A save writes the row and calls `reschedule_job`; a reset deletes the row and reschedules with the default.
- **Last run.** The wrapper `_run_with_session` records the finish time per job in memory, so it resets on restart. Persisting it is not needed for retuning.
- **Running job.** `max_instances=1` and `coalesce` stay. A change made during a run takes effect for the next run.
- **Scheduler disabled.** When `SCHEDULER_ENABLED` is false, the tab shows timings but saves only to the table, and the page says the scheduler is off.
- **Webapp.** `schedule` added to `ADMIN_TABS`; a `ScheduleTab` component with one row per job: timing editor, Save, Reset to default. Hooks follow the pattern in `useAdminMail.ts`.
- **Limits (my calls, adjustable in the registry).** Intervals have a per-job minimum; the watchdog and `service_status` allow 1 minute, `demand_interval` allows 1 minute, and the daily jobs accept any valid HH:MM in UTC unless the existing cron triggers use the server time zone, in which case the same zone applies and the tab labels it.
- **Docs.** A maintainer page in `docs/` (operations and core-api) and a `webapp/src/docs/` chapter for admins, plus the glossary and the guide's tab list, per CLAUDE.md. The `documentation` agent runs before handover.
- **Tests.** Registry validation, endpoints (auth, 422, save, reset), reschedule on a real `BackgroundScheduler` in `tests/test_scheduler.py`, and the tab in the webapp.

## Left to the builder

- Exact per-job minimums and the UI layout of a row.
- The time zone the existing cron triggers use, and how the tab labels it.
