# Core API + Data Model — Design Spec

Date: 2026-09-25
Status: Approved for planning (sub-project 1 of the energy monitoring system)

## Purpose

Track production/consumption from an Enphase Envoy solar/battery system, buy/sell energy
pricing (starting with Amber, pluggable to other providers), and a Davis WeatherLink Live
weather station — per site, with support for multiple sites, multiple users sharing
access to a site, and net vs. gross usage accounting. Must support demand-tariff billing
(peak-demand charges) and daily service-availability charges alongside volumetric costs.
Designed to start on SQLite and migrate to Postgres/MariaDB later without a data-model
rewrite. Frontend and any future device-control component are explicitly out of scope for
this sub-project — they will be separate deployables that only ever talk to this API, never
the database directly.

## Project decomposition (for context)

This system is being built as four sub-projects, in this order:

1. **Core API + data model** (this spec) — owns the database; the only thing with DB access.
2. **Envoy collector** — polls the Envoy local API every minute, pushes energy readings in.
3. **Pricing collector** — polls pricing providers (Amber first), pushes price intervals in.
4. **Weather collector** — polls WeatherLink Live, pushes weather readings in.
5. **Frontend webapp** — dashboards, net/gross toggle, site/source management.

(Envoy write/control operations are a future addition on top of the Envoy collector; nothing
in this design assumes read-only access, but no control functionality is designed here.)

## Boundaries

- **Core API**: FastAPI + SQLAlchemy (2.0) + Alembic migrations. Exposes ingestion endpoints
  (for collectors) and query endpoints (for the frontend). SQLite to start; Alembic and the
  ORM abstraction keep the Postgres/MariaDB move a config change, not a rewrite.
- **Collectors** (sub-projects 2–4, not built yet) are separate deployables. This earns its
  boundary from a concrete constraint: each collector must run on whatever LAN segment can
  reach its device's local API (Envoy, WeatherLink Live), which may be a different physical
  host than the Core API/DB. Not a reflex microservice split — a real deployment-locality
  requirement, reinforced by the decision to support flexible deployment (single host, Pi,
  or Docker-based NAS/home-server infra, in any combination).
- **Frontend webapp** (sub-project 5, not built yet): talks to Core API only, no DB access.
- **Inside Core API**: no repository/service/controller trio per entity — most endpoints are
  a route handler plus one SQLAlchemy query. The one dedicated module is **cost calculation**
  (net/gross + demand charges), because that's where actual business logic lives.

**What this design optimizes for**: correctness of the data model for a long-lived
(multi-year), low-traffic personal system — a handful of sites, per-minute ingestion, a
handful of users. Not scale. No caching layer, queue, or sharding is introduced.

## Data model

### Identity & access

- **User**: `id`, `email`, `password_hash`, `created_at`
- **Site**: `id`, `owner_user_id` (FK User, not null), `name`, `timezone`. Sole source of
  ownership truth — a site has exactly one owner, this FK.
- **SiteAccess**: `site_id`, `user_id`, `role` (`viewer`) — PK `(site_id, user_id)`. Models
  *additional* grants only, not ownership (no `owner` value — that would duplicate
  `Site.owner_user_id` and need its own "exactly one owner row" constraint for no benefit).
  A request is authorized for a site when the requesting user is `owner_user_id` (full
  read/write) or holds a `SiteAccess` row (currently `viewer` only: read-all, write-nothing;
  the enum leaves room for a future write-capable role without a schema rewrite).

### Sources (unified pluggable abstraction)

