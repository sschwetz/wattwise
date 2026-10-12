# Pricing collector (Amber Electric) — design spec

## Purpose

Sub-project 3 of 5 in `enphase-data`. A standalone `pricing-collector` project, sibling to
the already-built `envoy-collector` and `weather-collector`, that polls Amber Electric's
public API for one or more sites' electricity prices and pushes them into Core API as
`PriceInterval` batches — plus two categories of Core API extension this sub-project needs:
a per-user Amber credential, and new endpoints supporting a historical-backfill job.

## Boundaries

In scope: live 5-minute-aligned price polling for any number of Amber-connected sites from
one collector process, and a 12-hourly historical backfill job that fills in price history
Core API is missing (bounded by when the site's other data started existing).

Out of scope: Amber's `usage` (consumption) endpoint — that's the Envoy collector's job via
the meter. Pricing-source non-overlap enforcement (a known gap from Core API's own final
review) stays deferred. `controlledLoad` channel handling is implemented but untested against
real data (none of the account's current sites have one).

## Confirmed Amber API shape

Base URL `https://api.amber.com.au/v1`, auth `Authorization: Bearer {token}` (a personal
Amber API token, generated from the account's developer page — entirely separate from Core
API's own `collector_api_key`). Verified against a real account during design (site IDs, NMIs,
and the API token itself are placeholders below — never committed for real, same privacy
practice as the Envoy/WeatherLink device identifiers).

`GET /sites` — lists every site ever connected to the account, each with `id` (Amber's site
ID, an opaque ULID-like string — placeholder: `<amber_site_id>`), `nmi`, `channels` (which of
`general`/`controlledLoad`/`feedIn` this site's meter has), `network`, `status`
(`pending`/`active`/`closed`), `activeFrom`, `closedOn`, `intervalLength` (5 or 30 minutes —
the site's real billing granularity).

`GET /sites/{amber_site_id}/prices/current?previous=N&next=M` — the live-poll endpoint. With
no query params, returns only the single `CurrentInterval` per channel. `previous=1` also
returns the most recently *settled* interval per channel as `ActualInterval`. `next=M`
also returns `M` `ForecastInterval` entries per channel (Amber recomputes forecasts fully on
every request — they are not stable between polls).

`GET /sites/{amber_site_id}/prices?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD&resolution=5|30`
— the historical/backfill endpoint. **`startDate`/`endDate` span cannot exceed 7 days per
call.** Dates are whole NEM-calendar days, not precise timestamps — there is no way to ask
for less than a full day. Returns only `ActualInterval` entries (the historical record).

### Interval object fields (all endpoints, all interval types share this shape)

| Field | Meaning |
|---|---|
| `type` | `ActualInterval` (settled), `CurrentInterval` (locked in, not yet historical), or `ForecastInterval` (a prediction, changes every poll) |
| `channelType` | `general`, `controlledLoad`, or `feedIn` |
| `startTime`, `endTime` | UTC ISO8601. **Boundaries have a 1-second stagger** — e.g. `23:55:01Z` → `00:00:00Z` for a 5-minute interval. Not a bug; pass through as-is. |
| `duration` | Interval length in minutes (5 or 30) |
| `perKwh` | Price in **cents/kWh** — confirmed against Core API's `cost.py`, which treats `PriceInterval.price` as cents (`total_cost_c += import_kwh * buy_price`). No conversion needed. |
| `spotPerKwh`, `renewables`, `spikeStatus`, `descriptor`, `estimate`, `range`, `advancedPrice` | Not mapped — no corresponding Core API field, not needed for the stated purpose |
| `tariffInformation.demandWindow` | **Not used.** This reflects Amber's own view of the billing period, not this system's network (poles-and-wires) demand tariff, which is configured locally per site via Core API's own `SiteTariff`/`DemandWindow`. The collector never reads `tariffInformation` at all. |

## Field mapping → Core API `PriceIntervalIn`

| Amber | Core API | Notes |
|---|---|---|
| `channelType: general` | `channel: buy` | "Buy" is this system's import price — Amber's `general` channel is the site's normal grid-import tariff |
| `channelType: feedIn` | `channel: sell` | Export/feed-in price |
| `channelType: controlledLoad` | `channel: controlled_load` | Untested against real data — no current site has this channel |
| `startTime` | `interval_start` | Direct |
| `endTime` | `interval_end` | Direct |
| `perKwh` | `price` | Direct, cents/kWh |
| *(not read)* | `is_demand_window` | Always omitted (schema default `false`) — see above |
| `type` | *(not stored)* | Used only to decide inclusion (see "Which interval types" below), not written anywhere. `ActualInterval`/`CurrentInterval`/`ForecastInterval` all map to the same shape and share the same natural key `(pricing_source_id, channel, interval_start)`, so a forecast is silently overwritten by the real actual once it settles. |

`PriceChannel.DEMAND` is never populated by this collector — demand-window determination is
entirely Core API's own `SiteTariff`/`DemandWindow` → `DemandInterval` calculation,
independent of anything a pricing provider reports.

**Which interval types get pushed:** `ActualInterval`, `CurrentInterval`, and
`ForecastInterval` are all pushed. Forecasts add write churn (12 of them fetched and
overwritten every 5-minute poll) but the user wants them for a future "predicted price" view.

## Core API extensions

This sub-project touches Core API in five ways, alongside the standalone collector project.

### 1. `User.amber_api_key`

New nullable `str` column on `User`, via an Alembic migration. The Amber API token
authenticates against Amber's API for *all* of that user's sites — one account, one token —
so it belongs on the user, not duplicated into every pricing `Source.config`. The Amber
site ID, by contrast, is specific to one pricing connection and lives in that `Source`'s own
`config` JSON (`{"amber_site_id": "..."}`), the same mechanism the weather collector already
uses for `host`. No schema change needed for that part.

### 2. `GET /sources/{id}/config` — server-side merge for `amber` provider sources

Unchanged shape (`{"config": {...}}`) and unchanged behavior for every other provider. When
`source.provider == "amber"`, the config dict returned is
`{**source.config, "amber_api_key": source.site.owner_user.amber_api_key}` — the collector's
single config fetch gets both the site ID (already in `Source.config`) and the owner's key
(merged in) in one call, using the collector's existing per-source `collector_api_key` auth.
No new endpoint for this part.

### 3. `GET /sources/{id}/site-data-floor`

Returns `{"floor_ts": "<iso datetime>"}` — the earliest timestamp across every
`EnergyReading`, `WeatherReading`, and `PriceReading` row belonging to *any* source on that
pricing source's site (`source.site_id`, not just this one source). Defines how far back the
backfill job is allowed to reach: never before the site had *any* data from *any* collector,
since pricing data with nothing to join against is useless for the stated cost-calculation
purpose. Collector-key-authenticated, kind-agnostic (same auth dependency as `/config`).

If the site genuinely has zero readings anywhere yet (a brand new site), this returns `null`
and the backfill job for that site does nothing that cycle.

### 4. `GET /sources/{id}/prices/gaps?since=<floor_ts>`

Returns the calendar days (NEM-day granularity, matching Amber's own `date` field) between
`since` and today where this pricing source is missing `PriceInterval` coverage for a channel
it otherwise has data for elsewhere, **excluding** any day already recorded in
`PriceGapMarker` (see below) as confirmed-unavailable. Response shape:
`{"gaps": [{"channel": "buy", "start_date": "2026-01-01", "end_date": "2026-01-07"}, ...]}`
— coalesced into contiguous runs so the collector can turn each run directly into ≤7-day
Amber query chunks.

### 5. `PriceGapMarker` table + `POST /sources/{id}/prices/gap-markers`

New table: `PriceGapMarker(pricing_source_id, channel, gap_date)`, PK
`(pricing_source_id, channel, gap_date)`. Records "this day was checked against Amber's
historical endpoint and confirmed to have zero data — stop reporting it as a gap." Written via
a new endpoint, `POST /sources/{id}/prices/gap-markers` with body
`{"channel": "buy", "gap_date": "2025-03-01"}`, called by the backfill job whenever an
Amber historical response has no intervals at all for a channel on a requested day (e.g. the
site wasn't yet connected to Amber on that date, even though its Envoy/weather data reaches
further back). This keeps `PriceInterval` itself free of any sentinel/fake rows — no read or
query path touching `PriceInterval`/`PriceReading` needs to filter anything out, since real
price data and "we checked, nothing's there" markers never share a table.

## Collector design

### Configuration

- `CORE_API_URL` — shared across all configured sites.
- `PRICING_SOURCES` — a JSON array, one entry per site:
  `[{"source_id": 1, "collector_key": "..."}, {"source_id": 2, "collector_key": "..."}]`.
  No Amber-specific env vars — `amber_site_id` and `amber_api_key` both come from each
  source's `GET /sources/{id}/config` call.

### Process model: one master, forked children for the live poll

A single `pricing-collector` process (`main.py`) runs two independent schedules.

**Live poll (5-minute, wall-clock-aligned):** the master computes the next UTC `:00/:05/
:10/.../:55` boundary, sleeps until 15 seconds past it (a buffer for Amber's own settlement
lag), then forks one child process per entry in `PRICING_SOURCES` via Python's
`multiprocessing.Process` — which uses a real OS `fork()` on Linux, so every child inherits
the exact same tick and fires its Amber request at the same moment. The master then
`.join()`s all children with a 60-second timeout (generous versus the ~20s worst case of two
10-second-timeout network calls); any child still alive past that is `.terminate()`d and
reaped — a stale poll result past that point is useless anyway, so there's no reason to let
it keep running, and this avoids zombie processes. Once all children are done (or reaped),
the master goes back to sleep for the next aligned tick.

Each child is one-shot: given its `source_id`/`collector_key`, fetch config
(`GET /sources/{id}/config`, which returns the merged `amber_site_id` + `amber_api_key`), call
Amber (`GET /sites/{amber_site_id}/prices/current?previous=1&next=12`), map and isolate each
interval per-channel (an unrecognized `channelType` or a malformed interval is logged and
skipped individually, never fatal to the rest of the batch), push everything successfully
mapped in one `POST /sources/{id}/prices` call, then exit. No internal retry loop — a
failure just logs and the child exits; the next aligned tick spawns a fresh child regardless,
so retry happens naturally via the scheduler rather than needing an indefinite-retry-at-
startup pattern like the Envoy/Weather collectors use. This also means the child is fully
stateless between ticks — there's no per-site equivalent of the Weather collector's
`RainState`, since each poll's `previous=1` independently re-supplies the just-settled
interval.

Process-level isolation between sites is a side effect of forking: a crash in one site's
child cannot touch another site's child or the master's scheduling.

**Historical backfill (12-hourly, inline, sequential):** on its own 12-hour timer, the master
loops through each configured site *without forking* (no timing-alignment need, and Amber's
historical endpoint can return large date ranges, so running sites one at a time avoids many
concurrent large fetches): fetch the site's `site-data-floor`, fetch its `prices/gaps`, and
for each gap range, split it into ≤7-day chunks and call
`GET /sites/{amber_site_id}/prices?startDate=...&endDate=...&resolution=5` for each chunk.
Chunks with data get pushed via the same `POST /sources/{id}/prices` batch endpoint (the
upsert is idempotent, so pushing a whole day even when only part of it was actually missing
is harmless). A day with zero intervals returned for a channel gets reported via
`POST /sources/{id}/prices/gap-markers` so it's never re-fetched. Once a site's gaps are fully
closed, later 12-hourly runs for that site cost almost nothing — the gaps endpoint just
returns empty, a steady-state no-op rather than a growing cost. No throttling for an
unusually large first backfill (e.g. years of gap) — it simply takes as many 12-hour cycles
as it takes; acceptable at personal scale.

**Master-level guard:** the fork-and-join step (live poll) and the per-site backfill loop are
each wrapped in a last-resort `try`/`except` so a malformed `PRICING_SOURCES` value or an
unexpected error in one site's handling is logged rather than killing the scheduler.

## Explicitly deferred

- Pricing-source non-overlap enforcement (Core API's own known gap, application-layer only)
  — stays deferred; revisit alongside a future SQLite→Postgres migration where it can be
  enforced natively.
- `controlledLoad` channel mapping — implemented but unverified against real data.
- Amber's `usage` (consumption) endpoint — the Envoy collector already covers consumption via
  the meter.
- Rate-limit backoff — Amber's documentation states no rate limit, and this collector's
  request volume (one call per site per 5 minutes, plus one backfill pass per site per 12
  hours) is low regardless.
- `PriceChannel.DEMAND` — never populated by this collector.
- Throttling/pagination across runs for an unusually large first backfill — not built; a
  large gap just takes however many 12-hour cycles it takes to close.
- **Partial-day gap healing.** `GET /sources/{id}/prices/gaps` treats a calendar day as
  "covered" if it has *any* `PriceInterval` row at all, not a count matching the site's real
  interval cadence — so a multi-hour outage (a reboot, a network drop, a Core API restart)
  leaves that day permanently un-backfilled once at least one interval exists for it,
  even though most of the day is actually missing. Properly fixing this requires Core API to
  know each site's expected intervals-per-day (its real `intervalLength`, which isn't tracked
  anywhere in the current schema) to distinguish "fully covered" from "partially covered" —
  a real schema/logic change, not a bug fix, deliberately not attempted in this sub-project's
  one-shot final-review fix pass. Compounding this: `covered_days`/gap date bounds are computed
  in UTC calendar days, while Amber's historical endpoint's `startDate`/`endDate` are NEM
  (AEST, UTC+10) calendar days — a UTC-day request can miss the last ~10 hours of the
  corresponding NEM day. In a multi-day gap this only truncates the run's final day (usually
  `today`, which the live poll fills anyway), so it's a secondary effect of the same
  day-granularity limitation rather than an independent defect. Net effect: this sub-project's
  backfill reliably heals **entirely missing days**, not partial-day outages. Revisit together
  with adding per-site interval-length tracking to Core API's `Source.config`, if partial-day
  healing turns out to matter in practice.
