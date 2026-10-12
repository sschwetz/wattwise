import { describe, expect, it } from "vitest";
import type { DateRange } from "./useHistoricalData";
import {
  buildAxisTickPlan,
  buildHistoryChartData,
  bucketStartMs,
  enumerateBucketStarts,
  formatBucketTick,
  formatTooltipValue,
} from "./historyChart";

const HOUR: DateRange = {
  from: new Date("2026-10-08T07:00:00Z"),
  to: new Date("2026-10-08T08:00:00Z"),
};

describe("buildHistoryChartData", () => {
  it("renders grid export as negative kWh, matching the cost line's below-zero-when-exporting convention", () => {
    const data = buildHistoryChartData(
      [],
      [
        { ts: "2026-10-08T07:00:00Z", metric: "grid_export_w", value: 6000 },
        { ts: "2026-10-08T07:30:00Z", metric: "grid_export_w", value: 6000 },
      ],
      [],
      HOUR,
      "hour",
    );

    expect(data).toHaveLength(1);
    // Two minutes at 6000W = 2 * 0.1 kWh = 0.2 kWh exported, shown as -0.2.
    expect(data[0].gridKwh).toBeCloseTo(-0.2);
  });

  it("renders grid import as positive kWh in the same bucket/series as export", () => {
    const data = buildHistoryChartData(
      [{ ts: "2026-10-08T07:00:00Z", metric: "grid_import_w", value: 6000 }],
      [],
      [],
      HOUR,
      "hour",
    );

    expect(data).toHaveLength(1);
    expect(data[0].gridKwh).toBeCloseTo(0.1);
  });

  it("nets import and export within the same bucket into one signed value", () => {
    const data = buildHistoryChartData(
      [{ ts: "2026-10-08T07:05:00Z", metric: "grid_import_w", value: 600 }],
      [{ ts: "2026-10-08T07:10:00Z", metric: "grid_export_w", value: 6000 }],
      [],
      HOUR,
      "hour",
    );

    expect(data).toHaveLength(1);
    // +0.01 kWh import, -0.1 kWh export -> net -0.09 kWh for the hour.
    expect(data[0].gridKwh).toBeCloseTo(-0.09);
  });

  it("sums cost series cents into dollars per bucket, keeping the backend's sign", () => {
    const data = buildHistoryChartData(
      [],
      [],
      [
        { bucket_start: "2026-10-08T07:00:00Z", cost_c: -77 },
        { bucket_start: "2026-10-08T07:30:00Z", cost_c: -23 },
      ],
      HOUR,
      "hour",
    );

    expect(data).toHaveLength(1);
    expect(data[0].costDollars).toBeCloseTo(-1.0);
  });

  it("zero-fills buckets with no data, including ones that haven't happened yet", () => {
    const range: DateRange = { from: new Date("2026-10-08T07:00:00Z"), to: new Date("2026-10-08T10:00:00Z") };
    const data = buildHistoryChartData(
      [],
      [{ ts: "2026-10-08T07:00:00Z", metric: "grid_export_w", value: 6000 }],
      [{ bucket_start: "2026-10-08T07:00:00Z", cost_c: -50 }],
      range,
      "hour",
    );

    expect(data).toHaveLength(3);
    expect(data.map((p) => p.gridKwh)).toEqual([-0.1, 0, 0]);
    expect(data.map((p) => p.costDollars)).toEqual([-0.5, 0, 0]);
  });

  it("buckets by 15 minutes when requested, truncating to the quarter-hour start", () => {
    const ms = bucketStartMs("2026-10-08T19:42:00Z", "15min");
    expect(new Date(ms).toISOString()).toBe("2026-10-08T19:30:00.000Z");
  });

  it("buckets by 6 hours when requested, truncating to the UTC 6-hour window start", () => {
    const ms = bucketStartMs("2026-10-08T19:42:00Z", "6hour");
    expect(new Date(ms).toISOString()).toBe("2026-10-08T18:00:00.000Z");
  });
});

