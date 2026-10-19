import { describe, expect, it } from "vitest";
import { clipToDay } from "./dateRange";

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
