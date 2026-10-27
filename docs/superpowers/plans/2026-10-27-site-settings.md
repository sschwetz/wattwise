# Site Settings, Disabling Sites and Invitations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let owners disable a site, move all per-site configuration into a Settings page, and share sites by emailed invitation.

**Architecture:** `core-api` gets a `sites.enabled` column that the collector paths honour, plus an append-only `site_invites` table whose accept and revoke transitions insert and delete the existing `site_access` rows. The webapp gets a Settings route with site chips and a left tab rail, reusing the existing Sources and Billing sections as tabs.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic (SQLite), pytest; React, react-router, TanStack Query, vitest, Tailwind.

**Spec:** `docs/superpowers/specs/2026-10-27-site-settings-design.md`

## Global Constraints

- Existing sites come out of the migration with `enabled = true`.
- Invite rows are append-only: no code path deletes one; the only update is a status transition with its timestamp.
- Invite statuses: `invited`, `accepted`, `declined`, `revoked`. `declined` and `revoked` are final.
- Invites match on the recipient's lowercased email and need `User.email_confirmed` to be true. No token is stored.
- The invite response and email must not reveal to the sender whether the address has an account.
- At most 20 invites in `invited` status per site. No self-invite. A second invite for an address with an `invited` or `accepted` invite on that site returns 409 `already_invited`.
- Ingestion for a disabled site returns 403 `site_disabled`. `GET /collector/sources` leaves a disabled site's sources out.
- Prose, comments and commit messages follow `.claude/rules/avoid-ai-writing.md`. Commits end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- No `any` in TypeScript. No secrets in plaintext (none are added).
- Backend checks, run from `core-api/`: `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`. Webapp checks, run from `webapp/`: `npm run lint`, `npx tsc -b`, `npm test`.

## Review Focus

- A user invited under a different letter case (`Bob@Example.com` vs `bob@example.com`) still sees and can accept the invite.
- An account whose email is not yet confirmed does not see pending invites.
- Accepting an invite twice, or after it was revoked, fails with 409 and creates no second `site_access` row.
- A collector holding a stale source list gets 403 `site_disabled`, not a stored reading, on every ingest route including live samples and pricing backfill.
- Re-enabling a site returns its sources to `GET /collector/sources` with no other change.
- A user whose only sites are disabled, or who has only pending invites, never lands on a blank page.

## File Structure

Backend (`core-api/`):
- `core_api/models/identity.py`: add `Site.enabled`, `InviteStatus`, `SiteInvite`.
- `core_api/schemas.py`: `SiteOut.enabled`, `SiteUpdate.enabled`, invite schemas.
- `core_api/deps.py`: site-enabled check in `get_source_by_collector_key`.
- `core_api/routers/collector.py`: filter disabled sites.
- `core_api/routers/invites.py` (new): all invite endpoints; one file because they share status-transition helpers.
- `core_api/routers/sites.py`: remove the three `/access` routes.
- `alembic/versions/`: two migrations.

Webapp (`webapp/src/`):
- `sites/useSites.ts`: `enabled` on `Site`, `useEnabledSites`, `useUpdateSite`.
- `sites/useInvites.ts` (new): invite queries and mutations.
- `pages/SitePickerPage.tsx`: pending invitations section, enabled-only list.
- `settings/` (new): `SettingsPage.tsx`, `SiteChips.tsx`, `GeneralTab.tsx`, `SharingTab.tsx`.
- `pages/BillingPage.tsx`: export `TariffsTab` and `BillingTab`.
- `layout/Header.tsx`, `layout/SiteLayout.tsx`, `layout/SiteSwitcher.tsx`, `App.tsx`: menu item, trimmed nav, disabled banner, routes.

---

### Task 1: `sites.enabled` column and toggle

**Files:**
- Modify: `core-api/core_api/models/identity.py` (Site), `core-api/core_api/schemas.py` (SiteOut, SiteUpdate), `core-api/core_api/routers/sites.py` (update_site)
- Create: `core-api/alembic/versions/<new>_add_sites_enabled.py` (down_revision `c7e2a9d4b1f8`)
- Test: `core-api/tests/test_sites.py`

