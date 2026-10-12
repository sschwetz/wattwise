# Account Settings — Design Spec

Date: 2026-10-08
Status: Approved for planning

## Purpose

Let a logged-in user change their password, change their email, and re-enroll their
authenticator (lost/replaced device) from the Account page. None of this exists today —
Core API only supports setting the password at registration or via the unauthenticated
password-reset-by-email flow, and `totp_enabled` is a one-way switch set once at first
login. The frontend's `AccountPage` currently has only the Amber API key section.

This is a small addition to the existing Core API + webapp sub-project
(`docs/superpowers/specs/2026-10-01-frontend-webapp-design.md`), not a new sub-project.

## Design constraints carried over from the auth system

- TOTP is mandatory in this system (every user is forced through enrollment on first
  login — see `docs/superpowers/plans/2026-10-01-core-api-auth-mfa-password-reset.md`).
  Nothing here adds a way to turn 2FA off. "Manage TOTP" means re-enrollment (replacing
  the secret and recovery codes), not disablement.
- Every mutation here is step-up-gated: the caller must present their current password
  **and** a live TOTP code, even though they already hold a valid `access_token`. A bare
  `access_token` is not sufficient on its own for any of these three actions, since a
  hijacked or shoulder-surfed session token should not be enough to take over the
  account's credentials.
- Matches existing structured-error (`{code, message, detail}`), SHA-256-for-random-tokens
  (`hash_token`), and TanStack Query hook (`webapp/src/account/useAccount.ts`) conventions
  already established in this codebase.

## Data model changes (Core API)

- `User` gains `pending_totp_secret: str | None` (nullable `String(64)`, same shape as
  `totp_secret`). Holds a newly generated secret between `totp/reenroll/start` and
  `totp/reenroll/confirm`. The *live* `totp_secret` is left untouched until confirm
  succeeds, so an abandoned re-enroll never breaks the user's existing authenticator app.
- New `EmailChangeToken` model, same shape and treatment as `PasswordResetToken`: `id`,
  `user_id` (FK), `new_email` (`String(320)`), `token_hash` (`String(64)`, SHA-256 via
  the existing `security.hash_token`), `expires_at` (1 hour from issuance), `used_at`
  (nullable).

## API contract additions

All under the existing `account.py` router (`/api/v1/users/me`) unless noted otherwise.
All step-up checks (`current_password`, `totp_code`) use the existing `verify_password`
and `verify_totp_code` helpers and raise the existing `401 invalid_credentials` /
`401 invalid_code` structured errors on mismatch — no new error codes for these checks.

### Change password

- `POST /users/me/password` `{current_password, new_password, totp_code}` (auth:
  `access_token`) — verifies both current values, sets `password_hash =
  hash_password(new_password)`, `204`. No session/token invalidation (matches the
  existing "no refresh-token / no revocation" stance already accepted in the frontend
  webapp spec — this access token and any others remain valid until their natural
  expiry).

### Change email

- `POST /users/me/email/change/request` `{new_email, current_password, totp_code}`
  (auth: `access_token`) — verifies both current values; `409 email_taken` if
  `new_email` already belongs to another user (same check-via-insert-or-lookup as
  `register`, see Review Focus below); otherwise invalidates any outstanding
  `EmailChangeToken` for this user (mirrors `password_reset._invalidate_outstanding_tokens`),
  creates a new one, and emails a confirmation link to the **new** address (not the
  current one) via the existing `EmailSender`:
  `{frontend_base_url}/confirm-email-change?token=...`. Returns `200` with a generic
  confirmation message once the token is issued — unlike password-reset-request, this
  response does not need to be identical on the not-taken-but-request-failed path, since
  the caller is already an authenticated owner of the account, not an anonymous caller
  being guarded against account-enumeration.
- `POST /users/me/email/change/confirm` `{token}` (no auth — reached by clicking an
  emailed link, same shape as `password-reset/confirm`) — looks up the token by
  `hash_token`, `400 invalid_token` if unknown/used/expired (uniform message, same
  reasoning as `password_reset._load_valid_token`), else sets `user.email = new_email`,
  marks the token used, invalidates any other outstanding tokens for that user, `204`.

### Re-enroll TOTP

- `POST /users/me/totp/reenroll/start` `{current_password, totp_code}` (auth:
  `access_token`) — verifies both against the user's **current** `totp_secret`;
  generates a new secret via the existing `generate_totp_secret()`, stores it in
  `pending_totp_secret` (does **not** touch `totp_secret` yet), returns
  `{secret, otpauth_uri}` (same shape as `TotpSetupStartOut`, built from
  `totp_provisioning_uri`).
