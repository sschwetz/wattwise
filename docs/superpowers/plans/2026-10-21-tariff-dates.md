# Tariff and window dates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user choose start and end dates for tariffs, demand windows, export adjustments, conditional credits, billing cycles and local rate windows, with costs and stored demand charges recalculated to those dates.

**Architecture:** Add `effective_from`/`effective_to` columns to the three tariff child windows and filter every window lookup by them. Validate dates and overlaps in the billing router. After a date change, delete the stored `DemandCharge` rows the change touches and rebuild them inline. The webapp converts local calendar dates to UTC midnights in the site's time zone and suggests a start date from the latest ended matching item.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, SQLite, pytest; React, TypeScript, TanStack Query, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-21-tariff-dates-design.md`

## Global Constraints

- Start date is 00:00 local; end date is the last day the item applies, stored as the following local midnight (half-open `[effective_from, effective_to)`).
- Money stays `Decimal`; no secrets touched.
- Items without explicit dates must price exactly as before: migrated windows copy their tariff's dates.
- Tariffs and billing cycles reject overlap with another item of the same site (422). Demand, export and credit windows may overlap.
- A window must start inside its tariff's range and, when the tariff has an end, may not end after it. A window with no end under an ended tariff is allowed. Shortening a tariff does not touch its windows.
- Window create dates are optional in the API: omitted `effective_from` defaults to the tariff's `effective_from`, omitted `effective_to` means open ended.
- Prose follows `.claude/rules/avoid-ai-writing.md`.

## Review Focus

- Window that ends before it starts: rejected 422.
- Tariff ended on a date earlier than its start: rejected 422.
- Ending a tariff on 30 June and adding one on 1 July: no overlap error, no gap.
- Stored `effective_to` not at local midnight (legacy click-moment end): form shows its local date.
- Daylight-saving boundary dates convert to the right UTC instant.

---

### Task 1: Window date columns and date-aware lookups

**Files:**
- Modify: `core-api/core_api/models/billing.py` (DemandWindow, ExportAdjustmentWindow, ConditionalCreditWindow)
- Create: `core-api/alembic/versions/b1c5d8e2f4a6_add_dates_to_tariff_windows.py`
- Modify: `core-api/core_api/jobs/demand_config.py`, `core-api/core_api/cost.py`, `core-api/core_api/export_allowance_data.py`
- Test: `core-api/tests/test_window_dates.py`

**Interfaces:**
- Produces: `window_in_force(window, at: datetime) -> bool` in `jobs/demand_config.py`; columns `effective_from: datetime` (not null) and `effective_to: datetime | None` on the three window models.

