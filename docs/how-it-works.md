# How it works

Each section follows one process from start to finish. The code paths named in each section are the places to start reading.

## 1. A meter reading reaches the dashboard

Every 60 seconds the Envoy collector reads the solar system and sends the result to Core API.

```mermaid
sequenceDiagram
    autonumber
    participant EC as envoy-collector
    participant ENV as Envoy (LAN)
    participant API as core-api
    participant DB as SQLite
    participant UI as Browser

    Note over EC,API: Every 5 min: discovery
    EC->>API: GET /collector/sources?kind=energy (X-Collector-Key)
    API-->>EC: [{source_id, site_id, config{host, token, ...}}]

    Note over EC,ENV: Every 60 s: poll
    EC->>ENV: GET /production.json (pinned TLS cert, bearer token)
    EC->>ENV: GET /ivp/ensemble/power (battery)
    ENV-->>EC: watts now, battery watts and charge level
    EC->>EC: map to metrics at minute boundary
    EC->>API: POST /sources/{id}/readings/energy
    API->>DB: upsert (source, ts, metric)
    API-->>EC: 202 {"ingested": n}

    UI->>API: GET /sites/{id}/readings/energy (JWT)
    API->>DB: select
    API-->>UI: readings for the live tiles
```

What the collector does to the raw numbers ([`envoy_collector/mapping.py`](../envoy-collector/envoy_collector/mapping.py)):

- `production_w` comes from the CT-metered (`eim`) entry, never the inverter sum. The two disagree in practice.
- The sign of the net-consumption figure decides which of `grid_import_w` or `grid_export_w` is written. Only one is written per minute.
- On a site with a battery the Envoy's own consumption figure omits the battery. The collector adds net battery power back in to recover true household use.
- A battery that is charging writes `battery_charge_w`. One that is discharging writes `battery_discharge_w`.

Failures are local. If the Envoy does not answer, that cycle is skipped and logged. If Core API does not answer, the cycle's readings are dropped. There is no queue, because the next cycle carries fresh data and the demand logic tolerates gaps.

## 2. Prices from Amber

Amber prices arrive as 5-minute intervals. Three loops run in separate processes, because Amber revises prices after an interval closes.

```mermaid
flowchart TD
    subgraph LIVE["Live loop: every 5 min, aligned to Amber's publish time"]
        L1["Fetch 1 closed interval<br/>+ 300 forecast intervals (about 25 h)"] --> L2["Map channels:<br/>general to buy<br/>feedIn to sell<br/>controlledLoad to controlled_load"] --> L3["POST /sources/id/prices"]
    end
    subgraph RECENT["Recent loop: every 1 min"]
        R1["Fetch the last 3 closed intervals<br/>(15 min), no forecast"] --> R2["Resend, so a revised price<br/>lands within a minute"] --> L3
    end
    subgraph BACK["Backfill loop: every 12 h"]
        B1["Ask Core API for missing days<br/>since the site's first data"] --> B2["Fetch history in 7-day chunks<br/>(Amber's limit)"] --> B3{"Amber has data?"}
        B3 -- yes --> L3
        B3 -- "no" --> B4["POST gap-markers<br/>stop asking about that day"]
    end
    L3 --> API["core-api: upsert price_intervals<br/>and fan out one price_readings row per minute"]
```

Amber's API key is not on the source. Core API reads it from the site owner's user record and adds it to the config it hands the collector.

## 3. Prices from a fixed tariff (`local` provider)

A fixed or time-of-use tariff has no API to poll. The owner types **rate windows** in the web app. The local pricing collector expands them into price intervals and pushes them as if a provider had published them. Everything downstream (the minute fan-out, cost) is identical to Amber.

```mermaid
flowchart TD
    A["Owner adds, edits, ends or deletes a rate window"] --> B["core-api stores the window<br/>and writes a reprice marker:<br/>earliest affected instant + version"]
    B --> C["Collector tick, every minute"]
    C --> D{"What is due?"}
    D -- "scheduled: 23:59 site time<br/>(retried each minute until 02:00)" --> E["Price the next local day"]
    D -- "catch-up: new source, restart,<br/>windows changed, first check of a day" --> F["Compare stored prices with what the<br/>windows imply. Push only what is missing,<br/>back to the site's first data."]
    D -- "reprice marker present" --> G["Reprice from the marker to the<br/>end of tomorrow. Overwrites."]
    E --> H["POST /sources/id/prices<br/>(sell rate sign flipped to negative)"]
    F --> H
    G --> H
    H --> I["Clear marker with its version.<br/>A newer edit keeps its marker."]
```

