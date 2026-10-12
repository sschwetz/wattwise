# Header / Account Menu — Design Spec

Date: 2026-10-12
Status: Approved for planning

## Purpose

Add a persistent top-right header to the webapp: an avatar + the user's name, opening a
dropdown with "Account settings" and "Log off". None of this exists today — `logout()`
already exists on `AuthContext` but nothing in the UI calls it, there is no header
component anywhere, and `User` has no name field at all (only `email`).

This is sub-project 1 of 4 in a larger "profile + multi-site navigation" piece of work,
itself a small addition to the existing webapp sub-project
(`docs/superpowers/specs/2026-10-01-frontend-webapp-design.md`), not a new top-level
project:

1. **Header / account menu** (this spec).
2. Sites-switcher dropdown (icon listing the user's other sites, linking to each site's
   Billing, Sources, and Site management).
3. Site management page (rename/change timezone/delete a site — new `PATCH`/`DELETE
   /api/v1/sites/{id}` endpoints, since only create/list exist today).
4. Avatar upload (file storage decision + endpoint + migration), wired into the `Avatar`
   component this spec introduces.

Each ships independently; the header works with initials-only avatars until (4) lands.

## Data model changes (Core API)

- `User` gains `display_name: str | None` (nullable `String(100)`). No existing field
  covers this — `email` is the only identifying field today.

## API contract additions

- `UserMeOut` gains `display_name: str | None`.
- `PATCH /users/me/profile` `{display_name}` (auth: `access_token`) — sets
  `display_name`, `200` with the updated `UserMeOut`. No step-up (current
  password/TOTP) required: unlike password/email/TOTP changes, a display name carries
  no account-takeover risk, the same reasoning already applied to
  `PATCH /users/me` (Amber API key). Kept as its own route rather than folded into the
  existing Amber-key `PATCH /users/me`, matching this router's existing one-endpoint-
  per-concern pattern (password, email, TOTP each have their own route).

## Frontend

- `webapp/src/layout/Avatar.tsx` — new. Renders a circle with initials derived from
  `display_name` if set (first letter of up to the first two words), else the first
  letter of the email local-part. Background color is deterministic: a small hash of
  the string driving the initials, mapped into a fixed palette. No image support yet
  (sub-project 4).
- `webapp/src/layout/Header.tsx` — new. Right-aligned `Avatar` + display name (or email
  if unset) + chevron button. Click opens a dropdown (closes on outside click / Escape,
  matching standard menu accessibility — role="menu", keyboard-dismissible) with:
  - **Account settings** → `NavLink` to `/account`.
  - **Log off** → calls `logout()` from `useAuth()`; `ProtectedRoute` already redirects
    unauthenticated users to `/login`, so no explicit navigation is needed after.
- `webapp/src/layout/ProtectedLayout.tsx` — new. Renders `Header` then `Outlet`. Wraps
  every protected route in `App.tsx`:
  ```
  <Route element={<ProtectedRoute />}>
    <Route element={<ProtectedLayout />}>
      <Route path="/" element={<SitePickerPage />} />
      <Route path="/sites" element={<SitePickerPage />} />
      <Route path="/account" element={<AccountPage />} />
      <Route path="/sites/:siteId" element={<SiteLayout />}>
        ...
      </Route>
    </Route>
  </Route>
  ```
  `SiteLayout`'s sidebar nests inside `ProtectedLayout` unchanged, except:
- `SiteLayout.tsx` loses its "Account" sidebar link — now redundant with the header
  dropdown's "Account settings" entry. "Switch site" stays (sub-project 2 replaces it
  with the sites-switcher dropdown, not this one).
- `AccountPage.tsx` gains a new "Profile" section (same card/form pattern as the
  existing Amber API key section), first in the page: a single display-name field,
  submitting via a new `useUpdateDisplayName()` hook.
- `webapp/src/account/useAccount.ts`: `Me` interface gains `display_name: string |
  null`; new `useUpdateDisplayName()` mutation following the existing
  `useUpdateAmberApiKey` shape, hitting `PATCH /api/v1/users/me/profile`.

## Error handling

No new error codes. The profile-update endpoint can only fail on auth (existing
`401`/`403` via `get_current_user`) or validation (`display_name` empty beyond
Pydantic's `min_length`, handled client-side same as other forms in this page).

## Testing

- pytest: new test for `PATCH /users/me/profile` (sets the field, returned in
  `UserMeOut`; rejects empty string).
- Vitest + React Testing Library:
  - `Avatar.test.tsx` — initials from display name vs. email fallback; stable color for
    the same input.
  - `Header.test.tsx` — dropdown open/close, "Account settings" link, "Log off" calls
    `logout()`.
  - `SiteLayout.test.tsx` updated — no more "Account" link assertion.
  - `AccountPage.test.tsx` updated — new Profile section's submit path.

## Explicitly deferred / out of scope

- Avatar image upload/storage — sub-project 4.
- The sites-switcher icon and site management page — sub-projects 2 and 3.
- Any notification/audit trail on display-name change — matches this codebase's
  existing stance of not notifying on other account changes (see
  `2026-10-08-account-settings-design.md`'s deferred section).
