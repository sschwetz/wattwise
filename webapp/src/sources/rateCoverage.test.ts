import { describe, expect, it } from "vitest";
import { findCoverageGaps } from "./rateCoverage";
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

describe("findCoverageGaps", () => {
  it("finds nothing when there are no windows", () => {
    expect(findCoverageGaps([], NOW)).toEqual([]);
  });

  it("finds nothing for an all-day window on every day", () => {
    expect(findCoverageGaps([win()], NOW)).toEqual([]);
  });

  it("names the first uncovered time when only part of the day is covered", () => {
    expect(findCoverageGaps([win({ start_time: "07:00:00", end_time: "22:00:00" })], NOW)).toEqual([
      { channel: "buy", example: "Monday 00:00–07:00 in January" },
    ]);
  });

  it("finds nothing when two windows that touch at a boundary cover the day", () => {
    const night = win({ id: 1, start_time: "00:00:00", end_time: "07:00:00" });
    const day = win({ id: 2, start_time: "07:00:00", end_time: "00:00:00" });
    expect(findCoverageGaps([night, day], NOW)).toEqual([]);
  });

  it("counts an overnight window", () => {
    const overnight = win({ id: 1, start_time: "22:00:00", end_time: "07:00:00" });
    const daytime = win({ id: 2, start_time: "07:00:00", end_time: "22:00:00" });
    expect(findCoverageGaps([overnight, daytime], NOW)).toEqual([]);
  });

  it("ends the example at midnight when the gap runs to the end of the day", () => {
    expect(findCoverageGaps([win({ start_time: "00:00:00", end_time: "22:00:00" })], NOW)).toEqual([
      { channel: "buy", example: "Monday 22:00–00:00 in January" },
    ]);
  });

  it("names a whole missing weekday", () => {
    expect(findCoverageGaps([win({ days_of_week: [0, 1, 2, 3, 4] })], NOW)).toEqual([
      { channel: "buy", example: "Saturday all day in January" },
    ]);
  });

  it("names a whole missing month", () => {
    expect(findCoverageGaps([win({ months: [1, 2, 3] })], NOW)).toEqual([
      { channel: "buy", example: "Monday all day in April" },
    ]);
  });

  it("reports each direction with a gap and skips directions with no windows", () => {
    const buy = win({ id: 1, start_time: "07:00:00", end_time: "22:00:00" });
    const sell = win({ id: 2, channel: "sell", days_of_week: [0, 1, 2, 3, 4, 5] });
    expect(findCoverageGaps([buy, sell], NOW)).toEqual([
      { channel: "buy", example: "Monday 00:00–07:00 in January" },
      { channel: "sell", example: "Sunday all day in January" },
    ]);
  });

  it("ignores windows that have already ended", () => {
    const ended = win({ effective_to: "2026-06-01T00:00:00Z" });
    expect(findCoverageGaps([ended], NOW)).toEqual([]);
  });

  it("does not let a window that has not started hide a current gap", () => {
    const current = win({ id: 1, start_time: "07:00:00", end_time: "22:00:00" });
    const future = win({ id: 2, effective_from: "2027-01-01T00:00:00Z" });
    expect(findCoverageGaps([current, future], NOW)).toEqual([
      { channel: "buy", example: "Monday 00:00–07:00 in January" },
    ]);
  });
});
