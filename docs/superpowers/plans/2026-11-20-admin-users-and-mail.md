# Admin Settings, Users and Mail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give app-wide admins an Admin Settings page with a Users tab (admin flag, manager role, user edits) and an Email tab (provider settings, outbox, a new `mail-actor` that sends through smtp2go).

**Architecture:** Core-api gains `users.is_admin`, a `manager` site role, admin routers, two mail tables and an outbox email sender. A new `mail-actor` container declares providers, fetches the active provider's settings and claims queued mail from core-api. The webapp adds an `/admin` page with tabs.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic (SQLite), pytest; React, react-router, TanStack Query, vitest; httpx in the actor.

**Spec:** [`2026-11-20-admin-users-design.md`](../specs/2026-11-20-admin-users-design.md) and [`2026-11-20-admin-mail-settings-design.md`](../specs/2026-11-20-admin-mail-settings-design.md)

## Global Constraints

- Run core-api tests with `cd core-api && uv run pytest <path> -v`, webapp tests with `cd webapp && npx vitest run <path>`, lint with `npm run lint`.
- Current Alembic head is `c4e6a8b0d2f3`. Each migration sets `down_revision` to the previous task's revision. Test through the real migrations (`conftest.py` does).
- Any new required env var is added to `core-api/tests/conftest.py` too.
- Secrets: credentials use `EncryptedJSON`, message bodies use `EncryptedString`. No secret is returned by an API, logged, or written to docs. Admin routes return 403 `admin_required`.
- Error bodies use `AppError(status, code, message)`.
- Prose (comments, docs, commits) follows `.claude/rules/avoid-ai-writing.md`.
- Commit messages end with the `Co-Authored-By` trailer from the session's attribution reminder.
- After the last task of each slice: run `/simplify`, run the `documentation` agent, run the docs link and anchor check.
- Webapp screens reuse `formStyles`, `SubmitButton`, `QueryError`, `menuItemClass` and the confirm-dialog style already in the repo.

## Review Focus

- A non-admin calls any `/admin/...` route, with or without a token: 403 `admin_required` or 401, never data.
- The last admin clears their own flag, or two admins clear each other at once: 409 `last_admin`, one admin remains.
- A manager tries to add a manager or touch the owner: 403 `forbidden`.
- A provider's `PUT` with a blank secret keeps the stored secret, and no response or log line contains it.
- Two actor processes claim at once: no message is sent twice; a claim older than 2 minutes is reclaimable.

---

## Slice 1: Foundation

### Task 1: Admin flag and `require_admin`

**Files:**
- Modify: `core-api/core_api/models/identity.py` (User), `core_api/schemas.py` (`UserMeOut`), `core_api/deps.py`, `core_api/admin_user.py`, `core_api/routers/account.py` (build of `UserMeOut`)
- Create: `core-api/alembic/versions/<new>_add_users_is_admin.py`
- Test: `core-api/tests/test_admin_flag.py`

**Interfaces:**
- Produces: `User.is_admin: Mapped[bool]` (not null, default false, server default false); `UserMeOut.is_admin: bool`; `deps.require_admin(current_user: User = Depends(get_current_user)) -> User`.

- [ ] **Step 1: Write failing tests** in `test_admin_flag.py`: `test_me_returns_is_admin_false_by_default`; `test_migration_sets_lowest_id_user_admin` (build a db at the previous head with two users, upgrade, assert only the lowest id has `is_admin`); `test_bootstrap_admin_is_admin` (run `ensure_admin_user`, assert `is_admin`); `test_require_admin_rejects_non_admin` (mount a throwaway route using the dependency on `app`; non-admin gets 403 code `admin_required`, admin gets 200).
- [ ] **Step 2: Run** `uv run pytest tests/test_admin_flag.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the column, a migration that adds it with `server_default=false` and then sets it true on the user with the lowest `id`, `ensure_admin_user` creating the user with `is_admin=True`, `require_admin`, and the `/users/me` field.
- [ ] **Step 4: Run** the test file plus `tests/test_account.py tests/test_bootstrap.py tests/test_admin_user.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core-api): add the admin flag and require_admin`.

### Task 2: `my_role` on sites

**Files:**
- Modify: `core-api/core_api/models/identity.py`, `core_api/deps.py`, `core_api/schemas.py` (`SiteOut`), `core_api/routers/sites.py`
- Test: `core-api/tests/test_sites.py`

**Interfaces:**
- Produces: `SiteRole(str, Enum)` with `OWNER="owner"`, `MANAGER="manager"`, `VIEWER="viewer"`; `deps.site_role_for(db: Session, site: Site, user_id: int) -> SiteRole | None`; `SiteOut.my_role: SiteRole`. Route handlers build `SiteOut` through `site_out(db, site, user_id) -> SiteOut` in `routers/sites.py`.

- [ ] **Step 1: Write failing tests** `test_site_out_my_role_owner` and `test_site_out_my_role_viewer` (list and get both return the right value).
- [ ] **Step 2: Run** `uv run pytest tests/test_sites.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the enum, `site_role_for` (owner when `owner_user_id` matches, else the `site_access` role mapped to `SiteRole`, else None), and `site_out`. Every route that returns `SiteOut` uses it.
- [ ] **Step 4: Run** the full `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core-api): return my_role on sites`.

