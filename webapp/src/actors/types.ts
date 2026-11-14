// Mirrors core-api/core_api/actor_schemas.py and actor_failures.py.
// Datetimes arrive as ISO-8601 strings. No field is serialised as a decimal string.

export interface Option {
  value: string;
  label: string;
}

export interface ParamDecl {
  name: string;
  label: string;
  type: "number" | "choice" | "bool" | "multi_choice";
  min: number | null;
  max: number | null;
  unit: string | null;
  options: Option[];
}

export interface ActionDecl {
  id: string;
  label: string;
  restorable: boolean;
  params: ParamDecl[];
}

export interface ReadingDecl {
  id: string;
  label: string;
  unit: string | null;
  type: string;
  target_options: Option[] | null;
}

export interface Capabilities {
  version: 1;
  actions: ActionDecl[];
  readings: ReadingDecl[];
}

/** A clock time in site time, or sunrise or sunset at the site moved by up to three hours either way. */
export type TimeRefSpec =
  | { kind: "clock"; time: string }
  | { kind: "sun"; event: "sunrise" | "sunset"; offset_minutes: number };

export type TriggerSpec =
  | { type: "window_start"; window_id: number }
  | { type: "window_end"; window_id: number }
  | { type: "time"; time: string; days_of_week: number[] }
  | { type: "sun"; event: "sunrise" | "sunset"; offset_minutes: number; days_of_week: number[] }
  /** `rearm`: after firing, the trigger waits until the price is at or below it. Null waits until it is no longer above the level. */
  | { type: "price_above"; c_per_kwh: number; rearm?: number | null }
  /** `rearm`: after firing, the trigger waits until the reading is back at or past it (below for gt, above for lt). */
  | { type: "temperature"; reading: TemperatureReading; op: "gt" | "lt"; value: number; rearm?: number | null };

export type TemperatureReading = "indoor_temp_c" | "outdoor_temp_c";

/** A reading is above or below a value. Rows saved before the calendar rows existed have no `kind`. */
export interface ReadingConditionSpec {
  kind?: undefined;
  reading: string;
  op: "gt" | "lt";
  value: number;
  /** Left out or null when the reading has no target. */
  target?: string | null;
  /** False for a switched-off row. Rows saved before the flag existed leave it out and count as on. */
  enabled?: boolean;
}

/** The site's month (1-12) or day of the week (0 for Monday to 6) is or is not one of `values`. */
export interface MembershipConditionSpec {
  kind: "months" | "days";
  op: "in" | "not_in";
  values: number[];
  enabled?: boolean;
}

/** The week is odd or even, counted from the week holding 1970-01-01 (even). */
export interface WeekConditionSpec {
  kind: "week";
  parity: "odd" | "even";
  enabled?: boolean;
}

/** A demand window, or the one named by `window_id`, is or is not running now. */
export interface DemandWindowConditionSpec {
  kind: "demand_window";
  op: "active" | "not_active";
  window_id: number | null;
  enabled?: boolean;
}

/** The time of day is from `start` up to `end`; an `end` not later than `start` runs through midnight. */
export interface TimeOfDayConditionSpec {
  kind: "time_of_day";
  start: TimeRefSpec;
  end: TimeRefSpec;
  enabled?: boolean;
}

export type ConditionSpec =
  | ReadingConditionSpec
  | MembershipConditionSpec
  | WeekConditionSpec
  | DemandWindowConditionSpec
  | TimeOfDayConditionSpec;

export type ActionParamValue = number | string | boolean | string[];

export interface ActionSpec {
  id: string;
  params: Record<string, ActionParamValue>;
}

export interface RestoreSpec {
  enabled: boolean;
  when: "window_end" | "after_minutes" | "at_time" | "conditions_only";
  minutes: number | null;
  time: string | null;
  only_if: ConditionSpec[];
  early_if: ConditionSpec[];
}

export interface RetrySpec {
  max_attempts: number;
  interval_seconds: number;
  give_up_minutes: number;
}

/** How often a schedule may fire. `min_gap_minutes` runs from its last restore (or last run); 0 is off.
 * `run_again_after` holds the trigger until the next such time after its last run. */
export interface LimitsSpec {
  min_gap_minutes: number;
  run_again_after: TimeRefSpec | null;
}

