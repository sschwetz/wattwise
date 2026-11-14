# 11. Glossary

[Contents](index.md) · Previous: [Troubleshooting](10-troubleshooting.md)

| Term | Definition |
|------|-----------|
| **Actor** | A background program that sends commands to a device, such as an air conditioner, from your schedules. See [Schedules](08-schedules.md). |
| **Alert** | A problem or notice a collector or actor raises for you. It shows in the dashboard log. See [The log](08-schedules.md#the-log). |
| **Amber Electric** | An Australian electricity retailer that charges a wholesale-linked price that changes every 5 minutes. WattWise reads its prices through the Amber API. See [Connecting Amber Electric](05-sources.md#connecting-amber-electric). |
| **Armed** | A price or temperature schedule is armed when it can fire. It becomes disarmed when it fires, and armed again when the reading is back past its re-arm value. See [When](08-schedules.md#when-sunrise-and-sunset-price-and-temperature). |
| **Anchor day** | The day of the month a billing period starts. See [Billing cycles](07-billing.md#billing-cycles). |
| **Authenticator app** | A phone app that shows a 6-digit code that changes every 30 seconds. See [Two-factor authentication](03-signing-in.md#setting-up-two-factor-authentication). |
| **API_BASE_URL** | The setting that tells your browser where Core API is. See [Why the API must be reachable from the internet](01-installing-with-docker.md#why-the-api-must-be-reachable-from-the-internet). |
| **Billing cycle** | How many months a bill covers and the day it starts. See [Billing cycles](07-billing.md#billing-cycles). |
| **Billing period** | One bill's span of dates, from the billing cycle. |
| **Bulb** | The coloured dot at the start of a row in the dashboard log. Red is a failure, amber is a problem that leaves part of a device working, and blue is a notice or a successful run. See [The log](08-schedules.md#the-log). |
| **Buy price** | What you pay per kWh to import electricity. |
| **Collector** | A small program that reads one kind of device or service and sends the readings to Core API. See [How the pieces fit together](02-getting-started.md#how-the-pieces-fit-together). |
| **Core API** | The service that stores all data and answers the web app. Your browser calls it directly, so it must be reachable from the internet to use WattWise away from home. See [Using it outside your home network](01-installing-with-docker.md#using-it-outside-your-home-network). |
| **c/kWh** | Cents per kilowatt-hour: the price of energy. |
| **c/kW/day** | Cents per kilowatt per day: the rate of a demand charge. |
| **Conditional credit** | A fixed amount taken off your bill on days when your average grid import in a set time window stays below a limit. See [Conditional credits](07-billing.md#conditional-credits). |
| **Consumption** | The power your home is using. |
| **Credit** | A negative cost: you earned more than you spent. Shown in green. |
| **CT (current transformer)** | A clamp sensor that measures power flowing through a cable. The Envoy uses CTs to measure production and consumption. |
| **Daily allowance** | Part of an [export adjustment](07-billing.md#export-adjustments): kWh of exports per day, building up through the billing cycle, that are not adjusted. |
| **Demand charge** | A charge based on your highest average power draw in a billing period, within a demand window. See [Demand charges](07-billing.md#demand-charges). |
| **Demand window** | A repeating period (months, days, hours) when peak demand is measured. See [Demand windows](07-billing.md#demand-windows). |
| **Disabled site** | A site whose collection is switched off. It is hidden from the site list and switcher, and its data stays readable. See [Disabling a site](04-sites.md#disabling-a-site). |
| **Dismiss** | The button that removes a sticky notice from the dashboard log. See [The log](08-schedules.md#the-log). |
| **Envoy** | The Enphase gateway that talks to your solar inverters and battery. |
| **Export** | Sending power to the grid. |
| **Export adjustment** | A bonus or penalty, in cents per kWh, added to your feed-in price during a repeating period. See [Export adjustments](07-billing.md#export-adjustments). |
| **Feed-in (sell) price** | What you are paid per kWh for exporting. Amber publishes it as a negative number. |
| **Gross cost** | Cost counted as if all consumption were imported and all production exported. See [Net and gross cost](06-dashboard.md#net-and-gross-cost). |
| **Grid** | The public electricity network. |
| **Import** | Drawing power from the grid. |
| **Invitation** | An emailed offer of read-only access to a site. Its status is invited, accepted, declined or revoked. See [Sharing a site](04-sites.md#sharing-a-site). |
| **kW / kWh** | Kilowatt (power, a rate) and kilowatt-hour (energy, an amount). 1 kW for 1 hour is 1 kWh. |
| **Label** | An optional name on a source, for your own use. |
| **Minimum gap** | The **Minutes between runs** setting: how long a schedule waits after it was put back, or last ran, before it can run again. See [How often](08-schedules.md#how-often). |
| **Monthly fee** | A fixed monthly account charge on a tariff. See [Tariffs](07-billing.md#tariffs). |
| **Monthly fee discount** | A temporary lower monthly fee for a range of dates. See [Monthly fee discounts](07-billing.md#monthly-fee-discounts). |
| **Net cost** | Cost based on energy that actually crossed the grid meter. This is what your bill shows. |
| **Notice** | A blue entry in the dashboard log about a change, such as an Envoy firmware upgrade, rather than a fault. Most notices stay until you dismiss them. |
| **Production** | The power your solar panels generate. |
| **Provider** | The brand or system behind a source: `envoy`, `amber`, `local` (rates you type in) or `weatherlink_live`. |
| **Re-arm** | The level a price or temperature must come back to before a schedule can fire again. See [When](08-schedules.md#when-sunrise-and-sunset-price-and-temperature). |
| **Recovery code** | A single-use code that replaces an authenticator code. See [Recovery codes](03-signing-in.md#recovery-codes). |
| **Restore** | Putting a device back to how it was after a schedule's actions. See [When it ends](08-schedules.md#when-it-ends). |
| **Service availability charge** | The daily supply charge on a tariff, in cents per day. |
| **Site** | One monitored property. See [Sites](04-sites.md). |
| **Site settings** | The page for a site's name, collectors, tariffs, billing, schedule and sharing. See [Site settings](04-sites.md#site-settings). |
| **Schedule** | A rule that runs actions on a device at a trigger, such as turning the air conditioner off when a demand window starts. See [Schedules](08-schedules.md). |
| **Source** | A connection from a site to a device or service. See [Sources](05-sources.md). |
| **Run again after** | The **Do not run again until** setting: a time the schedule waits for after a run. See [How often](08-schedules.md#how-often). |
| **Reverse proxy** | A program such as Caddy or nginx that sits in front of WattWise, provides HTTPS and passes requests to the web app and Core API. See [Setup with Caddy](01-installing-with-docker.md#setup-with-caddy-and-docker-compose). |
| **Sunrise and sunset** | Times a schedule can use as a trigger, in a **Time of day** row, or in **Do not run again until**. They come from the site's [location](04-sites.md#location-and-sun-times), or from its time zone when no location is set. |
| **Sticky notice** | An alert that stays in the dashboard log after the cause is gone, until you click **Dismiss**. |
| **State of charge (SoC)** | How full the battery is, as a percentage. Shown on the **Battery** tile. |
| **Time of day** | An **Only if** row that passes between two times, each a clock time or sunrise or sunset. See [Only if](08-schedules.md#only-if). |
| **Tariff** | The fixed charges and rules your retailer applies. See [Tariffs](07-billing.md#tariffs). |
| **Token** | A secret that lets WattWise read your Envoy. See [Getting the token](05-sources.md#getting-the-token). |
| **W / kW** | Watt and kilowatt: units of power. 1 kW is 1,000 W. |

[Back to the contents](index.md)
