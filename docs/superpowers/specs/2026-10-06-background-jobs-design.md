# Background Jobs: Demand Tariff Billing + Retention Rollup — Design Spec

Date: 2026-10-06
Status: Approved for planning
Supersedes: the "Demand tariff & site billing" and "Retention & rollup" sections of
`docs/superpowers/specs/2026-09-25-core-api-design.md` (Core API design spec)

## Purpose

The Core API design spec built the `DemandInterval`/`DemandCharge`/`*Rollup5m` schema and
explicitly deferred two things to implementation time: no job code was ever written against
that schema, and the exact demand-charge arithmetic was left unguessed, "verified against
an actual bill" rather than assumed. This spec does both: corrects the schema where the
original design's assumptions didn't match how a real demand tariff (Amber, passing through
the network's actual tariff) is billed, and specifies the three periodic jobs that make the
schema live.

This sub-project adds no new service. All three jobs run in-process inside Core API via
APScheduler, per the original spec's own reasoning (no separate worker justified for
lightweight periodic tasks at this scale).

## What changed from the original design, and why

The original spec's `DemandInterval`/`DemandCharge`/`DemandWindow` fields were plausible but
unverified. Working through Amber's own published explanation of how a demand charge is
actually calculated surfaced three corrections:

1. **Granularity.** The original spec said "30-min buckets, or per
   `measurement_interval_minutes`" without pinning how that interacts with 5-minute-interval
   raw data. The corrected design stores `DemandInterval` at a fixed 5-minute granularity
   (matching `EnergyReadingRollup5m`'s existing 5-min convention) and derives each site's
   configured demand-measurement window (`DemandWindow.measurement_interval_minutes`, still
   defaulting to 30) as a **trailing sliding sum** over the last `N` 5-minute slots, where
   `N = measurement_interval_minutes / 5`. This finds the true highest sustained
   usage over any 30-minute (or whatever width) span, not just whichever usage happens to
   fall in a fixed, non-overlapping 30-minute clock-aligned block.

2. **Zero outside the demand window, by construction.** Each 5-minute slot's contribution
   to the rolling sum is the slot's real average import power only if the slot falls inside
   a demand window; otherwise `0`. This makes peak-finding a plain `MAX()` over the whole
   billing period with no separate `WHERE is_demand_window` filter — a slot that never
   mattered can never win a max. The one edge effect (the first `measurement_interval_minutes
   − 5` of a window's rolling sums are partially diluted by zeroed pre-window slots) can only
   ever understate, never overstate, a real peak, so it never produces a wrong answer and
   isn't worth engineering around.

3. **Units and the actual formula.** Amber's own explanation: peak usage is measured in
   kWh over the demand-measurement window, converted to an implied kW rate, then
   `charge = peak_kW × rate × days_in_billing_period`, where the rate is **$/kW/day** — not
   $/kWh as the original spec's `rate_c_per_kwh` field name assumed. Because this design
   already stores average *power* (W) per slot rather than raw energy (kWh), the "×2"
   conversion Amber's article describes (converting a half-hour *energy* reading into an
   implied hourly *power* rate) is unnecessary here — it only exists to correct for their
   meter reporting energy, not power. The corrected formula is detailed below.

## Schema changes

All in `core-api/core_api/models/billing.py`, via one Alembic migration. No job has ever
written to these tables, so these are clean renames/drops, not data migrations.

- `DemandInterval.avg_import_w` → renamed to **`rolling_sum_w`**: no longer a single
  5-minute average; it's the sum of the last `N` 5-minute slot contributions (zeroed outside
  a demand window, as above). `N` is determined per-site from whichever `DemandWindow` was
  effective at that slot's timestamp.
- `DemandInterval.is_demand_window` — **unchanged**. Redundant for peak-finding now that
  zeroing does that job, but kept for audit/debugging (so a wrong charge can be traced back
  to "was this slot really inside a window").
