# Price instability on the pricing graph

## Intent

Amber prices can spike with little warning. The today's-pricing chart shows one forecast line and gives no sign of when that forecast is unreliable. Amber sends three signals for this on each interval: `spikeStatus`, `range`, and the width of the `advancedPrice` band. The collector keeps only the `advancedPrice` band, and the API does not return even that.

## Goals

- The chart shades the forecast's low-to-high band behind the dashed predicted line.
- The chart marks intervals Amber flags as `potential` or `spike`.
- A line above the chart says when instability is expected today, for example "Price instability expected 4:30pm to 7:00pm".

## Non-goals

- No alerts, notifications, or automation based on instability.
- No change to the sell channel's adjusted-price logic. The band for `sell` is not shown in the first version (see Open questions).
- No backfill of `spike_status` for past intervals beyond what Amber's `/prices` endpoint returns during the existing gap backfill.
- No use of `descriptor`. It describes how cheap or dear a price is, not how uncertain it is.

## Signals

| Field | On | Use |
|---|---|---|
| `spikeStatus` (`none`, `potential`, `spike`) | all intervals | Amber's own flag. A non-`none` value marks the interval unstable. |
| `advancedPrice` `{low, predicted, high}` | forecast and current intervals | Already stored as `price_low` and `price_high`. Band width `high - low` measures forecast uncertainty. |
| `range` `{min, max}` | forecast and current intervals, only when volatile | Not stored. Skipped: its presence is mostly redundant with `spikeStatus` and the band. |

An interval is **unstable** when `spike_status` is not `none`, or its band width exceeds the band threshold. The first version uses a fixed threshold, `BAND_WIDTH_THRESHOLD_CENTS = 20` c/kWh, as a constant in `webapp/src/dashboard/priceChart.ts`. The value is a placeholder until we have a week of stored bands to look at.

## Design

### Collector (`amber-pricing-collector/amber_pricing_collector/mapping.py`)

`map_price_intervals` adds `spike_status` to every mapped interval, copied from `spikeStatus`. A missing or unrecognised value maps to `none`.

### Core API

- `PriceIntervalIn` (`schemas.py`) gets `spike_status: str = "none"`, validated against `none`, `potential`, `spike`. Older collectors that omit it keep working.
- `PriceInterval` (`models/pricing.py`) gets a non-null `spike_status` column, `String(16)` with a check constraint, default `none`. A new Alembic migration adds it with a server default so existing rows become `none`.
- `_upsert_price_interval` (`routers/ingestion.py`) writes it and includes it in the `on_conflict_do_update` set, so a settled actual overwrites an earlier forecast's flag.
- `PriceReadingOut` gets `price_low`, `price_high` (both nullable) and `spike_status`.

### Query path

`price_low`, `price_high` and `spike_status` live on `price_intervals`. `GET /prices` reads `price_readings`, which holds one row per minute with no band columns. `get_prices` must look up each reading's interval. Either join on `(pricing_source_id, channel)` with `interval_start <= ts < interval_end`, or load the intervals for the window and attach them in Python, as `fallback_ranges` already does for the fallback flag. Prefer the second: the window is one day, so about 288 intervals per channel. The exact approach is settled in the plan, with a query-count test.

Both channels carry bands, but `sell` values are raw while its line is adjusted, so the first version draws the band for `buy` only. See Open questions.

### Webapp

- `PriceReading` and `PricePoint` (`priceChart.ts`) carry `lowCentsPerKwh`, `highCentsPerKwh` and `spikeStatus`. `buildPriceChartData` copies them from the first reading of each 5-minute bucket, as it does for the value.
- A new pure function `findInstabilityWindows(points)` returns merged time ranges where consecutive points are unstable. It has unit tests for: no flags, a single point, adjacent points merging, a gap splitting two windows, and a band-only window.
- `TodayPriceChart.tsx` draws:
  - a Recharts `Area` for the low-to-high band behind the predicted line, future points only;
  - a `ReferenceArea` for each unstable window, tinted, with the `potential` and `spike` cases in different tints.
- `DashboardPage` renders the summary line from `findInstabilityWindows`, using the site timezone. It renders nothing when there are no windows.

## Tests

- Collector: `spikeStatus` maps through for each value, and an unknown value maps to `none`.
- Core API: ingestion round-trips `spike_status`; a later actual resets it; `/prices` returns the band and flag for forecast rows and nulls for actual rows; the migration upgrades and downgrades.
- Webapp: `buildPriceChartData` carries the new fields; `findInstabilityWindows` cases above; the chart renders the band and the summary line (and omits both when there is no instability).

## Probe findings (2026-10-20, live account, 5-minute billing site)

- **Locked current interval.** A `CurrentInterval` with `estimate: false` has no `advancedPrice`. With `estimate: true` it does (predicted 20.95 against `perKwh` 24.50). The mapper already stores `predicted` while the interval is an estimate and `perKwh` once it locks, so no mapper change is needed.
- **Export sign.** `feedIn` forecasts use the same sign as `perKwh` (both negative). The band is ordered by quality, not number: one interval had `low` -12.41, `predicted` -13.28, `high` -14.59. The chart must draw the band from `min(low, high)` to `max(low, high)`.
- **Forecast shape.** With `resolution=5`, `prices/current` returns 13 five-minute intervals, then 30-minute intervals out to about 24 hours (59 general rows for `next=288`). `FORECAST_INTERVALS = 300` is therefore more than Amber serves, which is harmless because the mapper uses `startTime` and `endTime`. The chart must not assume 5-minute spacing in the forecast. The webapp's 5-minute bucketing works because the backend fans each interval out to per-minute rows.
- **Band width.** General-channel bands across the forecast had min 2.3, median 6.7, p90 16.5 and max 26.1 c/kWh. A 20 c/kWh threshold would flag roughly the top 5% of intervals on a normal day.
- **Spike flags.** All 118 intervals sampled were `none`. No `potential` or `spike` sample exists yet, so `spikeStatus` behaviour remains unobserved.

## Open questions

1. **Does `spikeStatus` change often enough to be useful?** No spike was live when probed. Collect a few days of `spike_status` and band widths before fixing the threshold.
2. **Sell channel.** Export prices are adjusted by `adjusted_export_price`. Either apply the same adjustment to the band's low and high, or draw the band on `buy` only. Recommendation: `buy` only in the first version.
