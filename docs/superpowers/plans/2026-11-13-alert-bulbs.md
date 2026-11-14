# Alert bulbs and kept notices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show Dashboard alerts as one-line pulsing bulbs, keep cleared transient alerts for an hour, and keep Envoy re-pin and firmware notices until a user dismisses them.

**Architecture:** The `alerts` table gains `severity`, `cleared_at` and `sticky`. Core API applies the raise and clear rules and hides rows cleared over an hour ago. Collectors pass `severity` and `sticky` through the existing PUT. The webapp tile renders one line per row and tints itself by the most serious unresolved bulb.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, pytest (`uv run pytest` in each package). React, TypeScript, Tailwind, TanStack Query, Vitest (`npx vitest run <file>` in `webapp`).

**Spec:** `docs/superpowers/specs/2026-11-13-alert-bulbs-design.md`

## Global Constraints

- Severity values are `failure` (red), `partial` (amber), `notice` (pastel blue). Default is `failure`.
- Cleared transient alerts show for 1 hour after `cleared_at`, then drop off. No cleanup job.
- A repeat raise of an unresolved alert keeps `since`. A repeat raise of a cleared alert resets `since` to now and `cleared_at` to null.
- Sticky alerts are never cleared by a collector. Only `DELETE /sites/{site_id}/actors/alerts/{alert_id}` removes them. It returns 404 for another site's alert and 409 for a non-sticky one.
- Row text is `YYYY-MM-DD HH:MM` in the site time zone, then `Source - message`, then one button. Pulse length is 3s. Reduced motion stops the pulse.
- Re-pin on a firmware change is red and sticky. Re-pin inside the 100-day expiry window is blue and sticky. Firmware change with no re-pin is blue and sticky. A first firmware read with nothing stored raises no notice. If a re-pin matches both rules, the firmware rule wins.
- Amber: Envoy `meters`, WeatherLink `iss` and `barometer`, and schedule runs with outcome `partial`. All other codes are red.
- Prose (comments, docs, commit messages) follows `.claude/rules/avoid-ai-writing.md`. No `any`. Functions stay under 30 lines. Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- A sticky notice raised while Core API is down must not be lost. The collector keeps it and retries (Task 4).
- An unresolved alert re-raised with a different severity (amber to red) updates the severity and keeps `since` (Task 1).
- Two re-pin notices on one source before the first is dismissed leave one row, with the later message and time (Task 1).
- Dismissing another site's alert returns 404, and dismissing a non-sticky alert returns 409 (Task 2).
- A tile holding only cleared rows shows the heading `Needs attention` with no count and the neutral tone (Task 6).

---

## File Structure

| File | Change |
|------|--------|
| `core-api/core_api/models/alerts.py` | Add columns and severity constants |
| `core-api/alembic/versions/b7d1f4a2c6e9_alert_severity.py` | New migration, `down_revision = 'a3c5e7d9b1f2'` |
| `core-api/core_api/alerts.py` | Raise, clear and dismiss rules |
| `core-api/core_api/schemas.py` | `AlertIn` gains `severity`, `sticky` |
| `core-api/core_api/routers/collector_config.py`, `routers/actor.py` | Pass `severity` and `sticky` through |
| `core-api/core_api/actor_failures.py` | New `ActorFailure` fields, one-hour filter, partial runs amber |
| `core-api/core_api/routers/actors.py` | Dismiss route |
| `*/core_api_client.py`, `*/alerts.py` in the five service packages | Send `severity` and `sticky` |
| `envoy-collector/envoy_collector/main.py` | Notice rules, short messages |
| `webapp/src/actors/Bulb.tsx`, `alertTone.ts`, `FailuresTile.tsx`, `useActors.ts`, `types.ts`, `runFormat.ts`, `webapp/src/index.css` | Bulbs, tile tone, dismiss |

---

### Task 1: Alert storage and the raise/clear rules

