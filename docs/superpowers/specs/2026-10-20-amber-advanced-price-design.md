# Amber advancedPrice forecasts

## Intent

The dashboard's forecast line uses Amber's `perKwh` for `ForecastInterval`s. Amber also sends `advancedPrice` (`low`, `predicted`, `high`) on forecast intervals. Amber builds that value with CSIRO research, so it is a better guide to what export prices will be. The pricing collector currently drops it.

## Goals

- Forecast intervals store `advancedPrice.predicted` as their `price`. The dashboard forecast line changes with no webapp change.
- `low` and `high` travel through the Core API ingestion router and are stored, so a later change can draw a range band.

## Non-goals

- No webapp change. The chart does not draw the band yet.
- No new query endpoint for `low` and `high`.
- No change to actual or current intervals. They keep `perKwh`, the price you are billed.
- No backfill. Past forecasts were overwritten by later polls and Amber does not serve old forecasts.

## Design

### Collector (`amber-pricing-collector/amber_pricing_collector/mapping.py`)

`map_price_intervals` checks each interval for `advancedPrice`.

- Present: `price` is `str(advancedPrice["predicted"])`. The output also has `price_low` and `price_high`, built the same way from `low` and `high`.
- Absent: `price` is `str(perKwh)` as today, and `price_low` and `price_high` are omitted from the dict.

Prices stay strings, for the reason already in the function's docstring. An `advancedPrice` object missing one of its three keys falls into the existing malformed-interval path: log and skip that interval.

### Core API schema (`core-api/core_api/schemas.py`)

`PriceIntervalIn` gets `price_low: decimal.Decimal | None = None` and `price_high: decimal.Decimal | None = None`. Older collectors that send neither field keep working.

### Model and migration

`PriceInterval` (`core-api/core_api/models/pricing.py`) gets nullable `price_low` and `price_high` columns, both `Numeric(12, 4)`.

A new Alembic migration adds both columns. Its `down_revision` is `e8b2c4d6f1a9`, the current head. Existing rows get `NULL`. The downgrade drops the columns.

### Ingestion (`core-api/core_api/routers/ingestion.py`)

`_upsert_price_interval` writes both columns and includes them in the `on_conflict_do_update` set. A settled actual that replaces an earlier forecast for the same interval start resets `price_low` and `price_high` to `NULL`, because the payload carries `None`.

`_fan_out_price_readings` is unchanged. It fans out `interval.price`, which is now `predicted` for forecasts.

## Risks

- **Export sign is unverified.** The only forecast in `amber-pricing-collector/tests/fixtures/prices_current.json` is a `general` interval. No `feedIn` forecast shows whether `advancedPrice` uses the same sign as `perKwh`, which Amber reports as negative when you earn money. Compare `perKwh` and `advancedPrice.predicted` on a live `feedIn` forecast before release. If the signs differ, negate in the mapper.
- **Predicted can sit far from `perKwh`.** In the fixture, `perKwh` is 6.35 and `predicted` is 7.02. That is the intended change, but the forecast line will shift on deploy.

## Tests

- Mapping: a forecast interval maps to `predicted` plus low and high. An actual interval keeps `perKwh` and has no low or high keys. An `advancedPrice` missing a key is skipped.
- Fixture: add a `feedIn` `ForecastInterval` with `advancedPrice`, so the mapping tests cover the sell channel.
- Ingestion: low and high are stored. Re-posting an interval updates them. A later payload without them sets them back to `NULL`. A payload from an older collector without the fields is accepted.
- Migration: upgrade adds the columns and downgrade removes them.

## Docs

- Replace the `advancedPrice` row in the "Not mapped" list in `docs/superpowers/specs/2026-09-30-pricing-collector-design.md` with the new mapping.
- Update `docs/collectors.md`, `docs/how-it-works.md` and `webapp/src/docs/05-sources.md` where they describe the forecast, if they say it uses `perKwh`.

## Addendum: fallback marking (2026-10-20)

Amber stops sending `advancedPrice` at the end of its forecast window, so the last forecast interval falls back to `perKwh` and sits well off its neighbours (observed 33.8 beside 18.8). The 24-hour window is kept, and the fallback is shown instead of hidden.

- `GET /sites/{id}/prices` returns `forecast_fallback` on each reading. It is true when the reading falls in a `price_intervals` row that starts after now and has `price_low` `NULL`. The flag is computed at query time in `core_api/forecast_fallback.py`, so there is no migration. Settled and current intervals are never flagged.
- The Today's Pricing chart draws flagged intervals as a dotted line in the channel's colour, adds a legend entry only when one is present, and labels the tooltip "fallback: no advanced forecast".
- The chart covers the rest of today and the fallback interval is about 24 hours ahead, so it normally falls outside the chart. It shows only if Amber drops `advancedPrice` for nearer intervals.
