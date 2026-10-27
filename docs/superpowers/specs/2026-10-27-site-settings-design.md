# Site settings and disabling sites

## Why

A user can move house. The old site holds years of usage, price and billing data they still want to read, but it clutters the site picker at sign in and its collectors keep polling for nothing. There is no way to hide a site without deleting its data, and no delete endpoint exists.

Site configuration is also scattered. Sources and Billing are pages inside each site's navigation, and viewer sharing exists only as API routes (`/sites/{id}/access`) with no screen. This change gathers all per-site configuration into one Settings page and adds a disable switch.

## Goals

- An owner can disable a site. A disabled site:
  - does not appear in the site picker or the site switcher;
  - stops collecting: collectors no longer receive its sources, and ingestion for it is rejected;
  - keeps all existing data, which stays readable.
- An owner can re-enable a site. Collection resumes. The period it was off has no data.
- A new "Site settings" item in the top-right user menu, directly under "Account settings", opens a Settings page.
- The Settings page shows every site the user owns or has access to, disabled ones included, and the selected site's configuration in tabs.
- Sources and Billing move out of the per-site navigation into the Settings tabs. Per-site navigation keeps Dashboard and Reporting.
- Sharing is done by invitation:
  - The owner invites an email address. The invite is emailed whether or not that address has an account.
  - The owner sees the invite as **invited** until the recipient accepts, then **accepted**.
  - The recipient sees pending invites on the sites page and accepts from there.
  - Once accepted, either the owner or the viewer can revoke access. The invite then shows **revoked**.
  - The recipient can decline a pending invite. It then shows **declined**, not revoked.
  - An owner can also revoke an invite that is still pending.
  - Invites are never deleted and never edited apart from their status. Nothing removes a row.

## Non-goals

- Deleting a site.
- Per-source or per-viewer disabling. Source `enabled` stays as it is.
- Backfilling the gap left while a site was disabled.

## Anti-goals

- Disabling must never remove or hide data from the owner.
- A disabled site must not silently keep ingesting. A collector that holds a stale source list must get a clear 403, not a stored reading.

## Settings page layout (chosen: option B)

- Top: a row of site chips. A disabled site is dimmed with a dashed border. The last chip adds a site.
- Below: a vertical tab rail on the left, content on the right. The rail never wraps or scrolls sideways. Fields on the right use a multi-column grid so a tab fits one screen on a laptop where it can.
- Tabs, in order:
  1. **General**: name, time zone, Enabled toggle, collection status (collecting or off), a link to open the dashboard.
  2. **Collectors**: today's Sources page.
  3. **Tariffs**: the tariff section of today's Billing page.
  4. **Billing**: billing cycle and demand charges from today's Billing page.
  5. **Sharing**: invite by email, and the list of every invite for the site with its status (invited, accepted, declined, revoked), who sent it, and the date the status last changed. Owners can invite and revoke. A viewer sees only their own invite, with a revoke button.
- The selected site and tab are in the URL (for example `/settings/sites/:siteId/:tab`) so a tab can be linked and survives refresh.
- Writes are owner-only, as today. Viewers see a site's settings read-only where the API already allows reads.

## Behavior of a disabled site

- `GET /sites` returns every site with an `enabled` field. The picker, the switcher and the sign-in redirect use only enabled sites. The Settings page uses all of them.
- A user whose only site is disabled lands on the Settings page instead of the add-site form.
- The dashboard and reporting for a disabled site open from the General tab and by direct URL, and show a "disabled" banner.
- Disabling applies to everyone with access. Viewers do not see a disabled site in their picker either.

## Constraints

- Follow CLAUDE.md: a migration adds the column; no secrets are touched. Run ruff, mypy and the existing tests.
- The invite response and the invite email must not reveal to the sender whether the address has an account.
- Invite rows are append-only. No code path deletes one, and the only update is a status change with its timestamp.
- Existing sites must come out of the migration enabled.

## Decisions made by the user