**Files:**
- Modify: `core-api/core_api/models/alerts.py`, `core-api/core_api/alerts.py`, `core-api/core_api/schemas.py`, `core-api/core_api/routers/collector_config.py`, `core-api/core_api/routers/actor.py`
- Create: `core-api/alembic/versions/b7d1f4a2c6e9_alert_severity.py`
- Test: `core-api/tests/test_alerts.py` (new), `core-api/tests/test_collector_config.py`

**Interfaces:**
- Produces:
  - `models/alerts.py`: `SEVERITY_FAILURE = "failure"`, `SEVERITY_PARTIAL = "partial"`, `SEVERITY_NOTICE = "notice"`; `Alert.severity: Mapped[str]` (String(16), default `"failure"`), `Alert.cleared_at: Mapped[datetime | None]`, `Alert.sticky: Mapped[bool]` (default false).
  - `alerts.py`: `set_alert(db, site_id, subject_kind, subject_id, code, message, severity=SEVERITY_FAILURE, sticky=False) -> None`; `clear_alert(db, subject_kind, subject_id, code) -> None` (unchanged signature).
  - `schemas.py`: `AlertIn.severity: Literal["failure","partial","notice"] = "failure"`, `AlertIn.sticky: bool = False`.

- [ ] **Step 1: Write the failing tests in `tests/test_alerts.py`**

Use the `db` and `site` fixtures and `SUBJECT_SITE`. One test per rule, each asserting on the single `Alert` row:
  - `test_raise_inserts_with_since_now`
  - `test_raise_unresolved_keeps_since_and_updates_message_and_severity`: raise as `failure`, move `since` back an hour, raise again as `partial` with a new message. Expect `since` unchanged, `severity == "partial"`, `cleared_at is None`.
  - `test_raise_after_clear_replaces_the_resolved_row`: raise, clear, raise. Expect `cleared_at is None` and `since` within a second of now.
  - `test_clear_sets_cleared_at_once`: clear twice. The second call leaves `cleared_at` as set by the first.
  - `test_clear_leaves_sticky_rows`: sticky raise, clear. Expect `cleared_at is None`.
  - `test_sticky_raise_replaces_existing_row`: two sticky raises with different messages. Expect one row, the later message, `since` reset.
  - `test_clear_without_a_row_does_nothing`

In `tests/test_collector_config.py` add `test_put_alert_accepts_severity_and_sticky`: PUT `/alerts/token` with `{"message": "x", "severity": "partial", "sticky": true}` stores both. Add `test_put_alert_rejects_unknown_severity` expecting 422.

- [ ] **Step 2: Run to verify failure**

Run: `cd core-api && uv run pytest tests/test_alerts.py tests/test_collector_config.py -v`
Expected: FAIL (missing columns and parameters).

- [ ] **Step 3: Add the model columns and the migration**

`severity` has `server_default="failure"`, `sticky` has `server_default=sa.false()`. The migration adds all three columns and drops them in `downgrade`. Run `uv run alembic heads` and expect one head, `b7d1f4a2c6e9`.

- [ ] **Step 4: Implement `set_alert` and `clear_alert`**

Follow the raise and clear table in the spec. `set_alert` replaces in place on the same row when `sticky` or `cleared_at` is set, so the unique constraint holds. `clear_alert` sets `cleared_at = now` only on rows with `sticky` false and `cleared_at` null; it no longer deletes. Pass `payload.severity` and `payload.sticky` from both routers.

- [ ] **Step 5: Run to verify pass, then the whole suite**

Run: `uv run pytest tests/test_alerts.py tests/test_collector_config.py tests/test_actor_router.py -v`, then `uv run pytest -q`
Expected: PASS. Existing alert tests that expected a deleted row after a clear are updated to expect `cleared_at` set.

- [ ] **Step 6: Document and commit**

Update `docs/data-model.md` (new columns) and `docs/core-api.md` (body fields, rules). Run `uv run mypy` and `uv run ruff check .`.

```bash
git add core-api docs/data-model.md docs/core-api.md
git commit -m "feat(core-api): keep cleared alerts for an hour and add sticky notices"
```

---

### Task 2: Failures list and dismiss endpoint

