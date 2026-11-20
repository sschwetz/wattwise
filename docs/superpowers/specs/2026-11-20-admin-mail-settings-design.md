# Admin Settings and the mail actor

## Intent

Today core-api sends every email itself, over SMTP to the `mail-relay` Postfix container, which relays to a provider set in `RELAY_SMARTHOST`. Changing provider means editing environment variables and restarting. A failed send is logged and the user never learns of it.

An app-wide administrator should pick a mail provider in the web app, enter its credentials, and have WattWise send through it. smtp2go is first. Mailgun and others must be cheap to add later.

## Goals

- An **Admin Settings** item in the top-right menu, shown only to app-wide admins.
- An **Email** section on that page: choose a provider, fill in its fields, save, make it active, send a test email.
- Provider settings stored in the database, secrets encrypted.
- A new container, `mail-actor`, that fetches the active provider's settings from core-api and sends queued email through the provider's API.
- Core-api hands the actor its work through an outbox table.
- Adding a provider touches the actor only.
- The page looks and behaves like the existing Account and Site settings screens.

## Non-goals

- Removing `mail-relay`. It stays as the path when no provider is active.
- A screen for granting or revoking admin. [The Users design](2026-11-20-admin-users-design.md) covers it, and it builds the admin foundation this design uses.
- Email templates, HTML bodies, attachments, bounce or delivery webhooks, per-site senders.
- More than one active provider at a time.
- Step-up (password or TOTP) before saving provider settings.

## Anti-goals

- Existing email stops working before an admin configures a provider.
- A credential or an email body containing a token sits in the database as plaintext.
- An API response returns a stored secret.

## Constraints

- No admin concept exists. `users` has no role column.
- Actors authenticate with `ACTOR_KEY` and `X-Actor-Provider`, and all actors share the key (`docs/actors.md`).
- CLAUDE.md, Storing Secrets: values the app must send onward use `EncryptedString` or `EncryptedJSON`. Reset and confirmation links contain raw tokens, and the outbox must hold them until sent, so message bodies are encrypted.
- CLAUDE.md, Documentation: a `docs/` page and a `webapp/src/docs/` chapter ship in the same change.

## Approaches considered

1. **Provider definitions in core-api, send code in the actor.** Two places to touch per provider. The form works while the actor is down.
2. **The actor declares each provider's fields (chosen).** One file per provider, in the actor. The form is empty until the actor has reported once. Core-api validates against the last declaration it stored.
3. **One settings row with a JSON blob.** Least code. Switching providers loses the other provider's credentials.

Approach 2 was picked for a one-place change when adding a provider. This is the same shape as actor capabilities: the actor declares, the web app draws the form.

## Design

### Admin flag

`users.is_admin` (boolean, default false). A migration sets it on the bootstrap user, which is the user created by `ensure_admin_user`. In a database whose first user predates that, the migration sets it on the lowest `id`. `/users/me` returns `is_admin`. A `require_admin` dependency in `deps.py` returns 403 `admin_required` for anyone else.

### Tables

`mail_providers`, one row per provider the actor has declared:

| Column | Notes |
|---|---|
| `provider` | Primary key, the provider key, for example `smtp2go`. |
| `label` | Display name from the declaration. |
| `fields` | JSON list from the declaration: `name`, `label`, `secret` (bool), `required` (bool). |
| `settings` | `EncryptedJSON`, null until saved. Keys are field names. |
| `is_active` | Boolean. A partial unique index allows one true row. |
| `declared_at`, `updated_at` | Timestamps. |

A new declaration replaces `label` and `fields` and leaves `settings` and `is_active` alone. A saved setting whose field the actor no longer declares is dropped on the next save, not on declare.

`email_outbox`:

| Column | Notes |
|---|---|
| `id` | Primary key. |
| `to_address`, `subject` | Strings. |
| `body` | `EncryptedString`. Cleared to an empty string when the message is sent or fails for the last time. |
| `status` | `pending`, `sending`, `sent`, `failed`. Check constraint. |
| `attempts`, `last_error` | The error text never includes settings or the body. |
| `created_at`, `claimed_at`, `next_attempt_at`, `finished_at` | Timestamps. |

A scheduler job deletes finished rows older than 30 days.

### Sending path

A new `OutboxEmailSender` implements the existing `EmailSender` protocol and inserts a `pending` row. `get_email_sender` returns it when a provider is active, and the current `SmtpEmailSender` otherwise. Every caller, including `try_send_email`, stays as it is.

When a provider is active and the actor is down, mail queues and is not diverted to the relay. Delivery is delayed by up to the actor's poll interval (5 seconds) plus the send time.

### Actor endpoints

Under `/api/v1/actor/mail`, with `X-Actor-Key` and `X-Actor-Provider: mail`. The mail actor is one process for all providers, so the provider header names the actor kind and not the email provider.

