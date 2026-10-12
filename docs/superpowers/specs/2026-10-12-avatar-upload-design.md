# Avatar Upload — Design Spec

Date: 2026-10-12
Status: Approved for planning

## Purpose

Let a logged-in user upload a real photo as their avatar, replacing the initials circle
the header/account-menu work (`docs/superpowers/specs/2026-10-12-header-account-menu-design.md`)
shipped as a placeholder. That spec explicitly deferred this as its sub-project 4:

> Avatar upload — backend storage decision + endpoint + migration, wired into the
> Avatar component this spec introduces.

Nothing in this codebase stores or serves a file today — no object storage, no image
library, no multipart dependency. This spec is the smallest version of that: local-disk
storage on the existing data volume, no resizing, no new infrastructure.

## Data model changes (Core API)

- `User` gains `avatar_content_type: str | None` (nullable `String(255)`). Non-null
  doubles as the "has an avatar" flag — no separate boolean needed, and it's also
  exactly what `GET .../avatar` needs to set the response's `Content-Type`.

## Storage

- New setting `avatar_storage_dir`, following the existing `DATABASE_URL` pattern of a
  Docker-volume path in prod and a repo-relative path in dev:
  `os.environ.get("AVATAR_STORAGE_DIR", "./data/avatars")`, with `docker-compose.yml`
  setting it to `/data/avatars` (same `core_api_data` volume the sqlite db already
  lives on — no new volume).
- Each user's avatar is stored as `{avatar_storage_dir}/{user_id}`, no extension. The
  stored `avatar_content_type` is sufficient to serve it correctly; the filename never
  needs to encode the type.
- No image processing library. No resizing, no re-encoding, no EXIF stripping. The
  frontend renders whatever was uploaded inside a fixed-size circle via CSS
  (`object-fit: cover`), the same way a `width`/`height`-constrained `<img>` always has
  been handled in this webapp. Revisit only if a user actually uploads something that
  looks wrong cropped this way.
- New dependency: `python-multipart` (FastAPI requires it to parse `UploadFile`/`File`
  multipart bodies; not currently installed).

## API contract additions

All under `/api/v1/users/me/avatar`, new router or appended to the existing
`account.py` router (implementor's call — matches this router's existing
one-endpoint-per-concern shape either way). All three require the existing
`access_token` auth, same as every other `/users/me/*` route — **this app has no
unauthenticated endpoints today, and avatar images are not an exception.**

- `POST /users/me/avatar` — `multipart/form-data`, single field `file`. Validates:
  - Content-type is one of `image/jpeg`, `image/png`, `image/webp` — else
    `400 invalid_file_type`.
  - Size is at most 2 MiB (read via `UploadFile.size` or a capped read, not trusting a
    client-supplied `Content-Length`) — else `400 file_too_large`.
  Writes the file to `{avatar_storage_dir}/{user_id}` (overwriting any existing one —
  the write must be atomic enough that a concurrent `GET` never serves a half-written
  file; write to a temp file in the same directory and `os.replace` into place), sets
  `avatar_content_type` to the validated content-type, `200` with the updated
  `UserMeOut`.
- `DELETE /users/me/avatar` — removes the file if present (idempotent: no error if
  there was none), clears `avatar_content_type`, `200` with the updated `UserMeOut`.
- `GET /users/me/avatar` — streams the file back with `Content-Type` set to the stored
  `avatar_content_type`. `404 no_avatar` if `avatar_content_type` is `None` (the
  frontend only calls this when `has_avatar` is true, so a `404` here means a race —
  e.g. removed from another tab between mount and fetch — not a bug to design around
  further than "the UI falls back to initials").
- `UserMeOut` gains `has_avatar: bool` (`avatar_content_type is not None`), following
  the same shape as the existing `has_amber_api_key` — never the content-type itself,
  since that's an implementation detail, not something the UI needs to branch on
  beyond true/false.

## Frontend

