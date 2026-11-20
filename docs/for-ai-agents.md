# For AI agents

This page is a dense orientation for an automated agent (or a human in a hurry) working in this repository. It states facts and rules. Reasons and diagrams are in the pages linked from each row.

## What this is

Home-energy monitor. Collectors poll devices and push time series to one API. The API stores them in SQLite and computes electricity cost on request. A React app displays it. An actor does the reverse of a collector: it runs owner-defined schedules that command a device (today an Actron Neo air conditioner). A second kind of actor, `mail-actor`, sends queued email through an administrator-chosen provider. Single household scale, single process, no message broker.

## Services and entry points

| Path | Entry | Run | Test |
|---|---|---|---|
| `core-api/` | `core_api.main:create_app` (factory) | `uv run uvicorn core_api.main:create_app --factory --port 8000` | `uv run pytest` |
| `envoy-collector/` | `envoy_collector.main` | `uv run python -m envoy_collector.main` | `uv run pytest` |
| `amber-pricing-collector/` | `amber_pricing_collector.main` | `uv run python -m amber_pricing_collector.main` | `uv run pytest` |
| `local-pricing-collector/` | `local_pricing_collector.main` | `uv run python -m local_pricing_collector.main` | `uv run pytest` |
| `weatherlink-local-collector/` | `weatherlink_local_collector.main` | `uv run python -m weatherlink_local_collector.main` | `uv run pytest` |
| `actron-neo-actor/` | `actron_neo_actor.main` | `uv run python -m actron_neo_actor.main` | `uv run pytest -q`, `uv run ruff check .`, `uv run mypy` |
| `mail-actor/` | `mail_actor.main` | `uv run python -m mail_actor.main` | `uv run pytest -q`, `uv run ruff check .`, `uv run mypy` |
| `webapp/` | `src/main.tsx`, routes in `src/App.tsx` | `npm run dev` | `npm test`, `npm run lint`, `npm run build` |

Run Python commands from the project directory. Collectors need `CORE_API_URL` and `COLLECTOR_KEY`. The Actron actor needs `CORE_API_URL` and `ACTOR_KEY`. The mail actor needs `CORE_API_URL` and `MAIL_ACTOR_KEY`. Core API needs `JWT_SECRET`, `SECRETS_ENCRYPTION_KEY` (a Fernet key), `ENVOY_COLLECTOR_KEY`, `WEATHER_COLLECTOR_KEY`, `PRICING_COLLECTOR_KEY`, `ACTOR_KEY` and `MAIL_ACTOR_KEY`.

## Invariants (do not break)

1. **Only `core-api` accesses the database.** Collectors and the web app use HTTP.
2. **Timestamps are timezone-aware UTC.** Call `as_utc()` after reading from SQLite. Rules in local time use the site's `timezone` (IANA name) via `zoneinfo`.
3. **Sell price is negative when the customer is paid.** Local rate windows are stored positive; `local-pricing-collector` negates `sell` when pushing.
4. **Money is `Decimal`.** Prices are cents per kWh. kWh for one minute at N watts is `N / 60000`.
5. **Readings are keyed `(source_id, ts, metric)`, prices `(pricing_source_id, channel, interval_start)`.** Writes are upserts and must stay idempotent.
6. **Price lookups join on exact minute.** `POST /prices` floors derived minutes. Do not store price readings with seconds.
7. **`grid_import_w` and `grid_export_w` are written one at a time, as positive watts.**
8. **Authorization lives in FastAPI dependencies** (`deps.py`). Choose `require_site_access`, `require_site_manager`, `require_site_owner`, `get_current_user` or the collector dependencies before writing the function body.
9. **Collectors authenticate with one shared key per kind**, and optionally name their provider in `X-Collector-Provider`. Provider filtering is not a security boundary. Device actors use one shared `ACTOR_KEY` and a required `X-Actor-Provider`, on the same terms. The mail actor has its own `MAIL_ACTOR_KEY`, and neither key opens the other's routes.
10. **One Uvicorn worker.** Jobs and lockouts are in-process.
11. **Specs and plans in `docs/superpowers/` are history.** Do not rewrite them to match code.
12. **Never put real credentials in files.** `.env` is ignored; use `.env.example` placeholders.
13. **Never store a secret in plain text.** If the app only checks it, store a hash (`hash_password`, `hash_token`, `hash_recovery_code`). If the app must send it on, use `EncryptedString` or `EncryptedJSON` from `core_api/encrypted_types.py`, and ship a migration that converts existing rows. Encrypted columns cannot be filtered in SQL. Do not print secret values anywhere.

## File map: where to look for X