### Task 3: Admin menu item, guard and page shell

**Files:**
- Modify: `webapp/src/account/useAccount.ts` (`Me.is_admin: boolean`), `webapp/src/sites/useSites.ts` (`Site.my_role: "owner" | "manager" | "viewer"`), `webapp/src/layout/Header.tsx`, `webapp/src/App.tsx`
- Create: `webapp/src/admin/AdminRoute.tsx`, `webapp/src/admin/AdminSettingsPage.tsx`, `webapp/src/admin/tabs.ts`
- Test: `webapp/src/layout/Header.test.tsx`, `webapp/src/admin/AdminRoute.test.tsx`

**Interfaces:**
- Produces: route `/admin/:tab?` rendering `AdminSettingsPage`; `AdminRoute` redirects to `/` unless `me.is_admin`; `ADMIN_TABS` in `tabs.ts` (same shape as `settings/tabs.ts`), empty until Task 9 adds Users.

- [ ] **Step 1: Write failing tests:** Header shows "Admin Settings" when `is_admin` is true and not when false, between "Site settings" and "Help"; `AdminRoute` renders its child for an admin and navigates to `/` for others (and while `me` has not loaded, renders nothing).
- [ ] **Step 2: Run** `npx vitest run src/layout/Header.test.tsx src/admin`. Expected: FAIL.
- [ ] **Step 3: Implement** the `NavLink` to `/admin` using `menuItemClass`, the guard, the page shell following `SettingsPage` and `SettingsShell` for heading and tab list, and the route inside `ProtectedLayout`.
- [ ] **Step 4: Run** the tests, then `npm run lint` and `npm run build`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(webapp): add the Admin Settings menu item and page shell`.

---

## Slice 2: Users

### Task 4: Manager role and permission dependency

**Files:**
- Modify: `core-api/core_api/models/identity.py` (`SiteAccessRole.MANAGER`), `core_api/deps.py`, `core_api/routers/{actors,billing,invites,local_rates,sites,sources}.py`, `core_api/routers/collector_config.py`
- Create: `core-api/alembic/versions/<new>_add_manager_site_role.py`
- Test: `core-api/tests/test_manager_role.py`

**Interfaces:**
- Consumes: `SiteRole`, `site_role_for` (Task 2).
- Produces: `deps.require_site_manager(site_id: int, current_user, db) -> Site` (owner or manager, else 404 for no access and 403 `forbidden` for a viewer); `require_site_owner` unchanged. `site_role_for` returns `SiteRole.MANAGER` for a manager grant.

- [ ] **Step 1: Write failing tests** in `test_manager_role.py`: a manager can `PATCH` the site, create a source and read billing (200/201); a viewer gets 403 on the same; a manager gets 403 `forbidden` on creating an invite for a manager and on any route left owner-only; `my_role` is `manager`; the migration accepts `manager` and still rejects `admin` in the `role` column.
- [ ] **Step 2: Run** `uv run pytest tests/test_manager_role.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the enum value, a migration widening `ck_site_access_role` to `role IN ('viewer','manager')` (batch alter), and `require_site_manager`. Replace `require_site_owner` with it on every route except those that add, change or remove a manager or change `owner_user_id`; these stay owner-only. In `invites.py`, `sources.py` and `collector_config.py` replace direct `owner_user_id` comparisons with `site_role_for(...) in (OWNER, MANAGER)` where the check is about managing. Actor emails keep using `site_owner`.
- [ ] **Step 4: Run** the full `uv run pytest -q`. Expected: PASS, including existing owner tests.
- [ ] **Step 5: Commit** `feat(core-api): add the manager site role`.