- [ ] **Step 1: Write failing tests** in `tests/test_window_dates.py`: `test_window_in_force_half_open` (in force at `effective_from`, not at `effective_to`, open end always in force); `test_demand_window_outside_dates_not_matched` (`first_matching_demand_window` returns None before the window's `effective_from` and returns the window inside); `test_export_adjustment_total_ignores_window_out_of_dates` (`total_export_adjustment_c_per_kwh` sums only windows in force at the instant); `test_conditional_credit_not_applied_outside_dates` (via `_conditional_credit_total_c` or `compute_cost`); `test_allowance_span_clipped_to_window_dates`. Follow fixtures in `tests/test_cost.py` and `tests/test_export_allowance_data.py`.
- [ ] **Step 2: Run** `cd core-api && python -m pytest tests/test_window_dates.py -v`. Expected: FAIL.
- [ ] **Step 3: Add columns** to the three models (`DateTime(timezone=True)`; `effective_from` not null, `effective_to` nullable) and write the Alembic migration with `down_revision` set to the current head. It adds `effective_from` nullable, fills each row from its tariff, then makes it not null (`batch_alter_table`, SQLite). It also copies `effective_to`.
- [ ] **Step 4: Implement** `window_in_force` and apply it: `first_matching_demand_window`, `any_configured_demand_window` (filter window query results), `total_export_adjustment_c_per_kwh` (use `at_local`), `_conditional_credit_total_c` (test at `midnight`), `_window_export_kwh` and `allowance_window_spans` (clip `start`/`end` to the window's dates, skip empty spans).
- [ ] **Step 5: Run** the new tests plus `python -m pytest tests -q`. Expected: PASS. Fix existing fixtures that build windows directly by giving them dates.
- [ ] **Step 6: Commit** `feat(core-api): date-aware demand, export and credit windows`.

### Task 2: API schemas and validation

**Files:**
- Modify: `core-api/core_api/schemas.py`, `core-api/core_api/routers/billing.py`
- Test: `core-api/tests/test_billing.py`

**Interfaces:**
- Consumes: Task 1 columns.
- Produces: `effective_from`/`effective_to` on the three window `Create` (optional) and `Out` schemas; `WindowUpdate(effective_to: UtcDatetime)`; `PATCH /tariffs/{tid}/{demand-windows|export-adjustments|conditional-credits}/{wid}`.

- [ ] **Step 1: Write failing tests** in `tests/test_billing.py`: `test_tariff_end_before_start_rejected`, `test_tariff_overlap_rejected`, `test_tariff_adjacent_ok` (end 30 Jun midnight-next-day, start 1 Jul), `test_billing_cycle_overlap_rejected`, `test_window_defaults_to_tariff_start`, `test_window_before_tariff_start_rejected`, `test_window_after_tariff_end_rejected`, `test_window_open_end_under_ended_tariff_allowed`, `test_window_end_before_start_rejected`, `test_patch_window_effective_to`, `test_patch_tariff_effective_to_before_start_rejected`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Schemas.** `SiteTariffCreate` and `BillingCycleCreate` get a model validator `effective_to > effective_from`. Window `Create` schemas get optional dates plus the same validator; `Out` schemas expose required `effective_from` and optional `effective_to`.
- [ ] **Step 4: Router.** Add `_reject_overlap(rows, start, end, exclude_id=None)` used by tariff create/PATCH and billing cycle create/PATCH (`AppError(422, "overlap", ...)`). On PATCH, reject `effective_to <= effective_from`. Extend `_create_tariff_child` to default and range-check dates against the tariff. Add the three window PATCH routes through one shared helper.
- [ ] **Step 5: Run** `python -m pytest tests -q`. Expected: PASS. Fix existing tests that relied on overlapping tariffs.
- [ ] **Step 6: Commit** `feat(core-api): validate and update dates on tariffs and windows`.

### Task 3: Recalculate stored demand charges

**Files:**
- Modify: `core-api/core_api/jobs/demand_charge_job.py`, `core-api/core_api/routers/billing.py`
- Test: `core-api/tests/jobs/test_demand_charge_recalc.py`

**Interfaces:**
- Produces: `refresh_demand_charges(db: Session, site: Site, from_instant: datetime, to_instant: datetime | None, now: datetime) -> None` in `jobs/demand_charge_job.py`. It deletes `DemandCharge` rows with `billing_period_end > from_instant` and (`to_instant` is None or `billing_period_start < to_instant`), then runs `_finalize_for_site`.

- [ ] **Step 1: Write failing tests**: `test_refresh_deletes_overlapping_finalized_charge_and_rebuilds`, `test_refresh_leaves_other_periods_alone`, `test_open_ended_range_deletes_all_later_periods`. Reuse fixtures in `tests/jobs/`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** `refresh_demand_charges`. In the router call it after tariff create/PATCH and demand window create/PATCH (range = the changed item's range; for a PATCH, from the earlier of old and new `effective_to`), and after billing cycle create/PATCH with `to_instant=None` and `from_instant` the earliest changed instant.
- [ ] **Step 4: Add API test** that creating a demand window dated into a finalized period changes the stored charge (`GET /demand-charges`).
- [ ] **Step 5: Run** `python -m pytest tests -q`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(core-api): recalculate demand charges when dates change`.

### Task 4: Webapp date helpers and the tariff and billing cycle forms

**Files:**
- Create: `webapp/src/billing/dates.ts`, `webapp/src/billing/dates.test.ts`
- Modify: `webapp/src/billing/useBilling.ts`, `webapp/src/pages/BillingPage.tsx`, `webapp/src/pages/BillingPage.test.tsx`

**Interfaces:**
- Produces in `dates.ts`:
  - `startOfDayIso(date: string, timeZone: string): string` — UTC ISO of 00:00 local on `YYYY-MM-DD`.
  - `endOfDayIso(date: string, timeZone: string): string` — UTC ISO of 00:00 local on the following day.
  - `displayEndDate(iso: string, timeZone: string): string` — last day applied, per the spec's legacy rule.
  - `localDate(iso: string, timeZone: string): string` — `YYYY-MM-DD`.
  - `suggestStart(ended: { effective_to: string | null }[], timeZone: string, today: string): string`.
- `useEndTariff`/`useEndBillingCycle` take `{ id: number; effective_to: string }`. Create inputs accept `effective_from` and optional `effective_to`.

- [ ] **Step 1: Write failing tests** in `dates.test.ts`: Brisbane (no DST) and Sydney across the first Sunday of October (23 or 25 hour day), `endOfDayIso("2026-06-30", "Australia/Sydney")` equals `startOfDayIso("2026-07-01", ...)`, `displayEndDate` of a midnight value shows the previous day and of a 14:30 value shows its own date, `suggestStart` returns the day after the latest end or `today`.
- [ ] **Step 2: Run** `cd webapp && npx vitest run src/billing/dates.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** `dates.ts` with `Intl.DateTimeFormat` offsets.
- [ ] **Step 4: Update `BillingPage.test.tsx`** first: the tariff form has **Start date** (prefilled by `suggestStart`) and optional **End date**; **End this tariff** reveals an **End date** input and **Confirm end**; the PATCH body carries the converted `effective_to`; same for billing cycles. Find the site time zone from the site query already used by the page (see `useSites`/site detail hook).
- [ ] **Step 5: Implement** the form changes and hooks.
- [ ] **Step 6: Run** `npx vitest run` and `npx tsc --noEmit`. Expected: PASS.
- [ ] **Step 7: Commit** `feat(webapp): start and end dates for tariffs and billing cycles`.

### Task 5: Webapp window forms

**Files:**
- Modify: `webapp/src/billing/useBilling.ts`, `webapp/src/pages/BillingPage.tsx`, `webapp/src/billing/ConditionalCredits.tsx`, their tests

- [ ] **Step 1: Write failing tests**: each window form (demand, export adjustment, conditional credit) shows **Start date** (suggested from the latest ended window of the same tariff) and optional **End date**; each window row shows its date range and an **End** link that PATCHes `effective_to` with the converted value; ended windows are struck through.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.** Add `effective_from`/`effective_to` to the three window interfaces and create inputs; add `useEndDemandWindow`, `useEndExportAdjustmentWindow`, `useEndConditionalCreditWindow`. Share one `DateFields` component (start and end inputs) and one `EndDateControl` between the forms.
- [ ] **Step 4: Run** `npx vitest run` and `npx tsc --noEmit`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(webapp): start and end dates for tariff windows`.

### Task 6: Local rate windows

**Files:**
- Modify: `webapp/src/sources/AddWindowForm.tsx`, `webapp/src/sources/RateWindowsPanel.tsx`, `webapp/src/sources/useRateWindows.ts`, `webapp/src/sources/RateWindowsPanel.test.tsx`

- [ ] **Step 1: Read** the three files and the existing tests to see how `effective_from` is set today.
- [ ] **Step 2: Write failing tests**: **Start date** input suggested from the latest ended window of the same channel; **End** asks for an **End date** and sends the converted `effective_to` through the existing PATCH.
- [ ] **Step 3: Implement** using `dates.ts` and the shared date components.
- [ ] **Step 4: Run** `npx vitest run` and `npx tsc --noEmit`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(webapp): start and end dates for local rate windows`.

### Task 7: Docs

**Files:**
- Modify: `webapp/src/docs/07-billing.md`, `webapp/src/docs/05-sources.md`, `docs/data-model.md`, `docs/core-api.md`

- [ ] **Step 1:** Update the billing guide sections on adding and changing a tariff, windows and billing cycles; the sources guide section "Changing a rate"; the data model (window date columns) and the API list (window PATCH routes, overlap rule).
- [ ] **Step 2:** Run `cd webapp && npx vitest run src/help` (link checks). Expected: PASS.
- [ ] **Step 3: Commit** `docs: start and end dates for tariffs and windows`.

---

## Self-review

Spec coverage: dates and conversion (Tasks 1, 4–6), suggested start (4–6), window ranges (2), overlaps (2), recalculation (3), docs (7). Names match across tasks: `window_in_force`, `refresh_demand_charges`, `startOfDayIso`, `endOfDayIso`, `displayEndDate`, `suggestStart`.
