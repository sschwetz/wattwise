# 8. Troubleshooting

[Contents](index.md) · Previous: [Account settings](07-account-settings.md) · Next: [Glossary](09-glossary.md)

Find your symptom below. Each fix links to the chapter with the full steps.

## The dashboard is empty

The dashboard shows only what has been collected, so an empty page usually means no source is delivering yet.

| Check | What to do |
|-------|-----------|
| Is there an energy source? | Open **Sources**. If no `energy — envoy` row exists, [add one](04-sources.md#connecting-an-enphase-envoy). |
| Is it enabled? | The row should offer **Disable**, not **Enable**. If it says **Enable**, click it. |
| Are the Host and Token filled in? | Click **Edit config**. Host must be set, and either Token or both Enlighten fields. |
| Did you wait? | New and edited sources take a few minutes to start. The dashboard refreshes every 60 seconds. |
| Is the Envoy reachable? | The Envoy must be on the same network as your WattWise server. Try its address in a browser from a computer on that network. |
| Has the token expired? | Tokens last about a year. If **Enlighten email** and **Enlighten password** are set, the collector renews the token itself. If readings still stop, check the collector log for "Could not renew the Envoy token", usually a wrong Enlighten password. Otherwise [get a new one](04-sources.md#getting-the-token). |

If the live tiles say *Failed to load live readings.*, the web app cannot reach the server. Reload the page, then check that the server is running.

## The Envoy stopped reporting after a reset

The collector pins the Envoy's certificate on first contact and refuses a different one. After a factory reset or replacement, change **Host** (for example, between the IP address and `envoy.local`) and click **Save config**. Changing the host clears the stored certificate and serial number, and the collector pins the device again. You may also need a [new token](04-sources.md#getting-the-token) for the new serial number.

## Consumption or grid numbers have the wrong sign

If your home's consumption reads negative while importing, your Envoy's consumption sensor is installed the other way round. Open **Sources → Edit config** on the Envoy and tick **My consumption numbers are flipped (importing shows as negative)**, then **Save config**.

## Costs look too low or are missing

| Symptom | Cause and fix |
|---------|---------------|
| No **Today's cost** tile | No readings or no prices yet. Check [energy](#the-dashboard-is-empty) and [pricing](#no-prices-or-pricing-chart) sources. |
| **Cost** is lower than your bill | Minutes without a price are skipped. Pricing may have been added after those minutes, or the pricing collector was down. |
| No **Billing period cost** tile | No billing cycle. [Add one](06-billing.md#billing-cycles). This is normal until you do. |
| Fixed fees are missing | The daily charge needs a [tariff](06-billing.md#tariffs). The monthly fee needs a tariff with a monthly fee **and** a billing cycle. |
| Demand charges are missing | Needs a [demand window](06-billing.md#demand-windows) on the active tariff, a billing cycle, and some days of data. |
| Tariff change not reflected | Rates apply from the moment a tariff is added, not retroactively. |
| Cost is different between Net and Gross | Expected. See [Net and gross cost](05-dashboard.md#net-and-gross-cost). |

## No prices or pricing chart

1. Save your Amber API key in [Account settings](07-account-settings.md#amber-api-key).
2. Add a [pricing source](04-sources.md#connecting-amber-electric) with the correct **Amber site ID**.
3. Wait a few minutes. The chart fills as Amber publishes each 5-minute price.

The grid price tile appears only while WattWise knows whether you are importing or exporting.

## Weather tiles are missing

Add a [WeatherLink source](04-sources.md#connecting-a-weatherlink-live-station) with the unit's address. A site can have only one weather source.

## I cannot delete a source

A source that has stored readings cannot be deleted. Click **Disable** instead. See [Editing, disabling and deleting](04-sources.md#editing-disabling-and-deleting).

## Sign-in problems

| Symptom | Fix |
|---------|-----|
| Confirmation link says it is invalid or expired | Links last 24 hours and work once. [Register again](02-signing-in.md#creating-an-account) with the same address for a new link. |
| Reset link says it is invalid or expired | Reset links last 1 hour and work once. Request a [new one](02-signing-in.md#forgotten-password). |
| Authenticator code is rejected | Check that your phone's clock is set automatically. A code can be used only once, so wait for the next one. |
| Too many failed attempts | After 5 failures in 15 minutes, further attempts are refused until the older failures age out. Wait, then try again. |
| Phone lost | Use a [recovery code](02-signing-in.md#lost-your-authenticator-device). |
| **Session expired** on the setup or code page | Go back to the login page and sign in again. |
| Signed out unexpectedly | Sessions last 24 hours by default. |
| No email arrives | Check spam. Then ask your administrator whether outgoing email is configured. |

## Times or days look wrong

Check the [site's time zone](03-sites.md#creating-a-site). The dashboard's date ranges use your device's clock for midnight, while tariffs, demand windows and billing cycles use the site's time zone. If you view a site from a different time zone, the two can differ by hours.

## Still stuck

Note what you did, what you saw and the time, then ask your WattWise administrator, who can read the collector and server logs.
