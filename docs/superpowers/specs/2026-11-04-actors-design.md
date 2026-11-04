# Actors design

Date: 2026-11-04. Status: draft for approval.

## Intent

Collectors bring data into WattWise. An **actor** does the opposite: it reads site data from Core API and sends commands to a device. The first actor, `actron-neo-actor`, switches a ducted Actron Neo air conditioner off at the start of a tariff demand window and puts it back afterwards. The owner wants to avoid running the unit during demand periods without touching the wall controller.

Actors follow the collector design: one small Python container per kind of device, one shared key, discovery from Core API, no state that a restart cannot recover. They differ in direction, and in that they need a user-facing **Schedule** tab to say when and what.

The owner builds a schedule in the web app from dropdowns and tick boxes. The actor tells WattWise what it can do, and the web app draws the form from that description. A new actor needs no web app changes.

## Goals

- A **Schedule** page under each site (`/sites/:siteId/schedule`, a new item in the site side rail) listing the site's schedules, with an editor and a run history. The approved mockup is [`2026-11-04-actors-schedule-mockup.html`](2026-11-04-actors-schedule-mockup.html) in this folder; it uses the app's existing tokens, chips and form styles.
- A generic actor contract (below) that any future actor can implement.
- `actron-neo-actor` as the first implementation, with whole-system and per-zone control.
- Every run is recorded: what triggered it, what the actor did, the result, and why it skipped when it did.
- The unit is never left off without the owner being told (see Failure behaviour).
- A live tile at the top of the site Dashboard that shows current actor failures (see Web app).
- The actor keeps its record of the unit in sync with the unit *(owner's requirement)*. If the owner switches the unit on or off, or enables or disables a zone, from the wall controller or the Actron app, the actor's record and the Schedule page show it within seconds. The actor uses the library's push updates where the unit supports them and polls where not.
- The owner can always override: if they switch the unit on during an off window, the actor accepts it and leaves the unit alone for the rest of that window.

## Non-goals

These are the builder's assumptions to keep the first version small. Mark any you disagree with.

- Uploading or editing scripts through the web app. Actors are code the owner deploys.
- Actors other than `actron-neo-actor`. The contract is general; only one implementation is built.
- Several actions in one schedule. A schedule has one action and, optionally, its restore.
- One schedule driving several devices. A schedule belongs to one actor instance.

## Anti-goals

- A schedule that silently stops working. A skipped or failed run must show in the history.
- Commands sent for a disabled site, a disabled schedule or a disabled actor instance.
- Tokens or login details in logs, replies or plaintext columns (see CLAUDE.md, Storing Secrets).

## The actor contract

An actor is a container with `CORE_API_URL` and `ACTOR_KEY` from the environment. It sends `X-Actor-Key` and `X-Actor-Provider` (for example `actron_neo`) on every call, and Core API returns only that provider's instances. This copies how pricing collectors are scoped. One `ACTOR_KEY` covers all actors; adding a site needs no new key. *(My call.)*

Each tick the actor:

1. **Discovers** its instances: `GET /actor/instances?provider=…` (disabled sites and disabled instances are left out, as for collectors). The one exception is an instance with an unfinished action run: it stays in the list with `restore_only: true`, so the actor can still put the unit back. *(My call.)*
2. **Declares** what it can do: `PUT /actor/instances/{id}/capabilities`. The body is JSON with:
   - `actions`: each has an `id`, a `label`, and `params`. A param has a type (`number`, `choice`, `bool`, `multi_choice`), a label, optional min, max and unit, and its options. Options may be fixed or discovered from the device (the zone list), so the declaration is re-sent whenever the actor refreshes it.
   - `readings`: each has an `id`, a `label`, a unit and an optional target (for example one zone).
   - For each action, whether it can be restored, and which state it saves first.
3. **Fetches** its schedules and the site context (latest outdoor temperature, battery charge, current price, and the next occurrences of each tariff demand window as UTC start and end instants): `GET /actor/instances/{id}/schedules` and `…/context`. Core API works out the occurrences from the site time zone, including windows that cross midnight and seasonal months, so the actor does no time zone logic. *(My call.)*
4. **Decides** what is due, runs it, and **reports**: `POST /actor/instances/{id}/runs`, and `PUT …/readings` for the latest device state, whenever it changes and at least once a minute as a heartbeat (the web app shows it; only the latest value is kept, and the heartbeat sets `last_seen_at`).
5. **Reports pending authentication** when the device needs a sign-in (see the Actron section) through `PATCH …/discovered-config`, as the Envoy collector does.

**Run lifecycle** *(my call)*:
- The actor creates the run row with `POST …/runs` when a trigger comes due, with outcome `pending`. The unique key on (`schedule_id`, `kind`, `trigger_at`) makes a second create fail, so only one process runs a trigger.
- It updates the row to `ok`, `skipped` or `failed` when finished.
- The restore is its own row of kind `restore`, with `parent_run_id` pointing at the action run that holds the `snapshot`. Its `trigger_at` is the restore time.
- A `test` run is created by the web app as `pending`. The actor claims it with `PATCH` from `pending` to `running`, which Core API applies only if the row is still `pending`.
- One actor process per provider is supported.

Core API checks every saved schedule against the instance's declared capabilities, so the guided form cannot save an action or value the device does not support. The actor checks again before it acts.

## Data model (new tables)

- `actor_instances`: `site_id`, `provider`, `name`, `enabled`, `config` (`EncryptedJSON`, holds the refresh token), `auth_state` (plain JSON: the sign-in code and link while waiting, or `needs_sign_in`; cleared on approval), `capabilities` (JSON), `capabilities_updated_at`, `latest_readings` (JSON), `last_seen_at` (set on each actor call).
- `actor_schedules`: `instance_id`, `name`, `enabled`, `trigger` (JSON: demand window start or end with a window id, a set time with days, or a price above a level), `conditions` (JSON list), `action` (JSON: action id and params), `restore` (JSON: on or off, conditions that must hold, conditions that restore early), `retry` (JSON: attempts, seconds between tries, give-up after minutes), `notify_email` (bool, default false).
- `actor_runs`: `schedule_id`, `kind` (`action`, `restore`, `test`), `watchdog_notified_at` (set when the watchdog has emailed about this run), `trigger_at`, `started_at`, `finished_at`, `outcome` (`pending`, `running`, `ok`, `skipped`, `failed`, `overridden`), `message`, `attempts`, `parent_run_id` (restore rows only), `snapshot` (JSON: the state saved before acting). Unique on (`schedule_id`, `kind`, `trigger_at`), so a restart or a second actor process cannot run the same trigger twice.

Demand windows already exist as `DemandWindow` rows on the site tariff. The trigger references one by id; edits to the window change future triggers.

## Web app

- New route `/sites/:siteId/schedule`. The page has the schedule list, the numbered editor (actor, when, only if, do this, when the window ends, if it fails) and the run history. Step 6, "If it fails", has the retry settings and an "email me if it still fails" tick box.
- The restore time depends on the trigger *(my call)*. A demand window trigger restores at the window end. A set-time or price trigger has no end, so the step offers "restore after N minutes", "restore at a set time", or restore only when the early-restore conditions hold. The step is headed "When it ends", and the window wording appears only for window triggers.
- The "when it ends" step has: restore how it was before; only restore if conditions hold; restore early when conditions hold (for example indoor above a set temperature). *(Changed from the mockup's "Stay off if indoor is above 27", which read backwards for cooling. Please check.)*
- **Unit state panel** above the run history: power, mode, setpoint and each zone's on/off state, with the time of the last update. It shows "not updating" when `last_seen_at` is more than 2 minutes old. It is read-only; the Schedule page does not control the unit directly.
- **Test now** inserts a `test` run with outcome `pending`; the actor picks it up on its next tick.
- **Failures tile on the Dashboard** (`DashboardPage.tsx`), above the existing tiles. It lists the site's current actor failures, each with the schedule name, what went wrong, when, and a link to the Schedule page. It uses the `danger-glow` style while the list is not empty. A failure is current until a later run of the same schedule succeeds or is skipped for a normal reason, or the cause clears (the actor is back, or the sign-in is approved). The tile shows these cases:
  - an action or restore run marked `failed`;
  - the unit is still off after its restore time (the watchdog case below);
  - the actor has gone quiet (`last_seen_at` is old) with an unfinished run;
  - an instance that "needs sign-in".

  Core API serves it from `GET /sites/{id}/actors/failures`. The tile refreshes about every 15 seconds, like the other live tiles. When the site has actor instances and no failures it shows a quiet "All schedules OK"; with no actor instances it does not appear. *(My call on the quiet state; the builder may use the existing live feed instead of polling.)*
- Who can edit follows the existing site access rules. Viewers see schedules and history, not the editor.
- Adding an Actron instance shows the sign-in code and link while the actor is waiting for approval.
- Screens follow the existing patterns: `SubmitButton` with Saving and Saved, `QueryError`, `AccessNote`, and read-only fieldsets.

## actron-neo-actor

Uses `actron-neo-api` 0.5.x (unofficial, async, Python 3.12). Findings from running it against the owner's unit on 2026-11-04:

- **Sign-in:** OAuth2 device code. The actor calls `request_device_code()`, posts the code and link to Core API for the web app to show, then `poll_for_token()`. It stores the refresh token (encrypted) through `discovered-config` and restores sessions from it.
- **Power:** `set_system_mode("COOL")` turns the unit on, `set_system_mode("OFF")` turns it off. Each took about 6 seconds to show in `is_on`. `is_on` and `mode` are separate: after OFF the mode still read `COOL`. The actor must poll for the result for about 45 seconds before it counts a command as failed.
- **Zones:** `remote_zone_info[i].enable(bool)` works the same way (about 6 seconds). `enabled_zones` in the settings holds all eight states. Zone names come from `title` and are discovered at runtime.
- **Other actions available:** `set_temperature` (system and zone), `set_fan_mode`, `set_continuous_mode`, `set_quiet_mode`, `set_turbo_mode`, `set_away_mode`.
- **Readings:** indoor `master_info.live_temp_c`; outdoor `master_info.live_outdoor_temp_c`; humidity `master_info.live_humidity_pc`; per-zone `live_temp_c`; plus `is_on`, `mode`, `fan_mode`, both setpoints. Do **not** use `ac_system.outdoor_unit.amb_temp`: it read 0.0 while the real value was 18.6.
- **Sync** *(owner's requirement)*:
  - The actor calls `start_push([serial])` for each signed-in instance and uses `subscribe_system_updates` to keep an in-memory copy of the state: `is_on`, `mode`, `fan_mode`, both setpoints, `enabled_zones`, and the readings above.
  - If `start_push` returns `False`, or the push connection drops, it polls `update_status()` every 30 seconds until push is back.
  - Even with push working it polls once every 5 minutes, to catch missed messages.
  - It sends the state to Core API on every change and once a minute.
  - Push on the owner's unit is untested. Early in the build, check that changes made at the wall controller arrive by push and how fast. The polling fallback covers a unit where they do not.
- **Snapshot before acting:** only the fields the action changes. A system off action saves `is_on` and `mode`. A zone action saves the on/off state of each zone it will change. A temperature or fan action saves that setting.
- **Restore:** sends the saved mode if the unit was on, or `OFF` if it was off. For a zone action it restores only the zones the actor changed. If the synced state shows the owner has changed a field since the actor acted, the actor leaves that field alone (the override rule, applied per field). It then confirms by checking the synced state for about 45 seconds.
- The unit's raw status also holds the wall controller PIN and installer contact details. The actor must never log or store the raw status.

## Failure behaviour

- **Retries:** per schedule, set by the user (attempts, seconds between tries, give-up after). Applies to the action and the restore.
- **Giving up:** the run is marked `failed` with the last error. If `notify_email` is on, Core API sends one email to the owner through the existing mail relay (the actor cannot reach it).
- **Missed trigger:** if the actor was down at the trigger time, it still runs if it is within 10 minutes of it, otherwise the run is recorded as `skipped: missed`. *(My call.)*
- **Unit already on when the window starts** *(owner's decision)*: the actor turns it off. That is the normal action.
- **Owner turns the unit on during an off window** *(owner's decision)*: the actor accepts it and does not turn the unit off again.
  - On each state update while an action run is unfinished, the actor compares the synced state with the state it set. It waits up to 45 seconds after its own command before it reads a mismatch as the owner's change. This covers a zone as well: if the owner enables a zone the actor disabled, that zone's part of the run is `overridden`.
  - On a mismatch it updates its records: the action run's outcome becomes `overridden` with the message "owner turned unit on".
  - The run no longer counts as holding the unit off, so there is no restore pending, no watchdog email and no entry on the failures tile.
  - At the window end it does not run the restore, because the snapshot would overwrite the owner's settings. The restore row is recorded as `skipped: owner override`. *(The restore skip is my call.)*
- **Watchdog** *(owner's decision)*: a Core API job runs every minute. For each unfinished action run past its restore time, plus the give-up allowance, where the unit has not been restored or the actor's `last_seen_at` is more than 5 minutes old, it sets `watchdog_notified_at` and sends one email to the site owner, if the schedule has email on. The Dashboard tile shows the case whether or not email is on. The 5 minute and give-up values are my call.
- **Restart while the unit is off:** the run's `snapshot` is in Core API, so a restarted actor finds the unfinished action and carries on to the restore at the window end, or at once if the window has passed.
- **Disabling while off:** disabling a schedule, an instance or a site while it holds the unit off triggers the restore first. Discovery keeps the instance in the list as `restore_only` until that restore finishes. Deleting an instance or site is refused while a run holds the unit off. *(My call.)*
- **Overlapping schedules** *(my call)*: schedules on one instance are not checked for overlap when saved. While an action run is unfinished, another schedule's action on that instance is skipped with the message "another schedule is active". Snapshots therefore never record a state the actor itself changed.
- **Missing or stale readings** *(my call)*: the actor evaluates conditions. A reading older than 5 minutes, or absent, makes an "only if" condition fail, and the run is skipped with the message "reading unavailable". For restore conditions the actor restores anyway, so a missing reading never keeps the unit off.
- **Sign-in lost:** if the refresh token is rejected while the unit is off, the restore fails, the instance shows "needs sign-in", and the email goes out if the schedule has it on. *(My call.)*
- **Email recipient:** the site owner's account address. *(My call.)*
- **Provider scoping:** every actor endpoint checks that the instance belongs to the declared provider, as `get_source_by_collector_key` does for collectors. *(My call.)*
- **Device or API errors:** one failing instance never stops the others. The Actron API has no documented rate limit, so the polling intervals above are the only reads outside a run. A dropped push connection reconnects with a growing delay, up to 5 minutes.

## Left to the builder

- Exact JSON shape of the capabilities declaration and the trigger and condition types, within the rules above.
- Alembic migration, schemas, and routers following the existing patterns, plus tests.
- Compose service, Dockerfile, `.env.example` with a placeholder `ACTOR_KEY`, and the Core API setting for it.
- Docs: a new `docs/actors.md`, plus updates to architecture, operations and the data model.
- Tick length (30 seconds is a reasonable start) and the snapshot and polling details.
- How the web app renders each declared param type.
