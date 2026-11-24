# 14. Admin schedule

[Contents](index.md) · Previous: [Admin email](13-admin-email.md)

This chapter is for administrators. WattWise runs a few background jobs on its own: it calculates demand charges, tidies old data, watches the air conditioner actor and reports which version each part is running. The **Schedule** tab of Admin Settings shows when each job runs next and last ran, and lets you change how often or at what time it runs. You do not need to restart anything.

Do not confuse this tab with the **Schedule** tab in a site's settings, which holds air conditioner schedules. See [Schedules and the air conditioner](08-schedules.md).

## Opening the Schedule tab

Click your name in the top right corner, then **Admin Settings**. **Schedule** is the third tab on the left, after **Email** and **Users**. See [Admin email](13-admin-email.md) and [Admin users](12-admin-users.md) for those.

Each job has a row with its name, its timing, **Save**, and a line reading *Next run: ... Last run: ...*.

| Job | What it does | Default | Allowed |
|-----|--------------|---------|---------|
| Demand interval calculation | Works out the average demand for each 5-minute slot | Every 5 minutes | 1 to 1440 minutes |
| Daily demand charge | Calculates the demand charge for each billing period | 00:10 | Any time of day |
| Retention rollup | Turns readings older than a year into 5-minute averages and deletes the originals | 00:20 | Any time of day |
| Actor watchdog | Finds an air conditioner that stayed off too long or an actor that went silent, and emails the site owner | Every 1 minute | 1 to 1440 minutes |
| Mail outbox cleanup | Deletes old email records | 00:30 | Any time of day |
| Service status report | Records which version WattWise is running, for the About dialog | Every 1 minute | 1 to 2 minutes |

One more job, which deletes the first-start password file, is not listed. You cannot change it.

## Changing a job

1. Open the **Schedule** tab.
2. Change the number or the time in the job's row.
3. Click **Save**.

A job that runs at an interval takes a whole number of minutes. Each interval job has a smallest and a largest value, listed in the table above. WattWise refuses a smaller or larger value, even one you type into the box. A job that runs once a day takes a time of day as hours and minutes. The time is in the time zone printed next to it. That is the time zone of the server's core-api container, which is UTC unless whoever runs the server set another one. It is not your site's time zone.

The change applies at once and stays after a restart. A job that is running when you save finishes first, and the new timing starts from the next run. If WattWise refuses a value, the message under the row names the limit, such as *interval_minutes must be at most 1440*. The **Service status report** job stops at 2 minutes because the About dialog marks a service **not reporting** after 3 minutes without a report. See [Getting started](02-getting-started.md#the-screens) for the About dialog.

## Resetting a job

A row whose timing differs from the default shows **Reset to default**. Click it to put the job back on the default in the table above. **Reset to default** is hidden when the job is already on its default.

## Next run and last run

**Next run** and **Last run** use your browser's time zone, so a daily job's **Next run** can differ from the time zone beside its time box.

**Last run** shows *never* after WattWise starts, until the job has run once. WattWise keeps it in memory, not in the database, so it resets whenever the server restarts. A failed run counts as a run.

## When the scheduler is off

If the tab says *The scheduler is off. Changes are saved and apply when it is enabled.*, whoever runs the server has set `SCHEDULER_ENABLED=false`, and no job runs. **Next run** shows *not scheduled*. A save is stored. It takes effect after they enable the scheduler and restart core-api.

## Next run and last run

**Next run** and **Last run** use your browser's time zone, so a daily job's **Next run** can differ from the time zone beside its time box.

**Last run** shows *never* after WattWise starts, until the job has run once. WattWise keeps it in memory, not in the database, so it resets whenever the server restarts. A failed run counts as a run.

## When the scheduler is off

If the tab says *The scheduler is off. Changes are saved and apply when it is enabled.*, whoever runs the server has set `SCHEDULER_ENABLED=false`, and no job runs. **Next run** shows *not scheduled*. A save is stored. It takes effect after they enable the scheduler and restart core-api.

## Keep the service status report frequent

The About dialog marks a service **not reporting** when its last report is more than 3 minutes old. The **Service status report** job writes the reports for core-api and the web app. If you set its interval above 2 minutes, those two rows go stale between runs, and the dialog shows them as not reporting even though they work. Leave it at 1 or 2 minutes. See [Getting started](02-getting-started.md#the-screens) for the About dialog.

## Next

- [Troubleshooting Admin Settings](10-troubleshooting.md#admin-settings-problems)
- [Glossary](11-glossary.md): job and interval
- [Admin email](13-admin-email.md): the **Email** tab

[Back to the contents](index.md)
