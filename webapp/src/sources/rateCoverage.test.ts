import { describe, expect, it } from "vitest";
import { hasCoverageGap } from "./rateCoverage";
import type { RateWindow } from "./useRateWindows";

const NOW = new Date("2026-06-15T00:00:00Z");

function win(overrides: Partial<RateWindow> = {}): RateWindow {
  return {
    id: 1,
    source_id: 1,
    channel: "buy",
    label: "Flat",
    effective_from: "2026-01-01T00:00:00Z",
    effective_to: null,
    months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    days_of_week: [0, 1, 2, 3, 4, 5, 6],
    start_time: "00:00:00",
    end_time: "00:00:00",
    rate_c_per_kwh: "30.0000",
    ...overrides,
  };
}

describe("hasCoverageGap", () => {
  it("is false when there are no windows", () => {
    expect(hasCoverageGap([], NOW)).toBe(false);
  });

  it("is false for an all-day window on every day", () => {
    expect(hasCoverageGap([win()], NOW)).toBe(false);
  });

  it("is true when only part of the day is covered", () => {
    expect(hasCoverageGap([win({ start_time: "07:00:00", end_time: "22:00:00" })], NOW)).toBe(true);
  });

  it("is false when two windows that touch at a boundary cover the day", () => {
    const night = win({ id: 1, start_time: "00:00:00", end_time: "07:00:00" });
    const day = win({ id: 2, start_time: "07:00:00", end_time: "00:00:00" });
    expect(hasCoverageGap([night, day], NOW)).toBe(false);
  });

  it("counts an overnight window", () => {
    const overnight = win({ id: 1, start_time: "22:00:00", end_time: "07:00:00" });
    const daytime = win({ id: 2, start_time: "07:00:00", end_time: "22:00:00" });
    expect(hasCoverageGap([overnight, daytime], NOW)).toBe(false);
  });

  it("is true when some days of the week are missing", () => {
    expect(hasCoverageGap([win({ days_of_week: [0, 1, 2, 3, 4] })], NOW)).toBe(true);
  });

  it("is true when some months are missing", () => {
    expect(hasCoverageGap([win({ months: [1, 2, 3] })], NOW)).toBe(true);
  });

  it("ignores windows that have already ended", () => {
    const ended = win({ effective_to: "2026-06-01T00:00:00Z" });
    expect(hasCoverageGap([ended], NOW)).toBe(false);
  });
});
