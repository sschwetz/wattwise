import { describe, expect, it } from "vitest";
import { changeKind, currentWeekParity, weekNumber } from "./calendarConditions";
import type { ConditionRow, ReadingChoice } from "./scheduleForm";

const readings: ReadingChoice[] = [
  { id: "indoor_temp_c", label: "Indoor temperature", unit: "C", type: "number", targets: null },
];
const defaults = { readings, parity: "even" as const };
const reading: ConditionRow = { key: 4, use: false, kind: "reading", reading: "outdoor_temp_c", op: "gt", value: "30", target: null };

describe("weekNumber", () => {
  it("counts weeks from the one holding 1970-01-01", () => {
    expect(weekNumber(1970, 1, 1)).toBe(0);
    expect(weekNumber(1970, 1, 4)).toBe(0);
    expect(weekNumber(1970, 1, 5)).toBe(1);
    expect(weekNumber(2026, 11, 9)).toBe(2967);
    expect(weekNumber(2026, 11, 14)).toBe(2967);
    expect(weekNumber(2026, 11, 16)).toBe(2968);
  });

  it("alternates across the year end", () => {
    expect(weekNumber(2027, 1, 3)).toBe(2974);
    expect(weekNumber(2027, 1, 4)).toBe(2975);
  });
});

describe("currentWeekParity", () => {
  it("uses the site's calendar day", () => {
    expect(currentWeekParity(new Date("2026-11-14T02:00:00Z"), "Australia/Sydney")).toBe("odd");
    expect(currentWeekParity(new Date("2026-11-15T14:00:00Z"), "Australia/Sydney")).toBe("even");
  });
});

describe("changeKind", () => {
  it("keeps the key and the tick box", () => {
    for (const kind of ["months", "days", "week", "demand_window", "reading"] as const) {
      expect(changeKind(reading, kind, defaults)).toMatchObject({ key: 4, use: false, kind });
    }
  });

  it("starts each kind with sensible controls", () => {
    expect(changeKind(reading, "months", defaults)).toMatchObject({ op: "in", values: [] });
    expect(changeKind(reading, "days", defaults)).toMatchObject({ op: "in", values: [] });
    expect(changeKind(reading, "week", defaults)).toMatchObject({ parity: "even" });
    expect(changeKind(reading, "demand_window", defaults)).toMatchObject({ op: "not_active", windowId: "" });
    expect(changeKind(reading, "reading", defaults)).toMatchObject({ reading: "indoor_temp_c", op: "gt", value: "" });
  });
});
