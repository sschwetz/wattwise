// Test-only copy of the Core API's schedule rules: field constraints from
// core-api/core_api/actor_schemas.py (ScheduleCreate and its specs) and the
// capability checks from actor_capabilities.py. Returns every problem found.
import type { SchedulePayload } from "./scheduleForm";
import type { ActionParamValue, ActionSpec, Capabilities, ConditionSpec, MembershipConditionSpec, ParamDecl } from "./types";

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
const SITE_READING_IDS = new Set(["outdoor_temp_c", "battery_soc_pct", "price_c_per_kwh"]);
const isFiniteNumber = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isWhole = (v: unknown, floor: number) => isFiniteNumber(v) && Number.isInteger(v) && v >= floor;

const READING_KEYS = new Set(["reading", "op", "value", "target", "enabled"]);
const isIntIn = (v: unknown, low: number, high: number) => typeof v === "number" && Number.isInteger(v) && v >= low && v <= high;

function extraKeys(c: ConditionSpec, allowed: string[], where: string): string[] {
  return Object.keys(c).filter((k) => !allowed.includes(k)).map((k) => `${where}: extra key ${k}`);
}

/** The month and day rows: `values` in range, and at least one when switched on. */
function membershipProblems(c: MembershipConditionSpec, where: string): string[] {
  const [low, high] = c.kind === "months" ? [1, 12] : [0, 6];
  const problems = extraKeys(c, ["kind", "op", "values", "enabled"], where);
  if (c.op !== "in" && c.op !== "not_in") problems.push(`${where}: op`);
  if (!Array.isArray(c.values) || !c.values.every((v) => isIntIn(v, low, high))) problems.push(`${where}: values`);
  else if (c.enabled !== false && c.values.length === 0) problems.push(`${where}: values need at least one`);
  return problems;
}

/** ConditionSpec field rules, with extra="forbid". */
function conditionShapeProblems(c: ConditionSpec, where: string): string[] {
  if (c.kind === "months" || c.kind === "days") return membershipProblems(c, where);
  if (c.kind === "week") return [...extraKeys(c, ["kind", "parity", "enabled"], where), ...(c.parity === "odd" || c.parity === "even" ? [] : [`${where}: parity`])];
  if (c.kind === "demand_window") {
    const problems = extraKeys(c, ["kind", "op", "window_id", "enabled"], where);
    if (c.op !== "active" && c.op !== "not_active") problems.push(`${where}: op`);
    if (c.window_id !== null && !isWhole(c.window_id, 0)) problems.push(`${where}: window_id`);
    return problems;
  }
  if (c.kind !== undefined) return [`${where}: unknown kind`];
  const problems = extraKeys(c, [...READING_KEYS], where);
  if (typeof c.reading !== "string") problems.push(`${where}: reading`);
  if (c.op !== "gt" && c.op !== "lt") problems.push(`${where}: op`);
  if (!isFiniteNumber(c.value)) problems.push(`${where}: value`);
  if (c.target !== undefined && c.target !== null && typeof c.target !== "string") problems.push(`${where}: target`);
  if (c.enabled !== undefined && typeof c.enabled !== "boolean") problems.push(`${where}: enabled`);
  return problems;
}

/** actor_capabilities._check_condition, which validate_conditions_against skips for a switched-off condition
 * and for the calendar and demand window rows. */
function capabilityProblems(caps: Capabilities, c: ConditionSpec, where: string): string[] {
  if (c.enabled === false || c.kind !== undefined) return [];
  const target = c.target ?? null;
  const device = caps.readings.find((r) => r.id === c.reading);
  if (!device) {
    if (!SITE_READING_IDS.has(c.reading)) return [`${where}: reading ${c.reading} not available`];
    return target !== null ? [`${where}: site reading takes no target`] : [];
  }
  if (target === null) return [];
  const options = device.target_options;
  if (options === null) return [`${where}: reading takes no target`];
  return options.some((o) => o.value === target) ? [] : [`${where}: target ${target} not declared`];
}

function conditionProblems(caps: Capabilities, c: ConditionSpec, where: string): string[] {
  return [...conditionShapeProblems(c, where), ...capabilityProblems(caps, c, where)];
}

