# Live tiles over a WebSocket Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collectors push live samples to core-api, which relays them over a WebSocket to open dashboards so the live tiles update as soon as a sample arrives, without touching the database.

**Architecture:** core-api keeps the newest live sample per source in memory (`LiveHub`) and fans each push out to per-socket bounded queues. Two new collector-key-authenticated POST endpoints feed the hub; one new WebSocket endpoint serves browsers. The Envoy collector pushes from its existing live sampler; the WeatherLink collector gets a new fast poll loop. The webapp keeps live values for 15 seconds and overlays them on the polled tile data.

**Tech Stack:** Python, FastAPI/Starlette, `websockets` (new core-api dependency), pytest, React 19, TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-24-live-tiles-websocket-design.md`

## Global Constraints

- Live samples are never written to the database (spec: Goal).
- Collector push auth is the existing `X-Collector-Key` (and `X-Collector-Provider` where the collector already sends it), via `require_source_kind` (spec: Collector to core-api).
- Live metric names: energy `production_w`, `consumption_w`, `grid_import_w`, `grid_export_w`, `battery_charge_w`, `battery_discharge_w`, `battery_soc_pct`; weather: all `WeatherMetric` values except `rain_mm`.
- Out of scope: charts, price tiles, cost and billing-period tiles, the freshness line, the rain-today tile, the once-a-minute stored push (spec: Goals and non-goals).
- WebSocket path `GET /api/v1/sites/{site_id}/live`; first message `{"token": "<access token>"}`; unauthenticated sockets closed after 5 seconds; auth failures close with code 1008.
- Outgoing message shape `{"kind": "energy" | "weather", "ts": "<iso>", "values": {"<metric>": <number>}}`.
- A live value is fresh for 15 seconds after the client received it (client clock). Reconnect back-off 1 s doubling to a 30 s cap; no reconnect after an auth close.
- Weather `LIVE_POLL_SECONDS` default 10, minimum 2. Envoy uses the existing `LIVE_SAMPLE_SECONDS`.
- A pushed sample holds 1 to 16 values, all finite numbers.
- core-api runs as one uvicorn process; the in-memory store relies on it.
- Project rules (`CLAUDE.md`): no `any` in TypeScript, functions under about 30 lines, extract logic repeated more than twice, error handling on async work, prose follows `.claude/rules/avoid-ai-writing.md`. Commits end with the `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` trailer. Run `/simplify` before presenting the finished work.

## Review Focus

Failure modes the spec implies that most need a pinned test, most likely first:

1. A malformed push (unknown metric, more than 16 values, empty values, non-finite number) returns 422, stores nothing, broadcasts nothing. Pinned in Task 1.
2. An out-of-order push (older `ts` than the stored one) is ignored and not broadcast. Pinned in Task 1.
3. A socket that never authenticates, sends non-JSON, or presents a token for a site the user cannot see is closed, with the same result for a missing site and a forbidden one. Pinned in Task 2.
4. A slow or dead client does not block ingest or other sockets: its queue drops the oldest message when full. Pinned in Task 1.
5. A live value older than 15 seconds falls back to the polled value, and a live import-to-export flip shows only the export tile. Pinned in Task 5.

## File Structure

- Create `core-api/core_api/live.py`: `LiveSample`, `LiveHub`, `LiveSubscription`, metric sets. No FastAPI imports.
- Create `core-api/core_api/routers/live.py`: two push routes and the WebSocket route.
- Modify `core-api/core_api/schemas.py`, `core_api/deps.py`, `core_api/main.py`, `core-api/pyproject.toml`.
- Modify `envoy-collector/envoy_collector/{mapping,core_api_client,live_sampler,main}.py`.
- Modify `weatherlink-local-collector/weatherlink_local_collector/{config,mapping,core_api_client,main}.py`.
- Create `webapp/src/dashboard/liveStream.ts` (pure logic) and `webapp/src/dashboard/useLiveStream.ts` (socket hook).
- Modify `webapp/src/api/{client,config}.ts`, `webapp/src/pages/DashboardPage.tsx`, `webapp/src/test-setup.ts`, `webapp/docker-entrypoint.sh`.
- Modify docs: `docs/core-api.md`, `docs/collectors.md`, `docs/webapp.md`, `docs/operations.md`.

---

### Task 1: core-api live store and push endpoints

**Files:**
- Create: `core-api/core_api/live.py`, `core-api/core_api/routers/live.py`, `core-api/tests/test_live_push.py`
- Modify: `core-api/core_api/schemas.py`, `core-api/core_api/main.py`, `core-api/pyproject.toml` (and `uv.lock`)

**Interfaces:**
- Produces, in `core_api/live.py`:
  - `ENERGY_LIVE_METRICS: frozenset[str]`, `WEATHER_LIVE_METRICS: frozenset[str]`, `MAX_LIVE_VALUES = 16`
  - `@dataclass(frozen=True) class LiveSample: source_id: int; kind: SourceKind; ts: datetime.datetime; values: dict[str, float]`
  - `class LiveSubscription: queue: asyncio.Queue[LiveSample]; def close(self) -> None`
  - `class LiveHub: def __init__(self, queue_size: int = 50)`, `def publish(self, site_id: int, sample: LiveSample) -> bool` (False and no broadcast when `sample.ts` is older than the stored sample for that source), `def latest_for_site(self, site_id: int) -> list[LiveSample]`, `def subscribe(self, site_id: int) -> LiveSubscription`
- Produces, in `schemas.py`: `EnergyLiveSampleIn`, `WeatherLiveSampleIn` (`ts: UtcDatetime`, `values: dict[str, float]`; validator rejects keys outside the kind's live metric set, fewer than 1 or more than `MAX_LIVE_VALUES` entries, and non-finite numbers).
- Produces: `app.state.live_hub: LiveHub`, created in `create_app`. Routes `POST /api/v1/sources/{source_id}/live/energy` and `.../live/weather`, both returning 202 `{}`.

- [ ] **Step 1: Add the WebSocket library.** In `core-api/pyproject.toml` add `"websockets>=13,<16"` to the dependencies (uvicorn is installed without its `standard` extra, so it has no WebSocket support today). Run `cd core-api && uv lock` and commit the updated `uv.lock` with this task.

- [ ] **Step 2: Write failing hub tests** in `tests/test_live_push.py` using `asyncio` directly (no server):
  - `test_publish_stores_latest_per_source`: two sources on site 1; `latest_for_site(1)` returns both samples.
  - `test_older_ts_is_ignored`: publish ts=10:00:05 then ts=10:00:01 for one source; second returns False, `latest_for_site` still holds the first, and a subscriber's queue received only the first.
  - `test_subscriber_receives_only_its_site`: subscribers on sites 1 and 2; a publish on site 1 reaches only site 1's queue.
  - `test_full_queue_drops_oldest`: `LiveHub(queue_size=2)`, publish 3 samples without draining; the queue holds the last 2 and `publish` did not raise or block.
  - `test_close_removes_subscription`: after `close()` a publish does not enqueue to it.

- [ ] **Step 3: Write failing endpoint tests** using the `client` and `app_and_db_url` fixtures and `make_energy_source_full` from `tests/test_ingestion.py`:
  - `test_energy_push_returns_202_and_reaches_hub`: valid push with `X-Collector-Key: ENVOY_COLLECTOR_KEY`; 202; `app.state.live_hub.latest_for_site(site_id)` has the values; the readings table is still empty.
  - `test_push_with_wrong_key_is_401`, `test_weather_key_on_energy_source_is_rejected` (same codes as ingestion).
  - Review Focus 1, parametrised: unknown metric `bogus_w`, `rain_mm` on weather, 17 values, empty `values`, value `1e999` sent as raw JSON text. Each returns 422 and `latest_for_site` is empty.
  - Review Focus 2: pushing an older `ts` after a newer one returns 202 and leaves the newer sample as latest.

- [ ] **Step 4: Run** `cd core-api && uv run pytest tests/test_live_push.py -v`. Expected: FAIL (module not found).

- [ ] **Step 5: Implement `core_api/live.py` and the schemas.** `LiveHub` keeps `dict[site_id, dict[source_id, LiveSample]]` plus `dict[site_id, set[LiveSubscription]]`. `publish` uses `queue.put_nowait`; on `QueueFull` it removes the oldest with `get_nowait` and retries. `asyncio.Queue` is not thread-safe, so every caller must run on the event loop.

- [ ] **Step 6: Implement `routers/live.py` push routes as `async def`** (so they run on the event loop, not a worker thread). Each takes the body schema and `source: Source = Depends(require_source_kind(...))`, builds a `LiveSample`, calls `request.app.state.live_hub.publish(source.site_id, sample)`, and returns 202. No DB session beyond what `require_source_kind` already uses, and no commit. Register `live_router` in `create_app` with the `/api/v1` prefix; create `app.state.live_hub = LiveHub()` beside the other `app.state` assignments.

- [ ] **Step 7: Run** `uv run pytest tests/test_live_push.py -v`. Expected: PASS. Then `uv run pytest -q` for the whole suite. Expected: PASS.

- [ ] **Step 8: Commit** `feat(core-api): in-memory live store and live push endpoints`.

---

### Task 2: core-api WebSocket endpoint

**Files:**
- Modify: `core-api/core_api/deps.py`, `core-api/core_api/routers/live.py`
- Test: `core-api/tests/test_live_socket.py`

**Interfaces:**
- Consumes: `LiveHub`, `LiveSample`, `app.state.live_hub` from Task 1; `decode_access_token(token: str) -> int` from `core_api.security`.
- Produces: `def site_visible_to(db: Session, site_id: int, user_id: int) -> Site | None` in `deps.py` (the owner-or-`SiteAccess` check). `require_site_access` and `require_site_owner` keep their behavior and call it where they test visibility. `GET /api/v1/sites/{site_id}/live` WebSocket route.

- [ ] **Step 1: Write failing tests** with Starlette's `TestClient.websocket_connect`, using `create_access_token` for tokens (see `tests/test_ingestion.py` for fixture helpers):
  - `test_authorised_socket_gets_current_then_new_values`: push a sample first; connect, send `{"token": ...}`; first received message equals `{"kind": "energy", "ts": ..., "values": {...}}` for that sample; a later push arrives as the next message.
  - `test_other_sites_push_is_not_received`: a push on a second site produces no message on this socket (assert with a follow-up push on this site arriving first).
  - Review Focus 3, each asserting the socket closes with code 1008 and no data frame arrives first: missing token key, non-JSON text, token that fails to decode, expired token, valid token for a user with no access to the site, valid token for a site id that does not exist. The last two must close identically.
  - `test_unauthenticated_socket_closed_after_timeout`: monkeypatch the auth timeout constant `LIVE_AUTH_TIMEOUT_SECONDS` in `routers/live.py` to 0.2, connect and send nothing; the socket closes with 1008.
  - `test_site_access_user_can_connect`: a user granted `SiteAccess` (not the owner) connects.

- [ ] **Step 2: Run** `uv run pytest tests/test_live_socket.py -v`. Expected: FAIL.

- [ ] **Step 3: Extract `site_visible_to`** in `deps.py` and make the two existing dependencies use it. Run `uv run pytest tests/test_sites.py tests/test_deps.py -q`. Expected: PASS (behavior unchanged).

- [ ] **Step 4: Implement the WebSocket route** `live_socket(websocket: WebSocket, site_id: int)` in `routers/live.py`, with `LIVE_AUTH_TIMEOUT_SECONDS = 5`. Accept, wait for the first JSON message with `asyncio.wait_for`, validate shape, decode the token, open a session from `websocket.app.state.session_factory`, call `site_visible_to`, close the session. Any failure closes with 1008 and sends nothing first. Then subscribe, send `latest_for_site` samples, and loop forwarding queue items as the outgoing message shape until the client disconnects; `finally` calls `subscription.close()`. Split into small helpers (`_authenticate`, `_pump`) to stay under 30 lines each.

- [ ] **Step 5: Run** `uv run pytest tests/test_live_socket.py -v`, then `uv run pytest -q`. Expected: PASS.

- [ ] **Step 6: Commit** `feat(core-api): live WebSocket for a site's dashboard`.

