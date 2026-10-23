# Envoy Live Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Envoy collector stores each minute's readings as an average of samples from `/ivp/livedata/status`, falls back to today's single-sample polls, and stores per-phase grid voltage, lifetime energy counters and grid relay state.

**Architecture:** A background thread per source samples `/ivp/livedata/status` every few seconds into memory. `poll_once` (still every 60 s) drains and averages the samples, or falls back to `_fetch_and_map` when the feed is stale. Once a minute it also reads `/ivp/meters/readings` for voltage and counters. Core API gets the new metric names first.

**Tech Stack:** Python 3.11, pytest, stdlib `http.client`/`threading` in `envoy-collector`; FastAPI, SQLAlchemy, Alembic (SQLite) in `core-api`.

**Spec:** `docs/superpowers/specs/2026-10-24-envoy-live-data-design.md`

## Global Constraints

- Metric names (all at most 32 characters): `production_wh_lifetime`, `grid_import_wh_lifetime`, `grid_export_wh_lifetime`, `grid_voltage_ph_a_v`, `grid_voltage_ph_b_v`, `grid_voltage_ph_c_v`, `grid_relay_state`.
- Aggregation: `LAST_VALUE` for the three counters and `grid_relay_state`; `AVERAGE` for the voltages.
- Live feed units are milliwatts; stored values are watts. Battery sign: positive = discharge, negative = charge.
- Env vars: `LIVE_SAMPLE_SECONDS` (default 3), `LIVE_STALE_SECONDS` (default 30).
- Keepalive: `POST /ivp/livedata/stream` with body `{"enable": 1}`, at most once per `LIVE_STALE_SECONDS`.
- Counters are stored raw: no sign inversion, no battery correction.
- The webapp is not changed. Its `ENERGY_METRICS` list drives one request per metric on the dashboard, so do not add the new names to it.
- Nothing is written to disk by the collector. Deploy Core API before the collector.
- Commit messages: `feat(envoy-collector): ...` or `feat(core-api): ...`, ending with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Prose follows `.claude/rules/avoid-ai-writing.md`. No `any`-style loose types: annotate every new function.

## Review Focus

1. A batteryless gateway (zero `enc_agg_energy` and `acb_agg_energy`): no battery rows, no crash, consumption unaffected. (Task 3)
2. A frozen feed (calls succeed, `last_update` never changes): no samples are kept, the minute falls back, the keepalive fires at most once per stale window. (Task 4)
3. A single-phase site (only `agg_p_ph_a_mw` keys, one channel in `/ivp/meters/readings`): only `grid_voltage_ph_a_v`, battery sum still correct. (Task 3)
4. The first minute after a restart (empty sample list): one reading from the fallback path, no empty push. (Task 5)
5. `/ivp/meters/readings` failing or listing an unknown or disabled meter: power readings still push; only the meter rows are missing. (Task 5)

---

### Task 1: Core API accepts the new metrics

**Files:**
- Modify: `core-api/core_api/models/readings.py` (`EnergyMetric`, `ENERGY_METRIC_AGGREGATION`)
- Create: `core-api/alembic/versions/c7e2a9d4b1f8_add_envoy_live_metrics.py` (`down_revision = "b1c5d8e2f4a6"`)
- Test: `core-api/tests/test_schema_constraints.py`, `core-api/tests/test_bootstrap.py`

**Interfaces:**
- Produces: `EnergyMetric` members `PRODUCTION_WH_LIFETIME`, `GRID_IMPORT_WH_LIFETIME`, `GRID_EXPORT_WH_LIFETIME`, `GRID_VOLTAGE_PH_A_V`, `GRID_VOLTAGE_PH_B_V`, `GRID_VOLTAGE_PH_C_V`, `GRID_RELAY_STATE`, with the string values in Global Constraints.