function paramProblem(p: ParamDecl, v: ActionParamValue): string | null {
  const allowed = new Set(p.options.map((o) => o.value));
  if (p.type === "number") {
    if (!isFiniteNumber(v)) return `param ${p.name}: not a number`;
    if ((p.min !== null && v < p.min) || (p.max !== null && v > p.max)) return `param ${p.name}: out of range`;
    return null;
  }
  if (p.type === "choice") return typeof v === "string" && allowed.has(v) ? null : `param ${p.name}: not an option`;
  if (p.type === "multi_choice") return Array.isArray(v) && v.every((x) => allowed.has(x)) ? null : `param ${p.name}: not options`;
  return typeof v === "boolean" ? null : `param ${p.name}: not a bool`;
}

function entryProblems(caps: Capabilities, action: ActionSpec): string[] {
  const decl = caps.actions.find((a) => a.id === action.id);
  if (!decl) return [`action ${action.id} not supported`];
  const problems = Object.keys(action.params).filter((k) => !decl.params.some((p) => p.name === k)).map((k) => `action has no parameter ${k}`);
  for (const p of decl.params) {
    if (!(p.name in action.params)) problems.push(`action is missing parameter ${p.name}`);
    else {
      const problem = paramProblem(p, action.params[p.name]);
      if (problem) problems.push(problem);
    }
  }
  return problems;
}

/** 1 to 8 entries, each checked against the capabilities, errors prefixed `actions[i]`. */
function actionProblems(caps: Capabilities, body: SchedulePayload): string[] {
  const { actions } = body;
  if (actions.length < 1 || actions.length > 8) return [`actions: needs 1 to 8 entries, got ${actions.length}`];
  return actions.flatMap((a, i) => entryProblems(caps, a).map((m) => `actions[${i}]: ${m}`));
}

function restoreProblems(body: SchedulePayload): string[] {
  const r = body.restore;
  const problems: string[] = [];
  if (!["window_end", "after_minutes", "at_time", "conditions_only"].includes(r.when)) problems.push("restore.when");
  if (r.minutes !== null && !isWhole(r.minutes, 1)) problems.push("restore.minutes must be a whole number >= 1");
  if (r.time !== null && !HHMM.test(r.time)) problems.push("restore.time pattern");
  if (r.when === "after_minutes" && r.minutes === null) problems.push("restore.minutes required");
  if (r.when === "at_time" && r.time === null) problems.push("restore.time required");
  // actor_schedule_validation._check_window_end_restore
  if (r.enabled && r.when === "window_end" && body.trigger.type !== "window_start") problems.push("restore.when window_end needs a window_start trigger");
  return problems;
}

function triggerProblems(body: SchedulePayload): string[] {
  const t = body.trigger;
  if (t.type === "time") {
    const days = t.days_of_week.length > 0 && t.days_of_week.every((d) => isWhole(d, 0) && d <= 6);
    return HHMM.test(t.time) && days ? [] : ["trigger time"];
  }
  if (t.type === "price_above") return isFiniteNumber(t.c_per_kwh) ? [] : ["trigger price"];
  if (t.type === "temperature") return isFiniteNumber(t.value) ? [] : ["trigger temperature"];
  if (t.type === "sun") return [];
  return Number.isInteger(t.window_id) ? [] : ["trigger window_id"];
}

export function serverProblems(caps: Capabilities, body: SchedulePayload): string[] {
  const { retry } = body;
  const conditions = [
    ...body.conditions.map((c, i) => ({ c, where: `conditions.${i}` })),
    ...body.restore.only_if.map((c, i) => ({ c, where: `restore.only_if.${i}` })),
    ...body.restore.early_if.map((c, i) => ({ c, where: `restore.early_if.${i}` })),
  ];
  return [
    ...(body.name.length < 1 || body.name.length > 200 ? ["name length"] : []),
    ...triggerProblems(body),
    ...conditions.flatMap(({ c, where }) => conditionProblems(caps, c, where)),
    ...actionProblems(caps, body),
    ...restoreProblems(body),
    ...(isWhole(retry.max_attempts, 1) ? [] : ["retry.max_attempts"]),
    ...(isWhole(retry.interval_seconds, 5) ? [] : ["retry.interval_seconds"]),
    ...(isWhole(retry.give_up_minutes, 1) ? [] : ["retry.give_up_minutes"]),
  ];
}