---

### Task 3: Envoy collector live push

**Files:**
- Modify: `envoy-collector/envoy_collector/mapping.py`, `core_api_client.py`, `live_sampler.py`, `main.py`
- Test: `envoy-collector/tests/test_mapping.py`, `test_core_api_client.py`, `test_live_sampler.py`, `test_main.py`

**Interfaces:**
- Produces: `def map_live_sample_values(sample: LiveSample, invert_consumption_ct: bool = False) -> dict[str, float]` in `mapping.py`.
- Produces: `def push_live_sample(base_url: str, source_id: int, collector_key: str, ts: datetime.datetime, values: dict[str, float]) -> None` in `core_api_client.py`. POSTs `{"ts": iso, "values": ...}` to `/api/v1/sources/{source_id}/live/energy` with the same headers as `push_energy_readings`, timeout 2 seconds, expects 202, raises `CoreApiError` otherwise.
- Changes: `LiveSampler.__init__` gains keyword `on_sample: Callable[[LiveSample, dict], None] | None = None`. It is called from `_keep` with the new sample and `self.config`, after the sample is stored.

- [ ] **Step 1: Write failing mapping tests.** `test_live_values_match_stored_mapping`: for a sample with production 3000 W, grid +500 W (import), battery -200 W (charging), soc 80, the result equals `{"production_w": 3000, "consumption_w": 3300, "grid_import_w": 500, "battery_charge_w": 200, "battery_soc_pct": 80}`. Also: export case emits only `grid_export_w`; discharge emits only `battery_discharge_w`; `has_battery=False` omits all battery keys; `invert_consumption_ct=True` flips grid sign and the production term exactly as `map_livedata_readings` does. A test calling both functions on the same single sample asserts the shared metrics agree.

