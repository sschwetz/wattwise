# 9. Glossary

[Contents](index.md) · Previous: [Troubleshooting](08-troubleshooting.md)

| Term | Definition |
|------|-----------|
| **Amber Electric** | An Australian electricity retailer that charges a wholesale-linked price that changes every 5 minutes. WattWise reads its prices through the Amber API. See [Connecting Amber Electric](04-sources.md#connecting-amber-electric). |
| **Anchor day** | The day of the month a billing period starts. See [Billing cycles](06-billing.md#billing-cycles). |
| **Authenticator app** | A phone app that shows a 6-digit code that changes every 30 seconds. See [Two-factor authentication](02-signing-in.md#setting-up-two-factor-authentication). |
| **Billing cycle** | How many months a bill covers and the day it starts. See [Billing cycles](06-billing.md#billing-cycles). |
| **Billing period** | One bill's span of dates, from the billing cycle. |
| **Buy price** | What you pay per kWh to import electricity. |
| **c/kWh** | Cents per kilowatt-hour: the price of energy. |
| **c/kW/day** | Cents per kilowatt per day: the rate of a demand charge. |
| **Collector** | A background program that reads one kind of device or service and sends readings to WattWise. See [How the pieces fit together](01-getting-started.md#how-the-pieces-fit-together). |
| **Consumption** | The power your home is using. |
| **Credit** | A negative cost: you earned more than you spent. Shown in green. |
| **CT (current transformer)** | A clamp sensor that measures power flowing through a cable. The Envoy uses CTs to measure production and consumption. |
| **Demand charge** | A charge based on your highest average power draw in a billing period, within a demand window. See [Demand charges](06-billing.md#demand-charges). |
| **Demand window** | A repeating period (months, days, hours) when peak demand is measured. See [Demand windows](06-billing.md#demand-windows). |
| **Envoy** | The Enphase gateway that talks to your solar inverters and battery. |
| **Export** | Sending power to the grid. |
| **Export adjustment** | A bonus or penalty, in cents per kWh, added to your feed-in price during a repeating period. See [Export adjustments](06-billing.md#export-adjustments). |
| **Feed-in (sell) price** | What you are paid per kWh for exporting. Amber publishes it as a negative number. |
| **Gross cost** | Cost counted as if all consumption were imported and all production exported. See [Net and gross cost](05-dashboard.md#net-and-gross-cost). |
| **Grid** | The public electricity network. |
| **Import** | Drawing power from the grid. |
| **kW / kWh** | Kilowatt (power, a rate) and kilowatt-hour (energy, an amount). 1 kW for 1 hour is 1 kWh. |
| **Label** | An optional name on a source, for your own use. |
| **Monthly fee** | A fixed monthly account charge on a tariff. See [Tariffs](06-billing.md#tariffs). |
| **Monthly fee discount** | A temporary lower monthly fee for a range of dates. See [Monthly fee discounts](06-billing.md#monthly-fee-discounts). |
| **Net cost** | Cost based on energy that actually crossed the grid meter. This is what your bill shows. |
| **Production** | The power your solar panels generate. |
| **Provider** | The brand or system behind a source: `envoy`, `amber`, `local` (rates you type in) or `weatherlink_live`. |
| **Recovery code** | A single-use code that replaces an authenticator code. See [Recovery codes](02-signing-in.md#recovery-codes). |
| **Service availability charge** | The daily supply charge on a tariff, in cents per day. |
| **Site** | One monitored property. See [Sites](03-sites.md). |
| **Source** | A connection from a site to a device or service. See [Sources](04-sources.md). |
| **State of charge (SoC)** | How full the battery is, as a percentage. Shown on the **Battery** tile. |
| **Tariff** | The fixed charges and rules your retailer applies. See [Tariffs](06-billing.md#tariffs). |
| **Token** | A secret that lets WattWise read your Envoy. See [Getting the token](04-sources.md#getting-the-token). |
| **W / kW** | Watt and kilowatt: units of power. 1 kW is 1,000 W. |

[Back to the contents](index.md)