Details worth knowing:

- Each source is handled in its own child process with a two-hour limit, so a slow source cannot block the others.
- The collector remembers nothing between restarts. After a restart every source does a catch-up, which is safe because a catch-up only writes what is missing.
- The marker `version` is an optimistic-lock counter. Core API reads the marker before the windows. If the owner edits between the two reads, the collector gets new windows with the old version, its clear is ignored, and the next run reprices again. The reverse order could lose an edit.
- Minutes covered by no window have no price and are left out of cost totals. The web app warns about the first uncovered time it finds.

Code: [`local_pricing_collector/poll.py`](../local-pricing-collector/local_pricing_collector/poll.py), [`triggers.py`](../local-pricing-collector/local_pricing_collector/triggers.py), [`plan.py`](../local-pricing-collector/local_pricing_collector/plan.py), [`rates.py`](../local-pricing-collector/local_pricing_collector/rates.py).

## 4. How cost is calculated

Cost is not stored. `GET /sites/{id}/cost` runs [`core_api/cost.py`](../core-api/core_api/cost.py) over the requested range.

```mermaid
flowchart TD
    S["Range [start, end) and method"] --> M{"method"}
    M -- net --> N["import = grid_import_w<br/>export = grid_export_w"]
    M -- gross --> G["import = consumption_w<br/>export = production_w"]
    N --> J
    G --> J
    J["For each minute with an energy reading,<br/>join the buy and sell price for that exact minute"] --> X{"price missing?"}
    X -- yes --> K["Skip the minute.<br/>Count it in minutes_missing_price."]
    X -- no --> F["minute cost =<br/>import kWh x buy<br/>+ export kWh x (sell - export adjustment)"]
    F --> SUM["Sum over minutes"]
    SUM --> A1["+ service charge: tariff c/day per local day,<br/>prorated for today"]
    A1 --> A2["+ monthly fee / days in billing period<br/>(or the discounted fee), per day"]
    A2 --> A3["- conditional credits for qualifying days"]
    A3 --> A4["+ demand charge share for the range<br/>(estimate allowed)"]
    A4 --> OUT["total cents, import kWh, export kWh,<br/>minutes_missing_price"]
```

**Net and gross.** Net costs what actually crossed the meter: grid import, less grid export. Gross pretends the home has no solar: all consumption at the buy price, all production at the sell price. The difference is what the solar system saves.

**Sell price sign.** Sell is stored negative when you are paid, so the formula adds `export kWh × sell`, and a normal export minute reduces the total. The export adjustment is subtracted from the sell price, so a positive adjustment (an incentive) makes the price more negative and increases revenue.

**Worked example.** One minute, importing 1200 W with a buy price of 30 c/kWh:

```text
import kWh = 1200 / 60000 = 0.02
cost      = 0.02 x 30     = 0.6 cents
```

One minute exporting 3000 W with a sell price of -8 c/kWh and a +2 c/kWh adjustment:

```text
export kWh = 3000 / 60000     = 0.05
cost       = 0.05 x (-8 - 2)  = -0.5 cents   (revenue)
```