- [ ] **Step 2: Write failing client tests** with `httpx` mocked as the existing tests do: correct URL, headers, JSON body and 2 second timeout; a 500 raises `CoreApiError`.

- [ ] **Step 3: Write failing sampler tests.** `test_on_sample_called_for_new_samples_only` (repeated `last_update` does not call it); `test_on_sample_receives_current_config`; `test_on_sample_exception_does_not_stop_sampling` (callback raises; the sample is still stored in `drain()` and the next `sample_once` works).

- [ ] **Step 4: Write failing wiring test** in `test_main.py`: with `_sync_samplers` run on a source that has a pinned certificate and `push_live_sample` patched, a sample delivered to the sampler's `on_sample` calls `push_live_sample` with the source id, the collector key, and values from `map_live_sample_values` using the config's `invert_consumption_ct`. `test_live_push_failure_is_swallowed`: patched push raises `CoreApiError`; the callback returns normally and `poll_once`'s stored push is unaffected.

- [ ] **Step 5: Run** `cd envoy-collector && uv run pytest -q`. Expected: the new tests FAIL.

- [ ] **Step 6: Implement.** `map_live_sample_values` reuses the sign and consumption rules from `map_livedata_readings` (factor shared arithmetic out rather than copying it); battery figures come from the sample itself regardless of `LIVE_BATTERY_SOURCE` (an ensemble read is once a minute). In `LiveSampler._keep`, call `on_sample` inside `try/except Exception`, logging through a `FailureLog("Live push failed for <label>")` (`failure()` on error, `success()` otherwise). In `main._sync_samplers`, build `on_sample` as a small function that maps the sample and calls `push_live_sample(settings.core_api_url, source_id, settings.collector_key, now_utc, values)`.

