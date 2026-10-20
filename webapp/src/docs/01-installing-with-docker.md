# 1. Installing with Docker Compose

[Contents](index.md) · Next: [Getting started](02-getting-started.md)

This chapter is for whoever runs WattWise on their own machine. If someone else already runs it for you, skip it and start with [Getting started](02-getting-started.md).

WattWise runs as a set of containers managed by Docker Compose. One machine runs all of them. It must be on the same home network as your Envoy and WeatherLink Live, because the collectors reach those devices directly.

## What you need

- A computer that stays on, with [Docker](https://docs.docker.com/get-docker/) and Docker Compose installed. Compose 2.24 or later is needed for the published-image setup below.
- Port 80 (the web app) and port 8000 (Core API) free on that machine.
- The WattWise files: `docker-compose.yml`, `docker-compose.prod.yml` and `.env.example`.

## Set up the settings file

1. Copy `.env.example` to `.env`.
2. Fill in the secrets. Run each command separately and paste the output after the matching name:

| Setting | How to generate it |
|---------|--------------------|
| `JWT_SECRET` | `openssl rand -hex 32` |
| `SECRETS_ENCRYPTION_KEY` | `openssl rand -base64 32 \| tr '+/' '-_'` |
| `ENVOY_COLLECTOR_KEY` | `openssl rand -hex 32` |
| `WEATHER_COLLECTOR_KEY` | `openssl rand -hex 32` |
| `PRICING_COLLECTOR_KEY` | `openssl rand -hex 32` |

3. Set `RELAY_DOMAIN` and `RELAY_SMARTHOST` to your outgoing mail provider, for example `[smtp.example.com]:587`, and add `RELAY_USERNAME` and `RELAY_PASSWORD` if it needs a login. Compose refuses to start without the first two, even if you never send mail.
4. If you will open WattWise from other devices, set `FRONTEND_BASE_URL` and `API_BASE_URL` to the addresses your browser uses, such as `http://192.168.1.20` and `http://192.168.1.20:8000`.

> **Warning:** `SECRETS_ENCRYPTION_KEY` protects the Envoy tokens, passwords and Amber keys you enter in the app. Copy it somewhere off this machine. If you lose it, those saved values cannot be read and you must enter them again.

## Start WattWise

Use the ready-made images. This downloads them and starts everything in the background:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

To pin a release instead of the newest build, add `WATTWISE_VERSION=1.2.0` to `.env`.

If you have the full source code and want to build the images yourself, run `docker compose up --build -d` instead.

Check that everything is running:

```sh
docker compose ps
```

All services should show `running`, and `core-api` should show `healthy`. The first start can take about 30 seconds.

## First sign-in

1. Read the one-time admin password:

```sh
docker compose exec core-api cat /data/initial-admin-password.txt
```

2. Open `http://localhost` (or your `FRONTEND_BASE_URL`) and sign in as `admin@example.com`.
3. [Set up two-factor authentication](03-signing-in.md#setting-up-two-factor-authentication) and change the password in [Account settings](08-account-settings.md).
4. Delete the password file:

```sh
docker compose exec core-api rm /data/initial-admin-password.txt
```

If you don't delete the file, Core API deletes it once it is 5 days old.

Then follow the [first-day checklist](02-getting-started.md#first-day-checklist).

## Everyday commands

If you used the published images, add `-f docker-compose.yml -f docker-compose.prod.yml` after `docker compose` in each command below that starts or updates containers.

| Task | Command |
|------|---------|
| See what is running | `docker compose ps` |
| Read a service's log | `docker compose logs --tail=100 envoy-collector` |
| Restart one service | `docker compose restart envoy-collector` |
| Stop everything | `docker compose stop` |
| Start it again | `docker compose up -d` |

Your data lives in a Docker volume named `core_api_data`, so stopping or updating the containers keeps it.

> **Warning:** `docker compose down -v` deletes that volume, including every reading and setting. Do not add `-v` unless you mean to start over.

## Updating

Back up first, then pull the new images and restart:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --remove-orphans
```

Database changes apply automatically when Core API starts.

## Using it outside your home network

Ports 80 and 8000 use plain HTTP, so passwords and login tokens are not encrypted on the way. Keep WattWise on your home network, or put a reverse proxy that provides HTTPS in front of both ports before you open it to the internet. Set `FRONTEND_BASE_URL` and `API_BASE_URL` to the `https://` addresses when you do.

Backups, restores and log-reading are covered in more detail in the repository's `docs/operations.md`.

## Next

- [Getting started](02-getting-started.md)
- [Troubleshooting](09-troubleshooting.md)
