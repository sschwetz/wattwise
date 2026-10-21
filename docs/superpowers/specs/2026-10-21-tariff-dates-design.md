# Tariff and window dates

Date: 2026-10-21

## Problem

Rates change on fixed dates: a change of retailer, or a new financial year on 1 July. Today the Billing page cannot express that. **Add tariff** starts the tariff at the moment of the click, and **End this tariff** ends it at the moment of the click. The database columns `effective_from` and `effective_to` already exist, and the API already accepts them for tariffs, billing cycles and local rate windows, so the owner has been editing start dates in the database by hand.

Demand windows, export adjustments (bonus) and conditional credits have no dates of their own. They run for as long as their tariff does. Changing one demand rate on 1 July means ending the whole tariff and re-entering the service charge, the monthly fee and every other window.

## Goals

- The user picks the start date and the end date of:
  - a tariff (service charge and monthly fee),
  - a demand window,
  - an export adjustment window,
  - a conditional credit window,
  - a billing cycle,
  - a local rate window (the per-kWh rates of a `local` pricing source).
- Dates can be in the past or the future.
- Start and end are separate steps. The user ends the old item with an end date, then adds the new one with a start date.
- When the user adds an item, the form suggests a start date that continues from the matching item that has ended.
- Costs, monthly fees, credits and demand charges for the affected days are recalculated to the supplied dates, including for billing periods that are already finalized.

## Non-goals

- Editing the rates of an existing tariff or window in place. Changing a rate stays end-then-add.
- Monthly fee discount windows. They already carry an absolute `starts_at` and `ends_at`.
- Time-of-day patterns (`start_time`, `end_time`, months, days of week) on windows. They are unchanged and are not the same thing as the new dates.
- Changing how Amber prices are fetched.

## Behaviour

### Dates

- The user enters calendar dates. The site's time zone applies.
- A start date means 00:00 local on that day. An end date is the last day the item applies, so it ends at 00:00 local on the following day. An item ending 30 June covers all of 30 June, and a replacement starting 1 July meets it exactly with no gap or overlap. *(Confirmed by the user.)* The stored `effective_to` is that following midnight, which matches the half-open `[effective_from, effective_to)` test the cost code already uses.
- Conversion uses `local_midnight` in `core_api/time_utils.py`, so a daylight-saving day keeps its 23 or 25 hours.
- An end date is optional when adding. An item with no end date is open ended.
- An end date must not be before the start date. The API rejects it with a 422.

### Suggested start date

When the user opens the add form for a tariff, window or billing cycle, the start date is prefilled:

- If a matching item has ended, the suggestion is the day after its end date. If several match, use the one with the latest end date.
- Otherwise the suggestion is today.
- The user can overwrite it.

"Matching" means:

- Tariff: any ended tariff of the site.
- Billing cycle: any ended billing cycle of the site.
- Demand window, export adjustment, conditional credit: an ended window of the same type on the same tariff.
- Local rate window: an ended window of the same direction (buy, sell or controlled load) on the same source.

The suggestion is a form default only. The API does not apply it. *(Assumption: matching on type and tariff or direction only, not on time slot or rate.)*

### Window dates against the tariff

- A demand window, export adjustment or conditional credit gets `effective_from` and an optional `effective_to`.
- *(Assumption.)* A window's dates must fall inside its tariff's dates. The API returns a 422 for a window that starts before the tariff starts, or that ends after the tariff ends. If the tariff has no end date, the window may end on any date.
- Ending a tariff does not change its windows. A window with no end date stops applying when the tariff ends, because cost code only looks at windows of the tariff in force at that moment.

### Recalculation

- Cost calculation reads dates at the moment it runs, so any cost chart or total for a past range reflects the new dates immediately. This needs the tariff and window lookups to use the new window dates (see Design).
- Demand charges are stored per billing period. The demand charge job skips a period once it is finalized (`is_estimated` is false). When a demand window or a billing cycle is created, changed or deleted with dates that touch a finalized period, the stored `DemandCharge` rows for the affected periods are recalculated.
- Local rate windows already reprice on change through `local_reprice_markers`. Creating or ending a local rate window with a past date marks a reprice from the earliest affected instant. The reprice design (`2026-10-14-local-pricing-reprice-on-change-design.md`) covers the mechanism and is not changed here.

