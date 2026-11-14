// Pure form logic for the schedule editor. Numbers are held as the text the
// owner typed and parsed in toPayload; validateForm (scheduleValidation.ts)
// reports bad input first.
import type {
  ActionDecl,
  ActionParamValue,
  ActionSpec,
  ActorSchedule,
  Capabilities,
  ConditionSpec,
  LimitsSpec,
  ParamDecl,
  RestoreSpec,
  TemperatureReading,
  TimeRefSpec,
  TriggerSpec,
} from "./types";

export type TriggerType = TriggerSpec["type"];
export type RestoreWhen = RestoreSpec["when"];
/** A full schedule body as the editor sends it. */
export type SchedulePayload = Omit<ActorSchedule, "id">;

export type FormParamValue = string | boolean | string[];
export type FormParam = { kind: "number"; text: string } | { kind: "value"; value: FormParamValue };

interface RowBase {
  key: number;
  /** The tick box. Unticked rows are saved with enabled false and ignored by the server and the actor. */
  use: boolean;
}

export interface ReadingRow extends RowBase {
  kind: "reading";
  reading: string;
  op: "gt" | "lt";
  value: string;
  target: string | null;
}

/** Month or day of the week: Jan is 1, Mon is 0. */
export interface MembershipRow extends RowBase {
  kind: "months" | "days";
  op: "in" | "not_in";
  values: number[];
}

export interface WeekRow extends RowBase {
  kind: "week";
  parity: "odd" | "even";
}

export interface WindowRow extends RowBase {
  kind: "demand_window";
  op: "active" | "not_active";
  /** A window id as text; empty means any demand window. */
  windowId: string;
}

/** A clock time as typed (HH:MM), or sunrise or sunset with the offset in minutes as typed. */
export type TimeRefForm = { kind: "clock"; time: string } | { kind: "sun"; event: "sunrise" | "sunset"; offset: string };

export interface TimeOfDayRow extends RowBase {
  kind: "time_of_day";
  start: TimeRefForm;
  end: TimeRefForm;
}

export type ConditionRow = ReadingRow | MembershipRow | WeekRow | WindowRow | TimeOfDayRow;
export type ConditionKind = ConditionRow["kind"];

export interface TriggerForm {
  type: TriggerType;
  windowId: string;
  time: string;
  days: number[];
  price: string;
  /** Temperature trigger: which temperature, which way it crosses, and the level in C. */
  reading: TemperatureReading;
  op: "gt" | "lt";
  level: string;
  /** Sun trigger: sunrise or sunset, and the offset in minutes (negative is before). */
  event: "sunrise" | "sunset";
  offset: string;
  /** Price and temperature triggers: the value to get back to before it can fire again. Blank means as soon as the level is no longer passed. */
  rearm: string;
  /** True until the owner types a re-arm value, so a temperature level change carries it along. */
  rearmAuto: boolean;
}

/** Limits on how often the schedule fires. `againAfter` is null when "run again after" is off. */
export interface LimitsForm {
  minGap: string;
  againAfter: TimeRefForm | null;
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

/** One action card. `key` tells React the cards apart and does not go to the server. */
export interface ActionForm {
  key: number;
  actionId: string;
  params: Record<string, FormParam>;
}

export const MAX_ACTIONS = 8;

export interface ScheduleFormState {
  instanceId: number;
  name: string;
  enabled: boolean;
  trigger: TriggerForm;
  conditions: ConditionRow[];
  actions: ActionForm[];
  restore: RestoreForm;
  retry: RetryForm;
  limits: LimitsForm;
  notifyEmail: boolean;
}

export interface ReadingChoice {
  id: string;
  label: string;
  unit: string | null;
  /** "bool" for an on/off reading such as the unit being on; anything else is a number. */
  type: string;
  targets: { value: string; label: string }[] | null;
}

const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];
const DEFAULT_RESTORE_MINUTES = "60";
/** The minimum gap a temperature trigger starts with, and how far past its level its re-arm value starts. */
const DEFAULT_TEMPERATURE_GAP = "30";
const DEFAULT_REARM_MARGIN_C = 2;
export const MAX_OFFSET_MINUTES = 180;
export const MAX_GAP_MINUTES = 1440;
export const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Readings the Core API supplies for every site (actor_capabilities.SITE_READING_IDS). */
export const SITE_READINGS: ReadingChoice[] = [
  { id: "outdoor_temp_c", label: "Outdoor temperature", unit: "C", type: "number", targets: null },
  { id: "battery_soc_pct", label: "Battery charge", unit: "%", type: "number", targets: null },
  { id: "price_c_per_kwh", label: "Current price", unit: "c/kWh", type: "number", targets: null },
];

