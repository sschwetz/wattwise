# Envoy token renewal

Date: 2026-10-13

## Problem

An Envoy local-API token lasts one year. When it expires, readings stop. The
collector logs a link to the manual renewal page (`_renewal_message` in
`envoy-collector/envoy_collector/main.py`), and the owner has to visit it, copy
the token, and paste it into the webapp.

## Goal

The collector renews the token itself, using the owner's Enlighten
credentials stored on the source, and saves the new token where the old one
was. Nobody visits the Enlighten page.

## Enphase flow

Both calls are POSTs over https (from Enphase's "IQ Gateway local APIs or UI
access using token" document):

1. `POST https://enlighten.enphaseenergy.com/login/login.json`, form fields
   `user[email]` and `user[password]`. The response JSON has `session_id`.
2. `POST https://entrez.enphaseenergy.com/tokens`, JSON body
   `{"session_id": ..., "serial_num": <envoy serial>, "username": <email>}`.
   The response body is the token. It is valid for one year.

## Storage

`username` and `password` are new keys in the source's `config` JSON, next to
`token`. Nothing is encrypted at rest, same as the token today.

- `core_api/routers/sources.py`: add `username` to `_SENSITIVE_CONFIG_KEYS`
  (`password` is already there). Only the site owner sees either.
- `PATCH /sites/{id}/sources/{id}` already merges `config`, so the webapp and
  API users can set both with no change to that endpoint.
- Changing `host` does not clear the credentials or the token. They belong to
  the Enlighten account, not the device.

## Core API changes

- `DiscoveredSourceConfig` (`core_api/schemas.py`) accepts an optional `token`.
  The collector writes the renewed token through the existing
  `PATCH /sources/{id}/discovered-config`, which uses the collector key.
- The collector key can now write a token for sources it is authorised for.
  This is already true of `serial_num` and `cert_fingerprint`, and the
  endpoint is still scoped to one source by `get_source_by_collector_key`.

## Collector changes

New module `envoy_collector/enlighten_client.py`:

- `fetch_token(username, password, serial_num) -> str` runs the two POSTs
  with 10 second timeouts and normal CA verification.
- `EnlightenAuthError` for a rejected login. A generic `EnlightenError` for
  any other failure. Neither message includes the password, session ID or
  token.

New helper `token_expires_at(token) -> datetime | None`: base64-decodes the
JWT payload and reads `exp`. It does not verify the signature, since the
collector only needs the date. Returns None if the token is not a decodable
JWT.

Renewal in `main.py`, as one function `renew_token(settings, source_id,
config) -> dict` that returns the updated config:

- Skips (returns config unchanged) when `username`, `password` or
  `serial_num` is missing, or when the last attempt for this source was less
  than one hour ago. The last-attempt time is kept in memory per source.
- On success, sets `config["token"]` and reports it to Core API.
- On `EnlightenAuthError`, logs one error line saying the Enlighten login
  was rejected and credentials need updating. It does not retry until the
  hour passes.

Two triggers:

1. **On a 401.** In `poll_once`, the branch where the retry after the config
   refetch also raises `EnvoyAuthError` now calls `renew_token`. If it
   returns a new token, the cycle retries once. Otherwise it logs
   `_renewal_message` as before.
2. **Before expiry.** `_refresh_sources` (every `DISCOVERY_REFRESH_SECONDS`,
   default 300) checks each source's token. When `token_expires_at` is less
   than 14 days away, it calls `renew_token`. A token with no readable `exp`
   is left alone.

Logging never includes `password`, `token`, or the session ID.

### First-time setup with no token

The serial number does not depend on a token. Discovery reads it from the
Envoy's unauthenticated `/info.xml` and reports it to Core API as soon as the
host is pinned (`discover_and_sync_serial`). So a user who never generated a
token enters only **Host**, **Enlighten email** and **Enlighten password**:

1. The collector pins the certificate and stores `serial_num`.
2. The first poll sends an empty token and gets a 401.
3. The 401 path calls `renew_token`, which now has the serial, and saves the
   token.

Two details make this work:

- A skipped renewal (missing serial, username or password) does not start the
  one-hour wait. Only an actual call to Enlighten does. Otherwise the first
  cycle, which can run before the serial is stored, would block the next
  attempt for an hour.
- The collector must send the request even when `token` is missing or empty,
  so the Envoy's 401 reaches the renewal path. `_fetch_and_map` and
  `_fetch_battery_json_or_none` currently read `config["token"]`, which raises
  `KeyError` on a source with no token. The generic handler would log that
  and never reach renewal. Both change to `config.get("token", "")`.

If `/info.xml` is unreachable, there is no serial, and nothing else works
either. The user can still fix `serial_num` by hand through the raw config
editor.

## Webapp changes

`EnvoyConfigForm` in `webapp/src/pages/SourcesPage.tsx` gets two fields:

- **Enlighten email** (text), key `username`.
- **Enlighten password** (password input), key `password`.

Both are optional, and **Token** becomes optional when they are filled in.
A short hint under them says the collector uses them to
renew the token automatically. The form's `onSave` payload includes them.
Because the config PATCH merges, saving from a form opened before a renewal
could overwrite the new token with the old one. The form already has this
exposure for `token`, and this change does not make it worse.

Update `webapp/src/docs/04-sources.md` ("Getting the token") and
`08-troubleshooting.md` to say the token renews itself when credentials are
set, and that the manual steps remain the fallback. The in-app Help viewer
reads these files.

## Error handling

| Situation | Behaviour |
|-----------|-----------|
| No credentials stored | No renewal. Existing manual message on 401. |
| Wrong password | One error log, no retry for an hour. |
| Enlighten unreachable or 5xx | Logged, retried after an hour. Polling continues. |
| Core API rejects the token write | Logged. The collector keeps the new token in memory for this process, so polling works until the next config refresh replaces it. |
| Token not a JWT | Pre-expiry check skipped. The 401 path still works. |

## Testing

Tests first, in each package's existing test layout.

- `envoy-collector/tests/test_enlighten_client.py`: mocked HTTP for success,
  login rejected, tokens endpoint rejected, timeout. Checks that error text
  contains no secrets.
- `test_main.py`: renewal after a double 401; no renewal without
  credentials; hourly limit; pre-expiry trigger at 13 and 15 days; a token
  without `exp`.
- A `token_expires_at` test with a hand-built JWT.
- `core-api/tests`: `DiscoveredSourceConfig` accepts `token`; a collaborator
  does not see `username` or `password`; the owner does.
- `webapp/src/pages/SourcesPage.test.tsx`: the two fields render, are saved
  in the payload, and the password field is masked.

## Out of scope

- Encrypting `token` or `password` in the database.
- Per-field "write-only" handling in the webapp. The owner already receives
  `token` in the clear, and `password` follows the same rule.
- Renewal for non-Envoy providers.
