# 8. Schedules and the air conditioner

[Contents](index.md) · Previous: [Billing](07-billing.md) · Next: [Account settings](09-account-settings.md)

A **schedule** tells WattWise to do something to a device at a set moment. Today the device is an Actron Neo air conditioner. The usual use is to turn it off while a [demand window](07-billing.md#demand-windows) runs, so its draw stays out of your peak-demand charge, and to turn it back on afterwards.

This chapter is for the site owner. Other people with access to the site see the **Schedule** tab read-only.

A small program called an **actor** carries out the schedules. It runs next to the collectors, signs in to your Actron account and checks the schedules every 30 seconds. See [Getting started](02-getting-started.md#how-the-pieces-fit-together).

## Adding the air conditioner

1. Open **Site settings**, pick the site and open the **Schedule** tab.
2. Type a name for the air conditioner and add it.
![The Schedule tab while it waits for the Actron sign-in, showing a code and a link](images/schedule-sign-in.png)

3. The page shows an Actron code and a link. Open the link, sign in to Actron and enter the code. The page checks every 10 seconds. When you approve, the code disappears.
4. The actor reads what your unit can do. **New schedule** becomes available when it has.

If the code expires, the page shows a new one after about 30 seconds. Other people with access to the site see only a line saying you are signing in. They never see the code.

> **Note:** WattWise uses the first air conditioner on your Actron account.

## Making a schedule

Click **New schedule**. The form has numbered steps. Click **Save schedule** at the end.

![The Schedule tab with two schedules listed and the first one open in the editor](images/schedule-list.png)

![Steps 3 to 7 of the editor: Only if with a time of day row, Do this, When it ends, How often and If it fails](images/schedule-editor.png)

| Step | What you choose |
|------|-----------------|
| **Actor** | A name for the schedule. The device it commands is shown under the name. An **Enabled** switch turns the whole schedule on or off. |
| **When** | The trigger: a demand window starts or ends, a set time on chosen days, sunrise or sunset, the price goes above a level, or the indoor or outdoor temperature crosses a level. |
| **Only if** | Optional conditions. Every ticked row must pass or the run is skipped. |
| **Do this** | One to eight actions, run in order, such as turn off, set mode, set temperature, set fan speed, switch zones or set the temperature of one or more zones. |
| **When it ends** | Whether and when to put the unit back as it was. |
| **How often** | Optional limits: **Minutes between runs** and **Do not run again until**. |
| **If it fails** | How many tries, the seconds between tries, when to give up, and **Email me if it still fails**. |

### When: sunrise and sunset, price and temperature

**At sunrise or sunset** takes an **Event**, **Minutes after (negative is before)**, from -180 to 180, and the **Days** it runs. "30 minutes before sunset on weekdays" is Sunset, -30, Monday to Friday. A note under the fields says whether the times come from the site's location or are [estimated from its time zone](04-sites.md#location-and-sun-times). WattWise refuses to save a schedule that uses sunrise or sunset when the site has neither.

**Price goes above a level** and **A temperature crosses a level** fire while the level is passed, not only at the moment it is crossed. Each has a re-arm field:

- **Can fire again once it is back at or below (c/kWh)** for a price.
- **Can fire again once it is back at or below (C)** for a temperature that rises above a level, or **at or above (C)** for one that falls below it.

After the schedule fires, it waits until the reading is back past the re-arm value. This stops an air conditioner starting and stopping every few minutes while a reading hovers around the level. For a trigger at 30 C, a re-arm value of 28 C means the schedule fires again only after the temperature has dropped to 28 C or lower. Leave the field blank to allow it as soon as the level is no longer passed. For a temperature, the editor suggests 2 degrees back inside the level until you type a value. The re-arm value must be on the far side of the level: below it for a price or a rise, above it for a fall.

A schedule that is waiting to re-arm is **disarmed**. WattWise remembers this, so restarting the actor does not fire the schedule again. Saving a change to the trigger arms it again.

### Only if

A row can test a reading, such as indoor temperature above 27, or the calendar. A reading that is missing or older than 5 minutes fails the row. The calendar rows use the site's time zone and never fail for missing data:

| Row | Passes when |
|-----|-------------|
| **Month** | The current month is, or is not, one you picked |
| **Day of week** | The current day is, or is not, one you picked |
| **Week** | The week is odd or even. Weeks run Monday to Sunday. |
| **Demand window** | A window, or any window, is running now, or is not |
| **Time of day** | The time is **between** a start and an end |

A **Time of day** row has two times. Each is a **Clock time** such as 18:00 in the site's time zone, or **Sunrise** or **Sunset** with an offset in minutes. The row passes from the start up to the end. An end that is not later than the start runs through midnight, so Sunset to Sunrise is the night. The start and end must differ. A row that uses sunrise or sunset fails on a day when the sun does not rise or set. In **Only restore if** it never keeps the unit off for that reason.

For example, "only in summer, when it is over 30 outside and no demand window is running" is a Month row for December to February, a reading row for the outdoor temperature and a Demand window row set to is not active. Untick a row to keep it but ignore it.

### When it ends

The unit is put back only if every action in the schedule can be undone. The choices are:

- **When the window ends**, if the trigger is a demand window starting.
- **After a set number of minutes.**
- **At a set time.**
- **Only when a condition below is met**, with 24 hours as the limit.

Two optional condition lists refine this. **Only restore if** holds the unit off until every ticked row passes. **Restore early when** puts it back sooner when every ticked row is true. A reading that is missing counts as passing the first list, so a dead sensor never keeps the unit off.

If you changed a setting on the unit by hand while it was off, WattWise leaves that setting alone when it restores.

## How often

Step 6, **How often**, limits how soon a schedule can run again:

| Field | What it does |
|-------|--------------|
| **Minutes between runs** | The schedule does not run again until this long after it was last put back, or after it last ran if it is not put back. 0 turns it off. The editor sets 30 when you choose a temperature trigger and the field is 0. The limit is 1440 (a day). |
| **Do not run again until** | After a run, the schedule waits for the next time you give: a clock time, or sunrise or sunset with an offset. It is counted from the start of the last run. |

Skipped runs and test runs do not count towards either limit.

What you see while a limit holds depends on the trigger. A set time, a demand window or sunrise or sunset is recorded in the run history as **Skipped** with a message such as *waiting until Sun 06:00*. A price or temperature trigger records nothing and stays armed, so it fires when the limit ends if the price or temperature is still past the level.

Use **Minutes between runs** and the re-arm field together for a temperature trigger. The gap stops back-to-back runs. Re-arm stops a reading that hovers around the level from restarting the schedule the moment the gap ends.

## Testing

Select a schedule and click **Test now**. The actor runs every action within about 30 seconds, ignoring the trigger and conditions. If the schedule restores, it puts the unit back after 30 seconds. Use it after saving to check the unit responds.

## Run history

The bottom of the schedule's card, under **Test now**, lists that schedule's newest runs.

| Pill | Meaning |
|------|---------|
| **OK** | Every action worked. |
| **Skipped** | A condition failed, another schedule held the unit, or the actor was down for more than 10 minutes after the trigger. The message says which. |
| **Failed** | The actor gave up. The message has the last error. |
| **You took over** | You changed the unit yourself after the action. WattWise does not restore it. |
| **Queued** / **Running** | Not finished. |

If one action of several fails, the others still run and the run is recorded as partial, with the failed action named.

![The Test now button and run history at the bottom of a schedule, then the air conditioner's current state and each zone](images/schedule-run-history.png)

The unit's current state, with each zone, is shown in its own card below the schedule. It reads **Not updating** when the actor has not reported for 2 minutes.

## The log

At the top of the site's dashboard, the log lists what needs you: failed schedule runs and problems that collectors report. It also lists each action run that finished OK in the last 24 hours. When there is nothing to list, the log does not appear. Test runs and restore runs that succeed are not listed.

![The log with a red failed run, an amber weather station alert, a blue Envoy notice with a Dismiss button and a blue Ran OK row](images/dashboard-needs-attention.png)

Each row is one line: a coloured bulb, the date and time in the site's time zone, the source or schedule and a message, then one button on the right, except on a **Ran OK** row, which has none. Red is a failure, amber is a problem that leaves part of a device working, and blue is a notice. Red and amber bulbs pulse slowly while the problem lasts. The log glows red, amber or blue to match the worst row that is still a problem. A successful run does not change the glow.

When WattWise sees a problem go away, the bulb shows a grey centre inside a ring in its old colour. The row stays for one hour and then disappears. A cleared row does not change the log's colour. The log lists the five most serious rows and links to the rest.

| Row | Meaning | What to do |
|-----|---------|------------|
| **Ran OK** | The schedule's action finished OK. A blue row stays for 24 hours. | Nothing. |
| **Run failed** | An action or restore did not work after its tries. | Check the unit and turn it on if a restore failed. |
| **Still off** | The unit is still off after its restore time. | Turn it on, then check the restore settings. An **Only restore if** row that never passes causes this. |
| **Not reporting** | The unit is held off and the actor has been silent for 5 minutes. | Ask your administrator to check the actor container. See [Troubleshooting](10-troubleshooting.md#the-air-conditioner-schedule-is-not-working). |
| **Needs sign-in** | Actron rejected the stored sign-in. | Open the **Schedule** tab, click **Sign in again** and approve the code. This works while the unit is held off. |
| An alert from a collector | A device or service needs you, for example an Envoy that cannot be reached. The message is one short line. | Click **Open Collectors**, read the message and fix the cause. It clears itself. The collector log has more detail. |
| A notice with **Dismiss** | Something changed that you should know about, such as an Envoy firmware upgrade or a certificate re-pin. See [Certificate pinning](05-sources.md#certificate-pinning). | Read it and click **Dismiss**. It stays until you do, even after the cause is gone. |

**Email me if it still fails** emails you when a run fails, and once more if the unit stays off or the actor goes silent.

## Limits

- You cannot delete a schedule or the air conditioner while a run still owes a restore. Wait for the restore, or fix the sign-in.
- While a schedule holds the unit off, you cannot change its trigger, actions or restore settings. Disabling it brings the restore forward.
- Two schedules on one unit are not checked for overlap when saved. The second one is skipped while the first is active.
- Deleting a schedule or the air conditioner deletes its run history.

## Next

- [Billing](07-billing.md#demand-windows): the windows a schedule can follow
- [Troubleshooting](10-troubleshooting.md)
