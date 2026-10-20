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

describe("buildPriceChartData fallback", () => {
  const reading = (ts: string, value: number, forecast_fallback = false) => ({ ts, metric: "buy", value, forecast_fallback });
  const now = new Date("2026-10-08T00:07:00Z");

  it("moves fallback intervals onto their own series and off the predicted one", () => {
    const data = buildPriceChartData(
      [
        reading("2026-10-08T00:05:00Z", 22),
        reading("2026-10-08T00:10:00Z", 24),
        reading("2026-10-08T00:15:00Z", 40, true),
      ],
      now,
    );

    expect(data.map((p) => p.predictedCentsPerKwh)).toEqual([22, 24, null]);
    // The last predicted point is shared so the dotted segment connects to
    // the dashed one instead of leaving a gap.
    expect(data.map((p) => p.fallbackCentsPerKwh)).toEqual([null, 24, 40]);
  });

  it("leaves the fallback series empty when no reading is flagged", () => {
    const data = buildPriceChartData([reading("2026-10-08T00:05:00Z", 22), reading("2026-10-08T00:10:00Z", 24)], now);

    expect(data.map((p) => p.fallbackCentsPerKwh)).toEqual([null, null]);
  });

  it("treats readings with no flag at all as normal", () => {
    const data = buildPriceChartData([{ ts: "2026-10-08T00:10:00Z", metric: "buy", value: 24 }], now);

    expect(data[0].predictedCentsPerKwh).toBe(24);
    expect(data[0].fallbackCentsPerKwh).toBeNull();
  });
});

describe("mergeImportExportPrices fallback", () => {
  it("carries each channel's fallback series, and takes the absolute value of export", () => {
    const merged = mergeImportExportPrices(
      [{ ts: 0, actualCentsPerKwh: null, predictedCentsPerKwh: null, fallbackCentsPerKwh: 40 }],
      [{ ts: 0, actualCentsPerKwh: null, predictedCentsPerKwh: null, fallbackCentsPerKwh: -12 }],
    );

    expect(merged[0].fallbackImportCentsPerKwh).toBe(40);
    expect(merged[0].fallbackExportCentsPerKwh).toBe(12);
  });
});

describe("mergeImportExportPrices", () => {
  it("merges import and export points that share the same timestamps", () => {
    const importPoints = [
      { ts: 0, actualCentsPerKwh: 20, predictedCentsPerKwh: null, fallbackCentsPerKwh: null },
      { ts: 300_000, actualCentsPerKwh: null, predictedCentsPerKwh: 22, fallbackCentsPerKwh: null },
    ];
    const exportPoints = [
      { ts: 0, actualCentsPerKwh: -5, predictedCentsPerKwh: null, fallbackCentsPerKwh: null },
      { ts: 300_000, actualCentsPerKwh: null, predictedCentsPerKwh: -6, fallbackCentsPerKwh: null },
    ];

    const merged = mergeImportExportPrices(importPoints, exportPoints);

    expect(merged).toEqual([
      {
        ts: 0,
        actualImportCentsPerKwh: 20,
        predictedImportCentsPerKwh: null,
        actualExportCentsPerKwh: 5,
        predictedExportCentsPerKwh: null,
        fallbackImportCentsPerKwh: null,
        fallbackExportCentsPerKwh: null,
      },
      {
        ts: 300_000,
        actualImportCentsPerKwh: null,
        predictedImportCentsPerKwh: 22,
        actualExportCentsPerKwh: null,
        predictedExportCentsPerKwh: 6,
        fallbackImportCentsPerKwh: null,
        fallbackExportCentsPerKwh: null,
      },
    ]);
  });

  it("takes the absolute value of the export price, since Amber's feed-in price is stored negative", () => {
    const merged = mergeImportExportPrices(
      [],
      [{ ts: 0, actualCentsPerKwh: -8.5, predictedCentsPerKwh: null, fallbackCentsPerKwh: null }],
    );

    expect(merged[0].actualExportCentsPerKwh).toBe(8.5);
  });

  it("fills in null for a series missing a timestamp the other series has", () => {
    const merged = mergeImportExportPrices(
      [{ ts: 0, actualCentsPerKwh: 20, predictedCentsPerKwh: null, fallbackCentsPerKwh: null }],
      [{ ts: 300_000, actualCentsPerKwh: -5, predictedCentsPerKwh: null, fallbackCentsPerKwh: null }],
    );

    expect(merged).toEqual([
      {
        ts: 0,
        actualImportCentsPerKwh: 20,
        predictedImportCentsPerKwh: null,
        actualExportCentsPerKwh: null,
        predictedExportCentsPerKwh: null,
        fallbackImportCentsPerKwh: null,
        fallbackExportCentsPerKwh: null,
      },
      {
        ts: 300_000,
        actualImportCentsPerKwh: null,
        predictedImportCentsPerKwh: null,
        actualExportCentsPerKwh: 5,
        predictedExportCentsPerKwh: null,
        fallbackImportCentsPerKwh: null,
        fallbackExportCentsPerKwh: null,
      },
    ]);
  });

  it("sorts the merged points by timestamp", () => {
    const merged = mergeImportExportPrices(
      [{ ts: 300_000, actualCentsPerKwh: 22, predictedCentsPerKwh: null, fallbackCentsPerKwh: null }],
      [{ ts: 0, actualCentsPerKwh: -5, predictedCentsPerKwh: null, fallbackCentsPerKwh: null }],
    );

    expect(merged.map((p) => p.ts)).toEqual([0, 300_000]);
  });
});
