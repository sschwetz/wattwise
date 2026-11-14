// Checks on the schedule editor's form before it is sent, in plain words.
import type { DemandWindow } from "../billing/useBilling";
import type { Capabilities, ParamDecl } from "./types";
import {
  conditionReadings,
  MAX_ACTIONS,
  findAction,
  HHMM,
  isWindowTrigger,
  MAX_GAP_MINUTES,
  MAX_OFFSET_MINUTES,
  parseNumber,
  parseWhole,
  refPayload,
  type ActionForm,
  type ConditionRow,
  type FormParam,
  type FormParamValue,
  type LimitsForm,
  type ReadingChoice,
  type ReadingRow,
  type RestoreForm,
  type RetryForm,
  type ScheduleFormState,
  type TimeOfDayRow,
  type TriggerForm,
} from "./scheduleForm";

/** Field key -> message in plain words. Empty when the form can be sent. */
export type FormErrors = Record<string, string>;

const OFFSET_MESSAGE = `Enter a whole number of minutes from -${MAX_OFFSET_MINUTES} to ${MAX_OFFSET_MINUTES}.`;

/** A blank re-arm value is fine. A typed one must be a number back inside the level: below it for a price or a
 * rising temperature, above it for a falling temperature. */
function rearmError(t: TriggerForm): string | null {
  if (t.rearm.trim() === "") return null;
  const rearm = parseNumber(t.rearm);
  if (rearm === null) return "Enter a number, or leave it blank.";
  if (t.type === "price_above") {
    const price = parseNumber(t.price);
    return price !== null && rearm >= price ? "Must be below the price level." : null;
  }
  const level = parseNumber(t.level);
  if (level === null) return null;
  if (t.op === "gt") return rearm >= level ? "Must be below the level." : null;
  return rearm <= level ? "Must be above the level." : null;
}

function levelErrors(t: TriggerForm, missing: { key: string; text: string }, value: string): FormErrors {
  const errors: FormErrors = parseNumber(value) === null ? { [missing.key]: missing.text } : {};
  const rearm = rearmError(t);
  return rearm ? { ...errors, "trigger.rearm": rearm } : errors;
}

function dayErrors(t: TriggerForm): FormErrors {
  return t.days.length === 0 ? { "trigger.days": "Tick at least one day." } : {};
}

function triggerErrors(t: TriggerForm): FormErrors {
  if (isWindowTrigger(t.type)) return t.windowId === "" ? { "trigger.windowId": "Pick a demand window." } : {};
  if (t.type === "price_above") return levelErrors(t, { key: "trigger.price", text: "Enter a price in c/kWh." }, t.price);
  if (t.type === "temperature") return levelErrors(t, { key: "trigger.level", text: "Enter a temperature in C." }, t.level);
  if (t.type === "sun") {
    const offset = parseWhole(t.offset);
    const bad = offset === null || Math.abs(offset) > MAX_OFFSET_MINUTES;
    return { ...(bad ? { "trigger.offset": OFFSET_MESSAGE } : {}), ...dayErrors(t) };
  }
  const errors: FormErrors = dayErrors(t);
  if (!HHMM.test(t.time)) errors["trigger.time"] = "Enter the time as HH:MM, for example 16:00.";
  return errors;
}

const REF_MESSAGE = `Enter each time as HH:MM, or sunrise or sunset with an offset of up to ${MAX_OFFSET_MINUTES} minutes.`;

