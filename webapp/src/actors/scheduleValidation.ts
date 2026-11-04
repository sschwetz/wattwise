// Checks on the schedule editor's form before it is sent, in plain words.
import type { Capabilities, ParamDecl } from "./types";
import {
  conditionReadings,
  findAction,
  HHMM,
  isWindowTrigger,
  parseNumber,
  parseWhole,
  type ConditionRow,
  type FormParam,
  type FormParamValue,
  type ReadingChoice,
  type RestoreForm,
  type RetryForm,
  type ScheduleFormState,
  type TriggerForm,
} from "./scheduleForm";

/** Field key -> message in plain words. Empty when the form can be sent. */
export type FormErrors = Record<string, string>;

function triggerErrors(t: TriggerForm): FormErrors {
  if (isWindowTrigger(t.type)) return t.windowId === "" ? { "trigger.windowId": "Pick a demand window." } : {};
  if (t.type === "price_above") return parseNumber(t.price) === null ? { "trigger.price": "Enter a price in c/kWh." } : {};
  const errors: FormErrors = {};
  if (!HHMM.test(t.time)) errors["trigger.time"] = "Enter the time as HH:MM, for example 16:00.";
  if (t.days.length === 0) errors["trigger.days"] = "Tick at least one day.";
  return errors;
}

function numberParamError(decl: ParamDecl, text: string): string | null {
  const n = parseNumber(text);
  if (n === null) return `Enter a number for ${decl.label}.`;
  const { min, max } = decl;
  if ((min !== null && n < min) || (max !== null && n > max)) {
    if (min !== null && max !== null) return `${decl.label} must be between ${min} and ${max}.`;
    return min !== null ? `${decl.label} must be at least ${min}.` : `${decl.label} must be at most ${max}.`;
  }
  return null;
}

function choiceError(decl: ParamDecl, value: FormParamValue, allowed: Set<string>): string | null {
  if (typeof value !== "string" || value === "") return `Pick an option for ${decl.label}.`;
  return allowed.has(value) ? null : `${decl.label} "${value}" is no longer offered by the device. Pick another option.`;
}

function paramError(decl: ParamDecl, param: FormParam | undefined): string | null {
  if (param === undefined) return `Set a value for ${decl.label}.`;
  if (decl.type === "number") return param.kind === "number" ? numberParamError(decl, param.text) : `Enter a number for ${decl.label}.`;
  if (param.kind !== "value") return `Set a value for ${decl.label}.`;
  const allowed = new Set(decl.options.map((o) => o.value));
  if (decl.type === "choice") return choiceError(decl, param.value, allowed);
  if (decl.type === "bool") return typeof param.value === "boolean" ? null : `Set a value for ${decl.label}.`;
  const picked = Array.isArray(param.value) ? param.value : [];
  return picked.length > 0 && picked.every((v) => allowed.has(v)) ? null : `Tick at least one option for ${decl.label}.`;
}

function actionErrors(form: ScheduleFormState, capabilities: Capabilities): FormErrors {
  const action = findAction(capabilities, form.actionId);
  if (!action) return { action: "Pick an action this device supports." };
  const errors: FormErrors = {};
  for (const decl of action.params) {
    const message = paramError(decl, form.params[decl.name]);
    if (message) errors[`param.${decl.name}`] = message;
  }
  return errors;
}

/** True when the row's target is one the reading still offers, or the row has no target. */
export function targetOffered(row: ConditionRow, reading: ReadingChoice | undefined): boolean {
  return !row.target || (reading?.targets ?? []).some((t) => t.value === row.target);
}

function rowError(row: ConditionRow, readings: ReadingChoice[]): string | null {
  const reading = readings.find((r) => r.id === row.reading);
  if (!reading) return "This reading is no longer available. Pick another.";
  if (!targetOffered(row, reading)) return `Target "${row.target}" is no longer offered for ${reading.label}. Pick another.`;
  return parseNumber(row.value) === null ? `Enter a number for ${reading.label}.` : null;
}

/** Only ticked rows are checked; an unticked row is saved as typed. */
function rowErrors(prefix: string, rows: ConditionRow[], readings: ReadingChoice[]): FormErrors {
  const errors: FormErrors = {};
  for (const row of rows.filter((r) => r.use)) {
    const message = rowError(row, readings);
    if (message) errors[`${prefix}.${row.key}`] = message;
  }
  return errors;
}

function restoreErrors(r: RestoreForm, readings: ReadingChoice[]): FormErrors {
  if (!r.enabled) return {};
  const errors: FormErrors = { ...rowErrors("restore.onlyIf", r.onlyIf, readings), ...rowErrors("restore.earlyIf", r.earlyIf, readings) };
  const minutes = parseWhole(r.minutes);
  if (r.when === "after_minutes" && (minutes === null || minutes < 1)) errors["restore.minutes"] = "Enter a whole number of minutes, at least 1.";
  if (r.when === "at_time" && !HHMM.test(r.time)) errors["restore.time"] = "Enter the restore time as HH:MM, for example 21:00.";
  if (r.when === "conditions_only" && !r.earlyIf.some((c) => c.use)) {
    errors["restore.earlyIf"] = "Add a condition under Restore early when, or pick another time to restore.";
  }
  return errors;
}

function atLeast(text: string, floor: number): boolean {
  const n = parseWhole(text);
  return n !== null && n >= floor;
}

function retryErrors(r: RetryForm): FormErrors {
  const errors: FormErrors = {};
  if (!atLeast(r.maxAttempts, 1)) errors["retry.maxAttempts"] = "Attempts must be a whole number of at least 1.";
  if (!atLeast(r.intervalSeconds, 5)) errors["retry.intervalSeconds"] = "Seconds between tries must be a whole number of at least 5.";
  if (!atLeast(r.giveUpMinutes, 1)) errors["retry.giveUpMinutes"] = "Give up after must be a whole number of minutes, at least 1.";
  return errors;
}

export function validateForm(form: ScheduleFormState, capabilities: Capabilities): FormErrors {
  const readings = conditionReadings(capabilities);
  const name = form.name.trim();
  const nameError = name === "" ? "Enter a name for this schedule." : name.length > 200 ? "Keep the name to 200 characters or fewer." : null;
  return {
    ...(nameError ? { name: nameError } : {}),
    ...triggerErrors(form.trigger),
    ...rowErrors("conditions", form.conditions, readings),
    ...actionErrors(form, capabilities),
    ...restoreErrors(form.restore, readings),
    ...retryErrors(form.retry),
  };
}