**Interfaces:**
- Produces: `Site.enabled: Mapped[bool]` (not null, default true); `SiteOut.enabled: bool`; `SiteUpdate.enabled: bool | None`.

- [ ] **Step 1: Write failing tests in `tests/test_sites.py`**
  - `test_new_site_is_enabled`: create a site, `GET /sites/{id}` returns `enabled is True`.
  - `test_owner_can_disable_and_reenable_site`: `PATCH {"enabled": false}` returns 200 and `enabled false`; `PATCH {"enabled": true}` restores it.
  - `test_viewer_cannot_change_enabled`: viewer (a `SiteAccess` row inserted directly) gets 403 on the PATCH.
  - `test_list_sites_includes_disabled_sites_with_flag`: disabled site still in `GET /sites` with `enabled false`.
- [ ] **Step 2:** `uv run pytest tests/test_sites.py -q` fails (no `enabled` field).
- [ ] **Step 3:** Add the column, schema fields and `update_site` handling; write the migration with a server default of true so existing rows are enabled, then check `uv run alembic upgrade head` on a scratch DB.
- [ ] **Step 4:** Run the file again; all pass.
- [ ] **Step 5:** `git add -A core-api && git commit -m "feat(core-api): add sites.enabled"`

### Task 2: Disabled sites stop collecting

**Files:**
- Modify: `core-api/core_api/deps.py:get_source_by_collector_key`, `core-api/core_api/routers/collector.py:list_collector_sources`
- Test: `core-api/tests/test_collector_disabled_site.py` (new)

**Interfaces:**
- Consumes: `Site.enabled` from Task 1.
- Produces: 403 `site_disabled` from every dependency that uses `get_source_by_collector_key`.

- [ ] **Step 1: Write failing tests**, reusing `make_owner_and_site` and `auth` from `tests/test_sources.py`'s pattern:
  - `test_collector_source_list_omits_disabled_site`: enabled site's energy source is listed; after `PATCH enabled false` the list is empty; after re-enabling it is back.
  - `test_ingestion_rejected_for_disabled_site`: a parametrised test posting to one route per family (energy readings, weather readings, pricing readings, an energy live sample, one pricing backfill route) returns 403 with code `site_disabled` once the site is disabled.
  - `test_ingestion_accepted_after_reenable`: the energy POST returns 2xx again.
- [ ] **Step 2:** Run the new file; it fails.
- [ ] **Step 3:** In `get_source_by_collector_key`, after the existing `enabled` check, load the source's site and raise `AppError(403, "site_disabled", "This site is disabled")`. In `list_collector_sources`, join `Site` and add `Site.enabled.is_(True)`.
- [ ] **Step 4:** Run the file, then `uv run pytest -q`; all pass.
- [ ] **Step 5:** Commit `feat(core-api): stop collection for disabled sites`.

### Task 3: `site_invites` table and model

