# Live tiles over a WebSocket

Date: 2026-10-24

## Problem

The dashboard's live tiles poll the stored readings every 60 seconds
(`LIVE_POLL_MS`). A collector delivers a value, and the tile shows it up to a
minute later. The owner wants a tile to change as soon as core-api receives
the value.

## Goal

When a collector sends a live sample to core-api, every dashboard open on
that site shows it within a fraction of a second. Live samples are never
written to the database.

## Goals and non-goals

- In scope: the energy tiles (`MetricTileGrid`) and the weather tiles
  (`WeatherTiles`) on the dashboard.
- In scope: the Envoy collector and the WeatherLink collector both push live
  samples.
- Not in scope: charts, the price tiles, the cost and billing-period tiles,
  the "Energy data changed ... ago" freshness line, and the rain-today tile.
  They keep their current polling. `rain_mm` is a delta since the previous
  poll, so it has no meaningful live value; it stays on stored data.
- Not in scope: changing the stored once-a-minute readings. Collectors push
  them exactly as they do now.
- Anti-goal: a tile showing a stale live number as if it were current, or a
  dashboard that shows nothing when the socket is down.

## How it works

### Collector to core-api

Each collector sends a plain HTTP POST per live sample, using the collector
key it already has (`X-Collector-Key`, and `X-Collector-Provider` where the
collector sends it). Two new endpoints beside the ingestion routes, one per
kind (energy and weather), gated like the existing ones by
`require_source_kind`. They accept a single sample: a timestamp and a map of
metric name to value, using the same metric names as `EnergyMetric` and
`WeatherMetric` (`rain_mm` excluded). They return 202 and store nothing in
the database.

- Envoy collector: the existing live sampler already reads
  `/ivp/livedata/status` every `LIVE_SAMPLE_SECONDS` (default 3). Each new
  sample (one whose `last_update` changed) is also mapped to metric values
  and pushed. The per-minute average and stored push are unchanged. A failed
  live push is logged (rate-limited like the other failure logs) and
  dropped, never retried or queued.
- WeatherLink collector: a new loop polls `fetch_current_conditions` every
  `LIVE_POLL_SECONDS` (default 10, adjustable) and pushes the mapped values.
  The once-a-minute stored poll is unchanged. Failures are logged and
  dropped.

The mapping reuses each collector's existing mapping functions where they
fit. Energy values are the instantaneous values from the sample, not
averages.

### In-memory latest value

core-api keeps one record per source in a module-level store: the newest
timestamp and metric values. A newer push replaces the older one; a push
older than the stored timestamp is ignored. The store is lost on restart,
which is accepted: the tiles fall back to stored data until the next push.
core-api runs as a single uvicorn process (`docker-entrypoint.sh`), which
this relies on. Running several workers would need a shared channel and is
out of scope.

### WebSocket to the browser

`GET /api/v1/sites/{site_id}/live` upgrades to a WebSocket.

- Auth: a browser WebSocket cannot set an `Authorization` header, and a token
  in the URL would end up in logs. The client sends `{"token": "<access
  token>"}` as its first message. core-api validates it with the same
  decoding as `get_current_user`, checks site access as `require_site_access`
  does (same "not found" for a site the user cannot see), and closes with a
  policy-violation code if either fails. Nothing is sent before auth passes.
  The server closes a socket that has not authenticated within 5 seconds.
- On success the server sends the current stored live values for the site's
  energy and weather sources (if any), then each new push for those sources
  as it arrives.
- Message shape: `{"kind": "energy" | "weather", "ts": "<iso>", "values":
  {"<metric>": <number>}}`.
- Fan-out: an in-process registry of connected sockets per site. A push for
  a source is sent to every socket on that source's site. A socket that
  errors on send is dropped. The registry must not block the ingest request
  on a slow client.
- The access token is checked once, at connect. A socket outlives the token's
  expiry; it ends when the client disconnects or core-api restarts. (Builder
  may close the socket at token expiry if that is cheap; not required.)

### Webapp

A hook, `useLiveStream(siteId)`, owns the socket for the dashboard page.

- Connects on mount, authenticates with the current token, and keeps the
  latest value per metric with its timestamp.
- Reconnects with a short back-off (1 s doubling to a 30 s cap) after a
  close or error. Does not reconnect after an auth failure.
- A value counts as live while it is under 15 seconds old by the client's
  clock of receipt (not the sample's own timestamp, to avoid clock skew).
- `MetricTileGrid` and `WeatherTiles` show a live value for a metric when one
  is live, otherwise the polled stored value they show today. The 60-second
  polling stays on for those tiles as the fallback. When the socket is down,
  silent, or never connected, the tiles look and behave as they do now.
- The tiles' existing derived values (for example the sign handling and the
  battery tile background) work on the live value the same way as on a stored
  one.

The WebSocket address is the API base URL with `http` swapped for `ws`
(`https` for `wss`).

### Deployment

- `webapp/docker-entrypoint.sh` writes the Content-Security-Policy at
  container start. Its `connect-src` must allow the `ws://`/`wss://` form of
  `API_BASE_URL`, since a plain `https://host` source does not cover `wss://`
  in all browsers.
- The nginx in front of the webapp only serves static files, and the browser
  talks to core-api directly, so no proxy change is needed there. Anyone who
  puts a reverse proxy in front of core-api has to allow WebSocket upgrades;
  note this in `docs/operations.md`.
- Docs to update: `docs/core-api.md`, `docs/collectors.md`, `docs/webapp.md`,
  `docs/operations.md`, and the new settings below.

## Settings

- Envoy collector: none new; uses `LIVE_SAMPLE_SECONDS`.
- WeatherLink collector: `LIVE_POLL_SECONDS`, default 10, must be at least 2.

## Security

- Collector pushes are authenticated with the existing per-kind key. The new
  endpoints write nothing to the DB.
- The WebSocket is closed for a missing, invalid or expired token and for a
  site the user cannot access.
- Cap the size of a pushed message and the number of metrics per sample so a
  bad collector cannot grow memory.
- Live values are not secrets, and nothing here stores a credential.

## Testing

- core-api: push stores the latest and ignores an older timestamp; the
  WebSocket rejects a missing, invalid and wrong-site token; an authorised
  socket gets the current value and then each push; a push on one site does
  not reach another site's socket; a dead socket is dropped without failing
  the ingest request.
- Collectors: a new sample triggers a push; a push failure does not break the
  stored per-minute push; weather loop runs at its interval.
- Webapp: tiles show a live value when one arrives and fall back after 15
  seconds of silence; reconnect back-off; no reconnect after auth failure.

## Left to the builder

- Module layout in core-api for the store and the socket registry.
- The exact close codes and the endpoint paths for the two push routes.
- Whether the weather live loop is a thread or shares the existing loop.
