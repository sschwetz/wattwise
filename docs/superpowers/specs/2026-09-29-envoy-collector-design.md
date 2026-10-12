# Envoy Collector — Design Spec

Date: 2026-09-29
Status: Approved for planning (sub-project 2 of the energy monitoring system)

## Purpose

Poll an Enphase Envoy-S Metered's local API every minute for production, consumption, and
battery data, and push it into Core API as `EnergyReading` rows. Runs as its own deployable,
separate from Core API, so it can live on whatever LAN segment can reach the Envoy — matching
the project's [decomposition](2026-09-25-core-api-design.md) and its "collectors are separate
deployables" boundary decision.

This spec also covers a small set of additive changes to the already-shipped Core API: the
Envoy's connection details (host, auth token, serial number) live in the database on the
`Source` row, not in the collector's own environment, so credential lifecycle (token renewal)
is manageable through the API rather than by editing config files on whatever host the
collector happens to run on.

## Boundaries

- **Envoy collector**: a standalone Python project (`enphase-data/envoy-collector/`), no
  shared code or dependency on `core-api`'s package — it only ever talks to Core API over
  HTTP, using a pre-provisioned `collector_api_key`, matching how every other collector in
  this system is meant to work.
- **Core API** gains: a `config` field on the existing `PATCH /sites/{id}/sources/{id}`
  endpoint (owner-authenticated), and two new collector-authenticated endpoints scoped
  narrowly to what a collector legitimately needs (read its own connection config; report
  back its device's discovered serial number). No new tables, no schema migration — `config`
  is the existing JSON column on `Source`.
- **Provisioning a Source for this collector remains a manual, one-time step** via Core API's
  existing `POST /sites/{id}/sources` (`kind: "energy"`, `provider: "envoy"`), done by the
  site owner before the collector is first run. The collector is a config-driven client of an
  already-provisioned `Source`; it never creates or discovers new `Source` rows itself.

**What this design optimizes for**: matching Core API's existing scale and correctness
priorities (a personal, low-traffic system) — no retry queues, no async I/O, no dependency on
a message broker. One device, polled once a minute, on a plain loop.

## Envoy local API — confirmed field mapping

Verified against real responses from the user's own Envoy-S Metered (D8.3.5226 firmware,
JWT auth). All three data endpoints require `Authorization: Bearer <token>` and a
self-signed-cert-tolerant HTTPS client (`verify=False` — accepted tradeoff for a LAN-local
device, not worth cert-pinning at this scale); `/info.xml` requires no auth at all.

- **`GET /production.json`**:
  - `production[]` has two entries — `type: "inverters"` (sum reported by the microinverters)
    and `type: "eim"` (the CT meter reading). **Use the `eim` entry's `wNow`** for
    `production_w` — it's the billing-grade metered value; the two differ in practice (e.g.
    4920W vs 7474W in one sample) because the CT captures what actually crosses the meter,
    not what the inverters self-report.
  - `consumption[]` has two `eim` entries: `measurementType: "total-consumption"` → 
    `consumption_w` directly (`wNow`), and `measurementType: "net-consumption"` → derive
    `grid_import_w`/`grid_export_w`: positive `wNow` is import, negative is export. Confirmed
    by energy balance in sampled data: `total_consumption.wNow - production.wNow ≈
    net_consumption.wNow`.
  - `storage[]` (`type: "acb"`) is the older Enphase AC Battery API, not this system's IQ
    Battery/Ensemble setup — ignored.
