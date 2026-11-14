# WattWise web app

React single-page app: Vite, TypeScript, Tailwind, TanStack Query, React Router and Recharts. It talks to core-api over HTTP and holds no state beyond the login token. The structure, routes, data fetching and the Schedule tab are described in [`docs/webapp.md`](../docs/webapp.md).

## Commands

```sh
npm install
npm run dev      # Vite dev server on http://localhost:5173, calls core-api at http://localhost:8000
npm test         # Vitest
npm run lint     # Oxlint
npm run build    # type-checks with tsc, then builds to dist/
```

Start core-api first. The setup steps are in the root [README](../README.md#start-a-development-instance).

## The user guide

`src/docs/*.md` is the user guide. The in-app Help page bundles it at build time, and tests fail when a chapter link or image does not resolve. Change the matching chapter and screenshot in the same change as a screen.

## Runtime configuration

The container writes `public/config.js` from `API_BASE_URL` at start, so one image works for any deployment. See [Runtime configuration](../docs/webapp.md#runtime-configuration).
