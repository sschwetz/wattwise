# Frontend Webapp — Design Spec

Date: 2026-10-01
Status: Approved for planning (sub-project 5 of 5, final sub-project of the energy
monitoring system)

## Purpose

Give the end user (and later, invited viewers) a web UI to see live and historical
production/consumption/battery/pricing/cost data for their site(s), manage sources
(Envoy/Amber/WeatherLink connections) and billing/tariff configuration without hand-rolling
API calls, and log in securely. This is the last of the five sub-projects; Core API,
Envoy collector, Pricing collector, and Weather collector are all done and already pushing
data into Core API.

Built with half an eye on a possible future SaaS offering, but YAGNI governs v1: nothing
here is built ahead of a concrete need. Multi-tenancy already exists in Core API's data
model (`Site.owner_user_id`, `SiteAccess`); a future public marketing/signup surface, if it
ever happens, is a separate concern from this app and is explicitly out of scope.

## Project decomposition (for context)

1. Core API + data model — DONE.
2. Envoy collector — DONE.
3. Pricing collector (Amber) — DONE.
4. Weather collector (WeatherLink Live) — DONE.
5. **Frontend webapp** (this spec) — dashboards, net/gross toggle, site/source/billing
   management, authentication (including TOTP 2FA and password reset).

## Boundaries

- **Frontend webapp**: React + Vite + TypeScript SPA. Talks to Core API only, over HTTP,
  never touches the database. Built as static files, served by nginx in its own Docker
  container, deployed and scaled independently of Core API's container — the same
  deployment shape as the collectors. The two are never bundled into one image/process:
  a frontend-only redeploy (e.g. a UI tweak) must not require rebuilding or restarting
  Core API, and vice versa.
- **Core API**: its own separate container, unchanged in shape by this sub-project. Gains
  a handful of new endpoints and tables (auth register/TOTP/password-reset,
  `PATCH /sites/{id}`) but its existing query/ingestion surface is otherwise untouched.
  Still the only thing with DB access.
- **mail-relay (new deployable)**: a small Postfix container that relays outbound mail
  (password-reset emails) through a smarthost. Lives on the same Docker network as Core
  API; not reachable from outside it.

## Runtime configuration (frontend)

The Docker image is not rebuilt per deployment target. An nginx container-entrypoint
script writes a small `/config.js` (containing the Core API base URL) from an environment
variable at container start; the app reads this at runtime instead of having the URL
baked into the JS bundle at build time. This follows the same "flexible deployment,
single host or Pi or NAS, in any combination" principle the Core API spec established for
the collectors.

## Tech stack

