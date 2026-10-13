# Local pricing collector

Date: 2026-10-13

## Problem

WattWise gets per-kWh prices only from Amber. A user whose supplier runs a
fixed or time-of-use tariff has no price source, so their cost figures show
missing prices. The tariff items on the Billing page (supply charge, monthly
fee, billing cycle, demand windows, export adjustments, fee discounts) are
per site and already work for them. The per-kWh buy, sell and controlled-load
prices are the gap.

## Goal

A user can enter their tariff's rate windows in the webapp. A collector turns
them into price intervals and pushes them to core-api, the same way the Amber
collector does. Cost math, charts and the Billing page need no changes beyond
the tariff heading.

Each supplier gets its own collector, so anyone can add one for another vendor,
and a failure in one collector does not affect another.

## Decisions

- One collector per vendor: `amber-pricing-collector` (renamed from
  `pricing-collector`) and a new `local-pricing-collector`. They share no code.
- All pricing collectors share `PRICING_COLLECTOR_KEY`. Core-api separates them
  by provider: a filter on discovery and a provider header check.
- Rate windows live in a new table, not in `Source.config`.
- The tariff name is `Source.name` on the pricing source. No `SiteTariff`
  change.
- The local collector covers per-kWh prices only. Supply charge, monthly fee,
  billing cycle and demand charges stay on the existing site tariff.

## Rename

`pricing-collector/` becomes `amber-pricing-collector/`, package
`amber_pricing_collector`. Update the directory, `docker-compose.yml` service
and build path, the README services table and run commands, and the pricing
section of the webapp docs where it names the service. The env var stays
`PRICING_COLLECTOR_KEY`. Specs and plans under `docs/superpowers/` keep the old
name, since they record what was true when written.

## Core-api

### Provider filter and check

- `GET /collector/sources` takes an optional `provider` query parameter and
  returns only sources with that provider.
- Collectors send `X-Collector-Provider`. `get_source_by_collector_key` returns
  401 `invalid_collector_key` when the header is present and differs from
  `source.provider`. The header is optional so the other collector kinds are
  unaffected. The Amber and local collectors always send it.
- `amber-pricing-collector` requests `provider=amber`. `local-pricing-collector`
  requests `provider=local`.

### Table `local_rate_windows`

| Column | Notes |
|---|---|
| `id` | Primary key. |
| `source_id` | FK to `sources`, indexed. The source must have `provider = "local"`. |
| `channel` | `PriceChannel` (`buy`, `sell`, `controlled_load`) with a check constraint like `price_intervals`. |
| `label` | Free text, for example "Peak". |
| `effective_from` | Timestamp with time zone. |
| `effective_to` | Nullable. Null means the window is current. |
| `months` | JSON list of 1-12. |
| `days_of_week` | JSON list, same encoding as `DemandWindow`. |
| `start_time`, `end_time` | Site-local time. `end_time` earlier than `start_time` means the window wraps past midnight. |
| `rate_c_per_kwh` | `Numeric(12,4)`, entered positive for every channel. |

Channel to direction: `buy` is import, `sell` is export (feed-in),
`controlled_load` is import on the controlled-load circuit. Each channel has its
own windows and rates.

### Validation

- Two windows on the same channel must not overlap in date range, months, days
  of week and time of day. A save that overlaps returns 422 and names the
  clashing window. Windows on different channels never conflict.
- Gaps are allowed. A minute with no window produces no price and is counted by
  the existing `minutes_missing_price`.
- Only sources with `provider = "local"` accept windows.
- A local source requires `name` (1-100 characters). For other providers `name`
  stays optional.

### Endpoints

`POST`, `GET`, `PATCH` and `DELETE` under
`/sites/{site_id}/sources/{source_id}/rate-windows`, in the style of the tariff
routes in `routers/billing.py`. Only the site owner can use them.

### Config delivery

`resolve_source_config` adds the source's windows to its config when
`provider == "local"`, next to where it adds `amber_api_key` for Amber. The
collector reads them through the existing `GET /sources/{id}/config`. No new
collector-facing endpoint.

## local-pricing-collector

- Lists its sources with `provider=local`, then every minute fetches each
  source's config and builds `PriceIntervalIn` rows from the windows, evaluated
  in the site's time zone so a 07:00 peak start holds across daylight-saving
  changes.
- Pushes from now to `LOCAL_HORIZON_HOURS` ahead (default 48) to
  `POST /sources/{id}/prices`. The push is an upsert, so repeating it is safe
  and a window edit shows up within a minute.
- Flips the sign for `sell` before pushing, to match Amber's stored convention
  (negative when the customer is paid, and cost math adds it). `buy` and
  `controlled_load` are pushed as entered.
- Has no vendor API client, no backfill and no setting beyond `CORE_API_URL`
  and `COLLECTOR_KEY`. It copies the core-api client pattern from the Amber
  collector into its own package.
- Runs each source in its own process, so one failing source does not stop the
  others.
- Editing a rate does not change history already pushed, because the collector
  only generates intervals from now forward. Close the old window with
  `effective_to` and add a new one.

## Webapp

- Adding a pricing source offers a provider choice: Amber or Local tariff. A
  local source has no API key field and requires a tariff name.
- A local source gets a "Rate windows" panel with separate Import, Export and
  Controlled load tables. Each row has label, dates, months, days, times and
  rate. A banner lists any uncovered time. The form saves despite gaps.
- The Billing page heading comes from the site's active pricing source: Amber
  gives "Amber Tariffs", local gives "{name} Tariffs", no active source gives
  "Tariffs". With two active pricing sources the one with the earliest
  `effective_from` is used.
- A "Fixed tariffs" help page explains the local source. Amber docs and account
  settings are unchanged.

## Testing

- Core-api: overlap validation (same and different channel, midnight wrap, month
  and day intersections, date-range edges); rate window CRUD; the `provider`
  filter; the provider header mismatch returning 401; config delivery for local
  sources only; a cost test where a local `sell` price pushed negative is added
  by the cost math as the Amber path is.
- Collector: flat tariff, peak and off-peak, overnight window, a daylight-saving
  change day in `Australia/Sydney`, a gap with no window (no interval pushed),
  the sell sign flip, and one failing source not stopping another.
- Webapp: the heading rule, the three channel tables, the uncovered-time banner,
  and the provider choice on the source form.
- The existing `pricing-collector` tests must pass unchanged after the rename.

## Rollout order

Each step is a commit that leaves the repo working.

1. Rename to `amber-pricing-collector`. No behavior change.
2. Provider filter and header check in core-api, sent by the Amber collector.
3. Migration, model, CRUD, validation and config delivery for
   `local_rate_windows`.
4. `local-pricing-collector` with its Dockerfile and compose service.
5. Webapp: provider choice, rate windows panel, tariff name, Billing heading.
6. Docs: the "Fixed tariffs" help page and README updates.

## Not included

- Backfilling local prices for dates before a source existed. Cost for those
  days shows as missing price.
- Tiered or block pricing that depends on usage in the period.
- Importing a tariff from a file or a retailer's published plan.
- Per-vendor collector keys.
- A guide for writing a new vendor collector. The Amber and local collectors are
  the examples.