describe("enumerateBucketStarts", () => {
  it("produces one entry per hour across the range, excluding the end boundary", () => {
    const range: DateRange = { from: new Date("2026-10-08T00:00:00Z"), to: new Date("2026-10-08T03:00:00Z") };
    const starts = enumerateBucketStarts(range, "hour").map((ms) => new Date(ms).toISOString());
    expect(starts).toEqual([
      "2026-10-08T00:00:00.000Z",
      "2026-10-08T01:00:00.000Z",
      "2026-10-08T02:00:00.000Z",
    ]);
  });

  it("returns nothing for an empty or reversed range", () => {
    const range: DateRange = { from: new Date("2026-10-08T03:00:00Z"), to: new Date("2026-10-08T00:00:00Z") };
    expect(enumerateBucketStarts(range, "hour")).toEqual([]);
  });
});

describe("formatTooltipValue", () => {
  it("drops the sign on a cost credit -- the color conveys it instead", () => {
    expect(formatTooltipValue(-0.5, "Cost")).toEqual(["$0.50", "Cost"]);
  });

  it("keeps a plain positive cost as-is", () => {
    expect(formatTooltipValue(0.5, "Cost")).toEqual(["$0.50", "Cost"]);
  });

  it("still describes the Grid row's direction in words, not just color", () => {
    expect(formatTooltipValue(-0.2, "Grid")).toEqual(["0.20 kWh exported", "Grid"]);
    expect(formatTooltipValue(0.2, "Grid")).toEqual(["0.20 kWh imported", "Grid"]);
  });
});

describe("formatBucketTick", () => {
  it("always uses 24-hour time, never AM/PM", () => {
    // 14:00 local is unambiguous proof: 12-hour formatting would render "2:00 PM".
    const afternoon = new Date(2026, 9, 8, 14, 0).getTime();
    expect(formatBucketTick(afternoon, "15min")).toBe("14:00");
    expect(formatBucketTick(afternoon, "hour")).not.toMatch(/[AP]M/i);
  });
});

describe("buildAxisTickPlan", () => {
  it("thins to a handful of 24-hour-formatted ticks for a <=24h span", () => {
    const range: DateRange = { from: new Date(2026, 9, 8, 0, 0), to: new Date(2026, 9, 9, 0, 0) };
    const chartData = buildHistoryChartData([], [], [], range, "15min");
    const plan = buildAxisTickPlan(chartData, range);

    expect(plan.ticks.length).toBeLessThanOrEqual(8);
    for (const ts of plan.ticks) {
      expect(plan.formatTick(ts)).toMatch(/^\d{2}:\d{2}$/);
    }
  });

  it("labels local midnight with a weekday abbreviation and leaves local noon blank, for a >24h and <=7d span", () => {
    const range: DateRange = { from: new Date(2026, 9, 8, 0, 0), to: new Date(2026, 9, 10, 0, 0) };
    const chartData = buildHistoryChartData([], [], [], range, "15min");
    const plan = buildAxisTickPlan(chartData, range);

    const midnightOct8 = new Date(2026, 9, 8, 0, 0).getTime();
    const noonOct8 = new Date(2026, 9, 8, 12, 0).getTime();
    expect(plan.ticks).toContain(midnightOct8);
    expect(plan.ticks).toContain(noonOct8);
    expect(plan.formatTick(midnightOct8)).toBe("Thu");
    expect(plan.formatTick(noonOct8)).toBe("");
  });

  it("labels one (possibly thinned) tick per local calendar day with a short date, for a >7d span", () => {
    const range: DateRange = { from: new Date(2026, 9, 1, 0, 0), to: new Date(2026, 9, 31, 0, 0) };
    const chartData = buildHistoryChartData([], [], [], range, "6hour");
    const plan = buildAxisTickPlan(chartData, range);

    expect(plan.ticks.length).toBeGreaterThan(0);
    expect(plan.ticks.length).toBeLessThanOrEqual(10);
    for (const ts of plan.ticks) {
      // Locale-dependent month/day order and abbreviation length (e.g. "Sep 30"
      // vs. "30 Sept") aren't the point here -- just that each tick gets a
      // short, distinct, day-level label rather than a bare bucket timestamp.
      expect(plan.formatTick(ts)).toMatch(/\d{1,2}/);
    }
  });
});
