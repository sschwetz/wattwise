# About Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "About" item above "Log off" in the account menu that opens a two-tab dialog: running services with their commits, and the licences of the packages WattWise uses.

**Architecture:** Core-api stores one `service_status` row per service. The five Python services POST their commit to core-api every 60 s. Core-api writes its own row and polls `<FRONTEND_BASE_URL>/version` (JSON served by the webapp's nginx) with one scheduler job. `GET /api/v1/services` returns all 7 services with a server-computed `reporting` flag. Licences come from a generated static `licences.json` in the webapp's `public/`.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, APScheduler, httpx (core-api); httpx + threading (collectors, actor); nginx + sh (webapp container); React 19, react-query, Tailwind, Vitest (webapp).

**Spec:** `docs/superpowers/specs/2026-11-09-about-dialog-design.md`

## Global Constraints

- Services listed, in this order: `core-api`, `webapp`, `envoy-collector`, `weatherlink-local-collector`, `amber-pricing-collector`, `local-pricing-collector`, `actron-neo-actor`. `mail-relay` is excluded.
- Commit links use `https://github.com/sschwetz/wattwise/commit/<hash>` (the existing `REPO_URL` in `webapp/src/buildInfo.ts`).
- Report interval 60 s. "Not reporting" when `now - reported_at > 180 s` (3 missed intervals); never-reported services also show "not reporting".
- No new secrets. Reports reuse `X-Collector-Key` / `X-Actor-Key`. The service name is self-declared and checked against an allow-list per key.
- No Docker dependency at runtime: no socket, no container hostnames. Core-api reaches the webapp through the existing `FRONTEND_BASE_URL` setting.
- A build without `GIT_COMMIT` reports `unknown`. Commits are shortened to 7 characters everywhere.
- `CLAUDE.md`: no `any`, no function over 30 lines, async errors handled, prose follows `.claude/rules/avoid-ai-writing.md`.
- Core-api schema change needs an Alembic migration. Current head is `895863e431c6`.
- Python services are CI-checked with `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`; the webapp with `npm run lint`, `npm run build`, `npm test`.
- Tests run in the `core-api/` etc. directory of each package. The test database host is described in memory `test-database-host`; core-api tests use the SQLite fixtures in `tests/conftest.py`, so no external DB is needed.

## Review Focus

- Report with a service name that the presented key may not report as (for example the envoy key reporting as `actron-neo-actor`): 403, nothing stored.
- Report with an invalid or missing key: 401, nothing stored.
- Report with a commit string that is empty, longer than 40 characters or has non-hex characters other than `unknown`/`dev`: 422, nothing stored.
- `/version` returns non-JSON, a 500, or hangs: the webapp row keeps its old values and the job does not raise.
- Two reports for the same service: one row, newest commit and time win.
- A package whose licence the registry cannot supply: listed with licence "unknown" and empty text; generator still exits 0.
- `licences.json` missing or malformed: the Licences tab shows an error message, the Containers tab still works.

---

### Task 1: Service status table and report/list endpoints (core-api)

**Files:**
- Create: `core-api/core_api/models/service_status.py`, `core-api/core_api/service_status.py`, `core-api/core_api/routers/services.py`, `core-api/alembic/versions/<new>_add_service_status.py`
- Modify: `core-api/core_api/models/__init__.py`, `core-api/core_api/main.py` (include router), `core-api/core_api/config.py` (add `git_commit`)
- Test: `core-api/tests/test_services_router.py`, `core-api/tests/test_service_status.py`

**Interfaces:**
- Produces, in `core_api/service_status.py`:
  - `SERVICES: tuple[str, ...]` (the 7 names, in order)
  - `REPORT_INTERVAL_SECONDS = 60`, `STALE_AFTER = datetime.timedelta(seconds=180)`
  - `allowed_services_for_key(presented: str) -> frozenset[str]` (empty set when the key matches nothing). Mapping: `ENVOY_COLLECTOR_KEY` → `{"envoy-collector"}`; `WEATHER_COLLECTOR_KEY` → `{"weatherlink-local-collector"}`; `PRICING_COLLECTOR_KEY` → `{"amber-pricing-collector", "local-pricing-collector"}`; `ACTOR_KEY` → `{"actron-neo-actor"}`. Compare with `secrets.compare_digest` on UTF-8 bytes, like `check_collector_key`.
  - `record_report(db: Session, service: str, commit: str, now: datetime.datetime) -> None` (upsert, commits)
  - `list_statuses(db: Session, now: datetime.datetime) -> list[ServiceStatusRow]` where `ServiceStatusRow` is a dataclass `(service: str, commit: str | None, last_reported_at: datetime | None, reporting: bool)`, always 7 rows in `SERVICES` order.
- Model `ServiceStatus` in table `service_status`: `service` String(64) primary key, `commit` String(40) not null, `reported_at` DateTime(timezone=True) not null.
- `Settings.git_commit: str` from env `GIT_COMMIT` (default `"unknown"`, truncated to 7 characters).
- `POST /api/v1/services/report`, headers `X-Collector-Key` or `X-Actor-Key` (one of them), body `{"service": str, "commit": str}` → 204. Errors: 401 `invalid_service_key`, 403 `service_not_allowed`, 422 for a bad commit (pattern `^([0-9a-f]{7,40}|unknown|dev)$`).
- `GET /api/v1/services` (requires `get_current_user`) → `list[{service, commit, last_reported_at, reporting}]`.

- [ ] **Step 1: Write failing tests** in `test_service_status.py` for `allowed_services_for_key` (each of the 4 keys returns its set, a wrong key and a non-ASCII key return the empty set), `record_report` (second call for the same service leaves one row with the newer commit and time) and `list_statuses` (7 rows in order; a service reported 179 s ago has `reporting=True`, 181 s ago `False` with `last_reported_at` set; a never-reported service has `commit=None`, `last_reported_at=None`, `reporting=False`).
- [ ] **Step 2: Write failing router tests** in `test_services_router.py` using the `client`, `db`, `user` fixtures and the key constants in `tests/conftest.py`: 204 and a stored row for `X-Collector-Key: PRICING_COLLECTOR_KEY` as `local-pricing-collector`; 403 for the envoy key as `actron-neo-actor`; 401 for no key and for a wrong key; 422 for commit `"zzz"` and `""`; `GET /api/v1/services` returns 401 without a token and 7 rows with one.
- [ ] **Step 3: Run** `cd core-api && uv run pytest tests/test_service_status.py tests/test_services_router.py -q`. Expected: FAIL (modules not found).
- [ ] **Step 4: Implement** the model (export it from `models/__init__.py`), `service_status.py`, the router (prefix `/services`, tag `services`) and include it in `main.py` under `/api/v1`. Use `AppError` for errors, like `routers/actor.py`. Upsert with a get-then-set inside one transaction (SQLite and Postgres both supported). Add `git_commit` to `Settings`.
- [ ] **Step 5: Create the migration** with `cd core-api && uv run alembic revision -m "add service_status" --rev-id <new id>`, set `down_revision = "895863e431c6"`, write `upgrade`/`downgrade` for the table. Check `uv run alembic heads` shows one head.
- [ ] **Step 6: Run** the two test files plus `uv run pytest -q`, `uv run ruff check .`, `uv run mypy`. Expected: all PASS, no new lint or type errors.
- [ ] **Step 7: Commit** `feat(core-api): service status table with report and list endpoints`.

---

### Task 2: Core-api self-report and webapp `/version` poll (core-api)

**Files:**
- Create: `core-api/core_api/jobs/service_status_job.py`
- Modify: `core-api/core_api/scheduler.py`, `core-api/Dockerfile`
- Test: `core-api/tests/jobs/test_service_status_job.py`

**Interfaces:**
- Consumes: `record_report`, `Settings.git_commit`, `Settings.frontend_base_url`.
- Produces: `run_service_status_job(db: Session, now: datetime.datetime, fetch_version: Callable[[str], str | None] = fetch_webapp_commit) -> None` and `fetch_webapp_commit(base_url: str) -> str | None` (GET `<base_url>/version` with `httpx`, timeout 5 s, returns the `commit` string from the JSON body, or `None` on any transport error, non-200, non-JSON body or missing/invalid `commit`).

- [ ] **Step 1: Write failing tests**: the job writes a `core-api` row with `Settings.git_commit`; with `fetch_version` returning `"abc1234"` it writes a `webapp` row; with `fetch_version` returning `None` it writes no `webapp` row and leaves an existing one unchanged; `fetch_webapp_commit` against an `httpx.MockTransport` (patch the module's client factory, as the other core-api tests mock httpx) returns the commit for `{"commit":"abc1234"}` and `None` for a 500, for `not json`, for `{}` and for a timeout.
- [ ] **Step 2: Run** `uv run pytest tests/jobs/test_service_status_job.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the job file. Validate the fetched commit with the same pattern as Task 1 (export the compiled pattern from `service_status.py` and reuse it).
- [ ] **Step 4: Register the job** in `scheduler.py` `session_jobs` as `("service_status", run_service_status_job, IntervalTrigger(seconds=REPORT_INTERVAL_SECONDS))`, and make it also run once at scheduler start (`next_run_time=datetime.datetime.now(datetime.UTC)` on that job). Adjust the loop so `next_run_time` can be passed for this one job.
- [ ] **Step 5: Dockerfile**: add `ARG GIT_COMMIT=unknown` and `ENV GIT_COMMIT=$GIT_COMMIT` (copy the pattern and comment from `webapp/Dockerfile`).
- [ ] **Step 6: Run** `uv run pytest -q`, `uv run ruff check .`, `uv run mypy`. Expected: PASS.
- [ ] **Step 7: Commit** `feat(core-api): report own commit and poll the webapp /version`.

---

### Task 3: Webapp `/version` endpoint (webapp container)

**Files:**
- Create: `webapp/write-version.sh`, `webapp/write-version.test.sh`
- Modify: `webapp/docker-entrypoint.sh`, `webapp/nginx.conf`, `webapp/Dockerfile` (copy the script, point `HEALTHCHECK` at `/version`)

**Interfaces:**
- Produces: `write-version.sh <output-path>`, which writes `{"commit":"<first 7 chars of $GIT_COMMIT, or unknown>"}` to the path. nginx serves it at `GET /version` with `Content-Type: application/json` and `Cache-Control: no-store`.

- [ ] **Step 1: Write the failing test** `write-version.test.sh` (plain `sh`): with `GIT_COMMIT=0123456789abcdef` the file contains `{"commit":"0123456"}`; with `GIT_COMMIT` unset it contains `{"commit":"unknown"}`; with an empty value, `unknown`.
- [ ] **Step 2: Run** `sh webapp/write-version.test.sh`. Expected: FAIL (script missing).
- [ ] **Step 3: Implement** `write-version.sh` (POSIX sh, `set -eu`), call it from `docker-entrypoint.sh` with `/usr/share/nginx/html/version`.
- [ ] **Step 4: Add to `nginx.conf`** `location = /version { default_type application/json; add_header Cache-Control "no-store" always; include /etc/nginx/security-headers.conf; }` (the include is needed because `add_header` in a location drops inherited headers).
- [ ] **Step 5: Verify** `sh webapp/write-version.test.sh` passes. Then build and probe: `docker build --build-arg GIT_COMMIT=0123456789abcdef -t ww-webapp-test webapp && docker run --rm -d -p 8089:80 -e API_BASE_URL=http://localhost:8000 --name ww-webapp-test ww-webapp-test && curl -si localhost:8089/version; docker rm -f ww-webapp-test`. Expected: `200`, `application/json`, body `{"commit":"0123456"}`, and `docker inspect` health becomes `healthy`.
- [ ] **Step 6: Commit** `feat(webapp): serve the build commit as JSON at /version`.

---

### Task 4: Commit reporters in the five Python services

**Files (per service, same shape in each):**
- Create: `<pkg>/service_report.py`, `<pkg>/tests/test_service_report.py` (package dirs: `envoy_collector`, `weatherlink_local_collector`, `amber_pricing_collector`, `local_pricing_collector`, `actron_neo_actor`; each service has its own `tests/` directory)
- Modify: `<pkg>/main.py`, `<service>/Dockerfile`

**Interfaces (identical in each service; copy, do not share, because each image builds from its own directory):**
- `report_commit_once(base_url: str, key_header: str, key: str, service: str, commit: str) -> bool` POSTs `{"service","commit"}` to `<base_url>/api/v1/services/report` (header `key_header: key`, timeout 10 s) and returns `True` on 204, `False` on any other status or transport error (never raises).
- `start_commit_reporter(base_url: str, key_header: str, key: str, service: str, interval_seconds: float = 60.0) -> threading.Thread`: daemon thread that reads `GIT_COMMIT` from the environment (first 7 characters, `unknown` when unset or empty), reports immediately, then every `interval_seconds` until the process exits.

Per-service values:

| Service | `service` | `key_header` | Key and URL source |
|---|---|---|---|
| envoy-collector | `envoy-collector` | `X-Collector-Key` | `settings.collector_key`, `settings.core_api_url` |
| weatherlink-local-collector | `weatherlink-local-collector` | `X-Collector-Key` | same attributes |
| amber-pricing-collector | `amber-pricing-collector` | `X-Collector-Key` | same attributes |
| local-pricing-collector | `local-pricing-collector` | `X-Collector-Key` | same attributes |
| actron-neo-actor | `actron-neo-actor` | `X-Actor-Key` | `settings.actor_key`, `settings.core_api_url` |

- [ ] **Step 1: In envoy-collector, write failing tests** (`respx` is already used by that service's tests): `report_commit_once` sends the right header and JSON body and returns `True` on 204; returns `False` on 401, on 500 and on a connection error; `start_commit_reporter` with `interval_seconds=0.01` posts at least twice and the thread is a daemon; `GIT_COMMIT` unset sends `unknown`; `GIT_COMMIT=0123456789abcdef` sends `0123456`.
- [ ] **Step 2: Run** `cd envoy-collector && uv run pytest tests/test_service_report.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `service_report.py`; call `start_commit_reporter(...)` at the top of `main()` after settings load (for the actor, in `main()` before `asyncio.run`, using the `Settings` returned by `get_settings()`); add `ARG GIT_COMMIT=unknown` / `ENV GIT_COMMIT=$GIT_COMMIT` to the Dockerfile after the dependency layers so code-only changes keep the dependency cache.
- [ ] **Step 4: Run** that service's `uv run pytest -q`, `uv run ruff check .`, `uv run mypy`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(envoy-collector): report the build commit to core-api`.
- [ ] **Step 6: Repeat Steps 1-5** for the other four services using the table above, one commit each. Confirm each service's real `Settings` attribute names before wiring (they matched `collector_key` / `core_api_url` / `actor_key` when this plan was written).

---

### Task 5: Licence data generator

**Files:**
- Create: `scripts/generate_licences.py`, `scripts/test_generate_licences.py`, `webapp/public/licences.json` (generated and committed as the starting snapshot)
- Modify: `.github/workflows/publish-images.yml` (regenerate before the webapp build)

**Interfaces:**
- Produces file format (`webapp/public/licences.json`):
  `{"generated_at": "<ISO UTC>", "texts": {"<id>": "<licence text>"}, "packages": [{"name": str, "version": str, "ecosystem": "npm" | "python", "license": str, "text_id": str | null}]}`
  Identical texts share one `texts` entry; `text_id` is `null` when no text was found; `license` is `"unknown"` when none was found. Packages are sorted by lowercase name, then version. A package seen in several services appears once per `(ecosystem, name, version)`.
- Functions in the script: `npm_packages(lockfile: pathlib.Path, node_modules: pathlib.Path) -> list[Package]` (from `packages` entries without `dev: true`; licence from the lockfile's `license` field; text from the first `LICENSE*`/`LICENCE*`/`COPYING*` file in the installed package, if installed), `python_packages(service_dir: pathlib.Path) -> list[Package]` (names and versions from `uv export --frozen --no-dev --no-hashes --no-emit-project` run in the service directory; the registry JSON at `https://pypi.org/pypi/<name>/<version>/json` supplies `license_expression`, else `license` when under 100 characters, else the first `License ::` classifier; text from `info.license` when 100 characters or longer), `build_document(packages: list[Package]) -> dict`, and `main() -> None` (writes the file, exits 0 even when some lookups fail, prints a count of unknown licences).

- [ ] **Step 1: Write failing tests** with fixture lockfile and mocked PyPI responses: dev npm entries are skipped; a package present in two services is listed once; identical texts dedupe to one `texts` entry; a PyPI 404 gives `license: "unknown"`, `text_id: null` and no exception; output ordering is as specified.
- [ ] **Step 2: Run** `python3 -m pytest scripts/test_generate_licences.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the script (standard library plus `urllib.request`, so it needs no install in CI). Services scanned: the five Python services plus `core-api`; npm from `webapp/package-lock.json`.
- [ ] **Step 4: Run the generator** locally (`cd webapp && npm ci` first so licence files exist), review `webapp/public/licences.json` for a sample of known packages (react, fastapi, httpx), and commit it.
- [ ] **Step 5: CI**: in `publish-images.yml`, add steps before the build that run only when `matrix.service == 'webapp'`: `actions/setup-node@v4` with `npm ci` in `webapp/`, `astral-sh/setup-uv@v6`, then `python3 scripts/generate_licences.py`. The regenerated file is not committed back.
- [ ] **Step 6: Commit** `feat: generate the package licence list for the About dialog`.

---

### Task 6: About dialog and menu item (webapp)

**Files:**
- Create: `webapp/src/about/AboutDialog.tsx`, `ContainersTab.tsx`, `LicencesTab.tsx`, `useServices.ts`, `useLicences.ts`, and a test file next to each component
- Modify: `webapp/src/buildInfo.ts`, `webapp/src/layout/Header.tsx`, `webapp/src/layout/Header.test.tsx`

**Interfaces:**
- `buildInfo.ts` adds `commitUrl(commit: string): string | null` (null unless the commit matches `^[0-9a-f]{7,40}$`); `BUILD_COMMIT_URL` is rewritten to use it.
- `useServices.ts`: `interface ServiceStatus { service: string; commit: string | null; last_reported_at: string | null; reporting: boolean }` and `useServices(enabled: boolean)` (react-query key `["services"]`, `apiFetch<ServiceStatus[]>("/api/v1/services")`, `refetchInterval: 30_000`).
- `useLicences.ts`: `interface LicenceDocument { generated_at: string; texts: Record<string, string>; packages: LicencePackage[] }`, `interface LicencePackage { name: string; version: string; ecosystem: "npm" | "python"; license: string; text_id: string | null }` and `useLicences(enabled: boolean)` (key `["licences"]`, plain `fetch` of `${import.meta.env.BASE_URL}licences.json`, throws on a non-200 response or invalid JSON, `staleTime: Infinity`).
- `AboutDialog({ onClose }: { onClose: () => void })`: `role="dialog"`, `aria-modal="true"`, `aria-labelledby` pointing at the "About WattWise" heading; closes on Escape, on the Close button and on a backdrop click; tabs "Containers" and "Licences" use `role="tablist"`/`tab`/`tabpanel` with Left/Right arrow switching; only the active tab's data hook is enabled.
- `ContainersTab`: one row per service showing its name, the commit (a link when `commitUrl` is non-null, plain text otherwise) and the last-reported time. A row with `reporting: false` shows "not reporting" and, beneath it, "last reported <local date and time>" or "never reported" when `last_reported_at` is null.
- `LicencesTab`: a filter text box; a `role="listbox"` of `name version` options (`role="option"`, `aria-selected`, Up/Down/Home/End keys) with a detail pane showing the licence name and its text in a `<pre>` (or "No licence text available." when `text_id` is null). The first package is selected on load. An error message appears if `useLicences` fails.
- `Header.tsx`: a `role="menuitem"` button "About" directly above "Log off", styled with `menuItemClass`; it closes the menu and opens `AboutDialog`. When the dialog closes, focus returns to the account menu button.

- [ ] **Step 1: Write failing tests**. `buildInfo` tests: `commitUrl("abc1234")` is the GitHub URL, `commitUrl("unknown")` and `commitUrl("dev")` are null. `ContainersTab` tests (mock `useServices` or `apiFetch`): a reporting service shows its commit as a link to the right URL; a non-reporting service with a time shows "not reporting" and "last reported"; one without shows "never reported"; all 7 services render. `LicencesTab` tests (mock `fetch`): the list renders, selecting a second package shows its text, the filter narrows the list, a null `text_id` shows the fallback, a failed fetch shows an error and no crash. `AboutDialog` tests: Escape calls `onClose`, arrow keys switch tabs, the Licences request is not made until that tab is opened. `Header` test: the menu lists "About" immediately before "Log off", clicking it opens a dialog named "About WattWise", and after Escape focus is on the menu button.
- [ ] **Step 2: Run** `cd webapp && npx vitest run src/about src/layout/Header.test.tsx src/buildInfo` (add a `buildInfo.test.ts` if none exists). Expected: FAIL.
- [ ] **Step 3: Implement** the files above. Match the existing Tailwind tokens used in `Header.tsx` (`bg-surface`, `border-line`, `text-ink`, `text-ink-muted`, `hover:bg-surface-2`). Keep each component under 30 lines per function by splitting row and detail pieces into small components. Dialog width `max-w-3xl`, `max-h-[85vh]`, scroll inside the panes, single column below the `sm` breakpoint.
- [ ] **Step 4: Run** `npm test`, `npm run lint`, `npm run build`. Expected: all PASS.
- [ ] **Step 5: Run the app** (use the `run` skill or `npm run dev` with core-api up), open the dialog, and confirm in the browser: both tabs work, the webapp row shows "not reporting" in `vite dev` (no `/version`), Escape closes, focus returns. Report what was and wasn't checked.
- [ ] **Step 6: Commit** `feat(webapp): About dialog with container commits and package licences`.

---

### Task 7: Documentation and final checks

**Files:**
- Modify: `docs/core-api.md`, `docs/operations.md`, `docs/webapp.md`, `docs/architecture.md` (only where each describes endpoints, services or the menu); `README.md` if it lists the account menu.

- [ ] **Step 1:** Use the `documentation` agent to update the docs: the two endpoints, the `service_status` table, the 60 s report interval and 180 s cutoff, `GIT_COMMIT` build arg for every image, the `/version` endpoint and its use of `FRONTEND_BASE_URL`, mail-relay not reporting, and how to regenerate `webapp/public/licences.json`.
- [ ] **Step 2:** Run each package's checks: `uv run ruff check . && uv run mypy && uv run pytest -q` in the six Python packages; `npm run lint && npm run build && npm test` in `webapp`. Expected: all PASS.
- [ ] **Step 3:** Bring the stack up with `docker compose up -d --build`, wait 90 s, open the About dialog and confirm all 7 services show "reporting" with commit `unknown` (local builds pass no `GIT_COMMIT`), then stop `amber-pricing-collector` and confirm its row reads "not reporting" within about 4 minutes.
- [ ] **Step 4:** Run `/simplify` (required by `CLAUDE.md`) and the Code Review Standards checklist, then commit `docs: describe the About dialog and service status reporting`.