function limitsErrors(l: LimitsForm): FormErrors {
  const errors: FormErrors = {};
  const gap = parseWhole(l.minGap);
  if (gap === null || gap < 0 || gap > MAX_GAP_MINUTES) errors["limits.minGap"] = `Enter a whole number of minutes from 0 to ${MAX_GAP_MINUTES}.`;
  if (l.againAfter !== null && refPayload(l.againAfter) === null) errors["limits.againAfter"] = REF_MESSAGE;
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

function cardErrors(card: ActionForm, capabilities: Capabilities): FormErrors {
  const prefix = `actions.${card.key}`;
  const action = findAction(capabilities, card.actionId);
  if (!action) return { [`${prefix}.action`]: "Pick an action this device supports." };
  const errors: FormErrors = {};
  for (const decl of action.params) {
    const message = paramError(decl, card.params[decl.name]);
    if (message) errors[`${prefix}.param.${decl.name}`] = message;
  }
  return errors;
}

function actionErrors(form: ScheduleFormState, capabilities: Capabilities): FormErrors {
  if (form.actions.length === 0) return { actions: "Add an action." };
  if (form.actions.length > MAX_ACTIONS) return { actions: `Use at most ${MAX_ACTIONS} actions.` };
  return Object.assign({}, ...form.actions.map((c) => cardErrors(c, capabilities)));
}

/** True when the row's target is one the reading still offers, or the row has no target. */
export function targetOffered(row: ReadingRow, reading: ReadingChoice | undefined): boolean {
  return !row.target || (reading?.targets ?? []).some((t) => t.value === row.target);
}

function readingError(row: ReadingRow, readings: ReadingChoice[]): string | null {
  const reading = readings.find((r) => r.id === row.reading);
  if (!reading) return "This reading is no longer available. Pick another.";
  if (!targetOffered(row, reading)) return `Target "${row.target}" is no longer offered for ${reading.label}. Pick another.`;
  return parseNumber(row.value) === null ? `Enter a number for ${reading.label}.` : null;
}

/** What a row is checked against: the readings the device and site offer, and the demand windows still running. */
interface RowChoices {
  readings: ReadingChoice[];
  windows: DemandWindow[];
}

function rowError(row: ConditionRow, choices: RowChoices): string | null {
  switch (row.kind) {
    case "reading":
      return readingError(row, choices.readings);
    case "months":
      return row.values.length === 0 ? "Pick at least one month." : null;
    case "days":
      return row.values.length === 0 ? "Pick at least one day." : null;
    case "demand_window":
      return row.windowId === "" || choices.windows.some((w) => String(w.id) === row.windowId)
        ? null
        : "This demand window is no longer available. Pick another.";
    case "week":
      return null;
    case "time_of_day":
      return timeOfDayError(row);
  }
}

function timeOfDayError(row: TimeOfDayRow): string | null {
  const start = refPayload(row.start);
  const end = refPayload(row.end);
  if (!start || !end) return REF_MESSAGE;
  return JSON.stringify(start) === JSON.stringify(end) ? "The start and end must be different times." : null;
}

/** Only ticked rows are checked; an unticked row is saved as typed. */
function rowErrors(prefix: string, rows: ConditionRow[], choices: RowChoices): FormErrors {
  const errors: FormErrors = {};
  for (const row of rows.filter((r) => r.use)) {
    const message = rowError(row, choices);
    if (message) errors[`${prefix}.${row.key}`] = message;
  }
  return errors;
}

function restoreErrors(r: RestoreForm, choices: RowChoices): FormErrors {
  if (!r.enabled) return {};
  const errors: FormErrors = { ...rowErrors("restore.onlyIf", r.onlyIf, choices), ...rowErrors("restore.earlyIf", r.earlyIf, choices) };
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

export function validateForm(form: ScheduleFormState, capabilities: Capabilities, windows: DemandWindow[]): FormErrors {
  const choices = { readings: conditionReadings(capabilities), windows };
  const name = form.name.trim();
  const nameError = name === "" ? "Enter a name for this schedule." : name.length > 200 ? "Keep the name to 200 characters or fewer." : null;
  return {
    ...(nameError ? { name: nameError } : {}),
    ...triggerErrors(form.trigger),
    ...rowErrors("conditions", form.conditions, choices),
    ...actionErrors(form, capabilities),
    ...restoreErrors(form.restore, choices),
    ...limitsErrors(form.limits),
    ...retryErrors(form.retry),
  };
}