**Files:**
- Modify: `core-api/core_api/actor_failures.py`, `core-api/core_api/alerts.py`, `core-api/core_api/routers/actors.py`
- Test: `core-api/tests/test_actor_failures.py`, `core-api/tests/test_actor_router.py`

**Interfaces:**
- Consumes: `Alert.severity`, `Alert.cleared_at`, `Alert.sticky`, `SEVERITY_*` from Task 1.
- Produces:
  - `actor_failures.ActorFailure` gains `severity: str = "failure"`, `alert_id: int | None = None`, `cleared_at: datetime.datetime | None = None`, `sticky: bool = False`, after `subject`.
  - `alerts.ALERT_CLEARED_TTL = datetime.timedelta(hours=1)`.
  - `alerts.dismiss_alert(db: Session, site_id: int, alert_id: int) -> None`, raising `AppError(404, "not_found", ...)` or `AppError(409, "not_dismissable", ...)`.
  - Route `DELETE /sites/{site_id}/actors/alerts/{alert_id}` returning 204, using `require_site_access`.

- [ ] **Step 1: Write the failing tests**

In `test_actor_failures.py`:
  - `test_cleared_alert_is_listed_for_an_hour`: `cleared_at` 59 minutes ago is listed with `cleared_at` set. 61 minutes ago is not.
  - `test_alert_failure_carries_severity_id_and_sticky`
  - `test_partial_run_failure_has_severity_partial`: a run with outcome `PARTIAL` gives `severity == "partial"`, a `FAILED` run gives `"failure"`, both with `alert_id is None`.
  - `test_ordering`: unresolved first by severity (failure, partial, notice), newest `since` first within a severity, cleared rows last.

In `test_actor_router.py`: `test_dismiss_removes_a_sticky_alert` (204, row gone), `test_dismiss_other_sites_alert_is_404`, `test_dismiss_non_sticky_alert_is_409`, `test_dismiss_requires_login` (401).

- [ ] **Step 2: Run to verify failure**

Run: `cd core-api && uv run pytest tests/test_actor_failures.py tests/test_actor_router.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `_alert_failures` skip rows where `cleared_at` is older than `ALERT_CLEARED_TTL` and fill the new fields. In `_run_failed` set `severity="partial"` when `run.outcome == RunOutcome.PARTIAL`. Replace the final `sorted` key in `current_failures` with one that sorts by cleared (cleared last), then severity rank, then `since` descending. Implement `dismiss_alert` with one `db.get(Alert, alert_id)`, checking `site_id` and `sticky`. Keep each function under 30 lines; split the sort key into `_sort_key(f: ActorFailure)`.

- [ ] **Step 4: Run to verify pass, then the whole suite**

Run: `uv run pytest -q && uv run mypy && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Document and commit**

Update `docs/core-api.md` (failures response fields, dismiss route) and `docs/actors.md` where it describes the failures list.

```bash
git add core-api docs
git commit -m "feat(core-api): list severity and cleared state, add dismiss route"
```

---

### Task 3: Collectors send severity and sticky, with short messages

Covers `weatherlink-local-collector`, `amber-pricing-collector`, `local-pricing-collector` and `actron-neo-actor`. The envoy collector is Task 4.

**Files:**
- Modify in each package: `core_api_client.py`, `alerts.py`, and the module that calls `raise_alert` or `report_failure` (WeatherLink `main.py`, Amber `live_poll.py`, local pricing `poll.py`, Actron `runner.py` and `async_core.py`)
- Test: each package's existing alert tests and `tests/test_main.py` / `test_poll.py` / `test_live_poll.py`

**Interfaces:**
- Consumes: the `severity` and `sticky` body fields from Task 1.
- Produces, in each package:
  - `set_source_alert(base_url, source_id, collector_key, code, message, severity="failure", sticky=False)` (Actron: `CoreApiClient.set_alert(instance_id, code, message, severity="failure", sticky=False)`), sending both fields in the JSON body.
  - `alerts.raise_alert(..., message, severity="failure", sticky=False)` and `alerts.report_failure(..., message, severity="failure")` (Actron: the same on `InstanceAlerts.failure` and its raise path).
  - A raise with `severity` different from the last one sent for the key is sent again, even when the message is the same.

