# Operations

Everything needed to run WattWise day to day, and to take it over cold. If you are the person inheriting it, read [Handover checklist](#handover-checklist) first.

## What runs where

One machine runs the whole stack with Docker Compose. It must be able to reach the Envoy and the WeatherLink Live on the home network, because the collectors run in the same stack. The project assumes Docker on Colima. The commands below are plain `docker compose`.

| Port | Service | Notes |
|---|---|---|
| 80 | `webapp` | Plain HTTP |
| 8000 | `core-api` | Plain HTTP. The browser calls this directly. |

Both ports are plain HTTP. Keep them on the home network. To use WattWise from outside, put a reverse proxy that terminates HTTPS in front of both and make the API reachable from the internet. See [Putting WattWise on the internet](#putting-wattwise-on-the-internet).

The dashboard's live tiles use a WebSocket to `core-api` (`/api/v1/sites/{site_id}/live`). A reverse proxy in front of `core-api` must pass WebSocket upgrades (the `Upgrade` and `Connection` headers), or the tiles fall back to the 60 second poll. The webapp container sets its Content-Security-Policy `connect-src` to `API_BASE_URL` and the same address with the `ws://` or `wss://` scheme, so no extra setting is needed.

## Putting WattWise on the internet

The browser calls `core-api` directly, so the API must be reachable from the internet at the address in `API_BASE_URL` whenever anyone uses WattWise away from home. Publishing only the web app is not enough: the page loads, then sign-in fails. The collectors do not need this. They reach `core-api` over the Docker network.

`deploy/caddy/` holds a tested Compose override that adds Caddy, serves both the web app and the API from one hostname and gets the certificate itself.

```bash
# .env
WATTWISE_DOMAIN=wattwise.example.com
FRONTEND_BASE_URL=https://wattwise.example.com
API_BASE_URL=https://wattwise.example.com
COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml:deploy/caddy/docker-compose.caddy.yml
```

```bash
docker compose pull
docker compose up -d
curl https://wattwise.example.com/health      # {"status":"ok"}
```

| Step | Detail |
|---|---|
| DNS | An `A` record for the hostname pointing at the public IP. Use dynamic DNS if the address changes. |
| Router | Forward TCP 80 and 443 to the host. Port 80 is for the certificate challenge. Never forward 8000. |
| Compose override | Removes the published ports of `webapp` and `core-api` (`ports: !reset []`), adds `caddy` on the `app` network, keeps certificates in the `caddy_data` volume. |
| Routing | `/api/*` and `/health` go to `core-api:8000`. Everything else goes to `webapp:80`. `/docs` is not forwarded. |
| WebSocket | Caddy passes the live-tile WebSocket without extra settings. Another proxy needs upgrade headers and a read timeout of an hour or more. |
| Environment | Both `*_BASE_URL` values must be `https://`. A page on HTTPS cannot call an HTTP API. `FRONTEND_BASE_URL` must equal the browser's address-bar origin because it is the only CORS origin. |
| Changing the URLs | Run `docker compose up -d` so `webapp` and `core-api` are recreated. `webapp` writes `config.js` and its CSP at container start. |

For nginx, Nginx Proxy Manager and a two-hostname layout, see the user guide chapter [Installing with Docker Compose](../webapp/src/docs/01-installing-with-docker.md#using-it-outside-your-home-network). Back up `caddy_data` if you want to keep the certificate across a rebuilt host. A fresh one is issued automatically otherwise, within Let's Encrypt's rate limits.

If the proxy host differs from the stack host, the proxy forwards to the stack host's published ports and those ports must be blocked from the internet at the firewall.

**Exposure.** Anything under `/api/*` is public once the proxy is up: login, the user and site endpoints, and the collector endpoints (guarded by the collector keys). Login is protected by bcrypt passwords, TOTP, and the lockout described in [Core API](core-api.md). Keep the collector keys secret and long, and keep the images updated.

## First start

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | What to put |
|---|---|
| `JWT_SECRET` | `openssl rand -hex 32` |
| `SECRETS_ENCRYPTION_KEY` | `openssl rand -base64 32 \| tr '+/' '-_'`. Encrypts stored device credentials and Amber keys. **Copy it somewhere off this machine.** Without it the encrypted data in a backup cannot be read. |
| `ENVOY_COLLECTOR_KEY`, `WEATHER_COLLECTOR_KEY`, `PRICING_COLLECTOR_KEY` | A different `openssl rand -hex 32` for each |
| `FRONTEND_BASE_URL` | Where users open the web app. Default `http://localhost`. Also the CORS origin and the base of email links. |
| `API_BASE_URL` | Where the **browser** reaches Core API. Default `http://localhost:8000`. |
| `SMTP_FROM` | Sender address for system mail |
| `RELAY_DOMAIN`, `RELAY_SMARTHOST` | Required by Compose even if you never send mail. `RELAY_SMARTHOST` looks like `[smtp.example.com]:587`. |
| `RELAY_USERNAME`, `RELAY_PASSWORD` | If the smarthost needs a login |

Then:

```bash
docker compose up --build -d
docker compose ps
curl http://localhost:8000/health        # {"status":"ok"}
```

Core API's container runs migrations and creates the first user before it starts answering `/health`, and the other services wait for that health check. The health check allows 30 seconds of start-up before failures count.

### First login

```bash
docker compose exec core-api cat /data/initial-admin-password.txt
```

Sign in as `admin@example.com` (or the `ADMIN_EMAIL` set before the first start), set up two-factor, change the password, then delete the file:

```bash
docker compose exec core-api rm /data/initial-admin-password.txt
```

The file exists only if the database had no users at first start. If you don't delete the file, Core API deletes it once it is 5 days old. The check runs at every start and daily at 03:17.

## Day-to-day

### Is it working?

| Check | Command or place | Healthy |
|---|---|---|
| Containers up | `docker compose ps` | All `running`, `core-api` `healthy` |
| API answering | `curl http://localhost:8000/health` | `{"status":"ok"}` |
| Data flowing | Dashboard live tiles | Values change within a minute or two |
| Per-source problems | Logs (below) | No repeating errors |

### Reading logs

```bash
docker compose logs -f --tail=100 envoy-collector
docker compose logs -f --tail=100 core-api
docker compose logs --since=1h amber-pricing-collector local-pricing-collector
```

Collectors log one line per failure and carry on. A repeating line is the symptom to look for. Postfix logs to stdout, so `docker compose logs mail-relay` shows delivery failures.

### Restart one service

```bash
docker compose restart envoy-collector
```

Collectors and the web app can be restarted at any time. Restarting `core-api` drops in-progress requests and clears login lockouts, and collectors retry on their next cycle.

## Backups

All state is in the `core_api_data` volume, mounted at `/data` in `core-api`: the database (`core_api.db`) and uploaded profile photos (`avatars/`). Nothing else holds data.

Take a consistent copy of the database while it is running, using SQLite's own backup routine:

```bash
docker compose exec core-api python3 -c "
import sqlite3
src = sqlite3.connect('/data/core_api.db')
dst = sqlite3.connect('/data/backup.db')
src.backup(dst)
dst.close()
"
docker compose cp core-api:/data/backup.db ./wattwise-$(date +%F).db
docker compose exec core-api rm /data/backup.db
```

Copying `core_api.db` directly while Core API is writing can produce a corrupt copy. Use the routine above.

The backup holds credentials only in encrypted form, so it is useless without `SECRETS_ENCRYPTION_KEY`. Store the key and the backup in different places, and keep both. A restored database needs the same key it was written with.

Also copy the avatars if you care about them: `docker compose cp core-api:/data/avatars ./avatars-backup`.

Store backups somewhere other than this machine. The Python backup routine was tested here; the `docker compose cp` lines were not run against a live stack.

**Restore:**

```bash
docker compose stop core-api
docker compose cp ./wattwise-2026-10-14.db core-api:/data/core_api.db
docker compose start core-api
```

Startup runs any migrations the restored file is missing. Restoring onto a server with a different `SECRETS_ENCRYPTION_KEY` makes source configs, Amber keys and authenticator secrets unreadable.

**What a gap costs.** Readings the collectors could not deliver while Core API was down are lost (energy, weather). Prices are rebuilt: Amber by its backfill (one pass a minute after startup, then every 12 hours), local tariffs by the next catch-up.

## Upgrading

```bash
git pull
docker compose up -d --build --remove-orphans
```

To run the published images instead of building from source, add `-f docker-compose.yml -f docker-compose.prod.yml` to the `docker compose` commands and replace `git pull` with `docker compose pull`. See "Run in production" in the root `README.md`.

Migrations run automatically when `core-api` starts. Take a backup first. Rolling back means checking out the previous commit and restoring the backup taken before the upgrade. The encryption migration has a downgrade that decrypts the columns again, but downgrades are otherwise untested in this project.

**Upgrading to the encryption release (commit `79651e9` or later):**

1. Generate `SECRETS_ENCRYPTION_KEY` and add it to `.env` before running `docker compose up`. Compose refuses to start `core-api` without it.
2. Back up the key as described under [Backups](#backups).
3. After the upgrade, every user with two-factor enabled has lost their recovery codes. The migration deletes them because the old hashes cannot be converted. Tell users to open Account settings and use **Replace authenticator** to get new ones. Signing in with the authenticator app is unaffected.

`--remove-orphans` matters after a service rename. The `pricing-collector` service became `amber-pricing-collector`, and without the flag the old container keeps running and polls Amber alongside the new one.

## Secrets

| Secret | Where it lives | Effect of changing it | How to rotate |
|---|---|---|---|
| `JWT_SECRET` | `.env` | Every user is logged out | Edit `.env`, `docker compose up -d` |
| `SECRETS_ENCRYPTION_KEY` | `.env` | Stored device credentials, Amber keys and authenticator secrets become unreadable, and all recovery codes stop working | **Do not rotate.** No tool re-encrypts existing rows under a new key. |
| `*_COLLECTOR_KEY` (3) | `.env`, used by `core-api` and that kind's collectors | Collectors get 401 until both sides match | Edit `.env`, then `docker compose up -d` so both sides restart together |
| Admin password | Database (bcrypt) | n/a | Change it in Account settings |
| Envoy token, Enlighten login | `sources.config` in the database | Readings stop if wrong | Edit the source in the web app |
| Amber API key | `users.amber_api_key` | Prices stop if wrong | Account settings |
| SMTP login | `.env` (`RELAY_USERNAME`, `RELAY_PASSWORD`) | Email stops | Edit `.env`, restart `mail-relay` |

Never commit `.env`. The repository ignores it, and only `.env.example` is tracked.

Stored credentials are encrypted. `sources.config` (Envoy token, Enlighten login), `users.amber_api_key` and the authenticator secrets are Fernet-encrypted with `SECRETS_ENCRYPTION_KEY`. Passwords are bcrypt hashes and recovery codes are keyed hashes. A stolen database file does not expose these values without the key. The full list and the reasoning are in [Core API](core-api.md#where-secrets-are-stored).

The consequences for you:

- **Losing the key loses those values.** You would re-enter every source's credentials and Amber key, and every user would replace their authenticator.
- **The key lives in `.env` on the same machine as the stack**, so someone who can read `.env` and the database together can decrypt. The protection is against a copied database or backup, not against a compromised host.
- **The `core-api` container refuses to start without the key**, and Compose refuses to run `core-api` if `.env` lacks it.

## Things that break on a schedule

| What | When | How you notice | Fix |
|---|---|---|---|
| Envoy access token | About 1 year after issue | Envoy collector logs `still invalid`, with the renewal URL. Dashboard goes stale. | Store the Enlighten login on the source (automatic renewal), or paste a new token. |
| Envoy TLS certificate | The Envoy renews it when within 100 days of expiry | Log line about a certificate change | Normally automatic. If refused, see [Collectors](collectors.md#failure-behaviour-at-a-glance). |
| Amber API key | If revoked or rotated | Pricing log errors, no new prices | New key in Account settings |
| Raw readings | Past 365 days | Not visible. They turn into 5-minute averages overnight. | None |
| Demand charge estimates | Until a billing period closes | `is_estimated` flag on the Billing page | Becomes final at the next 00:10 job after the period closes |
| Rate windows | When your retailer changes rates | Costs drift from the bill | End the old window, add the new one from today |

## Known issues

- Core API must run as a single worker (the default `uvicorn` command). Lockouts and the job scheduler are per process.
- The live tile store and its WebSocket subscribers are also per process, and live samples are lost on restart.
- Lost readings are not queued. A restart of `core-api` during a collector's push drops that cycle.
- There is no bundled monitoring or alerting. Silence (a flat dashboard) is the signal.

## Handover checklist

Fill this in once and keep it with the backups, in a password manager rather than in this file. **Do not write secret values into the repository.** This table records where each one is, not what it is.

| Item | Where it is | Who else can get it |
|---|---|---|
| The machine running the stack, and how to log in | | |
| Location of the checkout and its `.env` | | |
| Backup location and last successful backup date | | |
| GitHub repository and who owns it | | |
| Domain name and where its DNS is managed (if any) | | |
| SMTP provider account used by `mail-relay` | | |
| Enphase Enlighten account (owns the Envoy token) | | |
| Amber account and API key | | |
| `SECRETS_ENCRYPTION_KEY` (a copy kept apart from the backups) | | |
| WattWise admin login and its two-factor device or recovery codes | | |
| Router settings: fixed addresses for the Envoy and WeatherLink | | |

**If you are taking this over, do these in order:**

1. Get shell access to the machine and run `docker compose ps`. Confirm all services run.
2. Take a backup using the [routine above](#backups), confirm you can open the file, and confirm you hold a copy of `SECRETS_ENCRYPTION_KEY`.
3. Sign in to the web app. Check that live tiles update. If two-factor blocks you, use a [recovery code](../webapp/src/docs/03-signing-in.md).
4. Read [Architecture](architecture.md) and [How it works](how-it-works.md).
5. Check the dates in [Things that break on a schedule](#things-that-break-on-a-schedule) against your accounts, starting with the Envoy token.
6. Run the test suites ([Development](development.md#running-the-tests)) to confirm you can change code safely.
