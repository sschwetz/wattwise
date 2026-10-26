# WattWise User Guide

WattWise shows what your solar panels, battery, home and electricity tariff are doing, and what that costs. It collects readings from your Enphase Envoy, your Amber Electric prices and your WeatherLink weather station, then turns them into a live dashboard and a running bill.

This guide covers every screen in the web app. Use the contents below, or the **Previous** and **Next** links at the top and bottom of each chapter.

## Contents

| Chapter | What it covers |
|---------|----------------|
| [1. Installing with Docker Compose](01-installing-with-docker.md) | For whoever runs the server: settings, starting, updating, backups, and reaching WattWise from the internet over HTTPS |
| [2. Getting started](02-getting-started.md) | What WattWise is, how data reaches it, and a first-day checklist |
| [3. Signing in and account security](03-signing-in.md) | Creating an account, two-factor authentication, recovery codes, password reset |
| [4. Sites](04-sites.md) | Creating sites, switching between them, time zones |
| [5. Sources](05-sources.md) | Connecting your Envoy, Amber, a fixed tariff and WeatherLink |
| [6. The dashboard](06-dashboard.md) | Live tiles, weather, the pricing, grid and battery charts, the Reporting page, net and gross cost |
| [7. Billing](07-billing.md) | Tariffs, billing cycles, demand windows, export adjustments, fee discounts |
| [8. Account settings](08-account-settings.md) | Name, photo, password, email, authenticator, Amber API key |
| [9. Troubleshooting](09-troubleshooting.md) | Fixes for missing tiles, wrong costs, sign-in problems and access from outside your home |
| [10. Glossary](10-glossary.md) | Plain-language definitions of the terms used in the app |

## Where to start

- **New to WattWise?** Read [Getting started](02-getting-started.md), then follow its [first-day checklist](02-getting-started.md#first-day-checklist).
- **Dashboard shows nothing?** Go to [Troubleshooting](09-troubleshooting.md#the-dashboard-is-empty).
- **Costs look wrong?** Check [Billing](07-billing.md#how-the-cost-is-calculated) and [Net and gross cost](06-dashboard.md#net-and-gross-cost).
- **Running the server yourself?** See [Installing with Docker Compose](01-installing-with-docker.md).
- **Want to use WattWise away from home?** The API must be reachable from the internet over HTTPS. See [Using it outside your home network](01-installing-with-docker.md#using-it-outside-your-home-network).
- **Lost your phone?** See [Lost your authenticator device](03-signing-in.md#lost-your-authenticator-device).

## Conventions in this guide

- **Bold** text is a button, link or field label exactly as it appears on screen.
- Sentences starting with **Note** explain behavior that can surprise you.
- Sentences starting with **Warning** describe an action you cannot undo.
- Every chapter ends with links to the chapters that follow from it.
