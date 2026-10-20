# Price Instability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show forecast price instability on the Today's Pricing chart: a shaded low-to-high band, tinted windows where Amber flags a spike, and a summary line.

**Architecture:** The collector forwards Amber's `spikeStatus`. Core API stores it on `price_intervals` and returns it, with `price_low` and `price_high`, from `GET /prices` by looking up each reading's interval in Python (the same pattern as `forecast_fallback.py`). The webapp derives instability windows from those fields and draws them on the chart.

**Tech Stack:** Python/FastAPI/SQLAlchemy/Alembic (core-api), httpx (collector), React/Recharts/Vitest (webapp).

**Spec:** `docs/superpowers/specs/2026-10-20-price-instability-design.md`

## Global Constraints

- `spike_status` values are exactly `none`, `potential`, `spike`. Anything else maps to `none` in the collector.
- Unstable interval: `spike_status != "none"` OR band width `abs(high - low) > BAND_WIDTH_THRESHOLD_CENTS`, with `BAND_WIDTH_THRESHOLD_CENTS = 20` (a constant in `webapp/src/dashboard/priceChart.ts`).
- The band is drawn from `min(low, high)` to `max(low, high)`. Probing showed `low` can be numerically above `high` on the export channel.
- Band, spike tint and summary line are for the `buy` channel only. `sell` readings still return the new fields, but the webapp does not draw them.
- Prose in docs and comments follows `.claude/rules/avoid-ai-writing.md`.
- Core API migration `down_revision` is `f1a8c3d5e7b2` (current head).

## Review Focus

- A day with no forecast bands and no spikes: no band, no tint, no summary line (Task 5).
- Readings with `price_low`/`price_high` null (settled actuals, fallback forecasts): `/prices` returns nulls and the chart draws no band for them (Tasks 2, 4).
- A spike run that crosses midnight or the zoom edge: windows clip to the visible range and do not throw (Task 4).
- Old collector payloads with no `spike_status`: ingestion defaults to `none` (Task 1).
- A late actual for an interval that was `spike` as a forecast: the stored flag resets to `none` (Task 1).

---

### Task 1: Core API stores `spike_status`

**Files:**
- Modify: `core-api/core_api/models/pricing.py` (`PriceInterval`), `core-api/core_api/schemas.py` (`PriceIntervalIn`), `core-api/core_api/routers/ingestion.py` (`_upsert_price_interval`)
- Create: `core-api/alembic/versions/<new id>_add_spike_status_to_price_intervals.py`
- Test: `core-api/tests/test_ingestion.py`

**Interfaces:**
- Produces: `PriceInterval.spike_status: str` (non-null, default `"none"`); `PriceIntervalIn.spike_status: Literal["none", "potential", "spike"] = "none"`.

- [ ] **Step 1: Write failing tests** in `test_ingestion.py`, following the existing price-interval ingestion tests there:
  - `test_price_interval_stores_spike_status`: post an interval with `"spike_status": "potential"`; the stored `PriceInterval.spike_status == "potential"`.
  - `test_price_interval_without_spike_status_defaults_to_none`: post without the field; stored value is `"none"`.
  - `test_later_actual_resets_spike_status`: post a `spike` forecast, then the same `interval_start` with `spike_status` omitted; stored value is `"none"`.
  - `test_price_interval_rejects_unknown_spike_status`: post `"bogus"`; response is 422.