| Endpoint | Purpose |
|---|---|
| `PUT /providers` | Declare all providers the actor implements. Replaces `label` and `fields` per row. Rows for providers the actor no longer lists are kept and shown as "not available". |
| `GET /active` | The active provider key and decrypted `settings`, or 204 when none is active. |
| `POST /outbox/claim?limit=` | Returns up to `limit` due messages with decrypted bodies and marks them `sending`. A `sending` row whose `claimed_at` is more than 2 minutes old is claimable again. |
| `PATCH /outbox/{id}` | Body `{outcome: "sent" \| "failed", error?}`. `failed` returns the row to `pending` with a backoff until `attempts` reaches 5, then sets `failed`. |

The claim is one conditional `UPDATE`, so two actor processes never take the same row. This is the same rule the watchdog uses.

### Admin endpoints

Under `/api/v1/admin/mail`, JWT plus `require_admin`.

| Endpoint | Purpose |
|---|---|
| `GET /providers` | Each provider with label, fields, `is_active`, `available`, and per-secret `"set"` or `"not set"`. Non-secret values are returned. |
| `PUT /providers/{provider}` | Save settings. A blank secret keeps the stored value. Unknown field names are 422. A missing required field is 422 `field_required`. |
| `POST /providers/{provider}/activate` and `/deactivate` | Activating needs saved settings with every required field set (409 `settings_incomplete`). Activating one provider deactivates the other. |
| `POST /test` | Queues a test email to the calling admin. 409 `no_active_provider` when none is active. |
| `GET /outbox/summary` | Counts of `pending`, `sent` in the last 24 hours and `failed` in the last 24 hours, plus the last error text. |

### smtp2go

The first file in `mail-actor/mail_actor/providers/`. Fields: `api_key` (secret, required) and `from_address` (required). `send()` posts to `https://api.smtp2go.com/v3/email/send` with the key in `X-Smtp2go-Api-Key` and a JSON body of `sender`, `to`, `subject` and `text_body`. It treats a non-2xx response or a body whose `data.succeeded` is not 1 as a failure. The planning step confirms these against <https://developers.smtp2go.com/docs/getting-started>.

A provider module exposes `KEY`, `LABEL`, `FIELDS` and `send(settings, message)`. A registry list in `providers/__init__.py` names them. Mailgun is another module and one line in that list.

### Mail actor

New directory `mail-actor/`, built like `actron-neo-actor`: `config.py` (`CORE_API_URL`, `MAIL_ACTOR_KEY`), `core_api_client.py`, a worker loop, `Dockerfile`, tests with a fake core. On start it declares providers, then every 5 seconds claims messages and sends them through the active provider. It re-fetches `/active` at most every 30 seconds and when a send fails with an auth error. It reports its commit through the existing service status path, and `service_status.py` lists `mail-actor` under `mail_actor_key`.

### Webapp

- `Header.tsx`: an **Admin Settings** `NavLink` after Site settings, rendered only when `me.is_admin`. It uses `menuItemClass`.
- Route `/admin` inside `ProtectedLayout`, wrapped in a guard that redirects non-admins to `/`.
- `AdminSettingsPage.tsx`, built from the Account page's layout, `formStyles`, `SubmitButton` and `QueryError`. The Email section has a provider selector, a form drawn from the declared `fields`, an Active toggle, a Send test email button, and the outbox summary line. When no provider has been declared the section says the mail actor has not reported and names the container to start.
- Secret inputs show "set" with a Change button, as the Amber key does on the Account page.

### Documentation

- `docs/`: a new `mail.md` page (tables, endpoints, adding a provider), plus edits to `architecture.md` (the relay and "actors never email" statements), `actors.md`, `core-api.md`, `data-model.md` and `development.md`.
- `webapp/src/docs/`: a new Admin settings chapter, plus the glossary, troubleshooting and the guide's index.
- The `documentation` agent runs after implementation, then the link and anchor check.

### Deploy

`docker-compose.yml` and `docker-compose.prod.yml` gain `mail-actor`, on the `app` network only, with `MAIL_ACTOR_KEY` (core-api gets it too). `mail-relay` is unchanged.

## Left to the builder

- Backoff schedule for retries, within the 5-attempt limit.
- Exact JSON shape of declared fields beyond the keys listed.
- Test layout, following the existing actor and core-api suites.
- The outbox summary's place on the page.

## Calls I made, not yours

- Mail falls back to the Postfix relay when no provider is active.
- Queued mail waits for the actor and never diverts to the relay.
- Message bodies are encrypted and cleared after the last attempt, because they hold reset and confirmation links.
- The mail actor has its own key, `MAIL_ACTOR_KEY`, and the provider header value `mail`. The first version shared `ACTOR_KEY`; the final review showed that a holder of the device key could then read reset links and take over an account, so the keys were split.
- Retention of 30 days for finished outbox rows.
