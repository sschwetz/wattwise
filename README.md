# WattWise

WattWise records and displays energy data for a home solar and battery system: production and consumption from an Enphase Envoy, buy and sell prices from Amber or from fixed rates entered in the webapp, and weather from a Davis WeatherLink Live. It supports several sites and users, net and gross usage, and demand-tariff billing.

## Services

| Directory | What it is |
|---|---|
| `core-api/` | FastAPI + SQLAlchemy + Alembic on SQLite. The only service that touches the database. Serves the REST API under `/api/v1` and runs the periodic jobs (demand intervals, demand charges, retention rollup). |
| `envoy-collector/` | Polls the Envoy local API every minute and pushes readings to core-api. Renews the Envoy token from stored Enlighten credentials. |
| `amber-pricing-collector/` | Polls Amber prices, with live polling and backfill. |
| `local-pricing-collector/` | Turns rate windows entered in the webapp into price intervals, for suppliers with a fixed tariff. At 23:59 in each site's time zone it pushes the next local day's prices. Within a minute of a new source, a restart or a change to the rate windows, and on the first check of each new day, it also fills what is missing, back to the site's first data. |
| `weather-collector/` | Polls the WeatherLink Live. |
| `webapp/` | React single-page app (Vite, Tailwind, TanStack Query, Recharts). Includes an in-app Help viewer. |
| `mail-relay/` | Postfix relay that sends password-reset and email-confirmation mail for core-api. |

The webapp and the collectors only talk to core-api over HTTP. Each collector kind authenticates with one shared key and discovers its sources from core-api, so adding a site in the webapp needs no collector restart.

Both pricing collectors use `PRICING_COLLECTOR_KEY`. Each one asks for its own provider's sources with `GET /api/v1/collector/sources?kind=pricing&provider=<name>` and sends an `X-Collector-Provider: <name>` header. The filter keeps the Amber collector off local sources and the reverse, and core-api rejects a per-source request whose header names a different provider from the source's. Neither is a security boundary: any process holding the shared key can name another provider.

To add a collector for another supplier, create a directory like `amber-pricing-collector/` and give it its own provider name. Use that name in the discovery request (`provider=<name>`) and in the `X-Collector-Provider` header. Read each source's settings from `GET /api/v1/sources/{id}/config` and push prices to `POST /api/v1/sources/{id}/prices`.

Design specs and implementation plans are in `docs/superpowers/`.

## Start a development instance

You need Python 3.11 or later with [uv](https://docs.astral.sh/uv/), and Node 22 or later. The steps below run core-api and the webapp natively with hot reload. Docker is only needed for the full-stack option further down.

### 1. Start core-api

```sh
cd core-api
uv sync

export JWT_SECRET=$(openssl rand -hex 32)
export ENVOY_COLLECTOR_KEY=dev-envoy-key
export WEATHER_COLLECTOR_KEY=dev-weather-key
export PRICING_COLLECTOR_KEY=dev-pricing-key

uv run python -m core_api.bootstrap
uv run uvicorn core_api.main:create_app --factory --reload --port 8000
```

`core_api.bootstrap` creates or upgrades the SQLite database (`core_api.db` in the current directory). Run it again after pulling new migrations.

Core-api refuses to start without `JWT_SECRET` and the three collector keys. Other settings and their defaults are in `core-api/.env.example` and `core-api/core_api/config.py`. The defaults work for development: CORS allows `http://localhost:5173`, which is where Vite serves the webapp.

Check that it is up:

```sh
curl http://localhost:8000/health
```

The interactive API docs are at <http://localhost:8000/docs>.

### 2. Get the admin login

On first start, with an empty users table, core-api creates a user and writes its one-time password to `initial-admin-password.txt` next to the database:

```sh
cat core-api/initial-admin-password.txt
```

The email is `admin@example.com` unless you set `ADMIN_EMAIL` before the first start. Log in, change the password, then delete the file.

Password-reset and email-confirmation mail goes to `SMTP_HOST` (default `mail-relay`). That host does not exist in a native run, so those emails fail to send. Log in with the admin password instead, or point `SMTP_HOST` and `SMTP_PORT` at a local catch-all such as [MailHog](https://github.com/mailhog/MailHog).

### 3. Start the webapp

```sh
cd webapp
npm install
npm run dev
```

Open <http://localhost:5173>. The app calls core-api at `http://localhost:8000` by default.

### 4. Start a collector (optional)

Create a site and a source in the webapp first. Then run the matching collector with the same key you gave core-api. This example is the Envoy collector:

```sh
cd envoy-collector
uv sync
CORE_API_URL=http://localhost:8000 COLLECTOR_KEY=dev-envoy-key \
  uv run python -m envoy_collector.main
```

The other collectors work the same way. The Amber and local pricing collectors both take `PRICING_COLLECTOR_KEY` as their `COLLECTOR_KEY` and run as `amber_pricing_collector.main` and `local_pricing_collector.main`. The weather collector takes `WEATHER_COLLECTOR_KEY` and runs as `weather_collector.main`. The Envoy and weather collectors must run on a machine that can reach the device's LAN address.

## Run the full stack in Docker

This builds every service and starts it behind the ports Compose publishes: the webapp on 80 and core-api on 8000.

```sh
cp .env.example .env
```

Edit `.env`: set `JWT_SECRET` and the three `*_COLLECTOR_KEY` values (`openssl rand -hex 32` works for each). The defaults for `FRONTEND_BASE_URL` and `API_BASE_URL` suit a single machine. `RELAY_DOMAIN` and `RELAY_SMARTHOST` are required by Compose even if you don't plan to send mail. Then:

```sh
docker compose up --build
```

Open <http://localhost>. The admin password is inside the core-api container:

```sh
docker compose exec core-api cat /data/initial-admin-password.txt
```

Data lives in the `core_api_data` volume. `docker compose down -v` deletes it.

### Upgrading from `pricing-collector`

The Compose service `pricing-collector` is now `amber-pricing-collector`. On a host that already runs the stack, `docker compose up -d` leaves the old `pricing-collector` container running, and it keeps polling Amber alongside the new one. Upgrade with:

```sh
docker compose up -d --build --remove-orphans
```

## Tests and lint

```sh
cd core-api && uv run pytest           # likewise in each collector directory
cd webapp && npm test && npm run lint
```

## Contributing

`CLAUDE.md` lists the review checks to run after a change. Prose, including comments and commit messages, follows `.claude/rules/avoid-ai-writing.md`.
