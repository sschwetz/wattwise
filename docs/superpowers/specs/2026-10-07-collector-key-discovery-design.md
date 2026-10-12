# Collector Fleet Discovery — Design Spec

Date: 2026-10-07
Status: Approved for planning

## Purpose

Replace the current per-`Source` collector credential with one shared secret per collector
*kind* (envoy / weather / pricing), plus a new discovery endpoint each collector polls to learn
which sites' sources it's responsible for. Today, adding a new site means: create a `Source` in
the webapp, copy its one-time-generated key, then hand-edit `docker-compose.yml`/`.env` to add a
new per-site service block (envoy/weather) or append to a JSON array (`PRICING_SOURCES`), and
restart. After this change, adding a new site of an existing kind is purely a webapp action —
the already-running collector picks it up on its next periodic discovery refresh. No compose or
`.env` edit, no restart.

This is viable because all of the operator's sites' Envoy/WeatherLink Live hardware are
reachable (same LAN or mesh VPN) from wherever each collector process runs — confirmed with the
user. Pricing (Amber, a cloud API) already has no such constraint.

## Boundaries

- **Core API** gains: three new per-kind secrets in `Settings` (plain env vars, like
  `JWT_SECRET` — not DB-stored, not rotatable without a core-api restart), a new
  collector-authenticated discovery endpoint, and a changed (but not resized) authorization
  check inside the existing `get_source_by_collector_key` dependency. The three existing
  collector-facing routers (`ingestion.py`, `collector_config.py`) that depend on it are
  **unchanged** — same dependency signature, same behavior, different secret source
  underneath.
- **Each collector** (`envoy-collector`, `weather-collector`, `pricing-collector`) gains a
  fleet-discovery step (startup + periodic refresh) that replaces its current
  single-source/static-list config, and loops its existing per-source poll logic over whatever
  the discovery call returns.
- **Removed**: `Source.collector_api_key_hash`, the per-source raw-key generation in
  `POST /sites/{id}/sources`, and its one-time display in the webapp. There is no deployed
  third party relying on the old mechanism, so this is a replacement, not an addition alongside
  it.
- **Out of scope** (see "Explicitly deferred" below): key rotation tooling, DB-stored keys,
  per-site revocation, parallel/forked polling for envoy and weather.

## Core API changes

### New settings (`core_api/config.py`)

```python
envoy_collector_key: str
weather_collector_key: str
pricing_collector_key: str
```

Read from `ENVOY_COLLECTOR_KEY` / `WEATHER_COLLECTOR_KEY` / `PRICING_COLLECTOR_KEY`, each
required (raise `RuntimeError` if unset — same pattern as `jwt_secret`, no insecure default).

### Changed: `get_source_by_collector_key` (`core_api/deps.py`)

Still takes `source_id` from the path and `X-Collector-Key` from the header; still returns the
`Source` or raises `invalid_collector_key`/`source_disabled`/`source_expired` exactly as today.
Only the key check changes:

```python
_KIND_KEY_ATTR = {
    SourceKind.ENERGY: "envoy_collector_key",
    SourceKind.WEATHER: "weather_collector_key",
    SourceKind.PRICING: "pricing_collector_key",
}

def get_source_by_collector_key(
    source_id: int,
    x_collector_key: str = Header(...),
    db: Session = Depends(get_db_session),
) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this source")
    expected = getattr(get_settings(), _KIND_KEY_ATTR[source.kind])
    if not secrets.compare_digest(x_collector_key, expected):
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this source")
    if not source.enabled:
        raise AppError(403, "source_disabled", "This source is disabled")
    if source.effective_to is not None and source.effective_to <= datetime.datetime.now(datetime.timezone.utc):
        raise AppError(403, "source_expired", "This source's effective period has ended")
    return source
```

`SourceKind.ENERGY` maps to `envoy_collector_key` because `provider` (not `kind`) distinguishes
Envoy from any future energy provider — out of scope today since only one energy provider
exists, but flagged here rather than silently baked in as an assumption.

