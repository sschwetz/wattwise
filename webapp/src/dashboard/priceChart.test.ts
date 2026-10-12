import { describe, expect, it } from "vitest";
import { buildPriceChartData, mergeImportExportPrices } from "./priceChart";

describe("buildPriceChartData", () => {
  it("dedupes the per-minute fan-out down to one point per 5-minute interval", () => {
    const readings = [0, 1, 2, 3, 4].map((minute) => ({
      ts: `2026-10-08T00:0${minute}:00Z`,
      metric: "buy",
      value: 25.5,
    }));
    const now = new Date("2026-10-08T01:00:00Z");

    const data = buildPriceChartData(readings, now);

    expect(data).toHaveLength(1);
    expect(data[0].ts).toBe(new Date("2026-10-08T00:00:00Z").getTime());
    expect(data[0].actualCentsPerKwh).toBe(25.5);
  });

  it("puts past intervals on the actual series and future ones on the predicted series, sharing the boundary point", () => {
    const readings = [
      { ts: "2026-10-08T00:00:00Z", metric: "buy", value: 20 }, // past
      { ts: "2026-10-08T00:05:00Z", metric: "buy", value: 22 }, // the "now" interval -- still actual
      { ts: "2026-10-08T00:10:00Z", metric: "buy", value: 24 }, // future -- forecast
    ];
    const now = new Date("2026-10-08T00:07:00Z"); // partway through the 00:05 interval

    const data = buildPriceChartData(readings, now);

    expect(data.map((p) => p.actualCentsPerKwh)).toEqual([20, 22, null]);
    // The 00:05 point is shared by both series so the two segments visually
    // connect instead of leaving a gap between solid and dashed.
    expect(data.map((p) => p.predictedCentsPerKwh)).toEqual([null, 22, 24]);
  });

  it("treats every interval as predicted when now is before all of them", () => {
    const readings = [{ ts: "2026-10-08T00:00:00Z", metric: "buy", value: 20 }];
    const now = new Date("2026-10-07T00:00:00Z");

    const data = buildPriceChartData(readings, now);

    expect(data[0].actualCentsPerKwh).toBeNull();
    expect(data[0].predictedCentsPerKwh).toBe(20);
  });
});

describe("mergeImportExportPrices", () => {
  it("merges import and export points that share the same timestamps", () => {
    const importPoints = [
      { ts: 0, actualCentsPerKwh: 20, predictedCentsPerKwh: null },
      { ts: 300_000, actualCentsPerKwh: null, predictedCentsPerKwh: 22 },
    ];
    const exportPoints = [
      { ts: 0, actualCentsPerKwh: -5, predictedCentsPerKwh: null },
      { ts: 300_000, actualCentsPerKwh: null, predictedCentsPerKwh: -6 },
    ];

    const merged = mergeImportExportPrices(importPoints, exportPoints);

    expect(merged).toEqual([
      {
        ts: 0,
        actualImportCentsPerKwh: 20,
        predictedImportCentsPerKwh: null,
        actualExportCentsPerKwh: 5,
        predictedExportCentsPerKwh: null,
      },
      {
        ts: 300_000,
        actualImportCentsPerKwh: null,
        predictedImportCentsPerKwh: 22,
        actualExportCentsPerKwh: null,
        predictedExportCentsPerKwh: 6,
      },
    ]);
  });

  it("takes the absolute value of the export price, since Amber's feed-in price is stored negative", () => {
    const merged = mergeImportExportPrices(
      [],
      [{ ts: 0, actualCentsPerKwh: -8.5, predictedCentsPerKwh: null }],
    );

    expect(merged[0].actualExportCentsPerKwh).toBe(8.5);
  });

  it("fills in null for a series missing a timestamp the other series has", () => {
    const merged = mergeImportExportPrices(
      [{ ts: 0, actualCentsPerKwh: 20, predictedCentsPerKwh: null }],
      [{ ts: 300_000, actualCentsPerKwh: -5, predictedCentsPerKwh: null }],
    );

    expect(merged).toEqual([
      {
        ts: 0,
        actualImportCentsPerKwh: 20,
        predictedImportCentsPerKwh: null,
        actualExportCentsPerKwh: null,
        predictedExportCentsPerKwh: null,
      },
      {
        ts: 300_000,
        actualImportCentsPerKwh: null,
        predictedImportCentsPerKwh: null,
        actualExportCentsPerKwh: 5,
        predictedExportCentsPerKwh: null,
      },
    ]);
  });

  it("sorts the merged points by timestamp", () => {
    const merged = mergeImportExportPrices(
      [{ ts: 300_000, actualCentsPerKwh: 22, predictedCentsPerKwh: null }],
      [{ ts: 0, actualCentsPerKwh: -5, predictedCentsPerKwh: null }],
    );

    expect(merged.map((p) => p.ts)).toEqual([0, 300_000]);
  });
});