- [ ] **Step 1: Write failing tests.** In `test_schema_constraints.py`: `test_every_energy_metric_has_an_aggregation_method` asserts `set(ENERGY_METRIC_AGGREGATION) == set(EnergyMetric)`; `test_new_live_metrics_aggregate_as_specified` asserts the voltages are `AVERAGE` and the other four `LAST_VALUE`; `test_energy_reading_accepts_new_metrics` inserts one `EnergyReading` per new member through the `db` and `site` fixtures (as `test_energy_reading_upsert_on_composite_pk_is_idempotent` does) and commits. In `test_bootstrap.py`: `test_migration_allows_new_energy_metrics`, which calls `run_migrations(f"sqlite:///{tmp_path/'m.db'}")`, then with `sqlite3` inserts a row into `energy_readings` with metric `grid_voltage_ph_a_v` and into `energy_readings_rollup_5m` with `production_wh_lifetime` (foreign keys are not enforced on a plain connection), and asserts inserting metric `bogus` raises `sqlite3.IntegrityError` in both tables.
- [ ] **Step 2: Run** `cd core-api && uv run pytest tests/test_schema_constraints.py tests/test_bootstrap.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Add the enum members and aggregation entries** in `readings.py`.
- [ ] **Step 4: Write the migration** with `op.batch_alter_table` on `energy_readings` and `energy_readings_rollup_5m`: `drop_constraint` then `create_check_constraint` for `ck_energy_readings_metric` and `ck_energy_readings_rollup_5m_metric`, using the full literal value list (write the strings out; do not import the enum). Follow `b4d7e1a9c3f2_add_spike_status_to_price_intervals.py` for style. The downgrade first deletes rows whose metric is one of the seven new names from both tables, then restores the original seven-value constraints.
- [ ] **Step 5: Run** the command from Step 2, then `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 6: Commit** `feat(core-api): accept envoy live-data metrics`.

---

### Task 2: Collector config and gateway client calls

**Files:**
- Modify: `envoy-collector/envoy_collector/config.py`, `envoy-collector/envoy_collector/envoy_client.py`, `envoy-collector/.env.example`
- Test: `envoy-collector/tests/test_config.py`, `envoy-collector/tests/test_envoy_client.py`

**Interfaces:**
- Produces: `Settings.live_sample_seconds: int`, `Settings.live_stale_seconds: int` (read from the env vars in Global Constraints); `fetch_livedata(host: str, token: str, cert_fingerprint: str) -> dict` (`GET /ivp/livedata/status`); `fetch_meters(host, token, cert_fingerprint) -> list[dict]` (`GET /ivp/meters`); `fetch_meter_readings(host, token, cert_fingerprint) -> list[dict]` (`GET /ivp/meters/readings`); `enable_livedata_stream(host: str, token: str, cert_fingerprint: str) -> None` (`POST /ivp/livedata/stream`, JSON `{"enable": 1}`). All raise `EnvoyAuthError` on 401 and `ValueError` on other 4xx/5xx, as `_envoy_get` does.
- `_request` gains keyword arguments `method: str = "GET"` and `body: bytes | None = None`; existing callers are unchanged. `_envoy_get` must accept list responses (`-> dict | list`).

- [ ] **Step 1: Write failing tests.** `test_config.py`: defaults are 3 and 30, env overrides work, and `Settings(...)` constructions in other tests keep working only if the new fields have defaults (give them defaults). `test_envoy_client.py`: extend `_Handler` with `/ivp/livedata/status`, `/ivp/meters`, `/ivp/meters/readings` (all bearer-checked, return small JSON; meters and readings return lists) and `do_POST` for `/ivp/livedata/stream` that records the received body and returns `{"sc_stream": "enabled"}`. One test per function: sends the bearer token, parses the JSON, raises `EnvoyAuthError` for a bad token, and (for the pinned fetchers) raises `EnvoyCertificateMismatch` for a wrong fingerprint. For the POST, assert the recorded body equals `{"enable": 1}`.
- [ ] **Step 2: Run** `cd envoy-collector && uv run pytest tests/test_config.py tests/test_envoy_client.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the config fields and the four client functions. Add both env vars, commented with defaults, to `.env.example`.
- [ ] **Step 4: Run** the Step 2 command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(envoy-collector): add live-data gateway calls and settings`.

---

### Task 3: Mapping from live-feed and meter responses

