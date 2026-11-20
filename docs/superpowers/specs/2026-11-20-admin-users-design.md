# Admin: Users screen and the manager role

## Intent

Only the bootstrap user can administer the app, and nobody can see who has access to which site. An app-wide admin should open **Admin Settings**, go to a **Users** heading, see every user with the sites they manage and the sites they can view, and click a user to change them. Admin status itself is granted here.

This builds on the `is_admin` flag, `require_admin` dependency and Admin Settings page from [the mail design](2026-11-20-admin-mail-settings-design.md). That design's non-goal "a screen for granting or revoking admin" is replaced by this one.

## Goals

- A **Users** tab on Admin Settings listing all users, each with their managed and viewed sites.
- A user detail view where an admin can:
  - edit display name and email, and remove the avatar
  - set or clear the user's Amber API key
  - send the user a password reset email
  - reset the user's TOTP so they enrol again at next login
  - add the user to a site as manager or viewer, change that role, or remove them
  - set or clear the admin flag
- A new site role, `manager`, with owner-level rights on one site.
- Screens match the existing Account and Site settings pages.

## Non-goals

- Several owners per site. `Site.owner_user_id` stays the single owner.
- Transferring ownership, creating users, or deleting users.
- An admin viewing a site's readings, schedules or billing through this screen. Admins see membership only.
- Typing a password for another user.
- Showing any stored secret to an admin, including the Amber key.
- An audit table.

## Anti-goals

- A manager promoting themselves or others to manager, or removing the owner.
- The last admin removing their own admin flag and locking everyone out.
- An admin endpoint returning a secret, or logging one.

## Constraints

- Roles today: owner (`Site.owner_user_id`) and `viewer` (`site_access`). `SiteAccessRole` has one member, and its comment says ownership stays out of that table. That still holds: `manager` is a grant, not ownership.
- `require_site_owner` has 33 route uses (`actors`, `billing`, `invites`, `local_rates`, `sites`, `sources`). Direct `owner_user_id` checks exist in `invites`, `sites`, `sources`, `collector_config`, `actor_email` and `actor_schemas`.
- `site_access.role` has a check constraint, so adding `manager` is a migration (SQLite batch alter).
- CLAUDE.md applies: secrets stay encrypted, nothing secret is logged, docs ship in the same change.

## Approaches considered

1. **New `manager` role in `site_access` (chosen).** One new enum value and a changed permission dependency. The owner stays single.
2. **Allow several owners.** Reworks `owner_user_id`, every check against it, and the actor emails that go to the owner. Much larger.
3. **Treat "manager" as a transfer of ownership.** Removes the owner's access, which is not what was asked for.

## Design

### Manager role

`SiteAccessRole.MANAGER = "manager"`, with the check constraint widened by migration.

A new dependency `require_site_manager` replaces `require_site_owner` on the 33 routes. It passes for the owner or a `manager` grant. `require_site_owner` stays, and is used only for:

- adding, changing and removing managers (this is also what the Sharing tab shows)
- anything that changes `owner_user_id`

Everything else a site owner can do, a manager can do: sources, schedules, billing, tariffs, invites for viewers, and the site's settings. This is my call. The builder confirms the list of routes that stay owner-only.

Emails that go to "the site owner" (run failure, watchdog) keep going to the owner only.

`SiteOut` gains `my_role` (`owner`, `manager` or `viewer`), and the webapp's owner checks use it. The Sites list and every `site_visible_to` caller include manager grants.

### Admin endpoints

Under `/api/v1/admin/users`. All need `require_admin` (403 `admin_required`).

| Endpoint | Purpose |
|---|---|
| `GET /` | All users: `id`, `email`, `display_name`, `is_admin`, `has_avatar`, `totp_enabled`, `email_confirmed`, and `sites`: a list of `{site_id, site_name, role}` where role is `owner`, `manager` or `viewer`. One query for users and one for access rows, joined in Python. |
| `GET /{user_id}` | One user, same shape, with `has_amber_key` (bool). |
| `PATCH /{user_id}` | `display_name`, `email`, `is_admin`. A duplicate email is 409 `email_taken`. Clearing `is_admin` on the last admin is 409 `last_admin`. |
| `DELETE /{user_id}/avatar` | Removes the avatar. |
| `PUT /{user_id}/amber-key` and `DELETE` | Sets or clears the key. The value is never returned. |
| `POST /{user_id}/password-reset` | Sends the user the normal reset email. 204. |
| `POST /{user_id}/totp/reset` | Clears `totp_secret`, `pending_totp_secret`, `last_totp_step` and sets `totp_enabled` false, so the next login runs the TOTP setup flow. The builder confirms the login path handles this state. |
| `PUT /{user_id}/sites/{site_id}` | Body `{role: "manager" \| "viewer"}`. Adds or changes the grant. 409 `is_owner` when the user owns the site. |
| `DELETE /{user_id}/sites/{site_id}` | Removes the grant. 409 `is_owner` for the owner. |

An email change by an admin sets the new address directly and leaves `email_confirmed` as it is. Tokens already issued for the old address stop working at the next login if the JWT carries the address; the builder checks which claim is used.

Each admin write logs one line with the admin's user id, the target user id and the action. Values are never logged.

### Webapp

- Admin Settings gets a tab list: **Email** and **Users**, following `SettingsPage` (`/admin/:tab?`). The menu item and `/admin` guard come from the mail design.
- **Users tab:** a search box (name or email) above a table. Columns: user (avatar, name, email, an Admin badge), **Manages** (site chips, owner and manager), **Views** (site chips). A row opens `/admin/users/:userId`.
- **User detail** has four sections:
  - **Details:** display name, email, avatar removal.
  - **Site access:** the user's sites with a role selector (manager, viewer) and Remove, plus an Add to site selector listing every site the user is not on. The owner's row is shown and not editable.
  - **Security:** Send password reset email, Reset two-factor sign-in, Amber API key (`set` with Change and Clear, or `not set`).
  - **Administrator:** a toggle. The toggle is disabled with an explanation for the last admin.
- Destructive actions (TOTP reset, removing access, clearing an admin) use the existing confirm dialog style.
- Components follow `AccountPage`: `formStyles`, `SubmitButton`, `QueryError`, and the table style the site list or sources page already uses.

### Documentation

- `docs/`: edit `core-api.md` (endpoints, the new dependency), `data-model.md` (the role value), `architecture.md` (permission table at the roles section); `docs/mail.md` from the mail design links here for admin setup.
- `webapp/src/docs/`: a new Admin users chapter, the glossary entry for manager, the Sharing tab's description, and the troubleshooting chapter.
- The `documentation` agent runs after implementation, then the link and anchor check.

## Build order

One plan, three slices. Each ships on its own.

1. Foundation: `is_admin`, `require_admin`, `my_role`, the menu item, the guarded `/admin` page with its tabs.
2. Users: the manager role, the admin endpoints and the Users tab.
3. Email: the mail design.

## Left to the builder

- Which of the 33 routes stay owner-only, starting from the two rules above.
- Whether the Sharing tab lets an owner see managers.
- Table pagination. The list loads all users and stays unpaginated while the user count is small.
- Test layout, following the existing suites.

## Calls I made, not yours

- Managers have owner rights except managing managers and changing the owner.
- Admins see membership, not site data.
- A reset by an admin sends the standard reset email. It does not set a password.
- The Amber key can be set or cleared and never read.
- Admin writes are logged, not recorded in a table.
- The last admin cannot be demoted.
