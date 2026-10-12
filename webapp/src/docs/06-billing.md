# 6. Billing

[Contents](index.md) · Previous: [The dashboard](05-dashboard.md) · Next: [Account settings](07-account-settings.md)

The **Billing** page (sidebar link **Billing**) holds the parts of your electricity bill that are not a per-kWh price: fixed charges, time-of-day rules and the date your bill closes. [Amber prices](04-sources.md#connecting-amber-electric) cover the per-kWh part.

The page has three cards: [Tariffs](#tariffs), [Billing cycle](#billing-cycles) and [Demand charges](#demand-charges).

![The Tariffs card with an active tariff and its demand, export and fee windows](images/billing-tariffs.png)

## Before you start

Have a recent bill or your retailer's tariff sheet open. You need:

- The daily supply (service availability) charge, in cents per day.
- The monthly account fee, if you have one, in dollars.
- Any demand charge: its time window, days, months and rate in cents per kW per day.
- Any time-based feed-in bonus or penalty.
- The day of the month your bill closes and how many months a bill covers.

## How the cost is calculated

WattWise builds each cost figure minute by minute:

1. **Energy bought**: kWh imported in that minute × the buy price.
2. **Energy sold**: kWh exported × the sell price (a payment to you). A positive [export adjustment](#export-adjustments) that applies at that time raises the payment, a negative one lowers it.
3. **Daily service charge**: the tariff's cents per day, added in proportion to how much of the day has passed.
4. **Monthly fee**: the tariff's monthly fee (or its [discounted amount](#monthly-fee-discounts)), divided evenly across the days of the [billing cycle](#billing-cycles). Without a billing cycle, no monthly fee is charged.
5. **Demand charge**: the [demand charge](#demand-charges) for the billing period, spread across the minutes inside the demand window.

The **Cost** total and the billing period tiles include all five parts. The cost line on the history chart shows only the energy parts (1 and 2) plus the demand charge, so fixed fees do not show up as a spike at midnight. Prices and energy come from your sources. Everything on this page comes from you. Whenever a minute has energy but no price, that minute is left out. See [Net and gross cost](05-dashboard.md#net-and-gross-cost) for the two ways the energy side can be counted.

## Tariffs

A **tariff** is a set of fixed charges that applies from the moment you add it until you end it.

### Adding a tariff

1. In the **Tariffs** card, fill in the form at the bottom:
   - **Service availability (cents/day)**: your daily supply charge, for example `98.45`.
   - **Monthly fee ($/month)**: optional. Leave it empty if your bill has no monthly account fee.
2. Click **Add tariff**.

The tariff starts at the moment you click the button.

### Changing a tariff

Tariffs are not edited in place, so past bills keep their original rates.

1. Click **End this tariff** on the active tariff.
2. Add a new tariff with the new numbers.

Ended tariffs stay in the list, struck through. Click one to see its windows. Always end the old tariff before you add its replacement.

> **Note:** Demand windows, export adjustments and fee discounts belong to one tariff. The forms for them appear under an active tariff once it exists. When you add a replacement tariff, enter them again under the new one.

### Demand windows

A **demand window** is a repeating period when your highest power draw sets a charge. Retailers use it to price peak demand on the network.

Add one under an active tariff:

| Field | What to enter |
|-------|---------------|
| **Window label** | A name, for example `Summer peak`. |
| **Months** | Month numbers separated by commas, `1` to `12`. For December to February: `12,1,2`. |
| **Days of week** | Day numbers separated by commas, `0` = Monday to `6` = Sunday. Weekdays: `0,1,2,3,4`. |
| **Start time** / **End time** | 24-hour times as `HH:MM`, for example `16:00` and `21:00`. |
| **Rate** | Cents per kW per day. |

Click **Add demand window**. The list shows each window as, for example, `16:00–21:00 · Mon, Tue, Wed, Thu, Fri · Dec, Jan, Feb · every 30 min`. The "every 30 min" is the measurement interval: WattWise averages your grid import over 30-minute spans and takes the highest one.

While a window is in effect, the [dashboard](05-dashboard.md#demand-window-notice) shows **DEMAND WINDOW ACTIVE**. Times use the [site's time zone](03-sites.md#creating-a-site).

### Export adjustments

An **export adjustment** raises or lowers your feed-in price during a repeating period. Use it for a retailer bonus (positive) or a penalty such as a low-export-price period (negative).

| Field | What to enter |
|-------|---------------|
| **Adjustment label** | A name. |
| **Export months**, **Export days of week**, **Export start time**, **Export end time** | Same format as a demand window. |
| **Adjustment** | Cents per kWh. Positive is an incentive, negative is a disincentive. |

Click **Add export adjustment**. If several adjustments cover the same minute, they add together.

### Monthly fee discounts

If your tariff has a monthly fee, a **fee discount** replaces that fee with a lower amount for a fixed span of dates, for example a first-year promotion. The form appears only under a tariff that has a monthly fee.

| Field | What to enter |
|-------|---------------|
| **Discount label** | A name. |
| **Discount starts (YYYY-MM-DD)** / **Discount ends (YYYY-MM-DD)** | The first and last dates, for example `2026-11-01`. |
| **Discounted fee** | The dollars per month you pay during the span instead of the normal fee. |

Click **Add fee discount**. Expired discounts stay in the list, struck through. Only one fee applies on a given day.

## Billing cycles

A **billing cycle** tells WattWise when your bill closes. It drives the **Billing period cost** tile and the [demand charge](#demand-charges) calculation.

1. In the **Billing cycle** card, enter:
   - **Cycle length (months)**: `1` for monthly, `3` for quarterly.
   - **Anchor day (1-31)**: the day of the month each bill period starts, for example `15`.
2. Click **Add billing cycle**.

The list shows it as, for example, *Every 1 month(s), anchored on day 15, from 13/10/2026*. To change it, click **End this cycle** and add a new one. Periods that were already worked out keep the old cycle.

Without a billing cycle, the dashboard hides the **Billing period cost** tile and no demand charges are calculated.

## Demand charges

The **Demand charges** card lists the demand charge for each billing period.

![Billing cycle and Demand charges cards](images/billing-cycle-demand.png)

WattWise works it out once a day, shortly after midnight, and again each day until the period ends:

1. It measures your grid import in rolling 30-minute spans (or the window's own interval).
2. It finds the highest span in the billing period.
3. It multiplies that peak, in kW, by the window's rate and by the number of days in the period.

Example: a peak of 6 kW, a rate of 45 cents per kW per day and a 30-day period give 6 × 45 × 30 = 8,100 cents, which is `$81.00`.

Notes:

- The card says *No demand charges yet* until enough data exists for a billing period.
- The figure for the period in progress is an estimate. It is final once the period ends and the data has no gaps. A collector outage can leave a period without a final figure.
- Without a demand window on the tariff, no demand charge exists.

## Next

- [The dashboard](05-dashboard.md#cost-and-credit): where these numbers show up
- [Troubleshooting costs](08-troubleshooting.md#costs-look-too-low-or-are-missing)
- [Glossary](09-glossary.md)