### Task 5: Webapp uses `my_role`

**Files:**
- Modify: `webapp/src/settings/useSiteAccess.ts`, `webapp/src/settings/SharingTab.tsx`, `webapp/src/actors/*` and `webapp/src/sites/*` where `owner_user_id` is compared
- Test: `webapp/src/settings/SharingTab.test.tsx`, `webapp/src/settings/SettingsPage.test.tsx`

**Interfaces:**
- Consumes: `Site.my_role` (Task 3).
- Produces: `useSiteAccess(site)` returns `readOnly = site.my_role === "viewer"`, with the note "Only the owner or a manager can change this site's settings."

- [ ] **Step 1: Write failing tests:** a manager sees editable settings; a viewer sees the note; the Sharing tab invite controls show for an owner or manager.
- [ ] **Step 2: Run** `npx vitest run src/settings src/actors`. Expected: FAIL.
- [ ] **Step 3: Implement** by replacing every `owner_user_id === me.id` check with `my_role`. Keep `owner_user_id` only where the owner's identity is shown.
- [ ] **Step 4: Run** `npx vitest run`, `npm run lint`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(webapp): treat managers like owners in site settings`.

### Task 6: Admin users, read endpoints

**Files:**
- Create: `core-api/core_api/admin_schemas.py`, `core_api/admin_users.py`, `core_api/routers/admin_users.py`
- Modify: `core_api/main.py` (include the router)
- Test: `core-api/tests/test_admin_users.py`

**Interfaces:**
- Produces: `AdminUserSite(site_id: int, site_name: str, role: SiteRole)`; `AdminUserOut(id, email, display_name, is_admin, has_avatar, totp_enabled, email_confirmed, has_amber_api_key, sites: list[AdminUserSite])`; `admin_users.list_admin_users(db) -> list[AdminUserOut]` (two queries, joined in Python); `admin_users.admin_user_out(db, user) -> AdminUserOut`; `GET /api/v1/admin/users` and `GET /api/v1/admin/users/{user_id}` (404 `not_found`). The router has `dependencies=[Depends(require_admin)]` and prefix `/admin/users`.

- [ ] **Step 1: Write failing tests:** list includes an owned site as `owner`, a manager grant and a viewer grant; no secret fields appear (`amber_api_key`, `password_hash`, `totp_secret` absent from JSON); non-admin gets 403 `admin_required`; no token gets 401; unknown id 404.
- [ ] **Step 2: Run** `uv run pytest tests/test_admin_users.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the schemas, module and router.
- [ ] **Step 4: Run** the file. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core-api): list users for admins`.

### Task 7: Admin users, details, access and admin flag

**Files:**
- Modify: `core-api/core_api/routers/admin_users.py`, `core_api/admin_users.py`
- Test: `core-api/tests/test_admin_users_write.py`

**Interfaces:**
- Consumes: Task 6 types.
- Produces: `PATCH /{user_id}` body `AdminUserPatch(display_name?: str | None, email?: str, is_admin?: bool)`; `DELETE /{user_id}/avatar`; `PUT /{user_id}/amber-key` body `{amber_api_key: str}` and `DELETE`; `PUT /{user_id}/sites/{site_id}` body `{role: "manager" | "viewer"}`; `DELETE /{user_id}/sites/{site_id}`. All return `AdminUserOut` except the deletes of avatar and key, which return it too. Errors: 409 `email_taken`, 409 `last_admin`, 409 `is_owner`, 404 `not_found` for user or site. `log_admin_action(admin: User, target_user_id: int, action: str) -> None` writes one `logging` line with ids and the action name only.

- [ ] **Step 1: Write failing tests:** email change lowercases and rejects a duplicate; display name set and cleared; `is_admin` grant works; clearing the only admin's flag is 409 `last_admin`; granting manager then viewer changes the row, not duplicates it; granting a role on the user's own site is 409 `is_owner`; removing a grant works; setting the Amber key returns `has_amber_api_key: true` and no key text in the response or in `caplog`; each write emits one log line with no values.
- [ ] **Step 2: Run** the file. Expected: FAIL.
- [ ] **Step 3: Implement.** The last-admin check and the update run in one transaction using a conditional `UPDATE ... WHERE (SELECT count(*) FROM users WHERE is_admin) > 1`, so two concurrent demotions cannot both succeed. Reuse the avatar-removal and Amber-key code from `routers/account.py`, extracted into shared helpers rather than copied.
- [ ] **Step 4: Run** the file and the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core-api): let admins edit users and site access`.