- **React + Vite + TypeScript** — SPA, simplest path that doesn't block a future SaaS
  pivot (a marketing/signup surface, if ever built, would be a separate deployable
  regardless of this app's framework).
- **Tailwind CSS** for styling.
- **React Router** for client-side routing.
- **TanStack Query** for API data-fetching, caching, and polling.
- **Recharts** for charts.
- **Vitest + React Testing Library** for component/unit tests.

## Data model changes (Core API)

### Auth / TOTP

- `User` gains `totp_secret` (nullable text) and `totp_enabled` (bool, default `false`).
- **RecoveryCode**: `id`, `user_id` (FK), `code_hash` (bcrypt, same treatment as
  passwords), `used_at` (nullable). Ten rows created at TOTP enrollment confirmation.

### Password reset

- **PasswordResetToken**: `id`, `user_id` (FK), `token_hash` (random token, bcrypt-hashed
  like a password — never stored or logged in plaintext), `expires_at`, `used_at`
  (nullable). Expiry: 1 hour.

## API contract additions

All under the existing `/api/v1` prefix.

### Registration & login

- `POST /auth/register` `{email, password}` — creates the `User` with
  `totp_enabled=false`. Returns `201` with no token (first login forces TOTP setup — see
  below). Returns a structured error if the email is already registered.
- `POST /auth/login` `{email, password}` — verifies credentials, then branches on
  `totp_enabled`:
  - not yet enrolled → `200 {status: "totp_setup_required", setup_token}`
  - enrolled → `200 {status: "totp_required", mfa_token}`

  `setup_token` and `mfa_token` are short-lived (5 min), scoped JWTs — a distinct
  dependency from the existing access-token auth, so each can only be used against its
  own next-step endpoint, nothing else in the API. Wrong email/password returns the same
  structured 401 regardless of which part was wrong (no "email not found" vs "wrong
  password" distinction, to avoid account enumeration).

### TOTP setup & verification

- `POST /auth/totp/setup/start` (auth: `setup_token`) — generates a TOTP secret (via
  `pyotp`), stores it on the user as pending, returns the secret and an `otpauth://` URI.
  No server-side QR image generation — the frontend renders the QR client-side from the
  URI (one less dependency, and the secret never needs to transit as an image).
- `POST /auth/totp/setup/confirm` (auth: `setup_token`) `{code}` — verifies the submitted
  code against the pending secret. On success: sets `totp_enabled=true`, generates 10
  recovery codes (returned once, plaintext, in this response only — never retrievable
  again), and returns a full `access_token`. On failure: `setup_token` remains valid until
  its own expiry (user can retry within the 5-minute window).
- `POST /auth/totp/verify` (auth: `mfa_token`) `{code}` — accepts either a live TOTP code
  or an unused recovery code. A matching recovery code is marked `used_at` and cannot be
  reused. Returns a full `access_token` on success. The JWT itself is stateless, so
  attempt counting is tracked server-side keyed by the token's `jti` claim (an in-memory
  counter is sufficient at this scale — the token's own 5-minute expiry already bounds
  the counter's lifetime); hitting 5 failed attempts invalidates the `jti` outright.
  Exhausting attempts or letting the token expire requires logging in again from scratch.

### Password reset

- `POST /auth/password-reset/request` `{email}` — always returns `200` with a generic
  message, regardless of whether the email is registered (anti-enumeration, matching the
  login error behavior above). If the email does exist, creates a `PasswordResetToken`
  and sends an email (via the `EmailSender` abstraction below) containing a link to
  `{FRONTEND_BASE_URL}/reset-password?token=...`.
- `POST /auth/password-reset/confirm` `{token, new_password}` — validates the token
  (exists, unexpired, unused), updates `password_hash`, marks the token used. Deliberately
  does **not** touch `totp_secret`/`totp_enabled` — an attacker who compromises only the
  user's inbox still cannot complete login without the TOTP device or a recovery code.

### Sites

- `PATCH /sites/{id}` — edit `name`/`timezone`. Doesn't exist today (only create/list/get);
  needed to make the "site management" screen actually functional rather than
  create-only.

## Email delivery (Core API)

`EmailSender` is a small interface (`send(to, subject, body)`) with one implementation
for v1:

- **SMTP backend** — host/port/from-address/optional-auth, all via config. Points at the
  mail-relay Postfix container by default, but works against any SMTP endpoint
  (a provider's SMTP relay, etc.) since config is just connection details.

A transactional-email-API backend (Postmark/SES/etc.) is a documented extension point,
not built now — there's no chosen provider yet, so building it would be dead code. Adding
one later is a new class implementing the same interface plus a config switch; no call
site changes.

## New deployable: mail-relay

A Postfix container, added to the docker-compose alongside Core API and the collectors.
Configured to relay outbound mail through a smarthost specified via an environment
variable (the user's ISP SMTP, a provider's SMTP endpoint, etc.) — it is not an
open-to-the-internet relay. Only reachable from Core API over the internal Docker
network.

Getting mail reliably delivered (SPF/DKIM/rDNS alignment for the sending domain) is a
DNS/operational setup step on whatever domain is used to send from, outside the
container's own configuration — documented as a deployment note, not code the project
builds.

## Frontend screens

- **Login** — email/password. Branches per the API response: `totp_setup_required` →
  Set up authenticator; `totp_required` → Enter code; success → app.
- **Register** — email/password/confirm. On success, routes to Login (not auto-logged
  in, since TOTP setup happens on first login, not at registration).
- **Set up authenticator** — QR code (rendered client-side from the `otpauth://` URI via
  a `qrcode` library) with the manual secret shown as a copyable fallback, 6-digit code
  input. On success, shows the 10 recovery codes once with an explicit "I've saved these"
  acknowledgment gate before continuing into the app.
- **Enter code** — 6-digit input, with a "use a recovery code instead" toggle.
- **Forgot password?** (linked from Login) — email input → generic confirmation message.
- **Reset password** (`/reset-password?token=...`) — new password + confirm → success →
  redirect to Login.
- **Site picker / switcher** — 0 sites → onboarding "create your first site" form
  (name, timezone); 1 site → skip straight to the dashboard; 2+ sites → switcher in the
  header.
- **Dashboard** (per site) — live tiles (production/consumption/grid import-export/
  battery SOC; polled ~60s to match collector cadence), historical charts with a
  time-range selector (today / 7d / 30d / custom), net/gross toggle, price overlay and
  computed cost (`GET /sites/{id}/cost`).
- **Sources** — list sources for the site; create (kind/provider + provider-specific
  config fields); shows the generated `collector_api_key` once at creation
  (copy-to-clipboard, since a collector needs it in its own env config); enable/disable;
  edit config.
- **Billing** — `SiteTariff` list/create/edit; `DemandWindow` create under a tariff;
  `BillingCycle` list/create/edit. Includes a read-only demand-charges view; this will
  likely show an empty state for now, since the `DemandInterval`/`DemandCharge`
  background derivation jobs were explicitly deferred out of sub-project 1 and aren't
  built yet — the UI treats "no demand charges yet" as an empty state, not an error.

Multi-user sharing UI (inviting/managing `SiteAccess` viewers) is explicitly deferred —
not part of v1.

## Auth token handling (frontend)

- `setup_token` / `mfa_token`: held only in memory for the duration of their specific
  flow (login → setup/verify → discarded). Never persisted.
- `access_token`: stored in `localStorage`, attached via a fetch wrapper's
  `Authorization: Bearer` header to every Core API request.
- A `401` response from any authenticated request clears the stored `access_token` and
  redirects to `/login`.
- There is no refresh-token mechanism in Core API (a single access token valid for
  `jwt_expiry_hours`, per existing config) — accepted for v1; expiry means re-login, not
  a silent refresh. Revisit only if session length in practice proves annoying.

## Error handling

- Core API already returns structured `{code, message, detail}` errors. Form submissions
  map `detail` to field-level errors where applicable; other failures show a dismissible
  banner.
- TanStack Query's loading/error states drive skeleton/error UI on data views (dashboard
  tiles, charts, lists).
- Network/5xx failures on polled live-tile requests fail silently into a "stale" badge on
  the tile rather than an intrusive error, since a single missed 60s poll isn't
  actionable by the user.

## Testing

- Vitest + React Testing Library for component/unit tests (forms, auth flow branching,
  chart data transforms).
- No end-to-end (Playwright) tests for v1 — YAGNI at this scale (single user, personal
  deployment); add later if the app and its regression surface grow.
- Core API additions (register, TOTP endpoints, password reset, `PATCH /sites/{id}`,
  `EmailSender`) get the same pytest-based testing treatment as the rest of Core API,
  including a fake `EmailSender` for tests (no real SMTP in the test suite).

## Explicitly deferred / out of scope for this sub-project

- Self-service multi-user sharing UI (`SiteAccess` invite/manage) — data model and API
  already support it; just no UI screen yet.
- Transactional-email-API `EmailSender` backend (Postmark/SES/etc.) — SMTP only for v1;
  add a provider-specific backend once a provider is actually chosen.
- Refresh-token / session-revocation mechanism — a password reset or suspected compromise
  does not invalidate already-issued access tokens before their natural expiry.
- Public marketing/signup site for a hypothetical future SaaS offering — not this
  sub-project; would be a separate deployable if it ever happens.
- End-to-end (Playwright) browser tests.
- `DemandInterval`/`DemandCharge` background derivation jobs — still not built (carried
  forward from sub-project 1); the Billing screen's demand-charges view is read-only
  against whatever Core API already returns, which may be empty.
- SPF/DKIM/rDNS configuration for outbound mail deliverability — operational/DNS setup
  outside the mail-relay container's own code.