- [ ] **Step 7: Run** `uv run pytest -q`. Expected: PASS.

- [ ] **Step 8: Commit** `feat(envoy-collector): push each live sample to core-api`.

---

### Task 4: WeatherLink collector live loop

**Files:**
- Modify: `weatherlink-local-collector/weatherlink_local_collector/config.py`, `mapping.py`, `core_api_client.py`, `main.py`
- Test: `tests/test_config.py`, `test_mapping.py`, `test_core_api_client.py`, `test_main.py`

**Interfaces:**
- Produces: `Settings.live_poll_seconds: int` (env `LIVE_POLL_SECONDS`, default 10; below 2 raises `RuntimeError("LIVE_POLL_SECONDS must be at least 2")`).
- Produces: `def map_live_values(conditions: list[dict]) -> dict[str, float]` in `mapping.py`: runs the existing ISS and barometer mappers with a throwaway `RainState()` and returns `{metric: value}` without `rain_mm`. Missing ISS or barometer blocks are skipped, like `poll_once` does.
- Produces: `def push_live_sample(base_url: str, source_id: int, collector_key: str, ts: datetime.datetime, values: dict[str, float]) -> None` (POST to `.../live/weather`, timeout 2, expects 202, raises `CoreApiError`).
- Produces: `def live_poll_once(settings: Settings, source_id: int, config: dict) -> None` and `def run_live_loop(settings: Settings, get_sources: Callable[[], dict[int, _Source]], stop: threading.Event) -> None` in `main.py`.

- [ ] **Step 1: Write failing tests.**
  - Config: default is 10; `LIVE_POLL_SECONDS=1` raises; `=5` is accepted.
  - Mapping: a fixture conditions payload yields temp, humidity, wind speed, wind direction, solar radiation and pressure keys and no `rain_mm`; calling it twice never carries rain state between calls.
  - Client: URL, header, body, timeout 2, 202, error handling.
  - `test_live_poll_once_pushes_values`: patched `fetch_current_conditions` and `push_live_sample`; one push with the mapped values.
  - `test_live_poll_failure_is_logged_once_per_streak`: fetch raises three times then succeeds then raises again; exactly two failure log records and one recovery record, and no exception escapes.
  - `test_run_live_loop_polls_every_source_and_stops`: with `settings.live_poll_seconds` patched small and two sources, both are polled at least once, and setting `stop` ends the loop promptly.