### Task 8: Admin password reset and TOTP reset

**Files:**
- Modify: `core-api/core_api/routers/admin_users.py`, `core_api/routers/password_reset.py` (extract `issue_password_reset(db: Session, user: User, email_sender: EmailSender) -> None`)
- Test: `core-api/tests/test_admin_users_security.py`, `tests/test_password_reset.py`

**Interfaces:**
- Produces: `POST /{user_id}/password-reset` (204) and `POST /{user_id}/totp/reset` (returns `AdminUserOut`).

- [ ] **Step 1: Write failing tests:** the reset email reaches the user's address through the fake sender with a working link, and the stored token is hashed; TOTP reset clears `totp_secret`, `pending_totp_secret`, `last_totp_step` and sets `totp_enabled` false; after a reset, `POST /auth/login` for that user returns the TOTP setup step (the existing setup-token path), not an error.
- [ ] **Step 2: Run** both files. Expected: FAIL.
- [ ] **Step 3: Implement** the extraction without changing the public reset route's behavior, then the two routes. If login does not reach setup for a user with `totp_enabled` false, fix `routers/auth.py` in this task and add the test.
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core-api): admin password reset and two-factor reset`.

### Task 9: Users tab

**Files:**
- Create: `webapp/src/admin/useAdminUsers.ts`, `webapp/src/admin/UsersTab.tsx`
- Modify: `webapp/src/admin/tabs.ts` (add `{ id: "users", label: "Users" }`), `webapp/src/admin/AdminSettingsPage.tsx`
- Test: `webapp/src/admin/UsersTab.test.tsx`

**Interfaces:**
- Produces: `AdminUser` and `AdminUserSite` TypeScript types matching Task 6; `useAdminUsers()` (query key `["admin","users"]`); `useAdminUser(id: number)`.

- [ ] **Step 1: Write failing tests:** rows show name, email, an Admin badge, **Manages** chips (owner and manager) and **Views** chips; the search box filters by name or email; clicking a row navigates to `/admin/users/:id`; a failed load shows `QueryError`.
- [ ] **Step 2: Run** `npx vitest run src/admin`. Expected: FAIL.
- [ ] **Step 3: Implement** the tab using the existing table and chip styles (`SiteChips.tsx`).
- [ ] **Step 4: Run** tests, lint, build. Expected: PASS.
- [ ] **Step 5: Commit** `feat(webapp): add the admin Users tab`.

### Task 10: User detail page

**Files:**
- Create: `webapp/src/admin/UserDetailPage.tsx`, `webapp/src/admin/useAdminUserMutations.ts`
- Modify: `webapp/src/App.tsx` (route `/admin/users/:userId` inside `AdminRoute`)
- Test: `webapp/src/admin/UserDetailPage.test.tsx`

**Interfaces:**
- Consumes: Task 7 and 8 endpoints, `useAdminUser`.
- Produces: sections **Details**, **Site access**, **Security**, **Administrator**, as in the spec.

- [ ] **Step 1: Write failing tests:** saving a display name and email sends one `PATCH`; the Add to site selector lists only sites the user is not on and sends `PUT` with the chosen role; the owner's row has no selector or Remove; changing a role and Remove call the right endpoints; the Reset two-factor and Remove actions show a confirm dialog first; the Amber key shows "set" and never a value; the Administrator toggle is disabled with an explanation when `last_admin` applies (409 shown as the message).
- [ ] **Step 2: Run** `npx vitest run src/admin/UserDetailPage.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** following `AccountPage.tsx` for layout and its dirty-state and submit-guard hooks. The list of all sites comes from the user list data already loaded, deduplicated by `site_id`.
- [ ] **Step 4: Run** `npx vitest run`, lint, build. Expected: PASS.
- [ ] **Step 5: Commit** `feat(webapp): add the admin user detail page`.

