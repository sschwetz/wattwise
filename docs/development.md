# Development

How to run, test and change the code. Setup steps for a native (non-Docker) run are in the root [README](../README.md#start-a-development-instance); this page covers what comes after.

## Repository layout

```text
wattwise/
├── core-api/                    FastAPI app, models, Alembic migrations, tests
├── envoy-collector/             one directory per collector, same shape each
├── amber-pricing-collector/
├── local-pricing-collector/
├── weatherlink-local-collector/
├── webapp/                      React app and the Markdown user guide (src/docs)
├── mail-relay/                  Postfix container
├── docs/
│   ├── (this documentation)
│   └── superpowers/             dated specs and plans for each feature
├── docker-compose.yml
├── CLAUDE.md                    review rules for AI-assisted work
└── .claude/                     agent definitions and the writing-style rules
```

Python projects use [uv](https://docs.astral.sh/uv/) with a committed `uv.lock`. Each has its own `pyproject.toml` and virtual environment. The webapp uses npm with `package-lock.json`.

## Running the tests

```bash
cd core-api && uv sync && uv run pytest            # API, jobs, cost maths
cd envoy-collector && uv sync && uv run pytest      # likewise for each collector
cd webapp && npm install && npm test                # Vitest
cd webapp && npm run lint                           # Oxlint
cd webapp && npm run build                          # type-checks, then builds
```

Each Python package is also checked with Ruff and mypy, configured in its `pyproject.toml`:

```bash
cd core-api && uv run ruff check . && uv run mypy   # likewise for each collector
```

The `CI` workflow (`.github/workflows/ci.yml`) runs Ruff, mypy and the tests for every Python package, and lint, `tsc` and Vitest for the webapp, on pushes to `master` and on pull requests.

Core API tests need no environment setup: `tests/conftest.py` sets the secrets (including a test `SECRETS_ENCRYPTION_KEY`; do not hardcode real keys anywhere else), builds a fresh SQLite database through the real migrations for every test, and swaps in a fake email sender. The scheduler never starts, because the tests do not run the app lifespan. If you add a required environment variable to `config.py`, add it to `conftest.py` too, or every test fails at startup.

Test files mirror modules: `core_api/cost.py` is covered by `tests/test_cost.py`, `routers/billing.py` by `tests/test_billing.py`, and the jobs by `tests/jobs/`. Collectors keep JSON fixtures of real device responses in `tests/fixtures/`.

## Changing the database

1. Edit the model in `core-api/core_api/models/`.
2. Generate a migration. The Alembic environment imports the app's settings, so the required variables must be set:

   ```bash
   cd core-api
   export JWT_SECRET=x ENVOY_COLLECTOR_KEY=x WEATHER_COLLECTOR_KEY=x PRICING_COLLECTOR_KEY=x
   export SECRETS_ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')
   uv run alembic revision --autogenerate -m "describe the change"
   ```

3. Read the generated file in `alembic/versions/`. Autogenerate misses some things (check constraints, partial indexes) and SQLite cannot alter most constraints in place, so changing one often needs a table rebuild with `batch_alter_table`. The existing migrations show the pattern.
4. Apply it with `uv run python -m core_api.bootstrap`, and add or update tests. `tests/test_schema_constraints.py` checks the constraints.

Enums are stored as text with a database `CHECK`. Adding an enum member (a new energy metric, a new channel) therefore needs a migration that replaces the constraint, not only a Python change.

## Conventions

These come from [`CLAUDE.md`](../CLAUDE.md) and the code itself.

**Review checklist** (from `CLAUDE.md`). After finishing a change, check for:

- functions longer than 30 lines
- logic duplicated more than twice
- any use of the TypeScript `any` type
- components with more than 3 props that could be grouped
- missing error handling on async operations
- secrets stored in plain text

**Storing secrets** (the Storing Secrets section of `CLAUDE.md`). Pick the storage by whether the app needs the value back:

| The app... | Use |
|---|---|
| Only checks the value (passwords, reset tokens, recovery codes) | A one-way hash: `hash_password`, `hash_token` for 256-bit random tokens, `hash_recovery_code` for short codes. Never plain SHA-256 on a low-entropy value. |
| Must send the value on (device tokens, third-party passwords, API keys, TOTP secrets) | The `EncryptedString` or `EncryptedJSON` column types in `core_api/encrypted_types.py` |

A new credential column needs one of these and a migration that converts existing rows, in the same change. Encrypted columns cannot be filtered in SQL, so load the row and compare in Python. Never print secret values in logs, replies, commit messages or docs, and keep keys in environment variables, not files.

**Feature workflow.** Larger changes are written up as a dated design spec in `docs/superpowers/specs/` and an implementation plan in `docs/superpowers/plans/` before code. Treat them as history: do not edit them to match later code. Add a short dated note at the end if a later change contradicts one.

**Prose.** Comments, docs, commit messages and PR text follow [`.claude/rules/avoid-ai-writing.md`](../.claude/rules/avoid-ai-writing.md): plain words, no inflated significance, specific claims.

**Comments.** Existing code explains why, not what, and often records the failure that motivated a choice. Keep that habit; it is how the next person learns that a quirk is deliberate.

**Money and time.** `Decimal` for money, never `float`. UTC everywhere; convert to the site's time zone only when evaluating a rule. Use `as_utc()` on any datetime read from SQLite.

## Recipes

### Add an API endpoint

1. Pick the dependency that matches who may call it (see [Core API](core-api.md#request-lifecycle)).
2. Add request and response models to `schemas.py`.
3. Add the function to the matching router in `routers/`, or a new router registered in `main.py`.
4. Raise `AppError(status, code, message)` for expected failures. Use a new stable `code` string when the web app needs to react to it.
5. Add tests in `tests/`, including the unauthorized and wrong-site cases.

### Add an energy or weather metric

1. Add the enum member in `models/readings.py` and an entry in the aggregation map (`ENERGY_METRIC_AGGREGATION` or `WEATHER_METRIC_AGGREGATION`).
2. Write a migration that replaces the `CHECK` constraints on the raw and rollup tables.
3. Emit it from the collector's `mapping.py`.
4. Show it in the web app and document it in the [Data model](data-model.md#metrics) and the user guide glossary.

### Add a kind of tariff rule

Follow `ConditionalCreditWindow`, the most recent one: model, migration, schema in `schemas.py`, endpoints in `routers/billing.py`, its effect in `cost.py` (with tests in `tests/test_cost.py`), a form on the billing page, a chapter section in the user guide, and a spec and plan.

### Add a background job

Write `run_x_job(db, now)` in `core_api/jobs/`, make it idempotent (upsert, or finalize once), and register it in `scheduler.py` with `coalesce=True, max_instances=1`. Take `now` as a parameter so tests can control time.

### Add a collector

See [Adding a provider](adding-a-provider.md#part-2-the-technical-contract).

## Traps

| Trap | What happens | Rule |
|---|---|---|
| Naive datetimes from SQLite | `can't compare offset-naive and offset-aware` | Run `as_utc()` after reading |
| Sell price sign | Revenue shown as cost, or the reverse | Sell is negative when paid. Local rates are stored positive and flipped by the collector. |
| Price timestamps with seconds | Cost misses the price because the join is on exact minutes | Ingestion floors to the minute. Do not bypass `_fan_out_price_readings`. |
| Several Uvicorn workers | Each runs the jobs, lockouts do not share | Keep one worker |
| SQLite-only SQL | `sqlite_insert ... on_conflict_do_update` fails on Postgres | Port the upserts first if the database ever moves |
| New env var in `config.py` | All tests fail on startup | Add it to `tests/conftest.py` |
| Help docs and screenshots | Link tests fail | Keep chapter links and image names valid |
| Editing the specs and plans | Rewrites history | Add a dated note instead |
| Filtering or comparing an encrypted column in SQL | Never matches, because each encryption has a random IV | Load the row and compare in Python, or test `IS NOT NULL` |
| Changing `SECRETS_ENCRYPTION_KEY` | Encrypted columns fail to decrypt; recovery codes stop matching | Treat the key as permanent |
| Changing a `provider` string | Existing sources stop matching their collector | Treat provider names as permanent |