One shared entity for energy, pricing, and weather connections, since all three have an
identical lifecycle (something polls/receives external data and pushes typed readings in
under this source's identity):

- **Source**: `id`, `site_id` (FK), `kind` (`energy` | `pricing` | `weather`), `provider`
  (`envoy`, `amber`, `weatherlink_live`, ...), `config` (provider-specific connection details
  — legitimate connection config, not a business-state blob), `enabled`,
  `collector_api_key` (generated service credential for ingestion auth),
  `effective_from` (not null), `effective_to` (nullable = currently active)

Invariants enforced at the DB layer:
- Partial unique index on `site_id` where `kind = 'weather'` — at most one weather source
  per site.
- Pricing-kind sources for the same site must not have overlapping
  `[effective_from, effective_to)` ranges — a site has exactly one active pricing source at
  a time, but the source used for billing history is whichever was effective during that
  period. Enforced at the application layer for now (SQLite has no native range-exclusion
  constraint); Postgres can enforce this natively post-migration.
- Energy sources remain a true one-to-many, concurrent (e.g. multiple metering points).
  Replacement is handled via `enabled`, not `effective_to` — historical readings stay tied
  to their original `source_id` regardless.

Collectors authenticate to Core API ingestion endpoints using the source's
`collector_api_key` (service credential), separate from user login (JWT).

### Time-series: energy & weather readings (narrow/long format)

```
EnergyReading(source_id, ts, metric, value)         -- PK (source_id, ts, metric)
EnergyReadingRollup5m(source_id, ts, metric, value)  -- same shape, ts bucketed to 5 min
WeatherReading(source_id, ts, metric, value)         -- PK (source_id, ts, metric)
WeatherReadingRollup5m(...)                          -- same shape
```

`metric` is a constrained enum, not a freeform string. Energy:
`production_w`, `consumption_w`, `grid_import_w`, `grid_export_w`, `battery_charge_w`,
`battery_discharge_w`, `battery_soc_pct` (not every site will populate every metric —
depends on the site's physical metering: net-metered sites won't have a separate
`consumption_w`, gross-metered sites will). Weather: `temp_c`, `humidity_pct`,
`wind_speed_kph`, `wind_direction_deg`, `rain_mm`, `solar_radiation_wm2`,
`barometric_pressure_hpa`, etc.

This narrow shape is why the pluggable-source model works without schema churn: a new
hardware/provider that reports a subset of *known* metrics needs no migration; a genuinely
new metric type gets added to the enum. The composite PK also makes ingestion **idempotent
by construction** — a collector retry after a network blip is just an upsert on the same
key.

### Time-series: pricing

```
PriceInterval(pricing_source_id, channel, interval_start, interval_end, price, is_demand_window)
PriceReading(pricing_source_id, ts, channel, value)          -- PK (pricing_source_id, ts, channel)
PriceReadingRollup5m(...)                                     -- same shape, 5-min buckets
```

`channel` enum: `buy`, `sell`, `controlled_load`, `demand`.

`PriceInterval` is the authoritative record of what the provider published — explicit
`interval_start`/`interval_end` bounds (not an assumed fixed width), so a site's price
interval length (5 min, 30 min, whatever the provider/smart-meter supports) needs no schema
support; it's just whatever bounds the collector reports. The pricing `Source.config` may
record an informational `interval_minutes` for the collector to request the right
resolution from the provider where the provider supports configuring it; where the
provider/meter can't be queried for its actual interval, the user is trusted to set this
correctly. `is_demand_window` lets the provider flag intervals that count toward demand
(e.g. Amber's demand channel), feeding the same `DemandInterval` calculation as
manually-configured windows.

`PriceReading` is a deliberate derived expansion: when a `PriceInterval` is ingested, its
price is fanned out into one row per covered minute, synchronously, in the same
request/transaction. This is intentionally redundant with `PriceInterval` — it exists so
the frontend can chart price at the same per-minute resolution as energy/weather without
every consumer re-implementing interval-expansion logic. Low volume, cheap to compute at
write time.

### Demand tariff & site billing

- **SiteTariff**: `id`, `site_id`, `effective_from`, `effective_to` (nullable),
  `service_availability_c_per_day` — the daily supply/availability charge; effective-dated
  since rates change (e.g. each financial year) and historical billing must use the rate
  that was actually in force.
- **BillingCycle**: `id`, `site_id`, `effective_from`, `effective_to` (nullable),
  `cycle_months` (positive int — `1` monthly, `3` quarterly, etc.), `anchor_day` (1–31, the
  day-of-month a cycle starts; clamped to the last day of shorter months, e.g. anchor `31`
  in February lands on the 28th/29th). Kept separate from `SiteTariff` — billing-cycle shape
  and tariff rates can change independently (e.g. a retailer switch mid-plan). Effective-
  dated for the same reason as `SiteTariff`/`DemandWindow`: a later cycle-length change must
  not retroactively rewrite already-finalized billing periods.
  If the cycle's `effective_from` falls before its first anchor day, there is no earlier
  anchor to count from. The first billing period is then a partial period that starts at
  `effective_from` (the day the cycle was submitted) and ends on that first anchor day.
  In this partial period the dashboard's Including Discounts tile shows the full discounted
  fee of a discount window that covers it (for example $25.00), not a share prorated to the
  partial period's length.
- **DemandWindow**: `id`, `site_tariff_id` (FK), `label`, `months` (set), `days_of_week`
  (set), `start_time`, `end_time`, `rate_c_per_kwh` (unit/conversion verified at
  implementation time — see Deferred below), `measurement_interval_minutes` (default 30,
  independent of the site's price-signal interval). Supports multiple windows per tariff
  (e.g. Ausgrid's separate summer/non-summer demand windows). A pricing source's
  `is_demand_window`-flagged `PriceInterval` rows are an alternate way of driving the same
  downstream calculation, not a separate code path.
- **DemandInterval**: `site_id`, `interval_start`, `interval_end` (30-min buckets, or per
  `measurement_interval_minutes`), `avg_import_w`, `is_demand_window`, `rate_applied` — PK
  `(site_id, interval_start)`. Derived from `EnergyReading`'s `grid_import_w` metric by a
  periodic job (same idempotent-by-construction pattern as the rollup jobs: runs every 30
  min, aggregates the closed bucket, flags it against whichever `DemandWindow`/`SiteTariff`
  was effective *at that timestamp* — not whatever is currently configured, so later config
  edits don't retroactively rewrite history).
- **DemandCharge**: `id`, `site_id`, `billing_period_start`, `billing_period_end`,
  `peak_interval_id` (FK DemandInterval), `peak_avg_import_w`, `rate_applied`,
  `demand_window_hours`, `charge_amount`, `is_estimated` (set when `EnergyReading` coverage
  for the period has gaps, so an understated peak from missing data is visible rather than
  silently wrong). One finalized row per site per billing period, written once the period
  closes — functions like an invoice line item, stable once written, for reconciling
  against actual bills. Upsert on `(site_id, billing_period_start)` so a re-run of the
  finalization job is safe. `billing_period_start`/`end` are computed in the site's
  `timezone` from whichever `BillingCycle` was effective at that period (walking forward
  `cycle_months` from `anchor_day`) — not whatever `BillingCycle` is currently configured,
  matching the same non-retroactive rule used for `DemandWindow`/`SiteTariff`.

### Per-minute cost (explicitly not persisted)

Per-minute cost (cents) is **computed on read** by the cost-calculation module, joining
`EnergyReading` against `PriceInterval`/`PriceReading`, parameterized by `site_id`, time
range, and net/gross method. Not persisted, because:
- Net vs. gross is selectable at query time — there is no single correct value per minute.
- Price corrections/backfills from providers would leave a materialized table stale.
- It's cheap at this scale (a month of 1-min readings joined against a handful of price
  intervals is trivial for SQLite/Postgres at this site/user count).

If long-range dashboard queries ever prove slow, the fix is a materialized rollup table
later — not built now (no evidence it's needed yet).

### Retention & rollup

- `EnergyReading`, `WeatherReading`, `PriceReading` keep raw 1-min data for 1 year. A
  scheduled job (in-process, e.g. APScheduler inside Core API — no separate worker service
  justified for one lightweight periodic task) aggregates rows older than 1 year into the
  matching `*Rollup5m` table and deletes the raw rows. Aggregation function is per-metric,
  not hardcoded per job: a `METRIC_AGGREGATION` mapping (`metric` enum → `average` |
  `last_value`) is a single lookup table the rollup job (and any future consumer) reads —
  `average` for power/price metrics, `last_value` for state metrics like `battery_soc_pct`.
  Adding a metric later means one entry in this table, not a new branch in job code.
- `PriceInterval` is not rolled up (already coarse, low volume).
- Query endpoints route through one shared "which table(s) does this range span" helper
  rather than duplicating raw/rollup selection logic per endpoint.

## API contract

Prefix: `/api/v1` (cheap now, avoids pain at the first breaking change).

**Ingestion** (collector-facing, `collector_api_key` header auth):
- `POST /sources/{id}/readings/energy`, `/readings/weather` — batch of `{ts, metric, value}`;
  upsert on the composite PK.
- `POST /sources/{id}/prices` — batch of `PriceInterval` rows; triggers the synchronous
  per-minute `PriceReading` fan-out in the same transaction.
- Batches are all-or-nothing: a malformed batch returns 422 with per-row detail; the
  collector fixes and retries the whole batch. No partial-success bookkeeping in Core API.

**Query** (frontend-facing, user JWT bearer auth; every route checks `SiteAccess` for the
requested `site_id`):
- `GET /sites/{id}/readings/{energy|weather}`, `/sites/{id}/prices` — time range + metric/
  channel filters.
- `GET /sites/{id}/cost?from=&to=&method=net|gross` — computed on read.
- `GET /sites/{id}/demand-charges` — persisted `DemandCharge` rows.
- CRUD for `Site`, `Source`, `SiteTariff`/`DemandWindow`, `BillingCycle`, `SiteAccess`.

Errors are structured (`code`, `message`, `detail`), not bare 500s or generic strings, so a
collector or the frontend can tell what happened and whether retrying will help.

## Failure handling

- **Collector → Core API**: idempotent upsert on the composite natural key makes
  retry-after-timeout safe without additional dedup logic.
- **Core API never calls external providers directly** — that's the collectors' job. Core
  API's failure surface is DB + incoming HTTP only.
- **Rollup / DemandInterval jobs**: idempotent by construction (same upsert pattern), so a
  crash mid-run is safe to re-run.
- **DemandCharge finalization**: upsert on `(site_id, billing_period_start)`; `is_estimated`
  flag surfaces data-gap risk rather than silently understating a bill.

## Explicitly deferred / out of scope for this sub-project

- Envoy, Amber, and WeatherLink Live collectors (sub-projects 2–4).
- Frontend webapp (sub-project 5).
- Envoy control/write operations — no design impact taken now; the `Source`/collector
  abstraction doesn't assume read-only, but nothing control-related is designed here.
- Exact demand-charge unit conversion (peak kW → `charge_amount`, e.g. the
  "peak × rate × 2 × demand window hours" arithmetic) — verified against an actual bill at
  implementation time rather than guessed into the schema now. The data model captures the
  inputs (`peak_avg_import_w`, `rate_applied`, `demand_window_hours`); the formula is an
  implementation detail of the cost-calculation module.
- Materialized cost caching — add only if query performance on long ranges proves to be a
  real problem.
- Login/JWT issuance, refresh, and password-reset flow — undecided. `User.password_hash`
  already models credential storage, so no schema impact is expected; the exact endpoints
  and token lifecycle are a decision for implementation time.