### Task 11: Docs for slices 1 and 2

**Files:**
- Modify: `docs/core-api.md`, `docs/data-model.md`, `docs/architecture.md`, `docs/webapp.md`, `webapp/src/docs/index.md`, `09-account-settings.md` (links), `10-troubleshooting.md`, `11-glossary.md`
- Create: `webapp/src/docs/12-admin-users.md` (screenshots follow the process in the recent docs commits)

- [ ] **Step 1:** Run the `documentation` agent with the diff of Tasks 1 to 10.
- [ ] **Step 2:** Run the link and anchor check. Expected: no broken links.
- [ ] **Step 3: Commit** `docs: describe admin users and the manager role`.

---

## Slice 3: Email

### Task 12: Mail tables

**Files:**
- Create: `core-api/core_api/models/mail.py`, `core-api/alembic/versions/<new>_add_mail_tables.py`
- Modify: `core-api/core_api/models/__init__.py`
- Test: `core-api/tests/test_mail_models.py`

**Interfaces:**
- Produces: `MailProvider` (`provider` str PK, `label`, `fields` JSON list of `{name, label, secret, required}`, `settings: EncryptedJSON | None`, `is_active: bool`, `declared_at`, `updated_at`; partial unique index `ix_mail_providers_one_active` on `is_active` where true). `OutboxStatus(str, Enum)`: `PENDING`, `SENDING`, `SENT`, `FAILED`. `EmailOutbox` (`id`, `to_address`, `subject`, `body: EncryptedString`, `status` with check constraint, `attempts`, `last_error`, `created_at`, `claimed_at`, `next_attempt_at`, `finished_at`).

- [ ] **Step 1: Write failing tests:** two active providers raise `IntegrityError`; the raw `settings` and `body` columns in the database do not contain the plaintext; an invalid status violates the check constraint.
- [ ] **Step 2: Run** the file. Expected: FAIL.
- [ ] **Step 3: Implement** models and migration; add both to `test_schema_constraints.py`'s expectations if it enumerates tables.
- [ ] **Step 4: Run** the file and `tests/test_schema_constraints.py tests/test_encrypted_storage.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core-api): add mail provider and outbox tables`.

### Task 13: Outbox logic and actor endpoints

**Files:**
- Create: `core-api/core_api/outbox.py`, `core-api/core_api/routers/actor_mail.py`
- Modify: `core_api/main.py`, `core_api/deps.py`
- Test: `core-api/tests/test_outbox.py`, `tests/test_actor_mail.py`

**Interfaces:**
- Produces: `outbox.enqueue_email(db, to: str, subject: str, body: str) -> EmailOutbox`; `outbox.claim_outbox(db, limit: int, now: datetime) -> list[EmailOutbox]` (one conditional `UPDATE` per row; claims `pending` rows with `next_attempt_at <= now` and `sending` rows with `claimed_at` older than 2 minutes); `outbox.finish_outbox(db, id: int, outcome: Literal["sent","failed"], error: str | None, now) -> None` (`failed` returns to `pending` with `next_attempt_at = now + 30s * 2**attempts` until `attempts == 5`, then `failed`; `sent` and the final `failed` set `body` to `""` and `finished_at`); `outbox.purge_outbox(db, now) -> int` (finished rows older than 30 days). Router prefix `/actor/mail` requires `X-Actor-Key` and `X-Actor-Provider: mail` (401 `invalid_actor_key` otherwise): `PUT /providers` body `list[ProviderDeclaration(provider, label, fields)]`; `GET /active` returns `{provider, settings}` or 204; `POST /outbox/claim?limit=` returns `list[{id, to_address, subject, body}]`; `PATCH /outbox/{id}` body `{outcome, error?}` returns 204.