| Question | File |
|---|---|
| How is cost computed? | `core-api/core_api/cost.py`, tests in `core-api/tests/test_cost.py` |
| Who may call this endpoint? | `core-api/core_api/deps.py`, then the router in `core_api/routers/` |
| What does a collector receive as config? | `resolve_source_config()` in `core_api/routers/collector_config.py` |
| How do prices fan out to minutes? | `_fan_out_price_readings()` in `core_api/routers/ingestion.py` |
| What runs on a schedule? | `core_api/scheduler.py`, `core_api/jobs/` |
| How are secrets stored? | `core_api/encrypted_types.py`, `hash_recovery_code` in `core_api/security.py`, the Storing Secrets section of `CLAUDE.md` |
| What are the tables? | `core_api/models/`, migrations in `core-api/alembic/versions/` |
| What can the web app call? | `webapp/src/api/client.ts` and the `use*.ts` hooks next to each page |
| Which providers does the UI offer? | `PROVIDERS_BY_KIND` in `webapp/src/pages/SourcesPage.tsx` |
| What does the user see described? | `webapp/src/docs/*.md` (also shown in-app) |
| How do actors, schedules and runs work? | `docs/actors.md`; `core_api/routers/actor.py` (actor side), `routers/actors.py` (owner side), `actor_failures.py`, `sun.py`, `jobs/actor_watchdog_job.py` |
| How does email get sent? Which provider, and how do I add one? | `docs/mail.md`; `core_api/outbox.py`, `email_sender.py` (`OutboxEmailSender`), `admin_mail.py`, `routers/actor_mail.py`, `routers/admin_mail.py`; `mail-actor/mail_actor/worker.py` and `providers/` |
| How does the Actron actor decide and act? | `actron-neo-actor/actron_neo_actor/worker.py` (tick), `schedule_eval.py`, `actions.py`, `restores.py`, `device.py` |
| How does the Schedule tab work? | `webapp/src/pages/SchedulePage.tsx`, `webapp/src/actors/` |
| How does the Envoy collector handle expiry and certificates? | `envoy-collector/envoy_collector/main.py` |
| When does the local pricing collector act? | `local-pricing-collector/local_pricing_collector/triggers.py`, `plan.py`, `poll.py` |
| Why was it built this way? | `docs/superpowers/specs/` (date-ordered; the Core API spec is `2026-09-25-core-api-design.md`) |

## API cheat sheet

Base path `/api/v1`. Errors are `{ "code": str, "message": str, "detail": any }`.

```text
People:      Authorization: Bearer <access JWT>   (obtained via /auth/login then /auth/totp/verify)
Collectors:  X-Collector-Key: <shared secret for the kind>
             X-Collector-Provider: <provider>      (optional, narrows to that provider)

GET   /collector/sources?kind=energy|pricing|weather[&provider=...]
GET   /sources/{id}/config
PATCH /sources/{id}/discovered-config               body: any JSON object, merged into config
POST  /sources/{id}/readings/energy                 {"readings":[{"ts","metric","value"}]}
POST  /sources/{id}/readings/weather                same shape
POST  /sources/{id}/prices                          {"intervals":[{"channel","interval_start","interval_end","price"}]}
GET   /sites/{id}/cost?start=&end=&method=net|gross
GET   /health                                       (no /api/v1 prefix)
```

The generated reference is at `/docs` on a running instance.

Actors use a separate credential and path prefix:

```text
Actors:      X-Actor-Key: <ACTOR_KEY>           (MAIL_ACTOR_KEY for /actor/mail)
             X-Actor-Provider: <provider>          (required on per-instance routes)

GET   /actor/instances?provider=...
PUT   /actor/instances/{id}/capabilities | /readings
GET   /actor/instances/{id}/schedules | /context | /runs
PATCH /actor/instances/{id}/schedules/{sid}         {"armed": bool}, 204
POST  /actor/instances/{id}/runs
PATCH /actor/instances/{id}/runs/{run_id}           a finished run changes only from ok to overridden
GET   /sites/{id}/actors/failures                   (people, JWT)
GET   /sites/{id}/sun                               (people, JWT) today's {estimated, sunrise, sunset}
DELETE /sites/{id}/actors/alerts/{alert_id}         (any user with site access; dismisses a sticky alert)

Mail actor (same key, X-Actor-Provider: mail; no instance id):
PUT   /actor/mail/providers                         declare the providers it implements, 204
GET   /actor/mail/active                            {provider, settings}, or 204 when none is active
POST  /actor/mail/outbox/claim?limit=1..50          queued messages, marked sending
PATCH /actor/mail/outbox/{id}                       {"outcome": "sent"|"failed", "error"}, 204

Admin mail settings (JWT of an administrator):
GET   /admin/mail/providers | /admin/mail/outbox/summary
PUT   /admin/mail/providers/{provider}              {"settings": {...}}, a blank secret keeps the stored one
POST  /admin/mail/providers/{provider}/activate | /deactivate
POST  /admin/mail/test                              202 queued

Alerts (collector key, or actor key under /actor/instances/{id}):
PUT    /sources/{id}/alerts/{code}                  {"message", "severity"?, "sticky"?}
DELETE /sources/{id}/alerts/{code}
```

## Enumerations

