// Has the owner changed the schedule form? Both forms are reduced to a
// canonical shape first, so "30" and "30.0", surrounding spaces, the order
// days were ticked in and condition row keys do not count as changes.
import { parseNumber, type ScheduleFormState } from "./scheduleForm";

type Canonical = string | number | boolean | null | Canonical[] | { [name: string]: Canonical };

/** Trimmed text, written as a number when it reads as one. The name stays text. */
function canonicalText(text: string, field: string): string {
  const trimmed = text.trim();
  if (field === "name") return trimmed;
  const n = parseNumber(trimmed);
  return n === null ? trimmed : String(n);
}

function canonicalArray(items: unknown[], field: string): Canonical[] {
  const values = items.map((item) => canonical(item, field));
  return field === "days" ? [...values].sort() : values;
}

function canonicalObject(value: object): Canonical {
  const entries = Object.entries(value)
    .filter(([name]) => name !== "key")
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

export function isDirty(form: ScheduleFormState, baseline: ScheduleFormState): boolean {
  return JSON.stringify(canonical(form, "")) !== JSON.stringify(canonical(baseline, ""));
}
