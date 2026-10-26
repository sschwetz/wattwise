# 2. Getting started

[Contents](index.md) · Previous: [Installing with Docker Compose](01-installing-with-docker.md) · Next: [Signing in and account security](03-signing-in.md)

## What WattWise does

WattWise records your home's energy flows once a minute and keeps them for a year. It combines those readings with your electricity prices to show what you are paying or earning right now, today, and for the current billing period.

It tracks:

- **Solar production**, **home consumption**, **grid import**, **grid export**, and **battery charge, discharge and level** from an Enphase Envoy.
- **Buy and sell prices**, including the forecast for the rest of the day, from Amber Electric.
- **Temperature, humidity, wind, rain, solar radiation and pressure** from a WeatherLink Live weather station.

## How the pieces fit together

Your browser loads the web app, and the page then talks to Core API, which stores everything. Small programs called **collectors** run next to Core API. Each collector reads one kind of device or service and sends the readings to Core API.

| Collector | Reads from | Gives you |
|-----------|-----------|-----------|
| Envoy collector | Your Enphase Envoy, over your home network | Production, consumption, grid, battery |
| Amber pricing collector | Amber Electric's online service | Buy and sell prices, forecasts |
| Local pricing collector | The rate windows you type in | Prices for a fixed or time-of-use tariff |
| Weather collector | Your WeatherLink Live unit, over your home network | Weather tiles |

Only your browser needs to reach the web app and Core API. If you want to use WattWise away from home, both must be reachable from the internet over HTTPS. See [Using it outside your home network](01-installing-with-docker.md#using-it-outside-your-home-network). The collectors do not need internet access to your home.

You never talk to a collector directly. You tell WattWise *what* to collect by adding a [source](05-sources.md) in the web app, and the matching collector finds it within a few minutes. No restart or configuration file change is needed.

## Main terms

| Term | Meaning |
|------|---------|
| **Site** | One property you monitor, for example "Home". Every reading, price and bill belongs to a site. See [Sites](04-sites.md). |
| **Source** | One connection from a site to a device or service: an Envoy, an Amber account or a weather station. See [Sources](05-sources.md). |
| **Tariff** | The fixed charges and special rates your electricity retailer applies. See [Billing](07-billing.md). |
| **Billing cycle** | How often your bill closes, and on which day. See [Billing cycles](07-billing.md#billing-cycles). |

The full list of terms is in the [Glossary](10-glossary.md).

## The screens

After you sign in, every screen has the header. The dashboard, Reporting, Sources and Billing pages also have the sidebar:

![The WattWise header at the top and the site sidebar down the left side](images/sidebar-and-header.png)

- **Header** (top): the WattWise logo and your name and photo. Click your name to open the user menu with **Account settings**, **Help** and **Log off**.

![The user menu open, showing Account settings, Help and Log off](images/header-menu.png)

- **Sidebar** (left, or a strip along the top on a phone): the **site switcher** and the links **Dashboard**, **Reporting**, **Sources** and **Billing**. The Account and Add site pages show the header only.
- **Main window**: the page you picked. This guide opens here too, through **Help** in the user menu.

![The in-app Help viewer showing this guide's contents page](images/help-viewer.png)

### Light and dark, and on a phone

WattWise follows your device's light or dark setting, so there is nothing to switch on. On a narrow screen the sidebar becomes a strip along the top, and the tiles stack in one column.

![The dashboard in the dark theme](images/dashboard-dark.png)

![The dashboard on a phone](images/dashboard-mobile.png)

## First-day checklist

Do these in order. Each step links to the chapter that explains it.

1. [Create your account](03-signing-in.md#creating-an-account) and confirm your email address.
2. [Set up two-factor authentication](03-signing-in.md#setting-up-two-factor-authentication) and store your recovery codes somewhere safe.
3. [Create your first site](04-sites.md#creating-a-site). Choose the correct time zone.
4. [Add your Envoy as an energy source](05-sources.md#connecting-an-enphase-envoy). You need its address on your home network and an access token.
5. If you use Amber: [save your Amber API key](08-account-settings.md#amber-api-key), then [add an Amber pricing source](05-sources.md#connecting-amber-electric).
6. If you have a weather station: [add a WeatherLink source](05-sources.md#connecting-a-weatherlink-live-station).
7. [Enter your tariff](07-billing.md#tariffs) and [billing cycle](07-billing.md#billing-cycles) so costs are calculated.
8. Open the [Dashboard](06-dashboard.md). Tiles appear within a few minutes of the first reading.

> **Note:** Cost figures need three things at once: energy readings, prices, and (for the service charge and billing period) a tariff and billing cycle. If one is missing, the dashboard leaves that figure out rather than guessing. See [Troubleshooting](09-troubleshooting.md#costs-look-too-low-or-are-missing).

## Next

- [Signing in and account security](03-signing-in.md)
- [Sources](05-sources.md) if you already have an account and a site
