# Weather Collector — Design Spec

Date: 2026-09-29
Status: Approved for planning (sub-project 4 of the energy monitoring system)

## Purpose

Poll a Davis WeatherLink Live's local API every minute for outdoor temperature, humidity,
wind, rain, and barometric pressure, and push it into Core API as `WeatherReading` rows.
Runs as its own deployable, separate from Core API, matching the project's [decomposition](2026-09-25-core-api-design.md)
and its "collectors are separate deployables" boundary decision — same shape as the
[Envoy collector](2026-09-29-envoy-collector-design.md) (sub-project 2), and several of its
design lessons carry over directly (see 'Lessons carried from the Envoy collector' below).

## Boundaries

- **Weather collector**: a standalone Python project (`enphase-data/weather-collector/`), no
  shared code or dependency on `core-api`'s package — it only ever talks to Core API over
  HTTP, using a pre-provisioned `collector_api_key`.
- **No Core API changes are needed for this sub-project.** WeatherLink Live's local API
  requires no authentication (confirmed — no `Authorization` header in any request), so
  there is no token-lifecycle problem to solve the way there was for the Envoy. The
  collector fetches its connection config (`{"host": "<ip>"}`) via the `GET
  /sources/{id}/config` endpoint already built for the Envoy collector — that endpoint is
  generic, not Envoy-specific, so it's reused as-is.
- **Provisioning a Source for this collector remains a manual, one-time step** via Core
  API's existing `POST /sites/{id}/sources` (`kind: "weather"`, `provider:
  "weatherlink_live"`), done by the site owner before the collector is first run. The
  DB-level partial unique index already enforces at most one weather source per site.

**What this design optimizes for**: same as the Envoy collector — a personal, low-traffic
system. No retry queues, no async I/O, one device polled once a minute on a plain loop.

## WeatherLink Live local API — confirmed field mapping

Verified against real responses from the user's own WeatherLink Live station, cross-checked
against Davis's published docs (https://weatherlink.github.io/weatherlink-live-local-api/).
`GET http://{host}/v1/current_conditions` requires no authentication at all.

Response shape: `{"data": {"did": "...", "ts": <unix>, "conditions": [...]}, "error": null}`.
`conditions[]` entries are discriminated by `data_structure_type`:

