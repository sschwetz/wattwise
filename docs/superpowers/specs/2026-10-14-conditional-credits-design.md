# Conditional Credits — Design Spec

Date: 2026-10-14
Status: Draft, awaiting review

## Purpose

Some retailers give a credit on days when the household keeps its grid import low
during a set period. The rule is: on set days, if the average grid import between
a:00 and b:00 is below y watts, credit z. Tariffs cannot express this today.

A tariff gets any number of conditional credits. Each is a flat credit per qualifying
day, applied in `compute_cost`.

## Decisions

- **Measure:** average of `grid_import_w` only. Export does not offset it.
- **Always grid import:** `compute_cost` uses `consumption_w` as its import metric under
  the `gross` method. The credit test ignores the method and always uses
  `grid_import_w`, so the same day qualifies under `net` and `gross`.
- **Computed on the fly** in `cost.py`, like the daily service and monthly fees. No
  background job and no stored per-day result. Editing a credit or a late reading
  corrects history.
- **Units:** `credit_c` is in cents, as every other money field is.

## Data model (core-api)

New table `conditional_credit_windows`, model `ConditionalCreditWindow` in
`models/billing.py`, with an Alembic migration. It hangs off `site_tariffs` the way
`ExportAdjustmentWindow` does.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `site_tariff_id` | FK `site_tariffs.id`, indexed | |
| `label` | `String(100)` | |
| `months` | JSON list of int | 1-12 |
| `days_of_week` | JSON list of int | Monday=0 to Sunday=6 |
| `start_time` | `Time` | |
| `end_time` | `Time` | |
| `max_avg_import_w` | `Numeric(10, 4)` | The credit applies when the average is strictly below this |
| `credit_c` | `Numeric(10, 4)` | Cents, greater than 0 |

## Calculation

A function in `cost.py`, called from `compute_cost` next to `_service_charge_rows`. It
loads the site's credit windows grouped by tariff (a helper in
`jobs/demand_config.py` beside `export_adjustment_windows_by_tariff`). For each local day
from `_local_days_in_range`:

1. Find the tariff in force at that day's local midnight (`tariff_effective_at`) and the
   credit windows on it whose months and days of week match the day.
2. Skip the window if its end time has not passed yet (compared with the real current
   time, whatever `prorate_fees` is).
3. Read `grid_import_w` and `grid_export_w` for the minutes from the window's start up
   to, but not including, its end. Every one of the window's `(end - start)` minutes
   needs a grid reading. The Envoy collector writes `grid_export_w` and no
   `grid_import_w` while the site exports, so an export-only minute counts as present
   at 0 W import.
4. If any minute has neither reading, no credit.
5. If the average is strictly below `max_avg_import_w`, subtract `credit_c` from the
   total.

Rules:

- Matching credit windows on the same day stack. Each is independent.
- The credit is part of `compute_cost` only. `compute_cost_series` leaves it out, as it
  leaves out the service and monthly fees, so a chart bucket does not spike.
- A day belongs to the range if its local midnight does, as with the fees. The window's
  readings are read from the database even when the range ends before the window does.
- A DST day uses the window's real local wall-clock bounds. The minute count comes
  from the converted UTC bounds.

## API contract additions

`routers/billing.py`, same shape as the export adjustment endpoints and reusing
`_create_tariff_child` and `_list_tariff_children`:

- `POST /sites/{site_id}/tariffs/{tariff_id}/conditional-credits` returns 201 and
  `ConditionalCreditWindowOut`.
- `GET /sites/{site_id}/tariffs/{tariff_id}/conditional-credits` returns a list.

`schemas.py` adds `ConditionalCreditWindowCreate` and `ConditionalCreditWindowOut`.
Validation on create, returning 422:

- `label` is 1 to 100 characters.
- `months` and `days_of_week` are non-empty, with values in range.
- `start_time` is before `end_time`. A window that wraps past midnight is rejected.
- `max_avg_import_w` is greater than 0.
- `credit_c` is greater than 0.

There is no edit or delete, matching the other windows. To change a credit, end the
tariff and add a new one.

## Webapp

- `webapp/src/billing/useBilling.ts`: a `ConditionalCreditWindow` type,
  `useConditionalCreditWindows` and `useCreateConditionalCreditWindow`.
- New `webapp/src/billing/ConditionalCredits.tsx` holds the list and the form. They are
  not added to `BillingPage.tsx`, which is already 650 lines. `BillingPage.tsx` renders
  the component under an active tariff, where the export adjustment list and form are
  rendered today.
- Form fields: label, months, days of week, start time, end time, "Average import below
  (W)" and "Credit (cents)". Their format and error handling follow the export
  adjustment form.

## Docs

- `webapp/src/docs/06-billing.md`: a "Conditional credits" subsection after export
  adjustments, covering the fields, the strict "below" test, the missing-readings rule
  and the wait until the window ends. Add conditional credits to the note that windows
  belong to one tariff and are re-entered under a replacement tariff.
- `webapp/src/docs/09-glossary.md`: an entry, if it lists the other window types.
- The tariff overview line in `06-billing.md` that lists demand windows, export
  adjustments and fee discounts gets conditional credits too.

## Testing

`core-api/tests/test_cost.py`:

- Average below the threshold gives a credit; equal to it gives none.
- One missing minute in the window gives none.
- A day whose window has not ended gives none.
- A non-matching weekday or month gives none.
- Two matching windows both apply.
- The tariff in force that day supplies the windows.
- The same result under `net` and `gross`.
- A range that ends before the window's end still sees the credit once the window has
  ended.
- `compute_cost_series` ignores the credit.

`core-api/tests/test_billing.py`: create and list, 404 on another user's tariff, and 422
for `start_time >= end_time`, an empty `days_of_week` and a non-positive credit or
threshold.

`core-api/tests/test_schema_constraints.py`: any constraint the migration adds.

Webapp: a test for `ConditionalCredits.tsx` that submits the form and shows the new
credit in the list.

## Out of scope

- Per-kWh or percentage credits. The credit is a flat amount per day.
- Windows that wrap past midnight.
- Editing or deleting a credit.
- A per-day or per-credit breakdown in the dashboard or billing page. The credit lowers
  the total only.
- Tolerance for missing readings. One missing minute voids the day.