- [ ] **Step 2: Run** `cd weatherlink-local-collector && uv run pytest -q`. Expected: new tests FAIL.

- [ ] **Step 3: Implement.** `live_poll_once` catches every exception, never touches the stored-push pending queue or rain state. `main()` keeps a shared `sources` reference the loop reads through `get_sources` (a small holder object updated where `sources` is reassigned), and starts `run_live_loop` on a daemon thread before the existing poll loop.

- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit** `feat(weatherlink-collector): push live conditions every 10 seconds`.

---

### Task 5: Webapp live stream logic and hook

**Files:**
- Create: `webapp/src/dashboard/liveStream.ts`, `webapp/src/dashboard/liveStream.test.ts`, `webapp/src/dashboard/useLiveStream.ts`, `webapp/src/dashboard/useLiveStream.test.tsx`
- Modify: `webapp/src/api/client.ts`, `webapp/src/api/config.ts`, `webapp/src/test-setup.ts`

**Interfaces:**
- Produces in `liveStream.ts`:
  - `LIVE_FRESH_MS = 15_000`
  - `interface LiveMessage { kind: "energy" | "weather"; ts: string; values: Record<string, number> }`
  - `interface LiveValue { value: number; ts: string; receivedAt: number }` and `type LiveValues = ReadonlyMap<string, LiveValue>`
  - `parseLiveMessage(data: unknown): LiveMessage | null` (null for anything not matching the shape, including non-numeric values)
  - `applyLiveMessage(current: LiveValues, message: LiveMessage, receivedAt: number): LiveValues` (new map; a value whose `ts` is older than the held one for that metric is ignored)
  - `freshLiveReadings(live: LiveValues, now: number): Reading[]` (only values with `now - receivedAt < LIVE_FRESH_MS`; `ts` is the sample's `ts`)
  - `overlayLive(polled: Reading[], live: Reading[]): Reading[]` (a live reading replaces the polled one for the same metric; other polled readings pass through; order of first appearance kept)
  - `backoffMs(attempt: number): number` (`min(1000 * 2 ** attempt, 30_000)`)
- Produces `useLiveStream(siteId: number): { energy: Reading[]; weather: Reading[] }` returning only fresh readings.
- Produces `getAccessToken(): string | null` exported from `api/client.ts`, and `toWebSocketUrl(path: string): string` in `api/config.ts` (swaps `http` for `ws`, `https` for `wss` on the API base URL).

- [ ] **Step 1: Write failing tests for the pure functions** in `liveStream.test.ts`: `parseLiveMessage` accepts a valid message and rejects missing `kind`, unknown `kind`, a string value, and non-objects; `applyLiveMessage` ignores an older `ts` for the same metric and accepts a newer one without mutating the input; `freshLiveReadings` drops a value at exactly 15 000 ms old and keeps one at 14 999; `overlayLive` replaces and passes through as described; `backoffMs(0)=1000`, `(3)=8000`, `(10)=30000`. Review Focus 5: after a live `grid_import_w`, then a newer live `grid_export_w`, `freshLiveReadings` returns both with the export newer, so the grid's existing collapse picks export only (assert through `collapseMutuallyExclusiveFlows` if exported, otherwise assert the `ts` ordering).

- [ ] **Step 2: Write failing hook tests** in `useLiveStream.test.tsx` with a `FakeWebSocket` class installed on `globalThis` that records instances, sent frames and lets the test call `open()`, `message(obj)`, `close(code)`:
  - connects to `toWebSocketUrl("/api/v1/sites/7/live")` and sends `{"token": <stored token>}` on open;
  - does not connect when there is no token;
  - a message appears in `energy` or `weather` by `kind`;
  - with fake timers, a value disappears from the result 15 s after it arrived without any new message (the hook re-renders at expiry);
  - after `close(1006)` it reconnects after 1 s, then 2 s on a second failure, and the attempt counter resets once a message arrives;
  - after `close(1008)` it does not reconnect;
  - unmount closes the socket and cancels a pending reconnect.

- [ ] **Step 3: Run** `cd webapp && npx vitest run src/dashboard/liveStream.test.ts src/dashboard/useLiveStream.test.tsx`. Expected: FAIL.

- [ ] **Step 4: Implement** `liveStream.ts`, `getAccessToken`, `toWebSocketUrl`, and `useLiveStream` (a `useEffect` keyed on `siteId` that owns the socket, the attempt counter and timers; state is `{energy: LiveValues; weather: LiveValues}` plus a timer that fires at the earliest `receivedAt + LIVE_FRESH_MS` to force a re-render). Keep each function under 30 lines by splitting socket setup from message handling.

- [ ] **Step 5: Quiet the existing tests.** In `test-setup.ts` install a `WebSocket` stub that never opens, so existing page tests do not open real sockets or schedule reconnects. Tests that need a working fake (Step 2) replace it locally.

- [ ] **Step 6: Run** `npx tsc -b && npx vitest run`. Expected: PASS.

- [ ] **Step 7: Commit** `feat(webapp): live stream hook and helpers`.

---

### Task 6: Wire the live tiles and the CSP

**Files:**
- Modify: `webapp/src/pages/DashboardPage.tsx`, `webapp/docker-entrypoint.sh`
- Test: `webapp/src/pages/DashboardPage.test.tsx`

**Interfaces:**
- Consumes: `useLiveStream`, `overlayLive` from Task 5.

- [ ] **Step 1: Write failing tests** in `DashboardPage.test.tsx` using the Task 5 `FakeWebSocket` helper (move it to `src/pages/dashboardTestUtils.ts` or a shared test util if both test files need it):
  - `shows a pushed live value on the tile before the next poll`: polled production 1000 W; a live message with `production_w: 2500` makes the tile read "2500 W".
  - `shows the instantaneous live value, not the 5-minute average`: with polled readings that would average to something else, the live value wins while fresh.
  - `falls back to the polled value after 15 seconds of silence` (fake timers).
  - `weather tiles update from a live weather message` (temperature changes).
  - `rain today stays on stored data` (a live weather message never changes the rain tile).
  - `tiles are unchanged when the socket never opens` (the existing live-tile assertions still pass with the stub).

- [ ] **Step 2: Run** `npx vitest run src/pages/DashboardPage.test.tsx`. Expected: new tests FAIL.

- [ ] **Step 3: Implement in `LiveTiles`:** `const live = useLiveStream(siteId)`; energy tiles get `overlayLive(averageLiveReadings(energy.data, Date.now()), live.energy)`; weather tiles get `overlayLive(weather.data, live.weather)`. Averaging applies only to the polled fallback (call: while live, tiles show the instantaneous value). Leave `MetricTileGrid` and `WeatherTiles` untouched.

- [ ] **Step 4: Update the CSP.** In `docker-entrypoint.sh` compute `WS_API_BASE_URL` from `API_BASE_URL` (`http` to `ws`, `https` to `wss`) and add it to `connect-src` after `${API_BASE_URL}`. Verify by running the script's header-writing part in a throwaway shell with `API_BASE_URL=https://api.example.test` and checking the file contains `wss://api.example.test`.

- [ ] **Step 5: Run** `npx tsc -b && npx vitest run`. Expected: PASS.

- [ ] **Step 6: Commit** `feat(webapp): live tiles update from the WebSocket`.

---

### Task 7: Docs

**Files:**
- Modify: `docs/core-api.md`, `docs/collectors.md`, `docs/webapp.md`, `docs/operations.md`

- [ ] **Step 1: Document each change in its doc.** `core-api.md`: the two push endpoints, the WebSocket (auth message, message shape, close codes, 5 second timeout), the in-memory store and its single-process assumption. `collectors.md`: Envoy pushes each live sample; WeatherLink's new `LIVE_POLL_SECONDS` loop (default 10, minimum 2); push failures are logged and dropped. `webapp.md`: the 15 second freshness rule, back-off, and that tiles fall back to polling. `operations.md`: a reverse proxy in front of core-api must allow WebSocket upgrades, and the CSP now names the `ws(s)` address.

- [ ] **Step 2: Check** that every setting and path named in the docs exists in the code (`grep` each). Expected: no stale names.

- [ ] **Step 3: Commit** `docs: describe the live tile WebSocket`.

- [ ] **Step 4: Run `/simplify`** over the whole branch diff and apply its fixes, then run all three suites once more: `cd core-api && uv run pytest -q`; `cd envoy-collector && uv run pytest -q`; `cd weatherlink-local-collector && uv run pytest -q`; `cd webapp && npx tsc -b && npx vitest run`. Expected: all PASS.