function emptyLevel(): Pick<TriggerForm, "reading" | "op" | "level" | "event" | "offset" | "rearm" | "rearmAuto"> {
  return { reading: "outdoor_temp_c", op: "gt", level: "", event: "sunset", offset: "0", rearm: "", rearmAuto: true };
}

/** The re-arm value a temperature level suggests: two degrees back inside it, or blank while the level is not a number. */
export function suggestRearm(level: string, op: "gt" | "lt"): string {
  const n = parseNumber(level);
  if (n === null) return "";
  return String(Math.round((op === "gt" ? n - DEFAULT_REARM_MARGIN_C : n + DEFAULT_REARM_MARGIN_C) * 100) / 100);
}

export function defaultClockRef(time = ""): TimeRefForm {
  return { kind: "clock", time };
}

export function sunRef(event: "sunrise" | "sunset"): TimeRefForm {
  return { kind: "sun", event, offset: "0" };
}

export function isWindowTrigger(type: TriggerType): boolean {
  return type === "window_start" || type === "window_end";
}

/** The device's readings first; a site reading with the same id is left out because the device one wins. */
export function conditionReadings(capabilities: Capabilities): ReadingChoice[] {
  const device = capabilities.readings.map((r) => ({ id: r.id, label: r.label, unit: r.unit, type: r.type, targets: r.target_options }));
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

function newCard(key: number, action: ActionDecl | undefined): ActionForm {
  return { key, actionId: action?.id ?? "", params: defaultParams(action) };
}

function nextKey(cards: ActionForm[]): number {
  return cards.reduce((max, c) => Math.max(max, c.key + 1), 0);
}

function mapCard(form: ScheduleFormState, key: number, change: (card: ActionForm) => ActionForm): ScheduleFormState {
  return { ...form, actions: form.actions.map((c) => (c.key === key ? change(c) : c)) };
}

/** Turn restore off unless every card can be restored. Returns the same form when nothing changes. */
function syncRestore(form: ScheduleFormState, capabilities: Capabilities): ScheduleFormState {
  if (!form.restore.enabled || allRestorable(form, capabilities)) return form;
  return { ...form, restore: { ...form.restore, enabled: false } };
}

/** Switch card `key` to `action` with its default params. Restore stays as it was, or goes off if any card cannot be restored.
 * An unknown key leaves the form as it was. */
export function resetActionParams(form: ScheduleFormState, key: number, action: ActionDecl, capabilities: Capabilities): ScheduleFormState {
  if (!form.actions.some((c) => c.key === key)) return form;
  const next = mapCard(form, key, (c) => ({ ...c, actionId: action.id, params: defaultParams(action) }));
  return syncRestore(next, capabilities);
}

/** Add a card with the first declared action, unless the schedule already has MAX_ACTIONS. */
export function addAction(form: ScheduleFormState, capabilities: Capabilities): ScheduleFormState {
  if (form.actions.length >= MAX_ACTIONS) return form;
  const card = newCard(nextKey(form.actions), capabilities.actions[0]);
  return syncRestore({ ...form, actions: [...form.actions, card] }, capabilities);
}

/** Drop card `key`. The last card stays, because a schedule needs an action. */
export function removeAction(form: ScheduleFormState, key: number): ScheduleFormState {
  if (form.actions.length <= 1 || !form.actions.some((c) => c.key === key)) return form;
  return { ...form, actions: form.actions.filter((c) => c.key !== key) };
}

/** Swap card `key` with the one before (-1) or after (1) it. Does nothing at either end. */
export function moveAction(form: ScheduleFormState, key: number, by: -1 | 1): ScheduleFormState {
  const from = form.actions.findIndex((c) => c.key === key);
  const to = from + by;
  if (from < 0 || to < 0 || to >= form.actions.length) return form;
  const cards = [...form.actions];
  [cards[from], cards[to]] = [cards[to], cards[from]];
  return { ...form, actions: cards };
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
  const align = (card: ActionForm): ActionForm => {
    const action = findAction(capabilities, card.actionId);
    if (!action) return card;
    return { ...card, params: Object.fromEntries(action.params.map((p) => [p.name, alignParam(p, card.params[p.name])])) };
  };
  return { ...form, actions: form.actions.map(align) };
}

/** True when every card names an action the device declares as restorable. */
export function allRestorable(form: ScheduleFormState, capabilities: Capabilities): boolean {
  return form.actions.every((c) => findAction(capabilities, c.actionId)?.restorable ?? false);
}

/** Turn restore off when a chosen action declares it cannot be restored. */
export function applyRestorable(form: ScheduleFormState, capabilities: Capabilities): ScheduleFormState {
  const blocked = form.actions.some((c) => findAction(capabilities, c.actionId)?.restorable === false);
  if (!blocked || !form.restore.enabled) return form;
  return { ...form, restore: { ...form.restore, enabled: false } };
}

/** Switch the trigger type. A temperature trigger also starts the minimum gap at 30 minutes when it is off. */
export function setTriggerType(form: ScheduleFormState, type: TriggerType): ScheduleFormState {
  const when = restoreOptions(type).includes(form.restore.when) ? form.restore.when : "after_minutes";
  const gap = type === "temperature" && form.limits.minGap.trim() === "0" ? DEFAULT_TEMPERATURE_GAP : form.limits.minGap;
  return { ...form, trigger: { ...form.trigger, type }, restore: { ...form.restore, when }, limits: { ...form.limits, minGap: gap } };
}

/** An on/off reading is tested as "is on" (above 0) or "is off" (below 1), so the server and actor need no special case. */
export function setSwitch(row: ReadingRow, on: boolean): ReadingRow {
  return { ...row, op: on ? "gt" : "lt", value: on ? "0" : "1" };
}

export function isSwitchedOn(row: ReadingRow): boolean {
  return row.op !== "lt";
}

/** The row pointed at `reading`: its first target, and "is on" when the reading is an on/off one. */
export function pointAtReading(row: ReadingRow, reading: ReadingChoice | undefined): ReadingRow {
  const next = { ...row, reading: reading?.id ?? row.reading, target: reading?.targets?.[0]?.value ?? null };
  return reading?.type === "bool" ? setSwitch(next, true) : next;
}

export function addCondition(rows: ConditionRow[], readings: ReadingChoice[]): ConditionRow[] {
  const key = rows.reduce((max, r) => Math.max(max, r.key + 1), 0);
  return [...rows, pointAtReading({ key, use: true, kind: "reading", reading: "", op: "gt", value: "", target: null }, readings[0])];
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
    trigger: { type: windowed ? "window_start" : "time", windowId: windowed ? String(context.firstWindowId) : "", time: "", days: [...ALL_DAYS], price: "", ...emptyLevel() },
    conditions: [],
    actions: [newCard(0, action)],
    restore: {
      enabled: action?.restorable ?? false,
      when: windowed ? "window_end" : "after_minutes",
      minutes: DEFAULT_RESTORE_MINUTES,
      time: "",
      onlyIf: [],
      earlyIf: [],
    },
    retry: { maxAttempts: "3", intervalSeconds: "30", giveUpMinutes: "15" },
    limits: { minGap: "0", againAfter: null },
    notifyEmail: false,
  };
}

