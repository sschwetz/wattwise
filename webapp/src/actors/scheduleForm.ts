// Pure form logic for the schedule editor. Numbers are held as the text the
// owner typed and parsed in toPayload; validateForm (scheduleValidation.ts)
// reports bad input first.
import type {
  ActionDecl,
  ActionParamValue,
  ActorSchedule,
  Capabilities,
  ConditionSpec,
  ParamDecl,
  RestoreSpec,
  TriggerSpec,
} from "./types";

export type TriggerType = TriggerSpec["type"];
export type RestoreWhen = RestoreSpec["when"];
/** A full schedule body as the editor sends it. */
export type SchedulePayload = Omit<ActorSchedule, "id">;

export type FormParamValue = string | boolean | string[];
export type FormParam = { kind: "number"; text: string } | { kind: "value"; value: FormParamValue };

export interface ConditionRow {
  key: number;
  /** The tick box. Unticked rows are saved with enabled false and ignored by the server and the actor. */
  use: boolean;
  reading: string;
  op: "gt" | "lt";
  value: string;
  target: string | null;
}

export interface TriggerForm {
  type: TriggerType;
  windowId: string;
  time: string;
  days: number[];
  price: string;
}

export interface RestoreForm {
  enabled: boolean;
  when: RestoreWhen;
  minutes: string;
  time: string;
  onlyIf: ConditionRow[];
  earlyIf: ConditionRow[];
}

export interface RetryForm {
  maxAttempts: string;
  intervalSeconds: string;
  giveUpMinutes: string;
}

export interface ScheduleFormState {
  instanceId: number;
  name: string;
  enabled: boolean;
  trigger: TriggerForm;
  conditions: ConditionRow[];
  actionId: string;
  params: Record<string, FormParam>;
  restore: RestoreForm;
  retry: RetryForm;
  notifyEmail: boolean;
}

export interface ReadingChoice {
  id: string;
  label: string;
  unit: string | null;
  targets: { value: string; label: string }[] | null;
}

const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];
const DEFAULT_RESTORE_MINUTES = "60";
export const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Readings the Core API supplies for every site (actor_capabilities.SITE_READING_IDS). */
export const SITE_READINGS: ReadingChoice[] = [
  { id: "outdoor_temp_c", label: "Outdoor temperature", unit: "C", targets: null },
  { id: "battery_soc_pct", label: "Battery charge", unit: "%", targets: null },
  { id: "price_c_per_kwh", label: "Current price", unit: "c/kWh", targets: null },
];

export function isWindowTrigger(type: TriggerType): boolean {
  return type === "window_start" || type === "window_end";
}

/** The device's readings first; a site reading with the same id is left out because the device one wins. */
export function conditionReadings(capabilities: Capabilities): ReadingChoice[] {
  const device = capabilities.readings.map((r) => ({ id: r.id, label: r.label, unit: r.unit, targets: r.target_options }));
  const ids = new Set(device.map((r) => r.id));
  return [...device, ...SITE_READINGS.filter((r) => !ids.has(r.id))];
}

/** Restore times that make sense for a trigger: only a window start has a window end still to come. */
export function restoreOptions(type: TriggerType): RestoreWhen[] {
  const always: RestoreWhen[] = ["after_minutes", "at_time", "conditions_only"];
  return type === "window_start" ? ["window_end", ...always] : always;
}

export function findAction(capabilities: Capabilities, id: string): ActionDecl | undefined {
  return capabilities.actions.find((a) => a.id === id);
}

function defaultNumber(param: ParamDecl): string {
  const { min, max } = param;
  if (min !== null && max !== null) return String(Math.round((min + max) / 2));
  return String(min ?? max ?? 0);
}

function defaultParam(param: ParamDecl): FormParam {
  if (param.type === "number") return { kind: "number", text: defaultNumber(param) };
  if (param.type === "choice") return { kind: "value", value: param.options[0]?.value ?? "" };
  if (param.type === "bool") return { kind: "value", value: false };
  return { kind: "value", value: [] };
}

function defaultParams(action: ActionDecl | undefined): Record<string, FormParam> {
  return Object.fromEntries((action?.params ?? []).map((p) => [p.name, defaultParam(p)]));
}

/** Switch to `action` with its default params. Restore stays as it was, or goes off if the action cannot be restored. */
export function resetActionParams(form: ScheduleFormState, action: ActionDecl): ScheduleFormState {
  return {
    ...form,
    actionId: action.id,
    params: defaultParams(action),
    restore: { ...form.restore, enabled: form.restore.enabled && action.restorable },
  };
}

