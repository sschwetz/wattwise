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

export type TriggerSpec =
  | { type: "window_start"; window_id: number }
  | { type: "window_end"; window_id: number }
  | { type: "time"; time: string; days_of_week: number[] }
  | { type: "price_above"; c_per_kwh: number };

export interface ConditionSpec {
  reading: string;
  op: "gt" | "lt";
  value: number;
  target: string | null;
}

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
  updated_at: string;
  setpoint_min_c: number;
  setpoint_max_c: number;
}

/** The sign-in code the actor posts, or the flag it posts when the stored sign-in is lost. */
export type AuthState =
  | { user_code: string; verification_uri: string; expires_at: string }
  | { needs_sign_in: true };

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
  action: ActionSpec;
  restore: RestoreSpec;
  retry: RetrySpec;
  notify_email: boolean;
}

/** Create payload. Any field except instance_id, name, trigger and action may be left out. */
export interface ScheduleBody {
  instance_id: number;
  name: string;
  enabled?: boolean;
  trigger: TriggerSpec;
  conditions?: ConditionSpec[];
  action: ActionSpec;
  restore?: Partial<RestoreSpec>;
  retry?: Partial<RetrySpec>;
  notify_email?: boolean;
}

/** Patch payload: fields left out keep their stored value. */
export type SchedulePatchBody = Partial<Omit<ScheduleBody, "instance_id">>;

export type RunKind = "action" | "restore" | "test";
export type RunOutcome = "pending" | "running" | "ok" | "skipped" | "failed" | "overridden";

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
}

export interface ActorRunListItem extends ActorRun {
  schedule_name: string;
}

export type ActorFailureKind = "run_failed" | "still_off" | "actor_silent" | "needs_sign_in";

export interface ActorFailure {
  kind: ActorFailureKind;
  instance_id: number;
  schedule_id: number | null;
  schedule_name: string | null;
  message: string;
  since: string;
}

export interface InstanceCreateBody {
  provider: string;
  name: string;
}

export interface InstancePatchBody {
  name?: string;
  enabled?: boolean;
}
