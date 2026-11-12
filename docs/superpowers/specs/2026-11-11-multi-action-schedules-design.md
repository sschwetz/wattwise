# Multi-action schedules design

Date: 2026-11-11. Status: draft for approval.

## Intent

A schedule runs one action today. The owner wants one schedule to do several things when it fires, for example switch the Kids and Study zones off, leave Family on, and set Family to 23°C. Today that takes one schedule per action, each with its own trigger, conditions and restore, and the copies drift apart.

This design lets a schedule hold an ordered list of actions, adds a per-zone temperature action so zones can differ, and records the result of each action in the run.

This reverses a non-goal of [the actors design](2026-11-04-actors-design.md): "Several actions in one schedule."

## Goals

- A schedule has 1 to 8 actions. The editor has an **Add action** button and a remove button on each action. Order is shown and can be changed.
- All actions fire at the same trigger, behind the same conditions, and run in list order with no wait between them.
- A new action, `set_zone_temperature`, sets one zone's setpoint (params: `zone`, `temperature_c`). One action per zone gives different temperatures per zone.
- Each action in a run gets its own result (`ok` or `failed`, the attempts, a message). When an action fails, the remaining actions still run. The run's log says what ran and what failed.
- One restore puts back everything the run changed.
- Existing schedules keep working with no owner action.

## Non-goals

- Per-action triggers, conditions, delays or restore rules. The schedule has one of each.
- Conditional flow between actions ("only if the previous one worked").
- Per-zone mode. The unit has one mode. Home Assistant's core Actron integration treats a zone's mode as on/off only, and the library has no zone mode setter.
- Per-zone fan speed in this version. See Open items.
- One schedule driving several devices.

## Anti-goals

- A run that changed the unit but owes no restore. This is the main risk of partial failure (see Run outcome).
- A zone temperature the unit silently clamps. The library clamps an out-of-range zone setpoint to its limit and reports success. The actor must fail the action instead.
- A failed action that hides the actions that worked.

## The zone temperature range

A zone's setpoint may only sit near the main unit setpoint. The actron-neo-api library (pinned at 0.5.16) exposes this as `zone.min_temp` and `zone.max_temp`: the unit's own limits for the current mode, narrowed to the main setpoint plus or minus `zone_temperature_setpoint_variance`, which the unit reports. A newer library version computes the same thing from `zone_setpoint_offsets`. The actor reads the range from the library and hard-codes no number.

`set_zone_temperature` rules:

- Before sending, the actor checks the requested value against `zone.min_temp` and `zone.max_temp` as they are at that moment. Outside the range, the action fails and sends nothing. Message example: `Study 28°C is outside 20.0–24.0°C (the unit is set to 22.0°C)`.
- The range depends on the main setpoint, so order matters: an earlier `set_temperature` in the same list moves the range for later zone actions. The editor says so next to the action.
- The unit in `FAN` or `OFF` mode refuses zone setpoints (the library raises `Cannot set temperature in … mode`). The action fails with that text. Put a `system_on` or `set_mode` action earlier in the list.
- The capabilities declaration gives the param the unit-wide limits as `min` and `max`, which Core API validates at save time. The tighter live range cannot be known at save time, so the editor shows it as a hint from the latest unit readings and does not block saving. *(My call.)*

## Contract and data changes

- `actor_schedules.action` (one `{id, params}`) becomes `actions` (a JSON list of `{id, params}`). A migration wraps each existing value in a list of one.
- `contracts/actor-api/schedules_list_entry.json` and the Core API schemas change to `actions`. Core API validates every entry against the instance's capabilities, as it does for the single action now. `PATCH` replaces the whole `actions` list when it is sent. While the schedule holds the unit off, a change to `actions` is refused with 409 `unit_held_off`, as for `action` today.
- During the rollout Core API also returns `action` (the first entry) and the actor reads `actions` first, then `action`. Deploying in either order is not safe. Deploy Core API first, then the actor, then the web app. The actor must not go first: an old Core API refuses the `steps` and `partial` the new actor sends with 422, so the run stays `running` and the unit stays held with no restore until Core API is upgraded. While Core API is new and the actor is still old, the old actor reads `action` (the first entry), so a multi-action schedule runs only its first action and reports `ok`, and its single-entry snapshot restores through the new code. The web app goes last because an old Core API rejects `actions` and the page reads `schedule.actions`. A later change removes `action`. *(My call.)*
- `actor_runs` gets a `steps` JSON column: a list of `{index, action_id, params, outcome, message, attempts}`. It is written when the run finishes; after a restart the actor re-derives progress from the unit's state. `outcome` of a step is `ok` or `failed`.
- `actor_runs.outcome` gets a new value, `partial`: at least one action worked and at least one failed. This needs a change to the `CHECK` constraint.
- The `snapshot` stays opaque JSON that the actor defines. It becomes a list of per-action entries (`{action, applied, saved}`, the current shape, once per action). Restore must still accept the old single-entry shape, because runs created before the upgrade can be owed a restore.

