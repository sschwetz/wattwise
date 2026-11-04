// Test-only copy of the Core API's schedule rules: field constraints from
// core-api/core_api/actor_schemas.py (ScheduleCreate and its specs) and the
// capability checks from actor_capabilities.py. Returns every problem found.
import type { SchedulePayload } from "./scheduleForm";
import type { ActionParamValue, Capabilities, ConditionSpec, ParamDecl } from "./types";

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
const SITE_READING_IDS = new Set(["outdoor_temp_c", "battery_soc_pct", "price_c_per_kwh"]);
const isFiniteNumber = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isWhole = (v: unknown, floor: number) => isFiniteNumber(v) && Number.isInteger(v) && v >= floor;

function conditionProblems(caps: Capabilities, c: ConditionSpec, where: string): string[] {
  const problems: string[] = [];
  if (c.op !== "gt" && c.op !== "lt") problems.push(`${where}: op`);
  if (!isFiniteNumber(c.value)) problems.push(`${where}: value`);
  const device = caps.readings.find((r) => r.id === c.reading);
  if (!device) {
    if (!SITE_READING_IDS.has(c.reading)) problems.push(`${where}: reading ${c.reading} not available`);
    else if (c.target !== null) problems.push(`${where}: site reading takes no target`);
  } else if (c.target !== null) {
    const options = device.target_options;
    if (options === null) problems.push(`${where}: reading takes no target`);
    else if (!options.some((o) => o.value === c.target)) problems.push(`${where}: target ${c.target} not declared`);
  }
  return problems;
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

function actionProblems(caps: Capabilities, body: SchedulePayload): string[] {
  const decl = caps.actions.find((a) => a.id === body.action.id);
  if (!decl) return [`action ${body.action.id} not supported`];
  const problems = Object.keys(body.action.params).filter((k) => !decl.params.some((p) => p.name === k)).map((k) => `action has no parameter ${k}`);
  for (const p of decl.params) {
    if (!(p.name in body.action.params)) problems.push(`action is missing parameter ${p.name}`);
    else {
      const problem = paramProblem(p, body.action.params[p.name]);
      if (problem) problems.push(problem);
    }
  }
  return problems;
}

function restoreProblems(body: SchedulePayload): string[] {
  const r = body.restore;
  const problems: string[] = [];
  if (!["window_end", "after_minutes", "at_time", "conditions_only"].includes(r.when)) problems.push("restore.when");
  if (r.minutes !== null && !isWhole(r.minutes, 1)) problems.push("restore.minutes must be a whole number >= 1");
  if (r.time !== null && !HHMM.test(r.time)) problems.push("restore.time pattern");
  if (r.when === "after_minutes" && r.minutes === null) problems.push("restore.minutes required");
  if (r.when === "at_time" && r.time === null) problems.push("restore.time required");
  return problems;
}

function triggerProblems(body: SchedulePayload): string[] {
  const t = body.trigger;
  if (t.type === "time") return HHMM.test(t.time) && t.days_of_week.every((d) => isWhole(d, 0) && d <= 6) ? [] : ["trigger time"];
  if (t.type === "price_above") return isFiniteNumber(t.c_per_kwh) ? [] : ["trigger price"];
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