**Flat daily items.** The service charge and monthly fee are added per local calendar day. For the current day they are prorated by the share of the day that has passed, unless the caller sends `prorate_fees=false` (the dashboard's live tiles do, so they show the committed daily amount). The monthly fee is divided by the length of that day's billing period.

**Conditional credits.** A matching day earns a credit only after its window has ended and only if grid readings cover every minute of the window. If the average grid import is strictly below the limit, the credit is subtracted. A missing minute means no credit, so an outage never looks like a quiet evening. Minutes spent exporting count as 0 W of import.

**What the chart leaves out.** `GET /cost/series` uses the same per-minute maths but omits the daily charges, so a flat fee does not spike one bucket. It includes demand charges only after a billing period closes.

## 5. Demand charges

Some tariffs charge for the highest sustained grid draw inside peak windows.

```mermaid
flowchart LR
    A["Every 5 min<br/>demand_interval_job"] --> B["For each 5-min slot:<br/>average grid_import_w"]
    B --> C{"Slot inside a demand window<br/>or flagged by a price interval?"}
    C -- yes --> D["rolling sum of the last N slots<br/>(N = measurement interval / 5)"]
    C -- no --> E["slot counts as 0"]
    D --> F["demand_intervals<br/>(upsert, last 3 h recomputed)"]
    E --> F
    G["00:10 daily<br/>demand_charge_job"] --> H["For each billing period:<br/>peak rolling sum / N = peak average W"]
    F --> H
    H --> I["charge = peak kW x rate c/kW/day<br/>x days in period"]
    I --> J["demand_charges row<br/>is_estimated until the period closes<br/>and every 5-min slot has readings"]
```

The last three hours are recomputed on every run, so a late or backfilled reading lands in the right slot. A finalized charge is never rewritten. An estimated one is recalculated each night. Billing periods come from the site's [billing cycle](data-model.md#billing-tables): a cycle length in months and an anchor day. The period before the first anchor day is a partial period.

## 6. Signing in

Passwords are never enough. Every account needs a second factor.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant API as core-api

    B->>API: POST /auth/login {email, password}
    alt wrong password, unknown email, or unconfirmed
        API-->>B: 401 invalid_credentials (same answer, same timing)
    else TOTP not yet enrolled
        API-->>B: {status: totp_setup_required, setup_token (5 min)}
        B->>API: POST /auth/totp/setup/start
        API-->>B: secret + QR for the authenticator app
        B->>API: POST /auth/totp/setup/confirm {code}
        API-->>B: recovery codes + access token
    else TOTP enrolled
        API-->>B: {status: totp_required, mfa_token (5 min)}
        B->>API: POST /auth/totp/verify {code or recovery code}
        API-->>B: access token (JWT, 24 h default)
    end
    B->>API: any call with Authorization: Bearer
```

![The login page](../webapp/src/docs/images/login.png)

Protections in place, and where they live ([`security.py`](../core-api/core_api/security.py), [`deps.py`](../core-api/core_api/deps.py), [`routers/auth.py`](../core-api/core_api/routers/auth.py), [`routers/totp.py`](../core-api/core_api/routers/totp.py)):

| Threat | Protection |
|---|---|
| Guessing passwords | After 5 failures in 15 minutes an email is locked, whether or not the account exists. |
| Learning which emails have accounts | An unknown email still runs a full bcrypt comparison. Registration returns the same message for new and existing addresses and pads its response time to at least 0.3 s. |
| Guessing the TOTP code | The attempt cap is keyed on the user, not the token, so logging in again does not reset it. |
| Replaying an observed TOTP code | The last accepted 30-second step is stored. An older or equal step is refused. |
| Stolen token for another purpose | Setup and MFA tokens carry a scope and expire in 5 minutes. They cannot call the normal API. |
| Copied database or backup | Device credentials, the Amber key and authenticator secrets are Fernet-encrypted. Recovery codes are keyed hashes. Both need `SECRETS_ENCRYPTION_KEY`, which is not in the database. |
| Leaked reset or confirmation link | Only hashes are stored, links are single-use, and issuing a new link burns the old ones. |

First boot: with an empty `users` table, Core API creates `admin@example.com` (or `ADMIN_EMAIL`) with a random password written to `initial-admin-password.txt` beside the database. See [Operations](operations.md#first-login).

## 7. Adding a site and a source

This is the flow that makes "no restart" work.

```mermaid
sequenceDiagram
    participant U as You (web app)
    participant API as core-api
    participant EC as Matching collector

    U->>API: POST /sites {name, timezone}
    U->>API: POST /sites/{id}/sources {kind, provider, name}
    API-->>U: 201 source (config empty)
    U->>API: PATCH /sites/{id}/sources/{sid} {config: {host: ...}}
    Note over EC: within DISCOVERY_REFRESH_SECONDS (300 s)
    EC->>API: GET /collector/sources?kind=...
    API-->>EC: includes the new source with its config
    EC->>EC: start polling it
```

![Sources page](../webapp/src/docs/images/sources-list.png)

When the collector learns something about a device, it reports it back with `PATCH /sources/{id}/discovered-config`. The Envoy collector does this for the serial number, the pinned certificate fingerprint and expiry, and renewed tokens. The web app then shows them on the source's settings form.