## Run behaviour

1. The trigger fires and conditions pass, as today. One `action` run is created. The unique key on (`schedule_id`, `kind`, `trigger_at`) is unchanged.
2. The actor builds the whole snapshot from the unit's state before any command is sent, and saves it with the claim (`pending` to `running`), so a crash cannot leave the unit changed with nothing saved.
3. It runs the actions in order. Each gets the schedule's retry rule (`max_attempts`, `interval_seconds`). `give_up_minutes` covers the whole run: an action not yet started when the time runs out is recorded as failed with `gave up`, and the next ones are tried the same way.
4. It records each action's result in `steps`, then sets the run outcome.

**Run outcome:**

| Actions | Outcome | Owes a restore |
|---|---|---|
| All `ok` | `ok` | Yes |
| Some `ok`, some `failed` | `partial` | Yes, for the actions that worked |
| All `failed` | `failed` | No |

`partial` counts as unfinished (it holds the unit) in every place `ok` does: the `unit_held_off` 409s, the `still_off` alert, the watchdog and sign-in reset. It counts as a failure for the Dashboard tile and the email. The log line for a partial run reads like `2 of 3 ran. Failed: Study 28°C, outside 20.0–24.0°C (the unit is set to 22.0°C)`.

**Restore:**

- One restore run per action run, as today. `parent_run_id` points at the action run.
- Only actions whose step is `ok` are restored.
- Each field goes back to its value from before the run. The owner-changed check compares the unit's current value with the value the run applied last. If two actions set the same field, the earlier saved value is the one restored.
- Main unit fields are restored before zone fields. A zone's old setpoint was valid for the old main setpoint, so it can be restored once the main setpoint is back. A zone restore that the unit would clamp is reported as a failed restore with the range in the message, not as success.
- The restore message lists what went back and what was left alone, as now.

**Test now** runs every action. The test hold and restore work as for a normal run.

## Web app

- The "Do this" step becomes a list of action cards. Each card has the current action picker and its params. Under the list: **Add action**. Each card has remove, move up and move down.
- `scheduleForm.ts` holds `actions: ActionForm[]` (each with a stable `key`) in place of `actionId` and `params`. Validation, dirty checking and payload building run per action, and an error shows on the card it belongs to.
- The schedule detail and the list show every action. The run history shows one line per step under each run, with failures first. The `partial` outcome gets its own chip.
- The approved mockup is [`2026-11-04-actors-schedule-mockup.html`](2026-11-04-actors-schedule-mockup.html). A new mockup of the list of actions is needed before the editor is built. *(To do in the plan.)*

## Docs and tests

- Update `docs/actors.md`, `docs/data-model.md`, the contract JSON and the actors design's non-goal list.
- Tests: Core API validation of a list (empty, more than 8, one invalid entry); migration of existing rows; actor runs with all `ok`, one failed in the middle, and all failed; `set_zone_temperature` inside, outside and at the edge of the range, and in `FAN`/`OFF` mode; restore of a partial run, of an old single-entry snapshot, and of two actions that set the same field; the `partial` outcome in the held-off and alert paths; editor add, remove, reorder and per-card errors.

## Left to the builder

The exact shape of the snapshot list and the `steps` entries, the migration mechanics, the reorder interaction in the editor, and the wording of messages, as long as they say what ran and what failed.

## Open items

- **Per-zone fan speed.** The owner reports the wall zone controllers have a fan setting and that Home Assistant shows it. The pinned library has no zone fan field, and Home Assistant's core Actron integration does not expose fan speed for zones. A read-only dump of the unit's raw state on 2026-11-11 showed no named fan field on any zone. The zone entries hold setpoints, live readings, `NV_` installer flags and the airflow fields `AirflowSetpoint`, `ZonePosition`, `ZoneMinPosition`, `ZoneMaxPosition` and `AirflowControlEnabled`, identical across all eight zones. Nothing is added unless a field turns up (for example after changing one zone's fan and dumping again). If found, it becomes `set_zone_fan_mode`, built like `set_zone_temperature`.