- [ ] **Step 2: Run** `cd core-api && uv run pytest tests/test_ingestion.py -q -k spike`. Expected: FAIL.
- [ ] **Step 3: Implement.** Add the model column `String(16)`, `nullable=False`, `server_default="none"`, with a check constraint named `ck_price_intervals_spike_status` restricting the three values. Add the schema field. Add `spike_status` to the `.values(...)` and the `on_conflict_do_update` `set_` in `_upsert_price_interval`. Write the Alembic migration with `op.add_column` (server default `none`, using `op.batch_alter_table` if the repo's other migrations do for SQLite) and a matching downgrade.
- [ ] **Step 4: Run** `uv run pytest -q` in `core-api`. Expected: all pass, including any test that runs migrations against an empty database.
- [ ] **Step 5: Commit** `feat(core-api): store Amber spike status on price intervals`.

### Task 2: `GET /prices` returns the band and spike status

**Files:**
- Create: `core-api/core_api/price_bands.py`
- Modify: `core-api/core_api/schemas.py` (`PriceReadingOut`), `core-api/core_api/routers/query.py` (`get_prices`)
- Test: `core-api/tests/test_query.py`

**Interfaces:**
- Produces: `PriceReadingOut.price_low: float | None = None`, `price_high: float | None = None`, `spike_status: str = "none"`.
- Produces in `price_bands.py`: `interval_bands(db, source_ids, channel, start, end) -> list[IntervalBand]` where `IntervalBand` is a frozen dataclass `(start, end, price_low, price_high, spike_status)`, `start` floored to the minute as in `fallback_ranges`; and `band_at(ts, bands) -> IntervalBand | None`.

- [ ] **Step 1: Write failing tests** in `test_query.py`, reusing `seed_forecast_intervals` or a new seed helper beside it:
  - `test_get_prices_returns_band_and_spike_status_for_forecast_rows`: a forecast interval with low/high and `potential`; the matching readings return those values.
  - `test_get_prices_returns_nulls_for_rows_without_a_band`: a settled interval gives `price_low is None`, `price_high is None`, `spike_status == "none"`.
  - `test_get_prices_without_interval_rows_returns_defaults`: mirror `test_get_prices_without_interval_rows_flags_nothing`.
  - `test_get_prices_runs_one_interval_query`: count SQL statements with an SQLAlchemy `before_cursor_execute` listener around the request. Assert the `price_intervals` queries do not grow with the number of readings (one for fallback ranges, one for bands).
- [ ] **Step 2: Run** `uv run pytest tests/test_query.py -q -k "band or spike"`. Expected: FAIL.
- [ ] **Step 3: Implement** `interval_bands` as one `select` on `PriceInterval` for the sources, channel and window (same filters as `fallback_ranges` minus the future-only and null-band conditions), using `_naive_utc` semantics for SQLite. `get_prices` calls it once and attaches fields per reading via `band_at`. Prices go through `float`; the sell channel returns the raw stored band.
- [ ] **Step 4: Run** `uv run pytest -q` in `core-api`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(core-api): return price band and spike status from /prices`.

### Task 3: Collector forwards `spikeStatus`

**Files:**
- Modify: `amber-pricing-collector/amber_pricing_collector/mapping.py`
- Test: `amber-pricing-collector/tests/test_mapping.py`

**Interfaces:**
- Consumes: Task 1's `spike_status` field on `PriceIntervalIn`.
- Produces: every dict from `map_price_intervals` has `"spike_status"` in `{"none", "potential", "spike"}`.

- [ ] **Step 1: Write failing tests**: one parametrized test over `none`, `potential`, `spike` (output equals input); `test_unknown_spike_status_maps_to_none`; `test_missing_spike_status_maps_to_none`.
- [ ] **Step 2: Run** `cd amber-pricing-collector && uv run pytest tests/test_mapping.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** a module constant `SPIKE_STATUSES = frozenset({"none", "potential", "spike"})` and add `"spike_status"` to the mapped dict, defaulting via `interval.get("spikeStatus")`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass. Update any existing mapping assertion that compares whole dicts.
- [ ] **Step 5: Commit** `feat(collector): forward Amber spikeStatus`.

### Task 4: Webapp derives bands and instability windows

**Files:**
- Modify: `webapp/src/dashboard/priceChart.ts`
- Test: `webapp/src/dashboard/priceChart.test.ts`

**Interfaces:**
- Produces:
  - `PriceReading` gains optional `price_low?: number | null`, `price_high?: number | null`, `spike_status?: "none" | "potential" | "spike"`.
  - `PricePoint` gains `band: [number, number] | null` (ordered min, max) and `spikeStatus: "none" | "potential" | "spike"`.
  - `TodayPricingPoint` gains `importBand: [number, number] | null` and `importSpikeStatus`, copied from the import points only.
  - `export const BAND_WIDTH_THRESHOLD_CENTS = 20`.
  - `export interface InstabilityWindow { from: number; to: number; level: "spike" | "potential" | "wide-band" }`.
  - `export function findInstabilityWindows(points: TodayPricingPoint[], intervalMs?: number): InstabilityWindow[]`. A point is unstable per Global Constraints. Consecutive unstable points merge into one window; `to` is the last point's `ts` plus `intervalMs` (default 5 minutes). `level` is the worst in the run, ordered `spike` > `potential` > `wide-band`. Only future-or-current points are considered: points whose `actualCentsPerKwh` is not null are skipped.

- [ ] **Step 1: Write failing tests** in `priceChart.test.ts`:
  - `buildPriceChartData` carries `band` ordered min/max, including a reversed `low`/`high` case; null low/high gives `band === null`.
  - `findInstabilityWindows`: no flags returns `[]`; one spike point returns one window of `intervalMs` length; adjacent points merge; a one-point gap splits into two windows; a band of width 20 is not unstable and 20.01 is; level takes the worst in a run; actual points are skipped; empty input returns `[]`.
- [ ] **Step 2: Run** `cd webapp && npx vitest run src/dashboard/priceChart.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** the types and functions above. `buildPriceChartData` copies `band` and `spikeStatus` from the first reading in each bucket, as it does for value. `mergeImportExportPrices` copies `band`/`spikeStatus` onto the `import*` fields only.
- [ ] **Step 4: Run** `npx vitest run && npx tsc --noEmit`. Expected: pass; fix existing tests that build `TodayPricingPoint` literals by adding the new fields.
- [ ] **Step 5: Commit** `feat(webapp): derive price bands and instability windows`.

### Task 5: Chart band, tinted windows, summary line, docs

**Files:**
- Modify: `webapp/src/dashboard/TodayPriceChart.tsx`, `webapp/src/docs/06-dashboard.md`
- Create: `webapp/src/dashboard/InstabilitySummary.tsx`
- Test: `webapp/src/dashboard/TodayPriceChart.test.tsx` (create if absent, following `DashboardPage.test.tsx` mocking of `apiFetch`), `webapp/src/dashboard/InstabilitySummary.test.tsx`

**Interfaces:**
- Consumes: `findInstabilityWindows`, `InstabilityWindow`, `TodayPricingPoint.importBand` from Task 4.
- Produces: `InstabilitySummary({ windows, timeZone }: { windows: InstabilityWindow[]; timeZone: string })` rendering nothing for `[]`, otherwise one muted line: `Price instability expected 4:30pm to 7:00pm` for one window, or `Price instability expected 4:30pm to 5:00pm and 6:15pm to 7:00pm`, formatted in `timeZone`.

- [ ] **Step 1: Write failing tests**:
  - `InstabilitySummary`: renders nothing for `[]`; one window text; two windows text; formats in the given time zone.
  - `TodayPriceChart`: with mocked prices containing a banded forecast, the chart contains an element for the band (query by a `data-testid` on the `Area`'s wrapper or by the legend swatch label `Forecast range`); with a `potential` interval, the summary line is present; with plain prices, neither the legend entry nor the summary line is present.
- [ ] **Step 2: Run** `npx vitest run src/dashboard`. Expected: FAIL.
- [ ] **Step 3: Implement.** Switch `LineChart` to Recharts `ComposedChart` (keep `syncId`, `syncMethod`, handlers and style). Add, before the lines, an `Area` with `dataKey="importBand"`, `type="stepAfter"`, `stroke="none"`, a low-opacity fill in `SWATCH_COLORS.gridImport`, `isAnimationActive={false}`. Add a `ReferenceArea` per window clipped to the visible range, tinted differently for `spike` and `potential`/`wide-band`. Add a legend swatch `Forecast range` shown only when some point has a band. Render `InstabilitySummary` above the chart using the site's time zone (take it from the same source `TodayPriceChart` or its parent already uses for day boundaries; if none, add a `timeZone` prop). Tooltip: show the band as `low to high` when present.
- [ ] **Step 4: Update docs.** In `06-dashboard.md`, describe the shaded range, the tinted windows and the summary line in plain terms. Run `npx vitest run && npx tsc --noEmit`. Expected: pass.
- [ ] **Step 5: Commit** `feat(webapp): show forecast range and price instability on the pricing chart`.

---

**After Task 5:** deploy order is core-api (Task 1, 2), then collector (Task 3), then webapp. Old collectors keep working throughout. Leave the 20 c/kWh threshold as is until a few days of stored bands and `spike_status` values exist.