`_hash_key` and the SHA-256 hashing of the header go away entirely — `compare_digest` on the raw
value is both simpler and constant-time, matching how `jwt_secret` is treated elsewhere (never
hashed, because it's a service secret, not a user password).

`_KIND_KEY_ATTR` is defined once in `deps.py` and imported by `collector.py` (below) — both
places that need "which `Settings` field backs this kind's key" read the same mapping, so it
can't drift between the two call sites.

### New: `GET /api/v1/collector/sources?kind={energy|weather|pricing}`

New router, `core_api/routers/collector.py`. Collector-key-authenticated, but — unlike every
other collector-facing endpoint — there's no single `Source` to resolve the expected key from
yet, since the whole point is listing many. The key is checked directly against the kind named
in the query param:

```python
@router.get("/collector/sources", response_model=list[CollectorSourceOut])
def list_collector_sources(
    kind: SourceKind,
    x_collector_key: str = Header(...),
    db: Session = Depends(get_db_session),
) -> list[CollectorSourceOut]:
    expected = getattr(get_settings(), _KIND_KEY_ATTR[kind])
    if not secrets.compare_digest(x_collector_key, expected):
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this kind")
    now = datetime.datetime.now(datetime.timezone.utc)
    sources = db.execute(
        select(Source).where(
            Source.kind == kind,
            Source.enabled.is_(True),
            or_(Source.effective_to.is_(None), Source.effective_to > now),
        )
    ).scalars().all()
    return [CollectorSourceOut(source_id=s.id, site_id=s.site_id, config=_resolve_config(db, s)) for s in sources]
```

`_resolve_config` reuses the same provider-conditional config-assembly logic
`collector_config.get_source_config` already has (e.g. injecting the owner's `amber_api_key`
for `provider == "amber"`) — extracted into a shared helper both endpoints call, rather than
duplicated.

`CollectorSourceOut` (`core_api/schemas.py`): `{source_id: int, site_id: int, config: dict}`.

Not scoped by owner/user — returns every matching source system-wide, matching the existing
"handful of users, not a multi-tenant boundary" posture already established for this app (no
other collector-facing endpoint is user-scoped either).

### Migration

Drop `collector_api_key_hash` from `sources` (new Alembic revision). No data worth preserving —
it's a credential whose entire auth model is being replaced.

### Removed from `POST /sites/{id}/sources`

`create_source` stops calling `secrets.token_urlsafe(32)`/`_hash_key`; `SourceCreateOut` loses
`collector_api_key`. Response becomes identical to `SourceOut` in shape — creating a source no
longer has a "shown once" secret to return, so `SourceCreateOut` as a distinct schema can be
retired in favor of just returning `SourceOut`.

## Collector changes

All three collectors get the same two changes: config shape, and a discovery-driven poll loop.
The per-source polling logic itself (Envoy field mapping, WeatherLink mapping, Amber fetch) is
unchanged — only *how many sources get looped over, and where that list comes from* changes.

### Config (all three)

Replace `CORE_API_SOURCE_ID`/`CORE_API_COLLECTOR_KEY` (envoy/weather) or `PRICING_SOURCES`
(pricing) with:

```
CORE_API_URL
COLLECTOR_KEY
DISCOVERY_REFRESH_SECONDS   (default 300)
```

`POLL_INTERVAL_SECONDS` (envoy/weather) and pricing's existing tick alignment are unchanged —
discovery refresh is a separate, slower cadence layered on top of the existing poll cadence, not
a replacement for it.

### Discovery client (new, shared shape across all three `core_api_client.py` modules)

```python
def fetch_collector_sources(core_api_url: str, kind: str, collector_key: str) -> list[dict]:
    """GET /collector/sources?kind=<kind>, X-Collector-Key: <collector_key>."""
```

### Poll loop restructuring

- **envoy-collector / weather-collector** (`main.py`): today's `main()` fetches one source's
  config once, then loops `poll_once(settings, config)` forever on a single config dict. New
  shape: a dict of `{source_id: config}` refreshed every `DISCOVERY_REFRESH_SECONDS`; each poll
  tick iterates that dict (sequentially — these are fast LAN calls, no need for pricing's
  fork-per-site isolation at the site counts in play here) calling the existing `poll_once`
  per source. A source's entry in `discovered_and_sync_serial`'s "refetch after 401" path still
  calls the existing per-source `/sources/{id}/config` endpoint directly — unchanged.
- **pricing-collector**: already loops over a list of sources per tick
  (`run_live_poll_forever`/`poll_one_site`) and already forks a child process per site — that
  structure is kept as-is. The only change is where the list comes from: `fetch_collector_sources`
  on a refresh timer, replacing the static `PRICING_SOURCES` env var list. `PricingSource` drops
  its `collector_key` field (now a single shared `collector_key` passed alongside the list, not
  per-entry).
- A source that disappears from the discovery list (disabled, expired, or deleted) is simply
  dropped from the in-memory dict/list on the next refresh — no special-case error handling,
  since "stop polling it" is the entire intended behavior.

### docker-compose.yml

Each collector service becomes static — no per-source `:?`-required env vars:

```yaml
envoy-collector:
  build: ./envoy-collector
  environment:
    CORE_API_URL: http://core-api:8000
    COLLECTOR_KEY: ${ENVOY_COLLECTOR_KEY:?set a long random secret, shared with core-api's own ENVOY_COLLECTOR_KEY}
  depends_on:
    - core-api
```

Same pattern for `weather-collector` (`WEATHER_COLLECTOR_KEY`) and `pricing-collector`
(`PRICING_COLLECTOR_KEY`). `core-api`'s own service block gains the matching three env vars
(`ENVOY_COLLECTOR_KEY`, `WEATHER_COLLECTOR_KEY`, `PRICING_COLLECTOR_KEY`), each sourced from the
*same* `.env` entry as the corresponding collector service — one value, defined once in `.env`,
substituted into two service blocks, the same way `JWT_SECRET` already is.

## Webapp changes

`SourcesPage.tsx`: remove the one-time `collector_api_key` reveal after creating a source.
Replace with a short note that the matching collector will start polling this source
automatically within its discovery refresh interval — no key to copy. `useSources.ts` drops
`collector_api_key` from its response type.

## Testing

- **Core API**: existing per-source-key tests in `tests/test_sources.py`,
  `tests/test_collector_config.py`, and the ingestion tests move from a per-source generated key
  fixture to a shared kind-key fixture (`monkeypatch.setenv("ENVOY_COLLECTOR_KEY", ...)`). New
  `tests/test_collector_discovery.py`: returns only enabled/non-expired sources of the requested
  kind, excludes other kinds, 401s on a wrong or missing key, empty list when none match, and
  the `provider == "amber"` config-injection behavior is exercised through the new endpoint too
  (via the shared `_resolve_config` helper).
- **Each collector's test suite**: config tests updated for the new env var shape; a new test
  that the poll loop's source dict is refreshed on the discovery interval and that a source
  removed from a refresh is no longer polled on the next tick.

## Explicitly deferred / out of scope

- **Key rotation tooling / DB-stored keys**: rotating a kind's key means changing its `.env`
  value and restarting core-api plus that collector — the same operational cost as rotating
  `JWT_SECRET` today. No admin endpoint or webapp UI for this; revisit only if rotation without
  downtime becomes an actual requirement.
- **Per-site revocation**: disabling a `Source` (existing `enabled` flag) already removes it
  from the next discovery refresh — there's no separate "revoke this site's access" concept
  needed on top of that.
- **Parallel/forked polling for envoy and weather**: pricing forks a child process per site
  because of Amber's own latency/isolation needs; envoy/weather LAN calls are fast enough that a
  sequential per-tick loop is sufficient at the site counts in play. Revisit if a future site
  count makes one slow/unreachable device's timeout noticeably delay the others' poll tick.
- **Multi-tenant scoping of `/collector/sources`**: returns every matching source system-wide,
  not scoped per owning user — matches every other collector-facing endpoint's existing
  all-sites-are-trusted posture; would need real design work if this ever became a multi-tenant
  SaaS rather than a personal/small-fleet deployment.
