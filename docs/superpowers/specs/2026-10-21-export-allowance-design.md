# Export adjustment daily allowance

Date: 2026-10-21

## Problem

An export tariff can charge `x.y` c/kWh on exported energy but include a free
allowance of `z` kWh per day. Unused allowance accumulates across the billing
period, so on day N the allowance is `z * N` kWh. The current
`ExportAdjustmentWindow` applies its c/kWh adjustment to every exported kWh in
the window and has no allowance.

## Behaviour

- A window gets an optional `daily_allowance_kwh`. 0 means no allowance, and the
  window behaves as it does today.
- Within a billing period, the **charged kWh** at instant `t` is
  `C(t) = max(0, E(t) - z * D(t))`.
  - `E(t)` is the grid export (kWh) inside the window's months, days of week and
    time of day, from the billing period start up to `t`.
  - `D(t)` is the number of local days elapsed in the period, counting the
    current day, so day 1 gives `D = 1`.
- Allowance applies backwards. A day-1 overage is cancelled by allowance that
  accrues on later days. With `z = 10`, exporting 30 kWh on day 1 charges 20 kWh,
  and exporting nothing on days 2 and 3 brings the charge back to 0.
- Everything resets at the start of each billing period.
- The charge is `adjustment_c_per_kwh * C(t)`. The sign convention is unchanged:
  a charge is a negative adjustment (enter `-3` with allowance `10`).
- Each window with an allowance has its own balance. Overlapping windows count
  the same export separately.
- The allowance counts grid export only (`GRID_EXPORT_W`). The gross cost method
  is being removed and is out of scope.

## Design

### Data model

- Add `ExportAdjustmentWindow.daily_allowance_kwh`, `Numeric(10, 4)`, not null,
  default 0, via a new Alembic migration.
- Add the field to `ExportAdjustmentWindowCreate` (must be >= 0) and
  `ExportAdjustmentWindowOut`.
- Billing page form: field **Daily allowance (kWh)**, 0 means none. Update
  `webapp/src/docs/07-billing.md`.

### Allowance calculation

New module `core-api/core_api/export_allowance.py`, pure functions with no
database access:

- `charged_kwh(window, export_by_minute, period_start, at, tz)` returns `C(at)`.
- `marginal_adjustment_c_per_kwh(window, ...)` returns the adjustment for the
  next kWh at `at`: the window's rate if `E(at) >= z * D(at)`, otherwise 0.

Nothing is stored. Late-arriving readings are picked up on the next
calculation.

### Cost

- Windows with `z = 0` keep the current per-minute path in `_minute_costs`.
- For windows with `z > 0`, the cost over `[a, b)` within one billing period is
  `rate * (C(b) - C(a))`, so any sub-range or bucket is correct.
- `C` falls at local midnight when a new day's `z` kWh arrives, which gives a
  refund. `_minute_costs` books it in the first minute of the new day and adds
  that row even when there is no export data.
- `E` always starts at the billing period start, so a query range that begins
  mid-period also reads the exports before it.
- A query range that crosses a period boundary is split at the boundary.

### Price graphs

`adjusted_export_price` and `_displayed_price_fn` show the marginal price: the
price of the next kWh exported at that moment.

- Past and current intervals use `E` from the actual readings.
- Future intervals (the tomorrow chart) assume no further export, so `E` stays at
  its value now while `z * D` keeps growing each day.
- Windows with `z = 0` are unchanged.

### Edge cases

- A window with an allowance but no active billing cycle behaves as if `z = 0`.
  The Billing page shows a note.
- A tariff change mid-period ends the old tariff's window balances.

## Tests

- Day-1 overage that clears by day 3.
- Reset at the billing period boundary.
- Refund in the cost series at local midnight.
- Sub-range cost equals the difference of `C` at its ends.
- `z = 0` matches the old behaviour exactly.
- Marginal price on the graph flips from raw to adjusted as cumulative export
  crosses the allowance, and flips back when a new day's allowance arrives.
- Future intervals use frozen `E`.
- API validation rejects a negative allowance.
- Webapp: form field renders and posts `daily_allowance_kwh`.

## Out of scope

- Removing the gross cost method.
- A per-day (non-accumulating) allowance mode.
