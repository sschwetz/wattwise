import { describe, expect, it } from "vitest";
import { clipToDay, pricingDayOffsets } from "./dateRange";

const day = { from: new Date("2026-10-19T00:00:00Z"), to: new Date("2026-10-20T00:00:00Z") };

describe("clipToDay", () => {
  it("returns the whole day when there is no zoom", () => {
    expect(clipToDay(null, day)).toBe(day);
  });

  it("keeps a zoom that lies inside the day", () => {
    const zoom = { from: new Date("2026-10-19T06:00:00Z"), to: new Date("2026-10-19T07:00:00Z") };
    expect(clipToDay(zoom, day)).toEqual(zoom);
  });

  it("clips a zoom that runs past the day's edges", () => {
    const zoom = { from: new Date("2026-10-18T20:00:00Z"), to: new Date("2026-10-19T03:00:00Z") };
    expect(clipToDay(zoom, day)).toEqual({ from: day.from, to: new Date("2026-10-19T03:00:00Z") });
  });

  it("falls back to the whole day when the zoom is on another day", () => {
    const zoom = { from: new Date("2026-10-10T06:00:00Z"), to: new Date("2026-10-10T07:00:00Z") };
    expect(clipToDay(zoom, day)).toBe(day);
  });
});

describe("pricingDayOffsets", () => {
  const at = (h: number, m = 0) => new Date(2026, 9, 20, h, m);
  it("shows yesterday and today from midnight until 4am", () => {
    expect(pricingDayOffsets(at(0))).toEqual([-1, 0]);
    expect(pricingDayOffsets(at(3, 59))).toEqual([-1, 0]);
  });
  it("shows only today from 4am until 8pm", () => {
    expect(pricingDayOffsets(at(4))).toEqual([0]);
    expect(pricingDayOffsets(at(19, 59))).toEqual([0]);
  });
  it("adds tomorrow from 8pm", () => {
    expect(pricingDayOffsets(at(20))).toEqual([0, 1]);
    expect(pricingDayOffsets(at(23, 59))).toEqual([0, 1]);
  });
});