// fromSchedule

function refForm(ref: TimeRefSpec): TimeRefForm {
  return ref.kind === "clock" ? { kind: "clock", time: ref.time } : { kind: "sun", event: ref.event, offset: String(ref.offset_minutes) };
}

/** The rearm text of a saved trigger. A saved trigger is not given a suggested value, so `rearmAuto` is off. */
function rearmText(trigger: TriggerSpec): string {
  return (trigger.type === "price_above" || trigger.type === "temperature") && trigger.rearm != null ? String(trigger.rearm) : "";
}

function triggerForm(trigger: TriggerSpec): TriggerForm {
  const sun = trigger.type === "sun" ? { event: trigger.event, offset: String(trigger.offset_minutes) } : {};
  return {
    type: trigger.type,
    windowId: "window_id" in trigger ? String(trigger.window_id) : "",
    time: trigger.type === "time" ? trigger.time : "",
    days: trigger.type === "time" || trigger.type === "sun" ? [...trigger.days_of_week] : [...ALL_DAYS],
    price: trigger.type === "price_above" ? String(trigger.c_per_kwh) : "",
    ...emptyLevel(),
    ...(trigger.type === "temperature" ? { reading: trigger.reading, op: trigger.op, level: String(trigger.value) } : {}),
    ...sun,
    rearm: rearmText(trigger),
    rearmAuto: false,
  };
}