- `POST /users/me/totp/reenroll/confirm` `{code}` (auth: `access_token`) — `400
  invalid_state` if `pending_totp_secret` is `None` (confirm called without a prior
  start); verifies `code` against `pending_totp_secret` via `verify_totp_code`, `401
  invalid_code` on mismatch; on success, promotes `pending_totp_secret` to
  `totp_secret`, clears `pending_totp_secret`, deletes/invalidates existing unused
  `RecoveryCode` rows for the user and generates 10 new ones (same
  `generate_recovery_codes()` + `hash_token` pattern as the original
  `totp.confirm_setup`), returns `{recovery_codes}` (plaintext, once, never retrievable
  again — identical guarantee to initial enrollment).

## Frontend

- `AccountPage` gains three new sections below the existing Amber API key section:
  - **Change password** — current password, new password, new password confirmation,
    TOTP code. Client-side checks new password == confirmation before submit; server
    error surfaces as a dismissible banner (existing error-handling convention).
  - **Change email** — shows current email; form for new email + current password +
    TOTP code; on success shows "check your new inbox" message (the generic message
    from `/email/change/request`).
  - **Manage two-factor authentication** — "Replace authenticator" button opens a
    re-enroll flow: step 1 prompts for current password + TOTP code and calls
    `reenroll/start`; step 2 shows the QR code (`qrcode` library, already a dependency
    per `TotpSetupPage`) and a 6-digit input, calling `reenroll/confirm`; step 3 shows
    the 10 new recovery codes with the same "I've saved these" acknowledgment gate
    already built for `TotpSetupPage`. All three steps reuse `TotpSetupPage`'s existing
    QR/code/ack-gate subcomponents rather than duplicating them.
- New route `/confirm-email-change?token=...` → `ConfirmEmailChangePage`, unauthenticated,
  same shape as `ResetPasswordPage`: reads `token` from the query string, calls
  `/email/change/confirm` on mount (or behind a single confirm button — match whichever
  of those two patterns `ResetPasswordPage` already uses), shows success/failure, links
  to `/login` (or the dashboard if already authenticated — match `ResetPasswordPage`'s
  existing post-action redirect).
- `webapp/src/account/useAccount.ts` gains the corresponding TanStack Query mutations
  (`useChangePassword`, `useRequestEmailChange`, `useConfirmEmailChange`,
  `useStartTotpReenroll`, `useConfirmTotpReenroll`), following the existing
  `useUpdateAmberApiKey` shape in that file.

## Error handling

No new error codes beyond what's listed above (`email_taken`, `invalid_token`,
`invalid_code`, `invalid_credentials`, `invalid_state`) — all via the existing
`AppError`/structured-error convention. Form-level validation (passwords matching,
email format) happens client-side before submit; server-side failures map to the
existing dismissible-banner pattern.

## Review focus (carried into the implementation plan)

- Two concurrent `email/change/request` calls for the same `new_email` against two
  different accounts must not both succeed — the loser's `/confirm` should fail
  (`409`-at-request-time via the same insert-and-catch-`IntegrityError` pattern
  `register` uses, not a precheck-then-insert race).
- `totp/reenroll/confirm` called with no prior `start` (`pending_totp_secret is None`)
  must be rejected, not crash on `verify_totp_code(None, code)`.
- `totp/reenroll/start` must not touch the live `totp_secret` — a test should log in
  with the *old* TOTP code after calling `start` but before calling `confirm`, and it
  must still work.
- A `password`/`email/change/request`/`totp/reenroll/*` call with a stale/wrong
  `totp_code` must be rejected even when `current_password` is correct — both checks
  are required, not either-or.
- An `email/change/confirm` token must be rejected a second time (used) and after its
  1-hour expiry, matching `PasswordResetToken`'s existing test coverage shape.

## Testing

- pytest + `FakeEmailSender` for the three new Core API endpoint groups, following the
  existing `test_password_reset.py` / `test_totp.py` structure and fixture conventions.
- Vitest + React Testing Library for the three new `AccountPage` sections and the new
  `ConfirmEmailChangePage`, following existing page-test conventions
  (`AccountPage.test.tsx`, `ResetPasswordPage.test.tsx`).

## Explicitly deferred / out of scope

- Disabling 2FA entirely — contradicts this system's mandatory-TOTP design; not built.
- Notifying the *old* email address when the email changes, or the user when the
  password changes — a nice-to-have security notification, skipped per YAGNI at this
  project's personal/single-user scale; revisit only if this becomes multi-tenant.
- Session/access-token revocation on password change or TOTP re-enrollment — no
  refresh-token or revocation mechanism exists anywhere in this system yet (already
  deferred in the frontend webapp spec); out of scope here too.
- Rate-limiting repeated `email/change/request` calls — not built anywhere else in this
  system either (e.g. `password-reset/request`); same stance here.