- **`GET /ivp/ensemble/power`**: one array entry per physical battery unit under a
  top-level key that is literally `"devices:"` **with a trailing colon** (a real quirk in
  Enphase's API — must be matched exactly, not `"devices"`). No site-level aggregate is
  provided; the collector sums across units itself:
  - `real_power_mw` is milliwatts (÷1000 for watts); sign is confirmed **negative = charging,
    positive = discharging** (verified live: the user's sample had all-negative values while
    SoC was simultaneously rising across two successive polls). Sum the (converted) values
    across all battery units, then split by sign into `battery_charge_w` /
    `battery_discharge_w` (whichever is zero when the aggregate sign is the other way).
  - `soc` is already 0–100; average across units for `battery_soc_pct` (no per-unit capacity
    data is exposed to weight by).
- **`GET /info.xml`**: unauthenticated. Serial number is at `envoy_info.device.sn` (XML).
  Used only for the auto-discovery flow below — never for reading energy data.
- **`GET /ivp/meters/readings`** was checked and gives the same production/net-consumption
  figures broken down per phase (a 3-phase installation) — not used, since Core API's schema
  has no per-phase fields and `production.json` alone is sufficient.

## Core API changes

All additive; no changes to existing endpoints' behavior beyond the two listed.

1. **`SourceUpdate` gains `config: dict | None = None`.** `PATCH /sites/{id}/sources/{id}`
   (existing endpoint, `require_site_owner`) shallow-merges a provided `config` into the
   `Source`'s existing `config` (`source.config = {**source.config, **payload.config}`), so
   an owner can update just `{"config": {"token": "<new-jwt>"}}` without needing to resend
   `host`/`serial_num`. Used for the yearly token-renewal flow.
2. **`SourceOut` gains a computed `renewal_url: str | None`.** Populated only when
   `provider == "envoy"` and `config.serial_num` is known:
   `https://enlighten.enphaseenergy.com/entrez-auth-token?serial_num=<serial_num>`; `null`
   otherwise (unknown serial number, or a different provider — this is deliberately
   provider-conditional, not a generic mechanism, since the renewal-URL shape is specific to
   Enphase's portal). The raw `config` dict (which holds the token) is never included in any
   response — only this derived field.
3. **New: `GET /sources/{source_id}/config`** — collector-key authenticated (same
   `get_source_by_collector_key` dependency ingestion already uses). Returns the `Source`'s
   full `config` dict (`host`, `token`, `serial_num`). This is how the collector gets its
   connection details at startup instead of its own environment variables for them.
4. **New: `PATCH /sources/{source_id}/discovered-config`** — collector-key authenticated,
   body is a narrow schema accepting only `{"serial_num": str}` (not the general
   `SourceUpdate` shape). Merges into `config` the same way. Deliberately restricted to this
   one field: a collector should be able to report back device identity it discovers, but
   never rewrite its own `host`/`token` — that stays an owner-only action via (1), so a
   leaked collector key can't redirect where its own polling points or forge a fresh
   credential.

Both new endpoints live in a new small router, `core_api/routers/collector_config.py`,
mounted alongside — but separate from — `ingestion.py`: they're collector-authenticated like
ingestion, but they're not ingestion (no reading data in), so keeping them out of
`ingestion.py` keeps that file's name accurate to what it does.

## Envoy collector design

**Config (environment variables)**: `CORE_API_URL`, `CORE_API_SOURCE_ID`,
`CORE_API_COLLECTOR_KEY`, `POLL_INTERVAL_SECONDS` (default 60). Notably absent: any Envoy
host/token config — those come from Core API at runtime.

**Startup**:
1. `GET {CORE_API_URL}/api/v1/sources/{CORE_API_SOURCE_ID}/config` → cache `host`/`token` in
   memory.
2. `GET https://{host}/info.xml` (no auth) → if the returned serial number differs from (or
   is missing from) the cached config, `PATCH .../discovered-config` with it. This runs on
   every startup, not just the first — cheap, and self-healing if the config was ever wiped
   or the collector's pointed at a replacement Envoy unit.

**Poll loop** (every `POLL_INTERVAL_SECONDS`):
1. `GET https://{host}/production.json` and `GET https://{host}/ivp/ensemble/power`
   (`Authorization: Bearer {token}`, `verify=False`).
2. Map to a batch of `{ts, metric, value}` readings per the field mapping above (`ts` = now,
   UTC).
3. `POST {CORE_API_URL}/api/v1/sources/{CORE_API_SOURCE_ID}/readings/energy` with the batch.

**Failure handling** (this is the collector's whole failure surface — it never talks to
anything but the Envoy and Core API):
- Envoy unreachable, times out, or returns a malformed/unexpected response: log and skip this
  cycle. No retry within the cycle — the next poll a minute later is the retry, and Core API's
  ingestion is idempotent, so a skipped minute is just a gap, not a correctness risk.
- Envoy returns 401: refetch config from Core API once (in case the owner just rotated the
  token) and retry the same cycle with the fresh token; if it still 401s, log a distinct,
  actionable message ("Envoy token invalid or expired — renewal_url: ...") rather than a
  generic network-error log, since this failure mode needs a human to act on it, not a retry.
- Core API rejects the push (e.g. the `Source` was disabled or its `effective_to` has passed,
  both of which now 403 per Core API's existing collector-auth checks): log clearly and keep
  polling — Core API's own state is authoritative on whether ingestion should be accepted,
  and re-enabling the source doesn't require restarting the collector.
- Core API is unreachable: log and skip this cycle's push (the Envoy data for that minute is
  lost, not queued) — consistent with the same low-stakes-gap tolerance as an Envoy-side
  failure.

**Testing**: unit tests using the user's actual sample JSON/XML as fixtures (`production.json`,
`ivp/ensemble/power`, `info.xml`), with both the Envoy HTTP calls and the Core API HTTP calls
mocked — verifying the field-mapping/sign-conversion logic and the startup/failure-handling
branches without needing real hardware or network access.

**Deployment**: runnable directly (`python -m envoy_collector`) for a systemd/cron setup on a
Pi or always-on host; a `Dockerfile` is also provided for NAS/Docker deployment. Neither is
required by the other — matches the project's stated flexible-deployment goal.

## Explicitly deferred / out of scope for this sub-project

- Automated Enlighten-login token refresh (scraping the login flow to auto-renew tokens
  without a human visiting the renewal URL) — tokens last ~1 year; a once-a-year manual step
  via the `renewal_url` is simpler and far less fragile than depending on an undocumented
  Enlighten login API that could change without notice.
- Envoy write/control operations (already noted as future work in the Core API spec).
- Multiple Envoy units per site — the data model already supports it (`Source` is
  many-concurrent for `kind: "energy"`), but this collector's provisioning/config-fetch flow
  is written against one `CORE_API_SOURCE_ID` per running instance; running two instances
  (one per Envoy, each with its own Source) is how a multi-Envoy site would be supported,
  not a change to this design.
- Per-phase (3-phase) readings from `/ivp/meters/readings` — Core API's schema has no field
  for them; would need a schema change to add, not something this collector should invent a
  workaround for.
