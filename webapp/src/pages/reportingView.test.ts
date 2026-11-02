import { describe, expect, it } from "vitest";
import { bucketOf, parentView, parseView, rangeForView, viewSearch } from "./reportingView";

const parse = (search: string) => parseView(new URLSearchParams(search));

describe("reportingView", () => {
  it("reads the month, day and hour from the URL and ignores malformed values", () => {
    expect(parse("month=2026-03&day=2026-03-05&hour=13")).toEqual({ month: "2026-03", day: "2026-03-05", hour: 13 });
    expect(parse("month=March&day=5&hour=13")).toEqual({ month: null, day: null, hour: null });
    expect(parse("day=2026-03-05&hour=24")).toEqual({ month: null, day: "2026-03-05", hour: null });
    expect(parse("hour=13")).toEqual({ month: null, day: null, hour: null });
  });

  it("round-trips through the URL", () => {
    const view = { month: "2026-03", day: "2026-03-05", hour: 0 };
    expect(parse(viewSearch(view))).toEqual(view);
  });

  it("lists days, then hours of a day, then minutes of an hour", () => {
    expect(bucketOf({ month: null, day: null, hour: null })).toBe("day");
    expect(bucketOf({ month: null, day: "2026-03-05", hour: null })).toBe("hour");
    expect(bucketOf({ month: null, day: "2026-03-05", hour: 7 })).toBe("minute");
  });

  it("goes up one level, keeping the month", () => {
    expect(parentView({ month: "2026-03", day: "2026-03-05", hour: 7 })).toEqual({
      month: "2026-03",
      day: "2026-03-05",
      hour: null,
    });
    expect(parentView({ month: "2026-03", day: "2026-03-05", hour: null })).toEqual({
      month: "2026-03",
      day: null,
      hour: null,
    });
  });

  it("spans one hour for minutes and stops at the end of the current minute", () => {
    const hourView = { month: null, day: "2026-03-05", hour: 7 };
    const past = rangeForView(hourView, null, new Date(2026, 5, 1));
    expect(past.from).toEqual(new Date(2026, 2, 5, 7));
    expect(past.to).toEqual(new Date(2026, 2, 5, 8));

    const during = rangeForView(hourView, null, new Date(2026, 2, 5, 7, 20, 30));
    expect(during.to).toEqual(new Date(2026, 2, 5, 7, 21));
  });

  it("spans the picked month, stopping at the end of today", () => {
    const view = { month: "2026-03", day: null, hour: null };
    expect(rangeForView(view, null, new Date(2026, 5, 1))).toEqual({
      from: new Date(2026, 2, 1),
      to: new Date(2026, 3, 1),
    });
    expect(rangeForView(view, null, new Date(2026, 2, 10, 15)).to).toEqual(new Date(2026, 2, 11));
  });
});