- Disabled means: hidden from the picker, collectors stop, data stays.
- Entry point is a second item in the top-right user menu.
- Layout B (site chips on top, tabs down the left).
- Sources and Billing move to Settings; they do not stay in both places.
- Sharing is built now, as part of the final product.
- Sharing is by invite, not by direct add. The invite is emailed even when the address has no account.
- Statuses as the sender sees them: invited, accepted, declined, revoked.
- The UI shows the date each invite's status last changed, for both sender and recipient.
- The recipient sees and accepts pending invites on the sites page.
- Either side can revoke once shared. Invites are immutable records and cannot be deleted.

## Calls made by me (check these)

- `sites.enabled` is a boolean column, default true, not null.
- Only the owner can toggle it, through `PATCH /sites/{id}`.
- The collector source list and the ingestion dependency each add a site-enabled check. Ingestion returns 403 `site_disabled`.
- Demand and rollup jobs keep running on a disabled site. They only derive from stored data.
- New table `site_invites`: `id`, `site_id`, `inviter_user_id`, `email` (lowercased), `status` (`invited`, `accepted`, `declined`, `revoked`), `created_at`, `accepted_at`, `declined_at`, `revoked_at`, `revoked_by_user_id`, and `invitee_user_id` set on accept. Rows are never deleted. Inviting the same address again after a revoke creates a new row.
- `site_access` stays as the thing permission checks read. Accepting an invite inserts the `site_access` row. Revoking deletes that grant row, and the invite row remains as the record. The old direct grant route (`POST /sites/{id}/access` with a `user_id`) is removed, along with its tests.
- Invites match by email, not by a token, so no secret is stored. The recipient must sign in as an account with that email and a confirmed address (`email_confirmed`). Registration already confirms the address, so nobody can claim an invite by registering an address they do not own.
- The invite email says to sign in or register with that address and open the sites page. It uses the existing `EmailSender`. The wording does not differ for existing and new accounts in a way the sender can see.
- Declining is allowed only while the invite is `invited`. Revoking applies to an `invited` invite (owner only) or an `accepted` one (owner or viewer). `declined` and `revoked` are final.
- The date shown is the timestamp of the latest transition (`created_at` while still invited), shown in the viewer's locale and time zone.
- Invites do not expire.
- An owner cannot invite their own address, or an address with a pending or accepted invite for that site (409 `already_invited`).
- At most 20 invites in `invited` status per site, to cap outgoing mail.
- Existing `site_access` rows become `accepted` invites in the migration, with the owner as inviter and the viewer's email.
- Endpoints: `POST /sites/{id}/invites`, `GET /sites/{id}/invites` (owner: all; viewer: own), `POST /sites/{id}/invites/{invite_id}/revoke` (owner or the invitee), `GET /invites/pending`, `POST /invites/{invite_id}/accept` and `POST /invites/{invite_id}/decline` for the recipient.
- Tab names and their split of the Billing page are as listed above. Each section keeps its own save, as it does today.
- Ingestion: every collector write route (energy, weather, pricing, live samples, pricing backfill) reaches `get_source_by_collector_key` in `core_api/deps.py`, so the site-enabled check goes there once. `GET /collector/sources` leaves a disabled site's sources out, so a collector sees an empty list, not an error.
- Collection status on the General tab mirrors `enabled`. It does not report last-ingest time.
- A viewer sees the Sharing tab with only their own invite and a revoke button, so viewers never see each other's emails.
- Viewers see disabled sites as dimmed chips in Settings, read-only. Reads use `require_site_access`, which does not check `enabled`, so a disabled site's dashboard still opens by URL with the banner.
- The old `/sites/:id/sources` and `/sites/:id/billing` URLs redirect to the matching Settings tab.
- A user whose sites are all disabled lands on Settings. A user with no sites at all gets the add-site form, as today.
- Any user can create a site, so the add chip shows for everyone.
- The last enabled site can be disabled. Toggling off asks for confirmation and says collection will stop.
- The sites page shows a Pending invitations section above the site list, with Accept and Decline. It shows even when the user has no sites yet.

## Left to the builder

- Route naming and component structure inside the webapp.
- Whether the Settings tabs reuse the existing page components as they are or extract their sections.
- Exact grid breakpoints and the mobile layout.
- Test layout, following the existing per-file `.test.tsx` convention.

## Open

Nothing is waiting on the user.