- [ ] **Step 1: Write failing tests:** declare creates then updates a row without touching `settings`; `GET /active` returns decrypted settings and 204 with no active provider; a claim marks rows `sending` and a second claim returns none; a row claimed 3 minutes ago is claimable again; a failure schedules a retry with the backoff and the fifth failure ends `failed` with body cleared; `sent` clears the body; wrong key or wrong provider header is 401; `purge_outbox` removes only finished rows older than 30 days.
- [ ] **Step 2: Run** both files. Expected: FAIL.
- [ ] **Step 3: Implement.** Error text stored in `last_error` is truncated to 500 characters.
- [ ] **Step 4: Run** both files. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core-api): add the outbox and the mail actor endpoints`.

### Task 14: Admin mail endpoints

**Files:**
- Create: `core-api/core_api/routers/admin_mail.py`
- Modify: `core_api/main.py`
- Test: `core-api/tests/test_admin_mail.py`

**Interfaces:**
- Consumes: `MailProvider`, `outbox.enqueue_email`, `require_admin`.
- Produces: `GET /admin/mail/providers` returns `list[{provider, label, available, is_active, fields: [{name, label, secret, required, value: str | None, is_set: bool}]}]` where `value` is null for secret fields; `PUT /providers/{provider}` body `{settings: dict[str, str]}` (a blank or missing secret keeps the stored value; unknown names 422 `unknown_field`; a missing required field 422 `field_required` when saving; 404 for an unknown provider); `POST /providers/{provider}/activate` (409 `settings_incomplete`) and `/deactivate`; `POST /test` (409 `no_active_provider`, otherwise queues one email to the admin's address); `GET /outbox/summary` returns `{pending, sent_24h, failed_24h, last_error}`.

- [ ] **Step 1: Write failing tests** for each rule above, plus: a response never contains a secret value (assert on the full JSON text), and activating one provider deactivates the other.
- [ ] **Step 2: Run** the file. Expected: FAIL.
- [ ] **Step 3: Implement.** Activation swaps the active row in one transaction to respect the partial unique index.
- [ ] **Step 4: Run** the file. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core-api): add admin mail provider endpoints`.

### Task 15: Outbox email sender and cleanup job

**Files:**
- Modify: `core-api/core_api/email_sender.py`, `core_api/deps.py` (`get_email_sender`), `core_api/scheduler.py`
- Test: `core-api/tests/test_email_sender.py`, `tests/test_scheduler.py`

**Interfaces:**
- Produces: `OutboxEmailSender(db: Session)` implementing `EmailSender.send(to, subject, body)` by calling `enqueue_email`; `get_email_sender` returns it when a provider has `is_active`, and the application's configured sender otherwise; a scheduler job `mail_outbox_cleanup` runs `purge_outbox` daily.

- [ ] **Step 1: Write failing tests:** with an active provider, a password-reset request writes an outbox row and the fake sender records nothing; with none, the fake sender records it and no row exists; the job is registered and calls `purge_outbox`.
- [ ] **Step 2: Run** both files plus `tests/test_password_reset.py tests/test_invites.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(core-api): queue email through the outbox when a provider is active`.

### Task 16: `mail-actor` container

**Files:**
- Create: `mail-actor/{pyproject.toml,Dockerfile,.env.example,.dockerignore}`, `mail-actor/mail_actor/{__init__,config,core_api_client,worker,main}.py`, `mail_actor/providers/{__init__,base,smtp2go}.py`, `mail-actor/tests/{fake_core.py,test_worker.py,test_smtp2go.py,test_core_api_client.py}`

Model the layout, config and client on `actron-neo-actor/` (`CoreApiClient`, `CoreApiError`, env `CORE_API_URL` and `ACTOR_KEY`, headers `X-Actor-Key` and `X-Actor-Provider: mail`).

**Interfaces:**
- Produces: `providers.base.OutboxMessage(id: int, to_address: str, subject: str, body: str)`; `providers.base.SendError(Exception)` with `retryable: bool`; `MailProvider` Protocol with `KEY: str`, `LABEL: str`, `FIELDS: list[Field]`, `send(settings: dict[str, str], message: OutboxMessage, http: httpx.Client) -> None`; `providers.PROVIDERS: list[MailProvider]`; `CoreApiClient.declare_providers(list)`, `.get_active() -> ActiveProvider | None`, `.claim(limit: int) -> list[OutboxMessage]`, `.report(id: int, outcome: str, error: str | None)`; `worker.run_once(client, providers, http) -> int` (messages handled).
- smtp2go: `KEY="smtp2go"`, fields `api_key` (secret, required) and `from_address` (required); `send` posts JSON `{sender, to: [to_address], subject, text_body}` to `https://api.smtp2go.com/v3/email/send` with header `X-Smtp2go-Api-Key`.