**Files:**
- Modify: `core-api/core_api/models/identity.py`, `core-api/core_api/models/__init__.py`
- Create: `core-api/alembic/versions/<new>_add_site_invites.py` (down_revision is Task 1's revision)
- Test: `core-api/tests/test_site_invites_migration.py` (new), plus a schema-constraint case in the existing `tests/test_schema_constraints.py`

**Interfaces:**
- Produces: `class InviteStatus(str, enum.Enum)` with `INVITED/ACCEPTED/DECLINED/REVOKED`; `SiteInvite` with columns `id`, `site_id`, `inviter_user_id`, `email` (String 320), `status`, `created_at`, `accepted_at`, `declined_at`, `revoked_at` (all nullable datetimes with tz), `revoked_by_user_id` (nullable FK users), `invitee_user_id` (nullable FK users). Index on `(site_id)` and on `(email)`. Check constraint on `status` via `enum_check`.

- [ ] **Step 1: Write failing tests**
  - `test_invalid_status_rejected` (schema constraint): inserting `status="bogus"` raises `IntegrityError`.
  - `test_migration_backfills_accepted_invites_for_existing_access`: build a DB at the previous revision with a site, a viewer and a `site_access` row, upgrade to head, and assert one `site_invites` row exists with `status accepted`, `email` equal to the viewer's lowercased email, `inviter_user_id` equal to the owner, `invitee_user_id` equal to the viewer.
- [ ] **Step 2:** Run both; they fail.
- [ ] **Step 3:** Add the model and migration, with the backfill as a SQL `INSERT ... SELECT` in `upgrade()`. `downgrade()` drops the table.
- [ ] **Step 4:** Run the two files and `uv run pytest -q`.
- [ ] **Step 5:** Commit `feat(core-api): add site_invites table`.

### Task 4: Create and list invites, send the email

**Files:**
- Create: `core-api/core_api/routers/invites.py`; register in `core-api/core_api/main.py` with the other routers
- Modify: `core-api/core_api/schemas.py`
- Test: `core-api/tests/test_invites.py` (new)

**Interfaces:**
- Consumes: `SiteInvite`, `InviteStatus` (Task 3); `require_site_owner`, `require_site_access`, `get_email_sender` from `deps`; `try_send_email` from `email_sender`.
- Produces: schemas `InviteCreate(email: EmailStr)`, `InviteOut(id, site_id, email, status, created_at, accepted_at, declined_at, revoked_at, inviter_user_id)`. Routes `POST /sites/{site_id}/invites` (201, owner) and `GET /sites/{site_id}/invites` (owner gets all newest first; a viewer gets only the invite whose `invitee_user_id` is theirs). Helper `status_changed_at(invite) -> datetime` returning the latest transition time.

- [ ] **Step 1: Write failing tests** (use `fake_email_sender` from conftest; `app_and_db_url` wires it into the app):
  - `test_invite_to_unregistered_email_sends_email_and_is_invited`: 201, `status "invited"`, one message recorded for that address, subject mentions the site name.
  - `test_invite_response_and_email_same_for_registered_and_unregistered`: same status code, same JSON keys, same subject and body shape for both.
  - `test_email_is_lowercased`: invite `Bob@Example.com`, stored and returned `bob@example.com`.
  - `test_cannot_invite_self`: owner's own address returns 400.
  - `test_duplicate_invite_returns_409_already_invited` for an `invited` and for an `accepted` invite; a `revoked` or `declined` one allows a fresh invite (new id).
  - `test_pending_invite_cap_is_20`: the 21st returns 409 with code `invite_limit`.
  - `test_viewer_cannot_create_invite`: 403.
  - `test_owner_lists_all_invites_viewer_lists_only_own`.
  - `test_email_failure_does_not_fail_the_request`: a sender whose `send` raises still gets 201.
- [ ] **Step 2:** Run the file; it fails.
- [ ] **Step 3:** Implement the routes. Send with `BackgroundTasks` and `try_send_email`, as `auth.register` does. Body text: who invited, the site name, and "sign in or register with this address, then open the sites page" with the `frontend_base_url` link. Do not vary the text by whether the account exists.
- [ ] **Step 4:** Run the file and `uv run pytest -q`.
- [ ] **Step 5:** Commit `feat(core-api): invite viewers to a site by email`.

### Task 5: Recipient flow: pending, accept, decline

**Files:**
- Modify: `core-api/core_api/routers/invites.py`, `core-api/core_api/schemas.py`
- Test: `core-api/tests/test_invites.py`

**Interfaces:**
- Produces: `PendingInviteOut(id, site_id, site_name, inviter_email, created_at)`; routes `GET /invites/pending`, `POST /invites/{invite_id}/accept` (returns `InviteOut`), `POST /invites/{invite_id}/decline` (returns `InviteOut`).

- [ ] **Step 1: Write failing tests**
  - `test_pending_lists_invites_for_confirmed_email_case_insensitively`.
  - `test_pending_hidden_when_email_not_confirmed`: user with `email_confirmed=False` gets `[]`.
  - `test_pending_excludes_non_invited_statuses`.
  - `test_accept_creates_site_access_and_sets_accepted_at_and_invitee`: afterwards `GET /sites` for the invitee includes the site.
  - `test_accept_twice_returns_409_and_one_access_row`.
  - `test_accept_someone_elses_invite_returns_404`.
  - `test_decline_marks_declined_with_timestamp_and_creates_no_access`.
  - `test_decline_after_accept_returns_409`.
- [ ] **Step 2:** Run; fails.
- [ ] **Step 3:** Implement. Look the invite up by id and compare `invite.email` with the caller's lowercased email and `email_confirmed`; return 404 on any mismatch so ids are not probeable.
- [ ] **Step 4:** Run the file and the suite.
- [ ] **Step 5:** Commit `feat(core-api): accept and decline invitations`.

### Task 6: Revoke, and remove the direct-grant routes

**Files:**
- Modify: `core-api/core_api/routers/invites.py`, `core-api/core_api/routers/sites.py` (delete `grant_access`, `list_access`, `revoke_access` and now-unused imports), `core-api/core_api/schemas.py` (remove `SiteAccessGrant`, `SiteAccessOut` if unused), `core-api/tests/test_sites.py` (replace `test_grant_list_and_revoke_site_access`), `docs/core-api.md`
- Test: `core-api/tests/test_invites.py`

**Interfaces:**
- Produces: `POST /sites/{site_id}/invites/{invite_id}/revoke` returning `InviteOut`.

- [ ] **Step 1: Write failing tests**
  - `test_owner_revokes_pending_invite`: status `revoked`, `revoked_by_user_id` is the owner, `revoked_at` set.
  - `test_owner_revokes_accepted_invite_removes_access`: the viewer's `GET /sites` no longer lists the site; the invite row still exists.
  - `test_viewer_revokes_own_accepted_invite`.
  - `test_viewer_cannot_revoke_pending_invite_or_anyone_elses`: 403 or 404.
  - `test_revoke_declined_or_revoked_returns_409`.
  - `test_reinvite_after_revoke_creates_new_row_and_old_row_unchanged`.
  - `test_no_endpoint_deletes_an_invite`: `DELETE /sites/{id}/invites/{invite_id}` returns 405.
  - `test_old_access_routes_are_gone`: `POST /sites/{id}/access` returns 404 or 405.
- [ ] **Step 2:** Run; fails.
- [ ] **Step 3:** Implement revoke; delete the old routes; rewrite the old `test_sites.py` grant test to use the invite flow.
- [ ] **Step 4:** `uv run ruff check . && uv run mypy && uv run pytest -q`.
- [ ] **Step 5:** Commit `feat(core-api): revoke invitations, drop direct access grants`.

### Task 7: Webapp data layer and picker filtering

**Files:**
- Modify: `webapp/src/sites/useSites.ts`, `webapp/src/pages/SitePickerPage.tsx`, `webapp/src/layout/SiteSwitcher.tsx`
- Create: `webapp/src/sites/useInvites.ts`
- Test: `webapp/src/pages/SitePickerPage.test.tsx`, `webapp/src/layout/SiteLayout.test.tsx`

**Interfaces:**
- Produces: `Site.enabled: boolean`; `useEnabledSites()` returning the `useSites()` query result with `data` filtered to enabled sites; `useUpdateSite(siteId: number)` mutation taking `{ name?: string; timezone?: string; enabled?: boolean }`; in `useInvites.ts`: `type InviteStatus = "invited" | "accepted" | "declined" | "revoked"`, `interface Invite`, `interface PendingInvite`, hooks `usePendingInvites()`, `useSiteInvites(siteId)`, `useCreateInvite(siteId)`, `useRevokeInvite(siteId)`, `useAcceptInvite()`, `useDeclineInvite()`. Mutations invalidate `["sites"]`, `["invites"]` and `["siteInvites", siteId]`.

- [ ] **Step 1: Write failing tests** (mock `apiFetch` as the existing tests do)
  - Picker lists only enabled sites; with one enabled and one disabled site it redirects to the enabled one.
  - Picker with every site disabled redirects to `/settings`.
  - Picker with no sites and no pending invites shows "Create your first site".
  - Picker with no sites and one pending invite shows "Pending invitations" with the site name, the inviter, and Accept and Decline buttons, and not the create form alone.
  - Clicking Accept calls `POST /api/v1/invites/{id}/accept` and refetches sites.
  - Clicking Decline calls the decline endpoint.
  - The switcher omits disabled sites.
- [ ] **Step 2:** `npm test -- SitePickerPage` fails.
- [ ] **Step 3:** Implement the hooks and update the picker and switcher.
- [ ] **Step 4:** `npm test`, `npm run lint`, `npx tsc -b`.
- [ ] **Step 5:** Commit `feat(webapp): hide disabled sites from the picker and show pending invitations`.

### Task 8: Settings shell, route, menu item, trimmed site navigation

**Files:**
- Create: `webapp/src/settings/SettingsPage.tsx`, `webapp/src/settings/SiteChips.tsx`
- Modify: `webapp/src/App.tsx`, `webapp/src/layout/Header.tsx`, `webapp/src/layout/SiteLayout.tsx`
- Test: `webapp/src/settings/SettingsPage.test.tsx` (new), `webapp/src/layout/Header.test.tsx`, `webapp/src/layout/SiteLayout.test.tsx`, `webapp/src/App.test.tsx`

**Interfaces:**
- Consumes: `useSites`, `Site` (Task 7).
- Produces: route `/settings/sites/:siteId/:tab` plus `/settings` (redirects to the first site's General tab). Tab ids: `general`, `collectors`, `tariffs`, `billing`, `sharing`. `SiteChips({ sites: Site[]; selectedId: number | undefined })`. Redirects `/sites/:siteId/sources` to `/settings/sites/:siteId/collectors` and `/sites/:siteId/billing` to `.../billing`.

- [ ] **Step 1: Write failing tests**
  - The user menu has "Site settings" directly after "Account settings" and links to `/settings`.
  - `/settings` renders a chip per site; a disabled site's chip has `aria-label` ending "(disabled)"; clicking a chip changes the URL's `siteId`.
  - The tab rail lists the five tabs in order, for owners and viewers alike.
  - `/sites/5/sources` redirects to `/settings/sites/5/collectors`; `/sites/5/billing` to `/settings/sites/5/billing`.
  - `SiteLayout` nav shows only Dashboard and Reporting.
- [ ] **Step 2:** Run; fails.
- [ ] **Step 3:** Implement the shell with placeholder tab content (`<div>Tab: {tab}</div>`) that later tasks replace. Use a left rail `<nav aria-label="Site settings">` with `NavLink`s, collapsing to a horizontal scroll row under the `sm` breakpoint.
- [ ] **Step 4:** Full webapp checks.
- [ ] **Step 5:** Commit `feat(webapp): add the site settings page shell`.

### Task 9: General tab, disabled banner

**Files:**
- Create: `webapp/src/settings/GeneralTab.tsx`
- Modify: `webapp/src/settings/SettingsPage.tsx`, `webapp/src/layout/SiteLayout.tsx`
- Test: `webapp/src/settings/GeneralTab.test.tsx` (new), `webapp/src/layout/SiteLayout.test.tsx`

**Interfaces:**
- Consumes: `useUpdateSite` (Task 7).
- Produces: `GeneralTab({ site }: { site: Site })`.

- [ ] **Step 1: Write failing tests**
  - Shows name and time zone fields, saves with `PATCH /api/v1/sites/{id}`.
  - The Enabled toggle on an enabled site opens a confirmation that says collection will stop; confirming sends `{ enabled: false }`; cancelling sends nothing.
  - Re-enabling sends `{ enabled: true }` with no confirmation.
  - Status line reads "Collecting" when enabled and "Collection is off" when disabled.
  - "Open dashboard" links to `/sites/{id}`.
  - For a viewer (`site.owner_user_id` differs from `useMe().id`) fields and toggle are disabled.
  - `SiteLayout` shows a banner containing "disabled" for a disabled site and none for an enabled one.
- [ ] **Step 2:** Run; fails.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Full webapp checks.
- [ ] **Step 5:** Commit `feat(webapp): general settings tab with enable toggle`.

### Task 10: Collectors, Tariffs and Billing tabs

**Files:**
- Modify: `webapp/src/pages/BillingPage.tsx`, `webapp/src/pages/SourcesPage.tsx`, `webapp/src/settings/SettingsPage.tsx`
- Test: `webapp/src/pages/BillingPage.test.tsx`, `webapp/src/pages/SourcesPage.test.tsx`, `webapp/src/settings/SettingsPage.test.tsx`

**Interfaces:**
- Produces: `export function TariffsTab({ siteId }: { siteId: number })` (the `TariffSection`) and `export function BillingTab({ siteId }: { siteId: number })` (`BillingCycleSection` plus `DemandChargesSection`) in `BillingPage.tsx`; `SourcesPage` takes an optional `siteId` prop and falls back to `useParams`. The page-level `BillingPage` export and its back link are removed when nothing imports them.

- [ ] **Step 1: Write failing tests**
  - `/settings/sites/3/collectors` renders the sources list for site 3 (reuse the existing SourcesPage test's mocks).
  - `/settings/sites/3/tariffs` shows the tariff heading and no "Billing cycle" heading.
  - `/settings/sites/3/billing` shows "Billing cycle" and "Demand charges" and no tariff form.
  - No tab shows a back link or an h1 of its own.
  - Existing BillingPage and SourcesPage tests are updated to render the tab components.
- [ ] **Step 2:** Run; fails.
- [ ] **Step 3:** Export the tab components, drop the page headers, wire them into `SettingsPage`.
- [ ] **Step 4:** Full webapp checks.
- [ ] **Step 5:** Commit `feat(webapp): move sources and billing into settings tabs`.

### Task 11: Sharing tab

**Files:**
- Create: `webapp/src/settings/SharingTab.tsx`
- Modify: `webapp/src/settings/SettingsPage.tsx`
- Test: `webapp/src/settings/SharingTab.test.tsx` (new)

**Interfaces:**
- Consumes: `useSiteInvites`, `useCreateInvite`, `useRevokeInvite` (Task 7); `Site`.
- Produces: `SharingTab({ site }: { site: Site })`.

- [ ] **Step 1: Write failing tests**
  - Owner sees an email field and Send invite button; submitting calls `POST /api/v1/sites/{id}/invites` with the email and the list refetches.
  - Each row shows email, a status label (Invited, Accepted, Declined, Revoked) and the date of the last status change, formatted with the viewer's locale and the site's time zone. The date shown for an accepted row is `accepted_at`, for a pending row `created_at`.
  - Revoke buttons appear on `invited` and `accepted` rows only; declined and revoked rows have none; no row has a delete control.
  - A 409 `already_invited` response shows its message under the field.
  - A viewer sees only their own row, no invite form, and a revoke button on it when accepted.
  - Success message reads "Invitation sent" whether or not the address has an account (the response has no such field).
- [ ] **Step 2:** Run; fails.
- [ ] **Step 3:** Implement; reuse the date helpers in `webapp/src/billing/dates.ts` where they fit rather than adding a new formatter.
- [ ] **Step 4:** Full webapp checks.
- [ ] **Step 5:** Commit `feat(webapp): sharing tab with invitations`.

### Task 12: Docs, simplify and final verification

**Files:**
- Modify: `webapp/src/docs/04-sites.md`, `webapp/src/docs/05-sources.md`, `webapp/src/docs/07-billing.md`, `docs/core-api.md`, `docs/data-model.md`; retake `webapp/src/docs/images/site-picker.png` only if the picker changed visibly

- [ ] **Step 1:** Dispatch the `documentation` agent with the spec path and ask it to update the pages above for disabling, Site settings, the moved tabs and invitations.
- [ ] **Step 2:** Run `/simplify` on the branch diff, as CLAUDE.md requires, and apply its fixes.
- [ ] **Step 3:** Review the diff against the CLAUDE.md list (functions over 30 lines, logic repeated more than twice, `any`, components with more than 3 props, async calls without error handling).
- [ ] **Step 4:** Run every backend and webapp check listed under Global Constraints. All pass.
- [ ] **Step 5:** Against a scratch copy of the DB (not the live test host), run `uv run alembic upgrade head` and confirm existing sites are enabled and existing `site_access` rows have invites.
- [ ] **Step 6:** Commit `docs: describe site settings, disabling and invitations`.