- [ ] **Step 1: Write the failing tests**

Per package, in the existing alerts or main tests:
  - `test_raise_alert_sends_severity_and_sticky`: the PUT body contains both fields.
  - `test_raise_alert_resends_when_severity_changes`
  - WeatherLink: `test_iss_and_barometer_alerts_are_partial`, `test_unreachable_alert_is_a_failure`.
  - Messages (assert the exact text): WeatherLink unreachable `Cannot connect to the weather station`; ISS `Not reporting outdoor (ISS) data`; barometer `Not reporting barometer data`; Amber key `Amber rejected the API key`; Amber `Cannot get prices from Amber`; local pricing `Rates are not being priced`, `Reprice request is malformed`, `Site time zone is not valid`.

- [ ] **Step 2: Run to verify failure**

Run, per package: `cd <package> && uv run pytest -q`
Expected: FAIL on the new tests only.

- [ ] **Step 3: Implement**

Add the two parameters through the client and wrapper layers and pass `severity="partial"` at the `iss` and `barometer` call sites. Replace the messages with the short forms above. Drop the IP address from the unreachable text. The client signatures keep working for existing callers through the defaults.

- [ ] **Step 4: Run to verify pass**

Run, per package: `uv run pytest -q && uv run ruff check . && uv run mypy` (where the package runs mypy in CI; check `.github/workflows`).
Expected: PASS.

- [ ] **Step 5: Document and commit**

Update `docs/collectors.md` (alert severities and the short messages).

```bash
git add actron-neo-actor amber-pricing-collector local-pricing-collector weatherlink-local-collector docs/collectors.md
git commit -m "feat: collectors send alert severity and sticky, with shorter messages"
```

---

### Task 4: Envoy collector notices

**Files:**
- Modify: `envoy-collector/envoy_collector/core_api_client.py`, `envoy-collector/envoy_collector/alerts.py`, `envoy-collector/envoy_collector/main.py`
- Test: `envoy-collector/tests/test_main.py`, `envoy-collector/tests/test_alerts.py` (new if none exists)

**Interfaces:**
- Consumes: the `set_source_alert` and `alerts.raise_alert` signatures from Task 3 (same shape in this package, but `raise_alert` takes `settings`).
- Produces:
  - `alerts.retry_pending(settings: Settings) -> None`: resends sticky notices whose PUT failed. `raise_alert` with `sticky=True` stores the notice in a pending dict when `set_source_alert` raises `CoreApiError`, and removes it on success. Sticky notices skip the dedup check.
  - `main.REPINNED_ALERT = "repinned"`, `main.FIRMWARE_ALERT = "firmware"`.
  - `main._repin_reason(config, device, now) -> Repin | None`, where `Repin` is a frozen dataclass `(reason: str, firmware_changed: bool)`.

- [ ] **Step 1: Write the failing tests**

In `test_main.py`, with `alerts.reset()` in setup and the Core API client mocked:
  - `test_repin_after_firmware_change_raises_red_sticky_notice`: expect `raise_alert(code="repinned", severity="failure", sticky=True)` and a message containing both firmware versions.
  - `test_repin_near_expiry_raises_blue_sticky_notice`: `severity="notice"`, `sticky=True`.
  - `test_firmware_rule_wins_when_both_apply`: firmware changed and expiry within 100 days gives `severity="failure"`.
  - `test_firmware_change_without_cert_change_raises_blue_notice`: discovery sees a new firmware, certificate unchanged. Expect `code="firmware"`, `severity="notice"`, `sticky=True`.
  - `test_first_firmware_read_raises_no_notice`
  - `test_firmware_notice_not_repeated_on_later_discovery`: a second discovery with the stored firmware now equal raises nothing.
  - `test_refusal_alert_stays_a_non_sticky_failure`
  - `test_cert_messages_are_short`: assert exact text for the three refusal messages: `No serial number stored to check the certificate against`, `Envoy serial is X, expected Y; not re-pinning`, `Certificate changed without a firmware upgrade; not re-pinning`.