/** A stored value kept only when it has the shape the declared type needs. Multi-choice
 * values the device no longer offers (a removed zone) are dropped. A choice value that is no
 * longer offered is kept so the editor can show it and validateForm can flag it. */
function alignParam(decl: ParamDecl, stored: FormParam | undefined): FormParam {
  const fallback = defaultParam(decl);
  if (stored === undefined) return fallback;
  if (decl.type === "number") return stored.kind === "number" ? stored : fallback;
  if (stored.kind !== "value") return fallback;
  const { value } = stored;
  if (decl.type === "choice") return typeof value === "string" ? stored : fallback;
  if (decl.type === "bool") return typeof value === "boolean" ? stored : fallback;
  if (!Array.isArray(value)) return fallback;
  return { kind: "value", value: value.filter((v) => decl.options.some((o) => o.value === v)) };
}

/** Rebuild the params from the declaration: declared names only, stored values where they fit, defaults for the rest. */
export function alignParams(form: ScheduleFormState, capabilities: Capabilities): ScheduleFormState {
  const action = findAction(capabilities, form.actionId);
  if (!action) return form;
  return { ...form, params: Object.fromEntries(action.params.map((p) => [p.name, alignParam(p, form.params[p.name])])) };
}

/** Turn restore off when the chosen action declares it cannot be restored. */
export function applyRestorable(form: ScheduleFormState, capabilities: Capabilities): ScheduleFormState {
  const action = findAction(capabilities, form.actionId);
  if (!action || action.restorable || !form.restore.enabled) return form;
  return { ...form, restore: { ...form.restore, enabled: false } };
}

export function setTriggerType(form: ScheduleFormState, type: TriggerType): ScheduleFormState {
  const when = restoreOptions(type).includes(form.restore.when) ? form.restore.when : "after_minutes";
  return { ...form, trigger: { ...form.trigger, type }, restore: { ...form.restore, when } };
}

export function addCondition(rows: ConditionRow[], readings: ReadingChoice[]): ConditionRow[] {
  const first = readings[0];
  const key = rows.reduce((max, r) => Math.max(max, r.key + 1), 0);
  const target = first?.targets?.[0]?.value ?? null;
  return [...rows, { key, use: true, reading: first?.id ?? "", op: "gt", value: "", target }];
}

export function emptyForm(
  capabilities: Capabilities,
  context: { instanceId: number; firstWindowId: number | null },
): ScheduleFormState {
  const action = capabilities.actions[0];
  const windowed = context.firstWindowId !== null;
  return {
    instanceId: context.instanceId,
    name: "",
    enabled: true,
    trigger: { type: windowed ? "window_start" : "time", windowId: windowed ? String(context.firstWindowId) : "", time: "", days: [...ALL_DAYS], price: "" },
    conditions: [],
    actionId: action?.id ?? "",
    params: defaultParams(action),
    restore: {
      enabled: action?.restorable ?? false,
      when: windowed ? "window_end" : "after_minutes",
      minutes: DEFAULT_RESTORE_MINUTES,
      time: "",
      onlyIf: [],
      earlyIf: [],
    },
    retry: { maxAttempts: "3", intervalSeconds: "30", giveUpMinutes: "15" },
    notifyEmail: false,
  };
}

// fromSchedule

function triggerForm(trigger: TriggerSpec): TriggerForm {
  return {
    type: trigger.type,
    windowId: "window_id" in trigger ? String(trigger.window_id) : "",
    time: trigger.type === "time" ? trigger.time : "",
    days: trigger.type === "time" ? [...trigger.days_of_week] : [...ALL_DAYS],
    price: trigger.type === "price_above" ? String(trigger.c_per_kwh) : "",
  };
}

function conditionRows(conditions: ConditionSpec[]): ConditionRow[] {
  return conditions.map((c, key) => ({
    key, use: c.enabled !== false, reading: c.reading, op: c.op, value: String(c.value), target: c.target ?? null,
  }));
}

function formParam(value: ActionParamValue): FormParam {
  if (typeof value === "number") return { kind: "number", text: String(value) };
  return { kind: "value", value: Array.isArray(value) ? [...value] : value };
}

export function fromSchedule(schedule: SchedulePayload): ScheduleFormState {
  const { restore, retry } = schedule;
  return {
    instanceId: schedule.instance_id,
    name: schedule.name,
    enabled: schedule.enabled,
    trigger: triggerForm(schedule.trigger),
    conditions: conditionRows(schedule.conditions),
    actionId: schedule.action.id,
    params: Object.fromEntries(Object.entries(schedule.action.params).map(([k, v]) => [k, formParam(v)])),
    restore: {
      enabled: restore.enabled,
      when: restore.when,
      minutes: restore.minutes === null ? DEFAULT_RESTORE_MINUTES : String(restore.minutes),
      time: restore.time ?? "",
      onlyIf: conditionRows(restore.only_if),
      earlyIf: conditionRows(restore.early_if),
    },
    retry: { maxAttempts: String(retry.max_attempts), intervalSeconds: String(retry.interval_seconds), giveUpMinutes: String(retry.give_up_minutes) },
    notifyEmail: schedule.notify_email,
  };
}