- `webapp/src/api/client.ts`: `apiFetch` currently force-sets
  `Content-Type: application/json` on every request. Change it to skip that when
  `options.body instanceof FormData` — the browser sets its own
  `multipart/form-data; boundary=...` header, and overriding it breaks the upload.
  Add a second helper, `apiFetchBlob(path): Promise<Blob>`, sharing the same
  auth-header/401-handling logic as `apiFetch` but calling `response.blob()` instead of
  `response.json()` (the avatar `GET` isn't JSON).
- `webapp/src/account/useAccount.ts`:
  - `Me` gains `has_avatar: boolean`.
  - `useUploadAvatar()` — `useMutation`, `mutationFn: (file: File) => apiFetch<Me>("/api/v1/users/me/avatar", { method: "POST", body: formDataFor(file) })`, invalidates `["me"]`
    and `["me", "avatar"]` on success (the image itself changed, not just the flag).
  - `useRemoveAvatar()` — same shape, `DELETE`, no body, same invalidation.
- `webapp/src/account/useAvatarImage.ts` — new. `useAvatarImage(hasAvatar: boolean):
  string | null`. A `useQuery` keyed `["me", "avatar"]`, `enabled: hasAvatar`, calling
  the new `apiFetchBlob("/api/v1/users/me/avatar")`. Wraps the result in
  `URL.createObjectURL` and revokes the previous object URL (via a `useEffect` cleanup)
  whenever the blob changes or the component unmounts, so this never leaks object URLs
  across re-fetches.
- `webapp/src/layout/Avatar.tsx` gains an optional `imageUrl?: string | null` prop.
  When set, renders a circular `<img src={imageUrl}>` (`h-9 w-9 rounded-full
  object-cover`, same footprint as the initials span) instead of computing initials at
  all; `null`/absent keeps today's exact behavior.
- `webapp/src/layout/Header.tsx`: calls `useAvatarImage(me?.has_avatar ?? false)` and
  passes the result as `Avatar`'s new `imageUrl` prop.
- `webapp/src/pages/AccountPage.tsx`'s `ProfileSection` gains:
  - An `Avatar` preview (reusing the same `useAvatarImage` hook) above the display-name
    form.
  - A file `<input type="file" accept="image/jpeg,image/png,image/webp">` plus an
    "Upload" button calling `useUploadAvatar()`.
  - A "Remove" button, rendered only when `me.has_avatar` is true, calling
    `useRemoveAvatar()`.
  - Client-side pre-check of type/size before the request, mirroring the server's
    rules, so the common rejection cases show instantly rather than round-tripping —
    server-side validation stays authoritative regardless.

## Error handling

Two new error codes, both via the existing `AppError` structured-error convention:
`invalid_file_type`, `file_too_large`. `no_avatar` on the `GET` is the third, used only
to let the frontend degrade silently (it is not surfaced as a banner anywhere — a
missing avatar is not a user-facing error state, just "show initials instead").

## Testing

- pytest: upload success persists the right bytes and content-type; rejects a
  disallowed content-type; rejects an oversized file; overwriting replaces rather than
  appending; delete removes the file and is idempotent when called twice; `GET` streams
  the right bytes/content-type back; `GET` 404s when unset; all three require auth.
- Vitest + RTL: `apiFetch`'s FormData content-type fix; `apiFetchBlob`; `useAvatarImage`
  (fetches when enabled, skips when not, revokes the previous object URL on change and
  on unmount — mock `URL.createObjectURL`/`revokeObjectURL`, not provided by jsdom by
  default); `Avatar`'s new `imageUrl` branch; `Header` rendering the real image when
  `has_avatar` is true; `AccountPage`'s upload/remove flow, including the client-side
  pre-check rejecting an oversized/wrong-type file without calling the API.

## Explicitly deferred / out of scope

- Any other user's avatar — nothing in this app renders another user's avatar yet (no
  shared-site member list UI exists), so only `/users/me/avatar` is built. Revisit when
  sub-project 2/3 (sites-switcher, site management) surfaces other users anywhere.
- Image resizing, cropping UI, EXIF stripping, re-encoding to a canonical format — YAGNI
  at this app's personal scale; see Storage above.
- Caching headers (`ETag`/`Cache-Control`) on `GET .../avatar` — the blob is re-fetched
  only on mount and after an explicit upload/remove invalidation, not on every render,
  so the request volume doesn't warrant it yet.
- Rate-limiting the upload endpoint — matches this router's existing stance (no
  endpoint in `account.py` has one).