In `test_alerts.py`: `test_sticky_notice_is_kept_and_retried_when_core_api_is_down`: the first PUT raises `CoreApiError`; `retry_pending(settings)` sends it again and a second `retry_pending` sends nothing. `test_sticky_notice_is_not_deduplicated`.

- [ ] **Step 2: Run to verify failure**

Run: `cd envoy-collector && uv run pytest -q`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement**

`_repin_reason` returns `Repin` instead of a string, checking firmware first. On success, `repin_certificate` raises the `REPINNED_ALERT` notice: severity `failure` when `firmware_changed`, else `notice`, `sticky=True`, message `Firmware updated {old} to {new}, certificate re-pinned` or `Certificate re-pinned before it expired`. In `discover_and_sync_serial`, when `config.get("firmware")` is set and differs from the device value, raise the `FIRMWARE_ALERT` notice (`Firmware updated {old} to {new}`, `notice`, sticky) before `_sync_discovered`. Call `alerts.retry_pending(settings)` at the top of `poll_once`. Shorten the three refusal messages and the `_not_an_upgrade_message` text to the forms above; the longer advice stays in `logger.error`. Meters alerts pass `severity="partial"`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: PASS.

- [ ] **Step 5: Document and commit**

Update `docs/collectors.md` (the notice rules table) and `docs/how-it-works.md` where it describes re-pinning.

```bash
git add envoy-collector docs
git commit -m "feat(envoy-collector): keep re-pin and firmware notices until dismissed"
```

---

### Task 5: Bulb, tile tone and theme tokens

**Files:**
- Create: `webapp/src/actors/Bulb.tsx`, `webapp/src/actors/Bulb.test.tsx`, `webapp/src/actors/alertTone.ts`, `webapp/src/actors/alertTone.test.ts`
- Modify: `webapp/src/index.css`, `webapp/src/actors/types.ts`, `webapp/src/actors/runFormat.ts`, `webapp/src/actors/runFormat.test.ts`

**Interfaces:**
- Produces:
  - `types.ts`: `export type AlertSeverity = "failure" | "partial" | "notice"`; `ActorFailure` gains `severity: AlertSeverity`, `alert_id: number | null`, `cleared_at: string | null`, `sticky: boolean`.
  - `Bulb.tsx`: `export function Bulb({ severity, cleared }: { severity: AlertSeverity; cleared: boolean }): JSX.Element`, a `span` with `aria-hidden` and classes `bulb bulb-${severity}` plus `bulb-cleared` when cleared.
  - `alertTone.ts`: `export type Tone = AlertSeverity | "neutral"`; `export function tileTone(failures: ActorFailure[]): Tone` (red if any unresolved `failure`, else amber for `partial`, else blue for `notice`, else `neutral`; a failure with `cleared_at` set is resolved).
  - `runFormat.ts`: `export function formatAlertTime(iso: string, timezone: string): string` returning `YYYY-MM-DD HH:MM` in that zone.
  - CSS: `.bulb` (11px), `.bulb-failure`, `.bulb-partial`, `.bulb-notice`, `.bulb-cleared`; tile classes `.partial-glow`, `.notice-glow` beside `.danger-glow`.

- [ ] **Step 1: Write the failing tests**

  - `Bulb.test.tsx`: `renders the severity class`, `adds bulb-cleared when cleared`, `is hidden from assistive tech`.
  - `alertTone.test.ts`: `red wins over amber and blue`, `amber wins over blue`, `blue alone gives notice`, `cleared rows are ignored`, `empty list is neutral`.
  - `runFormat.test.ts`: `formatAlertTime formats in the site zone`: `2026-11-13T09:12:00Z` in `Australia/Sydney` gives `2026-11-13 20:12`.

- [ ] **Step 2: Run to verify failure**