- [ ] **Step 1: Write failing tests** with `httpx.MockTransport`: smtp2go sends the exact request above and returns on `data.succeeded == 1`; a 5xx or timeout raises `SendError(retryable=True)`; a 401 or `succeeded == 0` raises `SendError(retryable=False)`; `run_once` reports `sent` per message, reports `failed` with the error text for a `SendError` and never includes the API key in the error; with no active provider `run_once` claims nothing; the active provider is cached for 30 seconds and refetched after an auth failure; startup declares every provider in `PROVIDERS`.
- [ ] **Step 2: Confirm against the smtp2go docs** (<https://developers.smtp2go.com/docs/getting-started>) the endpoint, key header and response shape. Adjust the code and tests to the docs if they differ.
- [ ] **Step 3: Run** `cd mail-actor && uv run pytest -v`. Expected: FAIL.
- [ ] **Step 4: Implement.** `main.py` loops every 5 seconds, survives `CoreApiError`, and logs without settings or bodies.
- [ ] **Step 5: Run** the tests. Expected: PASS.
- [ ] **Step 6: Commit** `feat(mail-actor): add the mail actor with smtp2go`.

### Task 17: Deploy wiring and service status

**Files:**
- Modify: `docker-compose.yml`, `docker-compose.prod.yml`, `core-api/core_api/service_status.py` (add `"mail-actor"` to the `actor_key` set), `core-api/tests/test_service_status.py`, `mail-actor` reporting call (copy the actron actor's `service_report.py` pattern)
- Test: `core-api/tests/test_service_status.py`, `mail-actor/tests/test_service_report.py`

- [ ] **Step 1: Write failing tests:** the actor key may report for `mail-actor`; the mail actor posts its commit once a minute.
- [ ] **Step 2: Run** both. Expected: FAIL.
- [ ] **Step 3: Implement** the compose service on the `app` network with `CORE_API_URL` and `ACTOR_KEY` only, and the status change.
- [ ] **Step 4: Run** both, then `docker compose config -q`. Expected: PASS and no output.
- [ ] **Step 5: Commit** `feat(deploy): run the mail actor`.

### Task 18: Email tab

**Files:**
- Create: `webapp/src/admin/useAdminMail.ts`, `webapp/src/admin/EmailTab.tsx`
- Modify: `webapp/src/admin/tabs.ts` (add Email first), `webapp/src/admin/AdminSettingsPage.tsx`
- Test: `webapp/src/admin/EmailTab.test.tsx`

**Interfaces:**
- Consumes: Task 14 endpoints.

- [ ] **Step 1: Write failing tests:** with no providers the tab says the mail actor has not reported and names the `mail-actor` container; selecting a provider draws its fields; secret fields show "set" with Change; saving sends only changed fields; Activate and Deactivate work; Send test email shows success and the `no_active_provider` error text; the summary line shows pending, sent and failed counts and the last error.
- [ ] **Step 2: Run** `npx vitest run src/admin/EmailTab.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** with the Account page's secret-field pattern for the Amber key.
- [ ] **Step 4: Run** `npx vitest run`, lint, build. Expected: PASS.
- [ ] **Step 5: Commit** `feat(webapp): add the admin Email tab`.

### Task 19: Docs for slice 3

**Files:**
- Create: `docs/mail.md`, `webapp/src/docs/13-admin-email.md`
- Modify: `docs/architecture.md` (the relay and "actors never email" statements), `docs/actors.md`, `docs/core-api.md`, `docs/data-model.md`, `docs/development.md`, `docs/README.md`, `webapp/src/docs/index.md`, `10-troubleshooting.md`, `11-glossary.md`, `README.md`, `.env.example` files if a variable changed

- [ ] **Step 1:** Run the `documentation` agent with the diff of Tasks 12 to 18. `docs/mail.md` includes "adding a provider": one module in `mail-actor/mail_actor/providers/` and one entry in `PROVIDERS`.
- [ ] **Step 2:** Run the link and anchor check. Expected: no broken links.
- [ ] **Step 3: Commit** `docs: describe the mail actor and Admin Settings email`.
