// Pure helpers for the month, day, week and demand window condition rows.
import { type ConditionKind, type ConditionRow, pointAtReading, type ReadingChoice } from "./scheduleForm";

const DAY_MS = 86_400_000;

/** The week holding 1970-01-01 (a Thursday) is week 0 and even; weeks run Monday to Sunday.
 * The actor uses the same rule (schedule_eval.week_number). Month is 1-12. */
export function weekNumber(year: number, month: number, day: number): number {
  return Math.floor((Date.UTC(year, month - 1, day) / DAY_MS + 3) / 7);
}

/** Whether the week holding `now` is odd or even, by the calendar day in the site's timezone. */
export function currentWeekParity(now: Date, timezone: string): "odd" | "even" {
  const key = new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  const [year, month, day] = key.split("-").map(Number);
  return weekNumber(year, month, day) % 2 === 1 ? "odd" : "even";
}

export interface KindDefaults {
  readings: ReadingChoice[];
  parity: "odd" | "even";
}

/** The row turned into `kind`, keeping its key and tick box and starting the other fields afresh. */
export function changeKind(row: ConditionRow, kind: ConditionKind, defaults: KindDefaults): ConditionRow {
  const base = { key: row.key, use: row.use };
  switch (kind) {
    case "reading":
      return pointAtReading({ ...base, kind, reading: "", op: "gt", value: "", target: null }, defaults.readings[0]);
    case "months":
    case "days":
      return { ...base, kind, op: "in", values: [] };
    case "week":
      return { ...base, kind, parity: defaults.parity };
    case "demand_window":
      return { ...base, kind, op: "not_active", windowId: "" };
  }
}