Run: `cd webapp && npx vitest run src/actors/Bulb.test.tsx src/actors/alertTone.test.ts src/actors/runFormat.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement**

CSS follows treatment B of the dot samples (artifact `https://claude.ai/artifact/3p1qgpFSAFkUVmHZ3oabCR`): radial-gradient lens with a highlight, `glow` keyframes at `--bulb-period: 3s`, cleared bulbs with grey centre `--color-bulb-off` and a ring in the original colour, no animation. Pulse only `.bulb-failure` and `.bulb-partial` when not cleared; `.bulb-notice` stays lit and still. Wrap the animation in `@media (prefers-reduced-motion: no-preference)`. Add tokens `--color-notice: light-dark(#6aa6e0, #9cc4f0)`, `--color-notice-soft: light-dark(#e6f0fb, #14263f)` and use `--color-solar` for amber. Add the two new glow classes to the existing reduced-motion rule.

- [ ] **Step 3b: Run to verify pass**

Run: `npx vitest run src/actors && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add webapp
git commit -m "feat(webapp): add alert bulb, tile tone and notice colour"
```

---

### Task 6: One-line rows, tile tone and Dismiss

**Files:**
- Modify: `webapp/src/actors/FailuresTile.tsx`, `webapp/src/actors/useActors.ts`, `webapp/src/actors/FailuresTile.test.tsx`
- Docs: `docs/webapp.md`

**Interfaces:**
- Consumes: `Bulb`, `tileTone`, `formatAlertTime`, `AlertSeverity`, and the new `ActorFailure` fields from Task 5; the dismiss route from Task 2.
- Produces: `useDismissAlert(siteId: number)` in `useActors.ts`, a `useMutation` calling `DELETE /api/v1/sites/${siteId}/actors/alerts/${alertId}` with the alert id as the variable and invalidating the failures query.

- [ ] **Step 1: Write the failing tests**

Extend `failure()` in the test file with `severity: "failure", alert_id: null, cleared_at: null, sticky: false`. Add:
  - `shows one line per alert: time, source and message`: expect text `2026-10-01 14:30`, `Weatherlink Live - Cannot connect to the API`, and no `Problem` label.
  - `uses Open Collectors for alerts, Open Schedule for schedule failures`
  - `shows Dismiss for sticky alerts and calls the dismiss route`: click, expect `apiFetch` called with the `/actors/alerts/9` path and `{ method: "DELETE" }`.
  - `tints the tile red, amber or blue by the highest unresolved bulb`: assert the section's class for each case.
  - `a cleared alert is neutral and does not count`: heading `Needs attention` with no count; tile has no glow class.
  - `orders unresolved by severity, cleared last`

- [ ] **Step 2: Run to verify failure**

Run: `cd webapp && npx vitest run src/actors/FailuresTile.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

Replace `KIND_LABEL` and `FailureRow` with the one-line row: `Bulb`, `<time>` via `formatAlertTime`, `` `${label} - ${message}` ``, and a right-aligned button (Dismiss for `sticky`, else Open Collectors for `kind === "alert"`, else Open Schedule). Schedule rows use `schedule_name` as the label and a short message phrase for `still_off`, `actor_silent` and `needs_sign_in`. Choose the section's glow class from `tileTone` of the current list. The heading count is the number of unresolved rows and is omitted when it is zero. Keep `MAX_SHOWN` and the "and N more" link. Split `FailureList` so no function passes 30 lines. Dismiss shows a disabled state while the mutation runs and keeps the row if it fails.

- [ ] **Step 4: Run to verify pass**

Run: `npx vitest run && npx tsc --noEmit && npm run lint`
Expected: PASS.

- [ ] **Step 5: Document and commit**

Update `docs/webapp.md` (the Needs attention tile).

```bash
git add webapp docs/webapp.md
git commit -m "feat(webapp): one-line alert rows with bulbs, tile tone and Dismiss"
```

---

## Self-review notes

- Spec coverage: data model and rules (Task 1), reads, API and amber partial runs (Task 2), amber codes and short messages (Tasks 3 and 4), notice rules and the retry (Task 4), bulbs, tone and colours (Task 5), rows, buttons, ordering, count and dismiss (Task 6). Docs updates are in the task that changes each behaviour.
- Added beyond the spec: `retry_pending` for sticky notices raised while Core API is down.
- Spec change: the dismiss path is `/sites/{site_id}/actors/alerts/{alert_id}`, to sit under the actors router.