- Source kind: `energy`, `pricing`, `weather`.
- Providers in use: `envoy`, `amber`, `local`, `weatherlink_live`. Free text in the database.
- Energy metrics: `production_w`, `consumption_w`, `grid_import_w`, `grid_export_w`, `battery_charge_w`, `battery_discharge_w`, `battery_soc_pct`, `production_wh_lifetime`, `grid_import_wh_lifetime`, `grid_export_wh_lifetime`, `grid_voltage_ph_a_v`, `grid_voltage_ph_b_v`, `grid_voltage_ph_c_v`, `grid_relay_state`.
- Weather metrics: `temp_c`, `humidity_pct`, `wind_speed_kph`, `wind_direction_deg`, `rain_mm`, `solar_radiation_wm2`, `barometric_pressure_hpa`.
- Price channels: `buy`, `sell`, `controlled_load` (`demand` exists but nothing uses it).
- Actor providers: `actron_neo`. Run kinds: `action`, `restore`, `test`. Run outcomes: `pending`, `running`, `ok`, `skipped`, `partial`, `failed`, `overridden`. Failure kinds: `run_failed`, `run_ok`, `still_off`, `actor_silent`, `needs_sign_in`, `alert`. Alert severities: `failure`, `partial`, `notice`. Trigger types: `window_start`, `window_end`, `time`, `sun`, `price_above`, `temperature`. Condition kinds: `reading` (no `kind` key), `months`, `days`, `week`, `demand_window`, `time_of_day`. Time reference kinds: `clock`, `sun`.
- Mail: outbox status `pending`, `sending`, `sent`, `failed`. Mail provider keys: `smtp2go`. Mail error codes: `unknown_field`, `field_required`, `settings_incomplete`, `provider_unavailable`, `no_active_provider`, `too_many_tests`, `not_sending`.

## Working in this repository

1. **Read before editing.** Comments here record why. A surprising line usually has a failure behind it.
2. **Write the test first** for behaviour changes. Test layout mirrors module layout.
3. **Schema changes need an Alembic migration.** Enums are stored as text with `CHECK` constraints, so adding an enum value needs a migration. SQLite often needs `batch_alter_table`.
4. **Run the relevant suite** (`uv run pytest` in the Python project you changed, `npm test` and `npm run lint` for `webapp/`). Report failures as they are.
5. **Follow `CLAUDE.md`.** Review for long functions (over 30 lines), repeated logic, TypeScript `any`, components with more than 3 props, and unhandled async errors. Run `/simplify` before presenting code.
6. **Write prose per `.claude/rules/avoid-ai-writing.md`.** That covers comments, docs and commit messages.
7. **Update the docs you affect.** The user guide chapters in `webapp/src/docs/` are tested for broken links. `python3 scripts/check_docs.py` (run by CI) checks links, anchors and table layout in `docs/`, but not whether the prose matches the code, so check that yourself. Follow the `documentation` agent's table of where each kind of fact lives.
8. **Do not guess at hardware.** Envoy and WeatherLink behaviour in the code comes from observed devices. Keep fixture JSON from real responses when you add a mapping.

## Known sharp edges

- The Envoy's own `consumption` figure omits the battery. The collector corrects it; do not "simplify" that away.
- WeatherLink rain is cumulative per day. The collector sends differences and rolls its baseline back when a push fails.
- Local-pricing reprice markers carry a `version`. A clear with an old version must not erase a newer marker.
- `DELETE` of a source with readings fails by design (`source_has_readings`). Disable it.
- A disabled pricing source still occupies the site's single pricing slot.
- `sources.config`, `users.amber_api_key` and the TOTP secrets are Fernet-encrypted with `SECRETS_ENCRYPTION_KEY`. Losing or changing the key makes them unreadable and invalidates every recovery code. No tool re-keys existing rows.
- When inspecting a table that holds secrets, select only the non-secret columns.
- Login lockouts are in memory and reset on restart.
- Actor runs are unique on (`schedule_id`, `kind`, `trigger_at`). For a window trigger the actor sets an action run's `trigger_at` to the window's start instant, and `actor_failures.py` finds the restore time by matching on it.
- `actor_instances.config` holds the Actron refresh token and is `EncryptedJSON`. The owner API (`ActorInstanceView`) must never return it. The actor must never log the unit's raw status, which holds the wall controller PIN.
- Deleting a schedule or instance deletes its run history, and returns 409 `unit_held_off` while an action run owes a restore.
- Mail: `email_outbox.body` and `mail_providers.settings` are encrypted, and the mail actor logs no address, subject, body or setting value. `OutboxEmailSender` commits through its own session, so commit your own writes before you send. With no active provider it falls back to SMTP. Delivery is at-least-once. See `docs/mail.md`.

## Pages

[Architecture](architecture.md) · [How it works](how-it-works.md) · [Data model](data-model.md) · [Core API](core-api.md) · [Collectors](collectors.md) · [Actors](actors.md) · [Mail](mail.md) · [Web app](webapp.md) · [Adding a provider](adding-a-provider.md) · [Operations](operations.md) · [Development](development.md)
