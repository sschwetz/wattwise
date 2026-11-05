// Has the owner changed the schedule form? Both forms are reduced to a
// canonical shape first, so "30" and "30.0", surrounding spaces, the order
// days or zones were ticked in and condition row keys do not count as changes.
import { type ConditionRow, parseNumber, type ScheduleFormState } from "./scheduleForm";

type Canonical = string | number | boolean | null | Canonical[] | { [name: string]: Canonical };

/** Trimmed text, written as a number when it reads as one. The name stays text. */
function canonicalText(text: string, field: string): string {
  const trimmed = text.trim();
  if (field === "name") return trimmed;
  const n = parseNumber(trimmed);
  return n === null ? trimmed : String(n);
}

/** Days and multi-choice values (`value` arrays) are sets, so their order does not count. */
function canonicalArray(items: unknown[], field: string): Canonical[] {
  const values = items.map((item) => canonical(item, field));
  return field === "days" || field === "value" ? [...values].sort() : values;
}

function canonicalObject(value: object): Canonical {
  const entries = Object.entries(value)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, item]) => [name, canonical(item, name)]);
  return Object.fromEntries(entries);
}

function canonical(value: unknown, field: string): Canonical {
  if (typeof value === "string") return canonicalText(value, field);
  if (typeof value === "number" || typeof value === "boolean") return value;
  if (Array.isArray(value)) return canonicalArray(value, field);
  if (value !== null && typeof value === "object") return canonicalObject(value);
  return null;
}

/** Condition rows without their `key`, which only tells React the rows apart. */
function withoutRowKeys(form: ScheduleFormState) {
  const strip = (rows: ConditionRow[]) => rows.map(({ key: _key, ...row }) => row);
  const { restore } = form;
  return { ...form, conditions: strip(form.conditions), restore: { ...restore, onlyIf: strip(restore.onlyIf), earlyIf: strip(restore.earlyIf) } };
}

function canonicalForm(form: ScheduleFormState): string {
  return JSON.stringify(canonical(withoutRowKeys(form), ""));
}

export function isDirty(form: ScheduleFormState, baseline: ScheduleFormState): boolean {
  return canonicalForm(form) !== canonicalForm(baseline);
}