## Design

### Data model

- Add `effective_from` (not null) and `effective_to` (nullable) to `DemandWindow`, `ExportAdjustmentWindow` and `ConditionalCreditWindow`, both `DateTime(timezone=True)`, via a new Alembic migration.
- The migration fills each existing window's dates from its tariff: `effective_from` is the tariff's `effective_from` and `effective_to` is the tariff's `effective_to`. Current costs do not change.
- The `Create` and `Out` schemas for the three window types gain the two fields, with the same `effective_to > effective_from` check that `LocalRateWindowCreate` has.
- The window creation endpoints validate that the window's range lies inside the tariff's range.

### API

- `SiteTariffCreate` and `BillingCycleCreate` already take the dates. Add the `effective_to > effective_from` check to `SiteTariffCreate` if it is missing.
- `SiteTariffUpdate` and `BillingCycleUpdate` already take `effective_to`. Add a `PATCH` for each of the three window types that takes `effective_to`, mirroring the tariff `PATCH`.
- The webapp sends dates as local calendar dates and the API converts them, or the webapp converts them using the site time zone and sends UTC. *(Left to the builder, which fits the existing code better.)*

### Cost and demand code

- Window lookups in `jobs/demand_config.py` (`first_matching_demand_window`, `any_configured_demand_window`, `total_export_adjustment_c_per_kwh`, `credit_window_applies_on`) and in `cost.py` (`_plain_export_adjustment_c_per_kwh`, `_conditional_credit_total_c`, `_minute_costs`) add a check that the instant falls in `[window.effective_from, window.effective_to)`.
- Demand charge recalculation: when a demand window or billing cycle changes, delete or reopen the stored `DemandCharge` rows whose billing period overlaps the changed range, so `run_demand_charge_job` rebuilds them. How to trigger it (inline in the request, or by marking the periods estimated so the next job run rebuilds them) is left to the builder.

### Webapp

- `BillingPage.tsx` and `useBilling.ts`: the tariff form gets a **Start date** field, with the suggested default. **End this tariff** opens an **End date** field. The same two fields appear on the forms and rows for demand windows, export adjustments, conditional credits and billing cycles.
- `SourcesPage.tsx`: the local rate window form gets the same **Start date** and an **End date** on the End action.
- Ended items stay listed, as ended tariffs do now.
- Update `webapp/src/docs/07-billing.md` ("Adding a tariff", "Changing a tariff", the window sections and the billing cycle section) and `webapp/src/docs/05-sources.md` ("Changing a rate"). Update `docs/data-model.md` and `docs/core-api.md`.

## Designer's calls

These were settled during the builder check. The user has not reviewed them.

- **Overlaps.** Tariffs and billing cycles reject an overlap with another item of the same site (422), the way local rate windows already do through `find_overlap`. Demand, export and credit windows may overlap, as they can today.
- **Window range rule.** The window-inside-tariff check runs when a window is created or its `effective_to` is changed. Shortening a tariff's end date does not touch its windows. A window that now extends past the tariff stops applying when the tariff ends. A window with no end date is allowed under a tariff that has one.
- **Showing stored end dates.** The forms show the last day an item applies. When a stored `effective_to` is at local midnight, the form shows the previous day. When it is not at local midnight (a click-moment end), the form shows that moment's local date.
- **Billing cycle changes.** A cycle change moves period boundaries. Delete every stored `DemandCharge` row whose period starts at or after the earliest changed instant, and let `run_demand_charge_job` rebuild them. For a window change, delete the rows whose period overlaps the changed range.
- **Other stored cost data.** The builder checks whether any other table caches per-period cost or fee amounts. `DemandCharge` is the only one known. Anything found is invalidated by the same trigger.
- **Time zone.** The site's time zone applies everywhere, including local rate windows, because every source belongs to a site.

## Constraints

- Money stays `Decimal`.
- No new secrets or credentials.
- Existing behaviour for items with no explicit dates must not change: a window inheriting its tariff's dates prices the same minutes as before.

## Left to the builder

- Whether date conversion happens in the webapp or the API.
- How the demand charge recalculation is triggered.
- The exact layout of the date fields in each form.
- Test structure, following the existing tests in `core-api/tests` and the `*.test.tsx` files.