**Files:**
- Modify: `envoy-collector/envoy_collector/mapping.py`
- Create: `envoy-collector/tests/fixtures/livedata_status.json`, `meters.json`, `meter_readings.json`
- Test: `envoy-collector/tests/test_mapping.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class LiveSample` with `last_update: int`, `production_w: float`, `grid_w: float` (positive = import), `battery_w: float` (positive = discharge), `soc: float`, `relay_state: int`, `has_battery: bool`.
- Produces: `parse_livedata(status_json: dict) -> LiveSample`; `map_livedata_readings(samples: list[LiveSample], ts: datetime.datetime, invert_consumption_ct: bool = False) -> list[dict]`; `map_meter_readings(meters: list[dict], readings: list[dict], ts: datetime.datetime) -> list[dict]`.

Fixture values (from the owner's gateway). `livedata_status.json`: only a `meters` object with `last_update` 1792768566, `soc` 10, `main_relay_state` 1, `enc_agg_energy` 2150, `acb_agg_energy` 0, `pv.agg_p_mw` 5394, `grid.agg_p_mw` 1121227, `storage.agg_p_mw` 37000 plus `agg_p_ph_a_mw` -13000, `agg_p_ph_b_mw` -15000, `agg_p_ph_c_mw` -16000. `meters.json`: eid 704643328 `production`, eid 704643584 `net-consumption`, both `state` `enabled`, `phaseCount` 3. `meter_readings.json`: three entries: eid 704643328 with `actEnergyDlvd` 2449808.591 and channel voltages 238.336, 239.727, 239.039; eid 704643584 with `actEnergyDlvd` 957182.412, `actEnergyRcvd` 1269929.026 and channel voltages 238.232, 239.839, 239.17; eid 1023410688 all zeros (not in `meters.json`). Each entry has a `channels` list whose items carry `voltage`; give the top-level `voltage` of the first two the phase sums (717.102, 717.241).

- [ ] **Step 1: Write failing tests.**
  - `test_parse_livedata_converts_milliwatts_and_sums_phase_storage`: `production_w == 5.394`, `grid_w == 1121.227`, `battery_w == -44.0`, `soc == 10`, `has_battery is True`.
  - `test_battery_sum_ignores_the_aggregate_storage_figure`.
  - `test_parse_livedata_batteryless_site`: zero `enc_agg_energy` and `acb_agg_energy` gives `has_battery is False` and `battery_w == 0.0`.
  - `test_parse_livedata_single_phase_sums_only_present_phase_keys`: storage with only `agg_p_ph_a_mw`.
  - `test_map_livedata_averages_samples`: two samples with `production_w` 100 and 300 give `production_w == 200`; `soc` is the last sample's.
  - `test_map_livedata_splits_grid_import_and_export`; `test_map_livedata_consumption_is_production_plus_grid_plus_battery` (5.394 + 1121.227 - 44 = 1082.621); `test_map_livedata_charging_yields_battery_charge_w`; `test_map_livedata_discharging_yields_battery_discharge_w`.
  - `test_map_livedata_batteryless_writes_no_battery_rows`.
  - `test_map_livedata_invert_consumption_ct` mirrors the existing test for `map_production_readings`: with the flag, grid sign and the `pv + grid` term flip, battery does not.
  - `test_map_livedata_includes_grid_relay_state` (last sample's `relay_state`).
  - `test_map_meter_readings_counters_and_voltages`: rows `production_wh_lifetime` 2449808.591, `grid_import_wh_lifetime` 957182.412, `grid_export_wh_lifetime` 1269929.026, `grid_voltage_ph_a_v` 238.232, `_ph_b_v` 239.839, `_ph_c_v` 239.17, all at `ts`; the zero meter produces nothing.
  - `test_map_meter_readings_skips_disabled_meter` and `test_map_meter_readings_single_phase_gives_only_ph_a`.
- [ ] **Step 2: Run** `cd envoy-collector && uv run pytest tests/test_mapping.py -q`. Expected: FAIL (names not defined).
- [ ] **Step 3: Implement** the dataclass and three functions. `parse_livedata` sums every key of `storage` matching `agg_p_ph_*_mw`. `map_livedata_readings` returns `[]` for an empty list. `map_meter_readings` matches `readings` to `meters` by `eid`, uses `measurementType` to pick production or net-consumption, and takes the voltage metric per entry of `channels` (letters a, b, c by position, limited to the grid meter's `phaseCount`).
- [ ] **Step 4: Run** the Step 2 command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(envoy-collector): map live-feed and meter responses`.

---

### Task 4: The live sampler

**Files:**
- Create: `envoy-collector/envoy_collector/live_sampler.py`
- Test: `envoy-collector/tests/test_live_sampler.py`

**Interfaces:**
- Consumes: `LiveSample`, `parse_livedata` (Task 3).
- Produces: `class LiveSampler` with `__init__(self, config: dict, fetch: Callable[[dict], dict], enable_stream: Callable[[dict], None], interval_seconds: float, stale_seconds: float, clock: Callable[[], float] = time.monotonic)`; attribute `config: dict` (the owner replaces it each cycle so a renewed token or re-pin is used); `start() -> None` (daemon thread), `stop() -> None`, `sample_once() -> None` (one iteration, what the thread loop calls, public so tests drive it without threads), `drain() -> list[LiveSample]` (returns and clears), `is_fresh() -> bool`.

Behavior: a sample is kept only when its `last_update` differs from the previous sample's. `is_fresh()` is true when a new sample arrived within `stale_seconds` by `clock`. When `fetch` succeeds but nothing new has arrived for `stale_seconds`, call `enable_stream(config)` and not again until another `stale_seconds` pass. Any exception from `fetch` or `enable_stream` is logged (`logger.exception` the first time, then `logger.warning` while it keeps failing) and never ends the thread.

- [ ] **Step 1: Write failing tests** with a list-backed fake `fetch` and a mutable fake clock, calling `sample_once()` directly:
  - `test_new_last_update_is_kept` and `test_repeated_last_update_is_dropped`.
  - `test_drain_returns_samples_once_and_clears`.
  - `test_is_fresh_false_before_first_sample` and `test_is_fresh_expires_after_stale_seconds`.
  - `test_frozen_feed_triggers_keepalive_once_per_stale_window`: advance the clock past `stale_seconds` with a frozen `last_update`, call `sample_once()` several times, assert `enable_stream` was called once, then again after another window.
  - `test_fetch_failure_does_not_raise_and_is_not_fresh`.
  - `test_replaced_config_is_passed_to_fetch`.
  - `test_start_and_stop_run_the_thread`: with a very small `interval_seconds` and a fake that records calls, assert at least two calls happen and that `stop()` ends the thread within a second.
- [ ] **Step 2: Run** `cd envoy-collector && uv run pytest tests/test_live_sampler.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `LiveSampler` with a `threading.Lock` around the sample list and a `threading.Event` for stop (wait on the event for the interval, so `stop()` is prompt).
- [ ] **Step 4: Run** the Step 2 command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(envoy-collector): add the live-feed sampler`.

---

### Task 5: Wire the sampler and meter readings into the poll loop

**Files:**
- Modify: `envoy-collector/envoy_collector/main.py`
- Test: `envoy-collector/tests/test_main.py`

**Interfaces:**
- Consumes: Tasks 2 to 4.
- Produces: `poll_once(settings: Settings, source_id: int, config: dict, sampler: LiveSampler | None = None) -> dict` (existing calls without `sampler` behave exactly as now); module `_samplers: dict[int, LiveSampler]`; `_sync_samplers(settings: Settings, sources: dict[int, dict]) -> None` (starts a sampler for each source whose config has `cert_fingerprint`, replaces `config` on existing ones, stops and removes samplers for sources that are gone); `_meter_readings_or_empty(source_id: int, config: dict, ts: datetime.datetime) -> list[dict]`; module `_meters_cache: dict[int, list[dict]]`.

Behavior:
- In `poll_once`, after the pin check: if `sampler` is not None and `sampler.is_fresh()`, readings = `map_livedata_readings(sampler.drain(), ts, invert_consumption_ct)`; otherwise `sampler.drain()` is still called to discard stale samples, and readings come from `_fetch_and_map` with the existing 401, certificate-mismatch and error handling unchanged.
- Append `_meter_readings_or_empty(...)` to the readings in both cases. It reads `/ivp/meters` once per source (cached in `_meters_cache`), calls `fetch_meter_readings`, and returns `map_meter_readings(...)`. Any exception is logged and returns `[]`. `_meters_cache[source_id]` is cleared on `EnvoyAuthError` and in `_refresh_sources`.
- `main()` calls `_sync_samplers` after each `_refresh_sources` and once before the loop. The sampler's `fetch` is `lambda cfg: fetch_livedata(cfg["host"], cfg.get("token", ""), cfg["cert_fingerprint"])` and `enable_stream` is the matching `enable_livedata_stream` call. Pass `_samplers.get(source_id)` to `poll_once`.

- [ ] **Step 1: Write failing tests** (monkeypatch the way existing tests in `test_main.py` do):
  - `test_poll_once_uses_live_samples_when_fresh`: a fake sampler with `is_fresh() -> True` and two samples; assert pushed metrics include `production_w` from the average and that `_fetch_and_map` was not called.
  - `test_poll_once_falls_back_when_sampler_is_stale`: `is_fresh() -> False`; assert `_fetch_and_map` was called and the pushed readings are its readings.
  - `test_poll_once_with_empty_sampler_falls_back` (first minute after a restart), and `test_poll_once_without_sampler_is_unchanged`.
  - `test_meter_readings_are_pushed_with_power_readings`; `test_meter_readings_failure_still_pushes_power_readings`; `test_unknown_meter_is_skipped_but_power_is_pushed`.
  - `test_meters_listing_is_fetched_once_per_source` and `test_meters_cache_cleared_on_auth_failure`.
  - `test_sync_samplers_starts_one_per_pinned_source_and_stops_removed_ones` (use a fake `LiveSampler` class monkeypatched into `main_module`).
  - `test_sync_samplers_skips_source_without_pin`.
- [ ] **Step 2: Run** `cd envoy-collector && uv run pytest tests/test_main.py -q`. Expected: new tests FAIL, old tests still PASS.
- [ ] **Step 3: Implement** the changes in `main.py`.
- [ ] **Step 4: Run** `uv run pytest -q` in `envoy-collector`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(envoy-collector): average live-feed samples each minute`.

---

### Task 6: Live verification and docs (owner-run against the real gateway)

**Files:**
- Modify: `docs/superpowers/specs/2026-10-24-envoy-live-data-design.md` (record results), `README.md` or the collector's docs section if it lists collector env vars.

- [ ] **Step 1: Battery sign.** While the battery is charging, then while it is discharging, run `curl -sk -H "Authorization: Bearer $TOKEN" https://<host>/ivp/livedata/status` and `.../ivp/ensemble/power`. Compare the per-phase `storage.agg_p_ph_*_mw` sum, the `storage.agg_p_mw` aggregate and `real_power_mw` summed over `devices:`, plus the app's reading. Record all numbers in the spec. If the per-phase sum disagrees with `/ivp/ensemble/power`, change `parse_livedata` so battery power and `has_battery` rows come from the ensemble call, as the spec's fallback says, and re-run Task 3 tests.
- [ ] **Step 2: Keepalive.** Close the mobile app for a few hours, then check `connection.sc_stream` and whether `last_update` still advances. Record the outcome. If it never freezes, delete the keepalive (`enable_stream`, its test and `enable_livedata_stream`).
- [ ] **Step 3: Collector run.** Start the collector against the owner's stack and confirm the log shows one push per minute, `grid_voltage_ph_*_v` and the three counters appear in `energy_readings`, and that stopping the gateway call (for example an invalid token) falls back without an exception.
- [ ] **Step 4: Docs.** Add `LIVE_SAMPLE_SECONDS` and `LIVE_STALE_SECONDS` wherever the collector's other env vars are documented, and note the new metrics.
- [ ] **Step 5: Commit** `docs: record envoy live-data verification`.
