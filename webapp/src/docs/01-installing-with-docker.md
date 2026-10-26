# 1. Installing with Docker Compose

[Contents](index.md) · Next: [Getting started](02-getting-started.md)

This chapter is for whoever runs WattWise on their own machine. If someone else already runs it for you, skip it and start with [Getting started](02-getting-started.md).

WattWise runs as a set of containers managed by Docker Compose. One machine runs all of them. It must be on the same home network as your Envoy and WeatherLink Live, because the collectors reach those devices directly.

## What you need

- A computer that stays on, with [Docker](https://docs.docker.com/get-docker/) and Docker Compose installed. Compose 2.24 or later is needed for the published-image setup below.
- Port 80 (the web app) and port 8000 (Core API) free on that machine. If you will use WattWise away from home, you instead need ports 80 and 443 free for a [reverse proxy](#using-it-outside-your-home-network).
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

By default WattWise works only for devices on your home network. To open it from your phone on mobile data, from work or while travelling, you put it on the internet behind HTTPS. This section explains why that includes the API, then shows the setup.

### Why the API must be reachable from the internet

Your browser talks to two addresses, not one:

1. The **web app** (`FRONTEND_BASE_URL`) sends the page to your browser.
2. The page then calls **Core API** (`API_BASE_URL`) straight from your browser to sign in, load readings and open the live feed. These calls do not pass through the web app.

So Core API must be reachable from every device that opens WattWise, at the address in `API_BASE_URL`. If you publish only the web app, the login page loads, but signing in fails with a network error, because your phone cannot reach the API.

Things that do **not** need to be reachable from the internet:

- The collectors. They run in the same Docker stack and talk to Core API over Docker's internal network.
- Your Envoy and WeatherLink Live. Only the collectors read them, from inside your home network.
- The mail relay.

Two more rules follow from the browser calling the API directly:

- **Use HTTPS for both.** A page loaded over `https://` cannot call an `http://` API. Browsers block the request ("mixed content"). It would also send your password and login token across the internet unencrypted.
- **`FRONTEND_BASE_URL` must match the address in your browser's address bar exactly.** Core API allows requests only from that origin, and it is the base of the links in confirmation and password-reset emails.

### What you need

- A domain name, or a dynamic DNS name, that points at your home's public IP address.
- A router that forwards TCP ports 80 and 443 to the machine running WattWise. Port 80 is used only to get and renew the HTTPS certificate. Give the machine a fixed address on your network so the forward keeps working.
- An internet connection with a public address. Some providers share one address between many customers (carrier-grade NAT), which makes forwarding impossible. If yours does, use a tunnel service or a private network such as Tailscale instead. Both ends of the connection still need an HTTPS address for the API.

> **Warning:** Do not forward port 8000 or port 80 of the web app to the internet directly. Those ports are plain HTTP. Forward only the ports of the reverse proxy.

### Setup with Caddy and Docker Compose

Caddy is a reverse proxy that gets and renews a free HTTPS certificate by itself. The repository includes a ready-made Compose file for it in `deploy/caddy/`. It serves everything from one hostname: paths that start with `/api` go to Core API, and every other path goes to the web app. One hostname means one certificate, no cross-site settings and a single address to remember.

1. Point your domain at your home's public IP address. For example, create an `A` record for `wattwise.example.com`.
2. Forward ports 80 and 443 on your router to the machine that runs WattWise.
3. Add these lines to `.env`, replacing the hostname with yours:

```sh
WATTWISE_DOMAIN=wattwise.example.com
FRONTEND_BASE_URL=https://wattwise.example.com
API_BASE_URL=https://wattwise.example.com
COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml:deploy/caddy/docker-compose.caddy.yml
```

`API_BASE_URL` is the same as `FRONTEND_BASE_URL` because Caddy answers both on one hostname. The `COMPOSE_FILE` line makes plain `docker compose` commands use all three files, so you do not retype the `-f` flags.

4. Start it:

```sh
docker compose pull
docker compose up -d
```

5. Check that it works:

```sh
curl https://wattwise.example.com/health
```

You should see `{"status":"ok"}`. The first request can take a few seconds while Caddy gets the certificate. Then open `https://wattwise.example.com` on your phone with Wi-Fi turned off and sign in.

The extra Compose file removes the published ports 80 and 8000 from the web app and Core API, so the only way in from outside is through Caddy on 80 and 443. Do not run `docker compose` without the `COMPOSE_FILE` line after this. Doing so starts the stack without the proxy and publishes 80 and 8000 again.

If the certificate does not arrive, read Caddy's log with `docker compose logs caddy`. The usual causes are a DNS record that does not point at your public address yet, or a router that does not forward port 80.

### Setup with another reverse proxy

Any reverse proxy works if it follows these rules:

| Rule | Why |
|------|-----|
| Serve HTTPS on port 443 with a valid certificate | Browsers refuse the mixed-content calls otherwise |
| Send paths starting with `/api/` to Core API on port 8000 | All data and sign-in calls use `/api/v1/...` |
| Send everything else to the web app on port 80 | It serves the page and its scripts |
| Pass WebSocket upgrades (`Upgrade` and `Connection` headers) on `/api/` | The dashboard's live tiles use `/api/v1/sites/{id}/live`. Without it the tiles update only once a minute. |
| Allow the WebSocket to stay open for an hour or more | Some proxies close idle connections after 60 seconds, which makes the live tiles reconnect over and over |
| Keep the `/api` prefix when forwarding | Core API expects the full path |

An nginx server block that follows them:

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name wattwise.example.com;
    ssl_certificate     /etc/letsencrypt/live/wattwise.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/wattwise.example.com/privkey.pem;

    location /api/ {
        proxy_pass http://core-api:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }

    location / {
        proxy_pass http://webapp:80;
        proxy_set_header Host $host;
    }
}
```

The names `core-api` and `webapp` work only when the proxy runs in the same Docker network as WattWise (`app`). A proxy outside Docker, or on another machine, uses the machine's address instead, for example `http://192.168.1.20:8000` and `http://192.168.1.20:80`. Those ports are published by `docker-compose.yml` unless you added the Caddy file. Block them from the internet at your router or firewall.

**Nginx Proxy Manager:** create one proxy host for your domain that forwards to the web app on port 80. Turn on **Websockets Support** and **Block Common Exploits**, and request a certificate on the **SSL** tab. Then open the **Custom locations** tab, add the location `/api`, forward it to the host and port of Core API (8000) and turn on **Websockets Support** there as well.

**Two hostnames instead of one:** if your proxy cannot route by path, give the web app and Core API their own names, for example `wattwise.example.com` and `api.wattwise.example.com`. Set `FRONTEND_BASE_URL=https://wattwise.example.com` and `API_BASE_URL=https://api.wattwise.example.com`. Core API then accepts requests from the web app's origin only.

After you change `FRONTEND_BASE_URL` or `API_BASE_URL`, run `docker compose up -d` so Compose recreates both containers. The web app reads `API_BASE_URL` only when its container starts.

### Keeping it safe on the internet

- Every account needs a password and an authenticator code, and repeated wrong guesses lock the account for a while. Set up [two-factor authentication](03-signing-in.md#setting-up-two-factor-authentication) on the admin account first, and delete `initial-admin-password.txt`.
- The Caddy setup does not forward Core API's `/docs` pages, so its interactive documentation is not public.
- Update the images regularly, as described under [Updating](#updating).
- Keep `.env` private. Collector keys protect the endpoints that collectors write to, and `SECRETS_ENCRYPTION_KEY` protects stored credentials.

Backups, restores and log-reading are covered in more detail in the repository's `docs/operations.md`.

## Next

- [Getting started](02-getting-started.md)
- [Troubleshooting](09-troubleshooting.md)
