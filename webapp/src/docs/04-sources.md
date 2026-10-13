# 4. Sources

[Contents](index.md) · Previous: [Sites](03-sites.md) · Next: [The dashboard](05-dashboard.md)

A **source** connects a site to one device or service. WattWise supports three kinds:

| Kind | Provider name | What it collects | Needs |
|------|---------------|------------------|-------|
| `energy` | `envoy` | Production, consumption, grid and battery readings | The Envoy's address and an access token |
| `pricing` | `amber` | Buy and sell prices, plus the day's forecast | An Amber site ID, and your [Amber API key](07-account-settings.md#amber-api-key) |
| `pricing` | `local` | Buy, sell and controlled-load prices from rates you type in | A tariff name and its [rate windows](#setting-up-a-fixed-tariff) |
| `weather` | `weatherlink_live` | Temperature, humidity, wind, rain, solar radiation, pressure | The WeatherLink Live unit's address |

Open the page from the sidebar with **Sources**.

![The Sources page listing an energy, a pricing and a weather source](images/sources-list.png)

## Adding a source

1. Open **Sources** in the sidebar.
2. At the bottom of the page, choose a **Kind**. The **Provider** list updates to the providers for that kind. Kind `pricing` offers **Amber** (`amber`) and **Local tariff** (`local`).
3. Optionally type a **Label**, for example `Garage Envoy`. Labels help when a site has more than one source of the same kind. A `local` pricing source needs a **Tariff name** instead, and the box is required.
4. Click **Create source**.
5. WattWise confirms: *Source created — the matching collector will start collecting for it automatically within a few minutes.*
6. Click **Edit config** on the new row and fill in its settings. The sections below show what each provider needs. A `local` pricing source has no **Edit config**; see [Setting up a fixed tariff](#setting-up-a-fixed-tariff).

Limits:

- A site can have one pricing source and one weather source. A second one is refused with an error. To change the Amber site or the weather station's address, use **Edit config** on the existing source.
- A site can have more than one energy source.

## Editing, disabling and deleting

Each source row has these actions:

| Action | What it does |
|--------|--------------|
| **Edit config** | Opens the settings form for that provider. Click **Save config** to apply. |
| **Disable** / **Enable** | Stops or restarts collection. History is kept. |
| **Delete** | Asks you to click **Confirm delete** (or **Cancel**). |
| **Label** and **Save label** | Renames the source. The label is only for you. |

> **Warning:** A source that has already stored readings cannot be deleted. WattWise tells you to disable it instead. Disabling is the normal way to retire a source. The exception is a pricing source you are replacing: a disabled pricing source still holds the site's one pricing place. See [Setting up a fixed tariff](#setting-up-a-fixed-tariff).

Changes reach the collector within a few minutes. You do not restart anything.

![The Envoy settings form open under its source row](images/sources-edit-config.png)

## Connecting an Enphase Envoy

Choose kind `energy` and provider `envoy`, then open **Edit config**.

| Field | What to enter |
|-------|---------------|
| **Host** | The Envoy's address on your home network, such as `192.168.1.50` or `envoy.local`. A fixed (reserved) address is more reliable than a name. |
| **Token** | A local-access token for your Envoy. Shown as dots while you type. Optional when the two Enlighten fields below are filled in. |
| **Enlighten email** | Optional. The email you use to sign in to Enlighten (enlighten.enphaseenergy.com). |
| **Enlighten password** | Optional. Shown as dots while you type. |
| **Serial number** | The Envoy's serial number. The collector fills it in when it can read it from the Envoy; type it yourself if it stays empty. Needed to renew the token. |
| **My consumption numbers are flipped (importing shows as negative)** | Tick this only if the dashboard's consumption looks upside down. See [Troubleshooting](08-troubleshooting.md#consumption-or-grid-numbers-have-the-wrong-sign). |

### Getting the token

The simplest route is to fill in **Enlighten email** and **Enlighten password** and leave **Token** empty. The collector needs your Envoy's serial number. It fills **Serial number** in itself when it can read it from the Envoy. If that box is still empty after a few minutes, type the serial number yourself. Then the collector signs in to Enlighten and fetches a token. It renews the token the same way 14 days before it expires. Your email and password are stored in the WattWise database next to the token, and only the site owner can see them.

If you would rather not store your Enlighten password, get the token by hand:

1. Save the **Host** first and wait a few minutes. The collector contacts the Envoy and reports its **Serial number** back, which then appears in **Serial number**. If it does not appear, type the number in yourself; it is printed on the Envoy and shown in Enlighten.
2. Open this address in a browser, replacing `<serial>` with that number, and sign in with your Enphase Enlighten account:

   `https://enlighten.enphaseenergy.com/entrez-auth-token?serial_num=<serial>`

3. Copy the token it gives you into **Token** and click **Save config**.

Tokens last about a year. Without stored credentials, readings stop when one expires, and you repeat the steps above.

### Certificate pinning

The Envoy uses a self-signed certificate. The first time the collector reaches your Envoy, it records the certificate's fingerprint and checks it on every later connection. It sends your token only after this check is in place. It also stores the certificate's expiry date (`cert_expires_at`) and its own serial number (`cert_serial`, not the Envoy's) in the source's configuration. **Edit config** shows both, with the days left until expiry.

The Envoy replaces its certificate when the old one is within 100 days of expiring. If the collector sees a different certificate, it pins the new one only when the stored expiry is 100 days away or less (or already past), the source has a **Serial number** stored, and the Envoy reports that same number. Any other certificate change is refused, because it could be another device answering at the Envoy's address.

Changing **Host** clears the stored certificate, its expiry and the serial number, so the collector pins the device again. If you replace or reset the Envoy and the collector stops connecting, see [Troubleshooting](08-troubleshooting.md#the-envoy-stopped-reporting-after-a-reset).

## Connecting Amber Electric

Pricing needs two things:

1. **Your Amber API key**, saved once under [Account settings](07-account-settings.md#amber-api-key). The key belongs to you, not to a site.
2. **An Amber pricing source** on the site, with the **Amber site ID** filled in.

To add the source, choose kind `pricing` and provider **Amber**, open **Edit config**, enter your **Amber site ID** and click **Save config**. The site ID is the identifier Amber assigns to your property. Amber's API returns it when you list your sites with your API key.

Once prices flow, the dashboard shows [Today's Pricing](05-dashboard.md#todays-pricing) and the [grid price tile](05-dashboard.md#live-tiles), and costs can be calculated.

## Setting up a fixed tariff

Use this when your supplier charges fixed or time-of-use rates and has no price API. You type the rates in, and WattWise prices every minute from them.

1. Open **Sources** and choose `pricing` as the **Kind** and **Local tariff** as the **Provider**.
2. Type a **Tariff name**, such as `Origin Fixed`. It must be 1 to 100 characters, and a rename follows the same rule. The [Billing page](06-billing.md#tariffs) uses it as its heading, so this one reads "Origin Fixed Tariffs".
3. Click **Create source**. A **Rate windows** panel appears under the source. A local source has no **Edit config** button.

A site can have one active pricing source at a time, so remove the Amber source before you add a local one. **Delete** works only while the Amber source has no stored prices. **Disable** does not free the place: WattWise still counts a disabled source as the site's pricing source. To retire an Amber source that has prices, set its end date (`effective_to`) through the API with `PATCH /api/v1/sites/{site_id}/sources/{source_id}`. The Sources page has no control for this.

### Rate windows

Each window is one rate for one direction. The panel lists the windows for each direction separately:

| Direction | What it prices |
|---|---|
| **Import** | Energy you buy from the grid. |
| **Export (feed-in)** | Energy you sell to the grid. Type the rate as a positive number. WattWise stores it the same way it stores Amber's feed-in prices, as a negative number for a payment to you, and the dashboard shows its size. |
| **Controlled load** | Energy on a separate controlled-load circuit. |

To add a window, fill in the form at the bottom of the panel:

| Field | What to enter |
|---|---|
| **Direction** | **Import**, **Export (feed-in)** or **Controlled load**. |
| **Window label** | A name, for example `Peak`. |
| **Rate (c/kWh)** | Cents per kWh, zero or more. |
| **From** | The date the window starts. Today's date starts the window at the moment you add it. A later date starts it at midnight in your browser's time zone. An earlier date does not add prices for past days. |
| **Start** / **End** | The times of day the rate applies. |
| **Months** / **Days** | The months and days of the week it applies to. All are ticked to begin with. |

Then click **Add window**.

How the times work:

- The start time is included and the end time is not, so a window that ends at 07:00 and one that starts at 07:00 do not overlap.
- Equal start and end times cover the whole day. Use them for a flat rate.
- An end time before the start time runs past midnight, so 22:00 to 07:00 is an overnight rate.
- Months and days apply to the minute being priced, in the site's time zone. A 22:00 to 07:00 window ticked only for Monday covers Monday 00:00 to 07:00 and Monday 22:00 to midnight, not the early hours of Tuesday.

Windows on the same direction cannot overlap. If a new window clashes with one already there, WattWise refuses it and names the window in the way.

If a direction that has current windows leaves some months, days or times uncovered, the panel names the direction and gives one uncovered time, for example: *Not covered: Import (for example Saturday 00:00–07:00 in January). Those minutes will have no price.* Windows that have ended or have not started yet are left out of this check. You can still save. Minutes without a price are left out of your cost figures.

### Changing a rate

1. Click **End** on the old window. This closes it at that moment. **End** appears only on a window that has started and has not ended.
2. Add the new window with **From** set to today. It starts at the moment you add it, so the new rate follows straight on from the old one.

Cost for earlier times keeps the old rate. **Delete** removes a window, and it stops new prices from it.

You cannot schedule a rate change for a future date in advance. Two windows on the same direction overlap if they cover the same minute in any month and day. A window with no end date overlaps every later window like that, so WattWise refuses the new one. End the old window on the day the rate changes.

### When changes take effect

Every 5 minutes, the collector writes prices from the start of the current UTC hour to 48 hours ahead. It never writes prices for earlier times, so a window whose **From** date is in the past adds no prices for the days already gone. A new or changed window reaches the dashboard within about 5 minutes.

Prices already written stay as they are after an **End** or **Delete**. That includes the next 48 hours, so minutes that no other window covers keep the old rate until those 48 hours have passed.

## Connecting a WeatherLink Live station

Choose kind `weather` and provider `weatherlink_live`, open **Edit config**, enter the **Host** (the address of your WeatherLink Live unit on your home network) and click **Save config**. Weather tiles appear on the [dashboard](05-dashboard.md#weather-tiles).

## Next

- [The dashboard](05-dashboard.md): see your data
- [Billing](06-billing.md): prices alone do not include fixed charges
- [Troubleshooting](08-troubleshooting.md#the-dashboard-is-empty) if tiles stay empty
