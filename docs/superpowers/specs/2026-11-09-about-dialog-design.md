# About dialog

## Intent

The owner wants to see, from inside the app, which build each service is running and which open-source packages WattWise uses. The commit shown in the header today covers only the webapp. The services run as separate containers and can drift apart after a partial update, so there is no way to check them from the UI.

## What gets built

An "About" item in the right-hand account menu (`webapp/src/layout/Header.tsx`), placed directly above "Log off". It opens a dialog with two tabs.

### Containers tab

- Lists 7 services: core-api, webapp, envoy-collector, weatherlink-local-collector, amber-pricing-collector, local-pricing-collector, actron-neo-actor. mail-relay is left out; it is a plain Postfix container and does not report.
- Each row shows the service name, its commit (short hash, linked to `https://github.com/sschwetz/wattwise/commit/<hash>` as `webapp/src/buildInfo.ts` does today), and when it last reported.
- All 7 rows always appear. A service that has not reported within the cutoff shows "not reporting" with its last-reported time, or "never" if it has no row yet.
- Data comes from a new authenticated core-api endpoint. The webapp does not talk to Docker.

### Reporting

- Each service sends its commit to core-api on startup and then on a repeating timer.
- Core-api stores one row per service: name, commit, last-reported timestamp. A report overwrites the row.
- Collectors and the actor reuse the credentials they already use to call core-api (collector key, `ACTOR_KEY`). The report endpoint accepts those.
- Core-api writes its own row at startup and on the same timer.
- The webapp does not report. It serves `GET /version`, which returns JSON containing its commit (for example `{"commit": "abc1234"}`), written at container start from `GIT_COMMIT`. Core-api fetches `<FRONTEND_BASE_URL>/version` on its timer and records the result as the webapp's row. It uses the existing `FRONTEND_BASE_URL` setting, not a container hostname, so this works without Docker. A failed fetch leaves the row as it was, so it ages into "not reporting".
- Every service image gets the `GIT_COMMIT` build arg. `publish-images.yml` already passes it to all 8 builds, but only `webapp/Dockerfile` declares it. Add it to the other Dockerfiles, exposed to the process as an environment variable. A build without it reports `unknown`.

### Licences tab

- A listbox of every package used, with name and version. Selecting one shows its licence name and licence text in a pane beside or below it.
- Covers npm packages in `webapp/package-lock.json` and Python packages in each service's `uv.lock`. A package used by several services appears once per name and version.
- The data is generated at build time and shipped as a static file with the webapp. It is not fetched live from a registry.

## Goals

- All 7 services visible in one place with their commits.
- A stopped or misconfigured service is visible as "not reporting".
- Licence list complete enough to answer "what licence is package X under".

## Non-goals

- No container control (start, stop, restart) and no logs.
- No Docker socket access.
- No history of past commits per service.
- No reporting from mail-relay.
- No alerting when a service stops reporting.

## Anti-goals

- Do not give any service a new secret just to report.
- Do not depend on Docker (no socket, no container hostnames); the app must also run outside it.
- Do not make the About dialog depend on a live call to a package registry.

## Constraints

- Follow `CLAUDE.md`: no `any` in TypeScript, async error handling, functions under about 30 lines, no secrets in plaintext.
- Core-api changes need an Alembic migration.
- The dialog must work with keyboard (Escape closes, focus returns to the menu button) and follow the existing menu styling in `Header.tsx`.

## Decided by the owner

- About sits above Log off.
- Services report their commit to core-api, and the UI reads it from there. The webapp is the exception: core-api polls its `/version` JSON endpoint at `FRONTEND_BASE_URL`.
- Services that are not reporting still appear, marked "not reporting", with last-reported time.
- mail-relay is excluded.

## Builder's call (author's guesses, not owner decisions)

- The "not reporting" cutoff: a few missed report intervals. Pick the interval and multiple.
- Report interval, table name and endpoint paths.
- How licence data is generated and what format the static file uses; how licence text is obtained when a package ships none.
- Dialog sizing, and how it behaves on a phone.

## Author's calls after the builder check (not owner decisions)

- The report names its own service. Core-api checks the name against an allow-list for the credential used. The two pricing collectors share a key and can therefore report as each other; that is accepted.
- Each service runs its own small timer to send reports.
- Python licence data is looked up from the registry at CI build time, not at runtime. A package whose licence is not found shows "unknown" and does not fail the build.
