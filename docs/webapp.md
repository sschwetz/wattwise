# Web app

Single-page app in [`webapp/`](../webapp). It is built to static files and served by nginx. It holds no state of its own beyond a login token: every number on screen is fetched from Core API.

| Tool | Use |
|---|---|
| React 19 and TypeScript | UI |
| Vite 8 | Dev server and build |
| Tailwind CSS 4 | Styling |
| TanStack Query 5 | Fetching, caching, polling |
| React Router 7 | Routes |
| Recharts 3 | Charts |
| `react-markdown` with `remark-gfm` | The in-app help |
| Vitest and Testing Library | Tests |
| Oxlint | Linting |

## Screens and routes

Defined in [`src/App.tsx`](../webapp/src/App.tsx). Routes under `ProtectedRoute` redirect to `/login` when there is no token.

| Route | Page | Purpose |
|---|---|---|
| `/login`, `/register`, `/confirm-registration` | Auth pages | Sign in, sign up, confirm the email link |
| `/totp-setup`, `/totp-verify` | Two-factor | Enrol an authenticator app, enter a code |
| `/forgot-password`, `/reset-password` | Reset | Request and use a reset link |
| `/confirm-email-change` | Email change | Confirm the new address |
| `/`, `/sites` | `SitePickerPage` | Choose a site |
| `/sites/new` | `AddSitePage` | Create a site and pick its time zone |
| `/account` | `AccountPage` | Name, photo, password, email, authenticator, Amber key |
| `/help/:slug?` | `HelpPage` | The user guide |
| `/sites/:siteId` | `DashboardPage` | Live tiles, pricing chart, history chart |
| `/sites/:siteId/sources` | `SourcesPage` | Add, edit, disable, delete sources and rate windows |
| `/sites/:siteId/billing` | `BillingPage` | Tariffs, windows, billing cycle, demand charges |

<table>
<tr>
<td><img src="../webapp/src/docs/images/site-picker.png" alt="Site picker" width="380"></td>
<td><img src="../webapp/src/docs/images/dashboard-live-tiles.png" alt="Dashboard live tiles" width="380"></td>
</tr>
<tr>
<td><img src="../webapp/src/docs/images/sources-edit-config.png" alt="Source settings form" width="380"></td>
<td><img src="../webapp/src/docs/images/billing-tariffs.png" alt="Billing tariffs" width="380"></td>
</tr>
</table>

More screenshots, with the explanation of every tile and field, are in the [user guide](../webapp/src/docs/index.md).

## Source layout

| Folder | Contents |
|---|---|
| `src/api/` | `client.ts` (fetch wrapper, `ApiError`), `config.ts` (API base URL) |
| `src/auth/` | `AuthContext` (token in `localStorage` under `access_token`), `ProtectedRoute` |
| `src/layout/` | Header, site sidebar, site switcher, avatar |
| `src/pages/` | One component per route, with its test beside it |
| `src/dashboard/` | Hooks for live readings and history, and pure helpers for charts (`historyChart.ts`, `priceChart.ts`, `compass.ts`) |
| `src/billing/` | `useBilling.ts` (all tariff and cycle calls), `ConditionalCredits.tsx`, `tariffHeading.ts` |
| `src/sources/` | `useSources.ts`, `useRateWindows.ts`, the rate window panel and form, `rateCoverage.ts` (finds uncovered times) |
| `src/sites/`, `src/account/` | Site and account hooks and forms |
| `src/help/` | The help viewer |
| `src/docs/` | The user guide as Markdown, plus `images/` |

Convention: a feature folder has a `use*.ts` hook per resource that wraps TanStack Query, and pure functions for anything worth testing without React.

## Talking to Core API

[`src/api/client.ts`](../webapp/src/api/client.ts) adds the `Authorization: Bearer` header and turns error bodies into an `ApiError` with `status`, `code`, `message` and `detail`.

A failed request logs the user out only when the code says the session itself is dead: `missing_token`, `invalid_token`, or `unknown_error` (a 401 with no body, usually from a proxy). Other 401s, such as a wrong current password on the account page, stay on screen as form errors.

Live data polls every 60 seconds (`LIVE_POLL_MS` in `src/dashboard/useLiveReadings.ts`), which matches the collectors' cadence. Nothing uses websockets.

## Runtime configuration

The API address is not compiled in. `public/config.js` sets `window.__APP_CONFIG__.apiBaseUrl`, and `src/api/config.ts` reads it (falling back to `http://localhost:8000`). In the container, [`docker-entrypoint.sh`](../webapp/docker-entrypoint.sh) rewrites `config.js` from the `API_BASE_URL` environment variable each time it starts. So one image works for any deployment, and changing the address needs a restart, not a rebuild.

`nginx.conf` serves `index.html` for every unknown path so that browser refreshes on client-side routes work.

## The in-app help is this repository's user guide

[`src/help/content.ts`](../webapp/src/help/content.ts) bundles every Markdown file in `src/docs/` and every image in `src/docs/images/` at build time. `HelpPage` renders them with GitHub-flavoured Markdown. The same files read correctly on GitHub, which is why this documentation links to them instead of copying them.

Tests protect the guide: `docs.test.ts` and `links.test.ts` fail when a chapter link or image does not resolve. When you change a screen, update the matching chapter and screenshot in the same change.

## Where to change things

| To change... | Edit |
|---|---|
| The providers offered when adding a source | `PROVIDERS_BY_KIND` in `src/pages/SourcesPage.tsx` |
| The settings form for a provider | `CONFIG_FORMS` in the same file. A provider without a form gets a raw JSON editor. |
| A dashboard tile | `src/pages/DashboardPage.tsx` and `src/dashboard/` |
| How a chart is shaped | `historyChart.ts` or `priceChart.ts`, which have unit tests |
| Tariff and billing forms | `src/pages/BillingPage.tsx` and `src/billing/` |
| Help text | `src/docs/*.md` |