- `DemandWindow.rate_c_per_kwh` → renamed to **`rate_c_per_kw_per_day`** (unit correction;
  no change to the stored values' meaning for any already-entered row, since no row has ever
  been written by a job — this only affects the column's name/semantics going forward).
- `DemandCharge.demand_window_hours` → **dropped**. Billing-period length in days is
  computed on the fly from `billing_period_start`/`billing_period_end` each time the job
  runs, not persisted as its own column.

## Job 1: DemandInterval derivation

**Cadence:** every 5 minutes, in-process (APScheduler).

**Per run, per site:**
1. Find the latest `DemandInterval.interval_start` already computed for this site (or the
   site's earliest `EnergyReading` timestamp if none exists yet).
2. For every closed 5-minute bucket since then, up to (now − 5 min) (closed-bucket safety
   margin, consistent with the ingestion-idempotency pattern used elsewhere in this system):
   - `raw_5min_avg_w` = average `grid_import_w` from `EnergyReading` rows in that bucket.
   - Determine whether the bucket's `interval_start` falls inside a `DemandWindow` (joined
     through the `SiteTariff` effective at that timestamp) or an `is_demand_window`-flagged
     `PriceInterval` — using whichever configuration was in force **at that timestamp**, not
     whatever is configured now (same non-retroactive rule the original spec already applied
     to `DemandWindow`/`SiteTariff` generally).
   - `this_slot_contribution` = `raw_5min_avg_w` if inside a demand window, else `0`.
   - `rolling_sum_w` = sum of `this_slot_contribution` and the preceding `N − 1` slots'
     stored contributions, where `N = measurement_interval_minutes / 5` for whichever
     `DemandWindow` applies to this slot (or the site's single configured window — a site is
     not expected to have conflicting `measurement_interval_minutes` values active
     simultaneously; the first matching `DemandWindow` wins if it ever happens).
   - `rate_applied` = the matching `DemandWindow.rate_c_per_kw_per_day`, or `null` if outside
     any window.
   - Upsert into `DemandInterval` keyed on `(site_id, interval_start)`.

**Idempotency:** safe to re-run — re-deriving an already-computed bucket produces the same
row (upsert), matching the original spec's idempotent-by-construction design for all
periodic jobs here.

**Catch-up:** because the job always resumes from the latest already-derived bucket (not
"now"), a gap (job downtime, or a collector backfilling delayed `EnergyReading` rows) is
naturally caught up on the next run rather than silently skipped.

## Job 2: DemandCharge estimation / finalization

**Cadence:** daily, in-process (APScheduler). No separate "finalization job" — the same
daily upsert produces both the running estimate and, once the period closes, the final
figure; there is nothing further to roll up afterward.

**Per run, per site, for every billing period that is either currently open or closed
without yet having a non-estimated `DemandCharge` row** (the latter clause is the catch-up
path for a missed run):
1. `peak_rolling_sum_w` = `MAX(DemandInterval.rolling_sum_w)` for this site where
   `interval_start` falls in `[billing_period_start, billing_period_end)`.
2. `peak_demand_kW` = `peak_rolling_sum_w / N / 1000`, where `N` is the same slot count used
   to build that rolling sum (`measurement_interval_minutes / 5`) — converts "sum of N
   5-minute averages" back into a true average power over the window, then W → kW.
3. `rate_applied` = the `rate_c_per_kw_per_day` recorded on the winning `DemandInterval` row
   (`peak_interval_start`).
4. `billing_period_days` = `(billing_period_end − billing_period_start)` in days, computed
   here, not stored.
5. `charge_amount` (cents) = `peak_demand_kW × rate_applied × billing_period_days`.
6. `is_estimated` = `True` if `billing_period_end` is still in the future (relative to "today"
   in the site's own `timezone`, consistent with how `billing_period_start`/`end` themselves
   are already computed in the site's timezone), or if `EnergyReading` coverage for the
   period has gaps (unchanged from the original spec's intent for this flag). Otherwise
   `False`.
7. Upsert into `DemandCharge` keyed on `(site_id, billing_period_start)`.

**Idempotency:** same upsert pattern; a crash mid-run or a missed day is safe to re-run and
self-heals on the next run via the catch-up clause above.

## Job 3: Retention rollup

**Cadence:** daily, in-process (APScheduler). Unchanged from the original spec's design —
this job is unrelated to demand charges; `DemandCharge` itself is already a final per-period
figure and was never intended to be rolled up (that only applies to the raw per-minute
reading tables below).

**Per run:** for `EnergyReading`, `WeatherReading`, and `PriceReading` rows older than 1
year: aggregate into the matching `*Rollup5m` table using a `METRIC_AGGREGATION` mapping
(`metric` enum → `average` | `last_value`; `average` for power/price metrics, `last_value`
for state metrics like `battery_soc_pct`), then delete the raw rows that were just rolled up.
This mapping does not yet exist in code and is introduced by this job — one entry per metric,
not a branch in job code, per the original spec's intent.

`PriceInterval` is not rolled up (already coarse, low volume) — unchanged from the original
spec.

## Testing focus

- `DemandInterval`: a slot exactly at a demand window's open/close boundary; a billing
  period whose `DemandWindow` changed mid-period (must use the window effective at each
  slot's own timestamp, not whatever is configured now); re-running the job over an
  already-derived range (must not double-count or change results).
- `DemandCharge`: a billing period with zero demand-window usage (charge should be exactly
  `0`, not an error); a period with `EnergyReading` gaps (`is_estimated` must be `True`); a
  period that closes between two daily runs (next run must finalize it, not skip it).
- Retention rollup: a `last_value` metric (e.g. `battery_soc_pct`) must roll up to the
  bucket's last reading, not an average of several incompatible instantaneous states.

## Explicitly out of scope

- `DemandInterval`/`DemandCharge` retention (how long these are kept, if ever rolled up or
  pruned) — not addressed by the original spec and not needed now; these are low-volume
  compared to raw readings.
- A site configuring multiple simultaneously-active `DemandWindow` rows with different
  `measurement_interval_minutes` at the same time — not supported; the first match wins, per
  Job 1 above. No evidence this is a real scenario for any tariff this system targets.