- **`1` = outdoor ISS transmitter** (the station's outdoor sensor suite):
  - `temp` (**°F**) → `temp_c`: `(temp - 32) * 5/9`.
  - `hum` (**%RH**) → `humidity_pct`: direct, no conversion.
  - `wind_speed_avg_last_1_min` (**mph**) → `wind_speed_kph`: `× 1.609344` (a mile is defined
    as exactly 1609.344m). Uses the 1-minute rolling average, not the instantaneous
    `wind_speed_last` — matches the ~1-minute poll cadence and smooths gust noise into a
    value representative of "wind during this minute," consistent with how other per-minute
    metrics in this system behave.
  - `wind_dir_scalar_avg_last_1_min` (**°degree**) → `wind_direction_deg`: direct, paired
    with the averaged wind speed above.
  - `solar_rad` (**W/m²**) → `solar_radiation_wm2`: direct, no conversion — but `null` when
    no solar sensor is installed (the user's station has none); skip emitting this reading
    entirely when `null`, don't coerce to 0.
  - `uv_index`: no corresponding Core API metric exists; not mapped.
- **`3` = the console's own built-in barometer** (always present on every WeatherLink Live
  — it's integrated hardware, not a separate purchasable sensor):
  - `bar_sea_level` (**inches** of Hg) → `barometric_pressure_hpa`: `× 33.8639`. Uses the
    sea-level-adjusted reading (`bar_sea_level`), not the raw `bar_absolute` — sea-level
    pressure is the standard meteorological convention for comparing readings across
    locations/elevations, matching how weather services report pressure generally.
- **`4` = indoor temp/humidity** (the console's own indoor sensor): not mapped to anything —
  Core API's weather metrics represent outdoor/ambient site conditions, not building
  interior climate.
- **A Davis AirLink** (a separate physical device, air quality: PM1/PM2.5/PM10,
  `data_structure_type: 6`, queried the same way at its own IP) is **explicitly out of
  scope** for this sub-project — no corresponding metric exists in Core API's `WeatherMetric`
  enum today. See 'Explicitly deferred' below.

### Rainfall — the one field that would have been silently wrong without checking the docs

`rain_rate_last`, `rainfall_last_15_min`, `rainfall_daily`, `rainfall_monthly`, and
`rainfall_year` are **all reported as raw tip-counts**, not a physical depth — despite
`rainfall_*` sounding like it should already be a measurement. The `rain_size` field gives
the physical depth per tip, per Davis's documented lookup table:

| `rain_size` | depth per tip |
|---|---|
| 0 | Reserved (unsupported) |
| 1 | 0.01 in |
| 2 | 0.2 mm |
| 3 | 0.1 mm |
| 4 | 0.001 in (as literally documented — unusually small, but that's what Davis's own docs say) |

`rainfall_daily` ("total rain count since local midnight") is the field used, converted to
mm via the table above. Because it's a cumulative-since-midnight counter, not a per-minute
amount, the collector tracks the previous poll's converted mm value **in memory** and reports
only the delta (new rain since the last poll) as `rain_mm` — this is what makes the metric
correctly summable over time by anything consuming it later, rather than a rolling window
that would double-count. Two edge cases this requires handling:
- **Midnight reset**: `rainfall_daily` drops back to 0 at local midnight. A raw
  `current - previous` would go negative; detect a decrease and treat the new count as the
  full delta (i.e. `current`, not `current - previous`) instead.
- **First poll after startup**: there is no previous value to diff against yet. Skip emitting
  `rain_mm` on the very first poll — start reporting deltas from the second poll onward, once
  a real baseline exists. This mirrors the same "don't fabricate data without a valid basis"
  principle the Envoy collector already follows (e.g. skipping a metric when the underlying
  sensor reports `null`).

## Collector design

**Config (environment variables)**: `CORE_API_URL`, `CORE_API_SOURCE_ID`,
`CORE_API_COLLECTOR_KEY`, `POLL_INTERVAL_SECONDS` (default 60) — identical shape to the
Envoy collector's config. No WeatherLink host/token env vars; the host comes from Core API
at runtime via the same `GET /sources/{id}/config` endpoint the Envoy collector uses (config
here is just `{"host": "<ip>"}` — no token field at all, since there's nothing to
authenticate).

**Startup**: fetch `{"host": ...}` from Core API and cache it in memory. No serial-number
discovery/reporting step is needed here (that existed for the Envoy solely to build an
Enlighten token-renewal URL; WeatherLink Live has no token to renew).

**Poll loop** (every `POLL_INTERVAL_SECONDS`):
1. `GET http://{host}/v1/current_conditions` (no auth header).
2. Map to a batch of `{ts, metric, value}` readings per the field mapping above (`ts` = now,
   UTC), consulting and updating the in-memory rain-tracking state.
3. `POST {CORE_API_URL}/api/v1/sources/{CORE_API_SOURCE_ID}/readings/weather` with the batch
   (the existing, already-built weather ingestion endpoint).

### Lessons carried from the Envoy collector's final review

The Envoy collector's whole-branch review found two Critical bugs after the fact: an
uncaught HTTP transport error could kill the process, and mapping running outside the poll
loop's guarded region meant a malformed sensor response could also kill it. Both are
designed in correctly from the start here rather than relearned:

- **The Core API HTTP client wraps every `httpx.HTTPError` (connection refused, timeout,
  DNS, etc.) into one `CoreApiError` type from the first line of code** — callers only ever
  need to catch one exception type, never `httpx`'s own.
- **Mapping runs inside the poll loop's guarded `try`/`except` region**, not after it — a
  malformed-but-200 `current_conditions` response (e.g. missing the ISS entry) is logged and
  skips the cycle instead of crashing on a `StopIteration`/`KeyError`.
- **Each `data_structure_type` entry's absence or malformed shape is isolated from the
  others** — same principle as the Envoy collector's battery-fetch isolation. If the
  barometer entry is ever missing or malformed, the ISS-derived readings (temp, humidity,
  wind, rain) still get pushed, and vice versa. In practice the barometer entry should always
  be present (it's the console's own integrated sensor), but the isolation costs nothing and
  avoids a repeat of the same class of bug.
- **The startup config fetch retries indefinitely** (backing off by the poll interval)
  rather than failing fast, so a Core API outage at collector-restart time doesn't require a
  manual restart once it recovers — same as the Envoy collector's `_fetch_initial_config`.
- **A last-resort exception guard wraps the poll loop's call site** in `main()`, so no
  unanticipated exception from anywhere in the poll path can exit the process.

**Failure handling** (mirrors the Envoy collector, minus anything auth-related — there's no
401 case here at all):
- WeatherLink unreachable, times out, or returns a malformed/unexpected response: log and
  skip this cycle. No retry within the cycle.
- Core API rejects the push (e.g. the `Source` was disabled or its `effective_to` has
  passed): log clearly and keep polling.
- Core API is unreachable: log and skip this cycle's push.

**Testing**: unit tests using real fixture JSON from the user's actual station (with the
real IP address and device IDs replaced by placeholders) as fixtures, `respx`-mocked HTTP,
verifying the field-mapping/unit-conversion logic and the rain delta-tracking/rollover
handling without needing real hardware.

**Deployment**: same as the Envoy collector — runnable directly
(`python -m weather_collector.main`) for a systemd/cron setup, plus a `Dockerfile` for
Docker deployment, built correctly from the start this time (two-stage `uv sync`, `CMD`
invoking the venv's Python directly rather than `uv run`) rather than repeating the Envoy
collector's initial Dockerfile mistake.

## Explicitly deferred / out of scope for this sub-project

- **Davis AirLink (air quality) integration** — a separate product with no corresponding
  Core API metric today. Adding it would mean extending `WeatherMetric` with new PM-related
  values, which is a real (if small) Core API schema change, not something to fold into this
  sub-project's "poll the weather station" scope. The AirLink's own `temp` reading could
  plausibly serve as a backup/failover outdoor temperature source if the primary WeatherLink
  Live ever becomes unreachable — a reasonable future enhancement, not built here.
- **Per-phase or raw sensor diagnostics** (`rx_state`, `trans_battery_flag`,
  `rain_storm`/`rain_storm_last`, `bar_trend`, etc.) — none have a corresponding Core API
  metric and none are needed for the stated purpose (tracking outdoor conditions
  alongside energy production).
- **Indoor temperature/humidity** — deliberately not mapped; out of scope for a system
  tracking site/outdoor weather conditions.
- **Multiple WeatherLink Live units per site** — the schema doesn't support this anyway (at
  most one weather source per site, enforced at the DB level), so it's a non-issue rather
  than a deferred feature.