/** What the actor reports about the unit (actron-neo-actor DeviceState.to_payload). */
export interface DeviceStatePayload {
  is_on: boolean;
  mode: string;
  fan_mode: string;
  cool_setpoint_c: number;
  heat_setpoint_c: number;
  enabled_zones: Record<string, boolean>;
  indoor_temp_c: number | null;
  outdoor_temp_c: number | null;
  humidity_pct: number | null;
  zone_temps_c: Record<string, number>;
  /** Per-zone setpoint and the range the zone allows, keyed by zone name. Absent from older actors. */
  zone_setpoints_c?: Record<string, number>;
  zone_ranges_c?: Record<string, [number, number]>;
  updated_at: string;
  setpoint_min_c: number;
  setpoint_max_c: number;
}

/** The sign-in code the actor posts, or the flag it posts when the stored sign-in is lost.
 * Only the site owner gets the code and link; anyone else gets `signing_in` in their place. */
export type AuthState =
  | { user_code: string; verification_uri: string; expires_at: string }
  | { signing_in: true }
  | { needs_sign_in: true };

/** A sign-in in progress: the code and link for the owner, or the flag a viewer gets. */
export type SigningInState = Exclude<AuthState, { needs_sign_in: true }>;

export function isSigningIn(auth: AuthState | null): auth is SigningInState {
  return auth !== null && ("user_code" in auth || "signing_in" in auth);
}

export interface ActorInstance {
  id: number;
  site_id: number;
  provider: string;
  name: string | null;
  enabled: boolean;
  // Open dict on the server; null until the actor declares it.
  capabilities: Capabilities | null;
  capabilities_updated_at: string | null;
  // The actor's reading payload is stored as is (not nested under "state").
  latest_readings: DeviceStatePayload | null;
  last_seen_at: string | null;
  auth_state: AuthState | null;
}

export interface ActorSchedule {
  id: number;
  instance_id: number;
  name: string;
  enabled: boolean;
  trigger: TriggerSpec;
  conditions: ConditionSpec[];
  /** One to eight actions, run in order. */
  actions: ActionSpec[];
  restore: RestoreSpec;
  retry: RetrySpec;
  limits: LimitsSpec;
  notify_email: boolean;
}

/** Create payload. Any field except instance_id, name, trigger and actions may be left out. */
export interface ScheduleBody {
  instance_id: number;
  name: string;
  enabled?: boolean;
  trigger: TriggerSpec;
  conditions?: ConditionSpec[];
  actions: ActionSpec[];
  restore?: Partial<RestoreSpec>;
  retry?: Partial<RetrySpec>;
  limits?: Partial<LimitsSpec>;
  notify_email?: boolean;
}

/** Patch payload: fields left out keep their stored value. */
export type SchedulePatchBody = Partial<Omit<ScheduleBody, "instance_id">>;

export type RunKind = "action" | "restore" | "test";
export type RunOutcome = "pending" | "running" | "ok" | "skipped" | "failed" | "overridden" | "partial";

/** One action of a run: whether it worked, the params it ran with and how many tries it took. */
export interface RunStep {
  index: number;
  action_id: string;
  params: Record<string, ActionParamValue>;
  outcome: "ok" | "failed";
  message: string | null;
  attempts: number;
}

export interface ActorRun {
  id: number;
  schedule_id: number;
  kind: RunKind;
  trigger_at: string;
  started_at: string | null;
  finished_at: string | null;
  outcome: RunOutcome;
  message: string | null;
  attempts: number;
  parent_run_id: number | null;
  snapshot: Record<string, unknown> | null;
  steps: RunStep[] | null;
}

export interface ActorRunListItem extends ActorRun {
  schedule_name: string;
}

export type ActorFailureKind = "run_failed" | "run_ok" | "still_off" | "actor_silent" | "needs_sign_in" | "alert";

export type AlertSeverity = "failure" | "partial" | "notice";

export interface ActorFailure {
  kind: ActorFailureKind;
  /** null for alert, which is about a source, an actor or the site rather than a schedule. */
  instance_id: number | null;
  schedule_id: number | null;
  schedule_name: string | null;
  message: string;
  since: string;
  /** What an alert is about: a source or actor name, or "Site". null for the other kinds. */
  subject: string | null;
  severity: AlertSeverity;
  /** Set for alert rows; null for the computed kinds. */
  alert_id: number | null;
  /** When the alert cleared, if it has and is still shown. */
  cleared_at: string | null;
  /** True when the alert stays on the tile until dismissed. */
  sticky: boolean;
}

export interface InstanceCreateBody {
  provider: string;
  name: string;
}

export interface InstancePatchBody {
  name?: string;
  enabled?: boolean;
}