// toPayload

/** The number in `text`, or null when it is blank or not a finite number. */
export function parseNumber(text: string): number | null {
  if (text.trim() === "") return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
}

export function parseWhole(text: string): number | null {
  const n = parseNumber(text);
  return n !== null && Number.isInteger(n) ? n : null;
}

function triggerPayload(t: TriggerForm): TriggerSpec {
  if (t.type === "time") return { type: "time", time: t.time, days_of_week: [...t.days].sort((a, b) => a - b) };
  if (t.type === "price_above") return { type: "price_above", c_per_kwh: Number(t.price) };
  return { type: t.type, window_id: Number(t.windowId) };
}

/** Every unticked row, sent as typed with 0 for a missing number (the server does not check
 * switched-off rows), and ticked rows with a number. A ticked row without one cannot be sent;
 * validateForm flags the visible ones. */
function conditionsPayload(rows: ConditionRow[]): ConditionSpec[] {
  return rows.flatMap((r) => {
    const value = parseNumber(r.value);
    if (r.use && value === null) return [];
    return [{ reading: r.reading, op: r.op, value: value ?? 0, ...(r.target ? { target: r.target } : {}), enabled: r.use }];
  });
}

function paramValue(param: FormParam): ActionParamValue {
  return param.kind === "number" ? Number(param.text) : param.value;
}

/** Would the server's capability check accept this condition? Mirrors actor_capabilities.validate_conditions_against,
 * which skips switched-off conditions. */
export function conditionAccepted(c: ConditionSpec, capabilities: Capabilities): boolean {
  if (c.enabled === false) return true;
  const target = c.target ?? null;
  const device = capabilities.readings.find((r) => r.id === c.reading);
  if (!device) return SITE_READINGS.some((r) => r.id === c.reading) && target === null;
  return target === null || (device.target_options ?? []).some((o) => o.value === target);
}

function restorePayload(r: RestoreForm, capabilities: Capabilities): RestoreSpec {
  const whole = parseWhole(r.minutes);
  const minutes = r.when === "after_minutes" && whole !== null && whole >= 1 ? whole : null;
  const time = r.when === "at_time" && HHMM.test(r.time) ? r.time : null;
  const onlyIf = conditionsPayload(r.onlyIf);
  const earlyIf = conditionsPayload(r.earlyIf);
  if (r.enabled) return { enabled: true, when: r.when, minutes, time, only_if: onlyIf, early_if: earlyIf };
  // Restore is off, so its fields are hidden and validateForm does not check
  // them. Send only what the server accepts: "conditions_only" when the
  // minutes or time is missing, and only conditions that pass its checks.
  const missing = (r.when === "after_minutes" && minutes === null) || (r.when === "at_time" && time === null);
  const accepted = (c: ConditionSpec) => conditionAccepted(c, capabilities);
  return {
    enabled: false,
    when: missing ? "conditions_only" : r.when,
    minutes,
    time,
    only_if: onlyIf.filter(accepted),
    early_if: earlyIf.filter(accepted),
  };
}

/** Only the params the action declares; an undeclared action keeps what it has (validateForm blocks it). */
function paramsPayload(form: ScheduleFormState, capabilities: Capabilities): Record<string, ActionParamValue> {
  const declared = findAction(capabilities, form.actionId)?.params.map((p) => p.name);
  const names = declared ?? Object.keys(form.params);
  return Object.fromEntries(names.flatMap((name) => (form.params[name] ? [[name, paramValue(form.params[name])]] : [])));
}

export function toPayload(form: ScheduleFormState, capabilities: Capabilities): SchedulePayload {
  const { retry } = form;
  return {
    instance_id: form.instanceId,
    name: form.name.trim(),
    enabled: form.enabled,
    trigger: triggerPayload(form.trigger),
    conditions: conditionsPayload(form.conditions),
    action: { id: form.actionId, params: paramsPayload(form, capabilities) },
    restore: restorePayload(form.restore, capabilities),
    retry: { max_attempts: Number(retry.maxAttempts), interval_seconds: Number(retry.intervalSeconds), give_up_minutes: Number(retry.giveUpMinutes) },
    notify_email: form.notifyEmail,
  };
}