function conditionRow(c: ConditionSpec, key: number): ConditionRow {
  const use = c.enabled !== false;
  switch (c.kind) {
    case "months":
    case "days":
      return { key, use, kind: c.kind, op: c.op, values: [...c.values] };
    case "week":
      return { key, use, kind: "week", parity: c.parity };
    case "demand_window":
      return { key, use, kind: "demand_window", op: c.op, windowId: c.window_id === null ? "" : String(c.window_id) };
    case "time_of_day":
      return { key, use, kind: "time_of_day", start: refForm(c.start), end: refForm(c.end) };
    default:
      return { key, use, kind: "reading", reading: c.reading, op: c.op, value: String(c.value), target: c.target ?? null };
  }
}

function conditionRows(conditions: ConditionSpec[]): ConditionRow[] {
  return conditions.map(conditionRow);
}

function formParam(value: ActionParamValue): FormParam {
  if (typeof value === "number") return { kind: "number", text: String(value) };
  return { kind: "value", value: Array.isArray(value) ? [...value] : value };
}

function actionCard(spec: ActionSpec, key: number): ActionForm {
  return { key, actionId: spec.id, params: Object.fromEntries(Object.entries(spec.params).map(([k, v]) => [k, formParam(v)])) };
}

export function fromSchedule(schedule: SchedulePayload): ScheduleFormState {
  const { restore, retry } = schedule;
  return {
    instanceId: schedule.instance_id,
    name: schedule.name,
    enabled: schedule.enabled,
    trigger: triggerForm(schedule.trigger),
    conditions: conditionRows(schedule.conditions),
    actions: schedule.actions.map(actionCard),
    restore: {
      enabled: restore.enabled,
      when: restore.when,
      minutes: restore.minutes === null ? DEFAULT_RESTORE_MINUTES : String(restore.minutes),
      time: restore.time ?? "",
      onlyIf: conditionRows(restore.only_if),
      earlyIf: conditionRows(restore.early_if),
    },
    retry: { maxAttempts: String(retry.max_attempts), intervalSeconds: String(retry.interval_seconds), giveUpMinutes: String(retry.give_up_minutes) },
    limits: { minGap: String(schedule.limits.min_gap_minutes), againAfter: schedule.limits.run_again_after === null ? null : refForm(schedule.limits.run_again_after) },
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

/** A time reference as the server takes it, or null when its text is not a time or a whole offset. */
export function refPayload(ref: TimeRefForm): TimeRefSpec | null {
  if (ref.kind === "clock") return HHMM.test(ref.time) ? { kind: "clock", time: ref.time } : null;
  const offset = parseWhole(ref.offset);
  return offset !== null && Math.abs(offset) <= MAX_OFFSET_MINUTES ? { kind: "sun", event: ref.event, offset_minutes: offset } : null;
}

function triggerPayload(t: TriggerForm): TriggerSpec {
  const days = [...t.days].sort((a, b) => a - b);
  const rearm = parseNumber(t.rearm);
  const withRearm = rearm === null ? {} : { rearm };
  if (t.type === "time") return { type: "time", time: t.time, days_of_week: days };
  if (t.type === "sun") return { type: "sun", event: t.event, offset_minutes: Number(t.offset), days_of_week: days };
  if (t.type === "price_above") return { type: "price_above", c_per_kwh: Number(t.price), ...withRearm };
  if (t.type === "temperature") return { type: "temperature", reading: t.reading, op: t.op, value: Number(t.level), ...withRearm };
  return { type: t.type, window_id: Number(t.windowId) };
}

/** A time-of-day row. A row whose times are not valid cannot be sent, ticked or not; validateForm flags the ticked ones. */
function timeOfDayPayload(r: TimeOfDayRow): ConditionSpec[] {
  const start = refPayload(r.start);
  const end = refPayload(r.end);
  return start && end ? [{ kind: "time_of_day", start, end, enabled: r.use }] : [];
}

/** A reading row: unticked, it is sent as typed with 0 for a missing number (the server does not check
 * switched-off rows). A ticked row without a number cannot be sent; validateForm flags the visible ones. */
function readingPayload(r: ReadingRow): ConditionSpec[] {
  const value = parseNumber(r.value);
  if (r.use && value === null) return [];
  return [{ reading: r.reading, op: r.op, value: value ?? 0, ...(r.target ? { target: r.target } : {}), enabled: r.use }];
}

function rowPayload(r: ConditionRow): ConditionSpec[] {
  switch (r.kind) {
    case "reading":
      return readingPayload(r);
    case "months":
    case "days":
      if (r.use && r.values.length === 0) return [];
      return [{ kind: r.kind, op: r.op, values: [...r.values].sort((a, b) => a - b), enabled: r.use }];
    case "week":
      return [{ kind: "week", parity: r.parity, enabled: r.use }];
    case "demand_window":
      return [{ kind: "demand_window", op: r.op, window_id: r.windowId === "" ? null : Number(r.windowId), enabled: r.use }];
    case "time_of_day":
      return timeOfDayPayload(r);
  }
}

function conditionsPayload(rows: ConditionRow[]): ConditionSpec[] {
  return rows.flatMap(rowPayload);
}

function paramValue(param: FormParam): ActionParamValue {
  return param.kind === "number" ? Number(param.text) : param.value;
}

/** Would the server's capability check accept this condition? Mirrors actor_capabilities.validate_conditions_against,
 * which skips switched-off conditions. */
export function conditionAccepted(c: ConditionSpec, capabilities: Capabilities): boolean {
  if (c.enabled === false || c.kind !== undefined) return true;
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
function actionPayload(card: ActionForm, capabilities: Capabilities): ActionSpec {
  const declared = findAction(capabilities, card.actionId)?.params.map((p) => p.name);
  const names = declared ?? Object.keys(card.params);
  const params = Object.fromEntries(names.flatMap((name) => (card.params[name] ? [[name, paramValue(card.params[name])]] : [])));
  return { id: card.actionId, params };
}

function limitsPayload(l: LimitsForm): LimitsSpec {
  return { min_gap_minutes: parseWhole(l.minGap) ?? 0, run_again_after: l.againAfter === null ? null : refPayload(l.againAfter) };
}

export function toPayload(form: ScheduleFormState, capabilities: Capabilities): SchedulePayload {
  const { retry } = form;
  return {
    instance_id: form.instanceId,
    name: form.name.trim(),
    enabled: form.enabled,
    trigger: triggerPayload(form.trigger),
    conditions: conditionsPayload(form.conditions),
    actions: form.actions.map((c) => actionPayload(c, capabilities)),
    restore: restorePayload(form.restore, capabilities),
    retry: { max_attempts: Number(retry.maxAttempts), interval_seconds: Number(retry.intervalSeconds), give_up_minutes: Number(retry.giveUpMinutes) },
    limits: limitsPayload(form.limits),
    notify_email: form.notifyEmail,
  };
}
