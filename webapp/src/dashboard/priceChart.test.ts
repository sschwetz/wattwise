import { describe, expect, it } from "vitest";
import {
  BAND_WIDTH_THRESHOLD_CENTS,
  buildPriceChartData,
  findInstabilityWindows,
  mergeImportExportPrices,
  type SpikeStatus,
  type TodayPricingPoint,
  windowSpanWithin,
} from "./priceChart";

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
      [{ ts: 0, actualCentsPerKwh: null, predictedCentsPerKwh: null, fallbackCentsPerKwh: 40, band: null, spikeStatus: "none" as const }],
      [{ ts: 0, actualCentsPerKwh: null, predictedCentsPerKwh: null, fallbackCentsPerKwh: -12, band: null, spikeStatus: "none" as const }],
    );

    expect(merged[0].fallbackImportCentsPerKwh).toBe(40);
    expect(merged[0].fallbackExportCentsPerKwh).toBe(12);
  });
});

describe("mergeImportExportPrices", () => {
  it("merges import and export points that share the same timestamps", () => {
    const importPoints = [
      { ts: 0, actualCentsPerKwh: 20, predictedCentsPerKwh: null, fallbackCentsPerKwh: null, band: null, spikeStatus: "none" as const },
      { ts: 300_000, actualCentsPerKwh: null, predictedCentsPerKwh: 22, fallbackCentsPerKwh: null, band: null, spikeStatus: "none" as const },
    ];
    const exportPoints = [
      { ts: 0, actualCentsPerKwh: -5, predictedCentsPerKwh: null, fallbackCentsPerKwh: null, band: null, spikeStatus: "none" as const },
      { ts: 300_000, actualCentsPerKwh: null, predictedCentsPerKwh: -6, fallbackCentsPerKwh: null, band: null, spikeStatus: "none" as const },
    ];

    const merged = mergeImportExportPrices(importPoints, exportPoints);

    expect(merged).toEqual([
      {
        ts: 0,
        importBand: null,
        importSpikeStatus: "none",
        actualImportCentsPerKwh: 20,
        predictedImportCentsPerKwh: null,
        actualExportCentsPerKwh: 5,
        predictedExportCentsPerKwh: null,
        fallbackImportCentsPerKwh: null,
        fallbackExportCentsPerKwh: null,
      },
      {
        ts: 300_000,
        importBand: null,
        importSpikeStatus: "none",
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
      [{ ts: 0, actualCentsPerKwh: -8.5, predictedCentsPerKwh: null, fallbackCentsPerKwh: null, band: null, spikeStatus: "none" as const }],
    );

    expect(merged[0].actualExportCentsPerKwh).toBe(8.5);
  });

  it("fills in null for a series missing a timestamp the other series has", () => {
    const merged = mergeImportExportPrices(
      [{ ts: 0, actualCentsPerKwh: 20, predictedCentsPerKwh: null, fallbackCentsPerKwh: null, band: null, spikeStatus: "none" as const }],
      [{ ts: 300_000, actualCentsPerKwh: -5, predictedCentsPerKwh: null, fallbackCentsPerKwh: null, band: null, spikeStatus: "none" as const }],
    );

    expect(merged).toEqual([
      {
        ts: 0,
        importBand: null,
        importSpikeStatus: "none",
        actualImportCentsPerKwh: 20,
        predictedImportCentsPerKwh: null,
        actualExportCentsPerKwh: null,
        predictedExportCentsPerKwh: null,
        fallbackImportCentsPerKwh: null,
        fallbackExportCentsPerKwh: null,
      },
      {
        ts: 300_000,
        importBand: null,
        importSpikeStatus: "none",
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
      [{ ts: 300_000, actualCentsPerKwh: 22, predictedCentsPerKwh: null, fallbackCentsPerKwh: null, band: null, spikeStatus: "none" as const }],
      [{ ts: 0, actualCentsPerKwh: -5, predictedCentsPerKwh: null, fallbackCentsPerKwh: null, band: null, spikeStatus: "none" as const }],
    );

    expect(merged.map((p) => p.ts)).toEqual([0, 300_000]);
  });
});

describe("buildPriceChartData band", () => {
  const now = new Date("2026-10-08T00:07:00Z");
  const reading = (low: number | null, high: number | null, spike?: SpikeStatus) => ({
    ts: "2026-10-08T00:10:00Z",
    metric: "buy",
    value: 24,
    price_low: low,
    price_high: high,
    spike_status: spike,
  });

  it("carries the band and spike status of the first reading in each bucket", () => {
    const [point] = buildPriceChartData([reading(20, 30, "potential")], now);

    expect(point.band).toEqual([20, 30]);
    expect(point.spikeStatus).toBe("potential");
  });

  it("orders the band min to max when low is numerically above high, as export prices are", () => {
    const [point] = buildPriceChartData([reading(-12.4, -14.6)], now);

    expect(point.band).toEqual([-14.6, -12.4]);
  });

  it("has no band when low or high is missing, and treats a missing spike status as none", () => {
    const points = buildPriceChartData(
      [reading(null, null), { ts: "2026-10-08T00:15:00Z", metric: "buy", value: 25 }],
      now,
    );

    expect(points.map((p) => p.band)).toEqual([null, null]);
    expect(points.map((p) => p.spikeStatus)).toEqual(["none", "none"]);
  });
});

describe("mergeImportExportPrices band", () => {
  it("carries the band and spike status from import points only", () => {
    const base = { ts: 0, actualCentsPerKwh: null, predictedCentsPerKwh: 20, fallbackCentsPerKwh: null };
    const merged = mergeImportExportPrices(
      [{ ...base, band: [18, 25], spikeStatus: "spike" }],
      [{ ...base, band: [-9, -7], spikeStatus: "potential" }],
    );

    expect(merged[0].importBand).toEqual([18, 25]);
    expect(merged[0].importSpikeStatus).toBe("spike");
    expect(merged[0]).not.toHaveProperty("exportBand");
  });
});

describe("findInstabilityWindows", () => {
  const STEP = 300_000;
  const point = (index: number, over: Partial<TodayPricingPoint> = {}): TodayPricingPoint => ({
    ts: index * STEP,
    actualImportCentsPerKwh: null,
    predictedImportCentsPerKwh: 20,
    fallbackImportCentsPerKwh: null,
    actualExportCentsPerKwh: null,
    predictedExportCentsPerKwh: null,
    fallbackExportCentsPerKwh: null,
    importBand: [19, 21],
    importSpikeStatus: "none",
    ...over,
  });
  const spike = (index: number) => point(index, { importSpikeStatus: "spike" });

  it("returns nothing when nothing is flagged, or when there are no points", () => {
    expect(findInstabilityWindows([point(0), point(1)])).toEqual([]);
    expect(findInstabilityWindows([])).toEqual([]);
  });

  it("makes a one-interval window from a single flagged point", () => {
    expect(findInstabilityWindows([point(0), spike(1), point(2)])).toEqual([
      { from: STEP, to: 2 * STEP, level: "spike" },
    ]);
  });

  it("merges adjacent flagged points and splits on a gap", () => {
    const windows = findInstabilityWindows([spike(0), spike(1), point(2), spike(3)]);

    expect(windows).toEqual([
      { from: 0, to: 2 * STEP, level: "spike" },
      { from: 3 * STEP, to: 4 * STEP, level: "spike" },
    ]);
  });

  it("flags a band wider than the threshold, but not one exactly at it", () => {
    const wide = point(0, { importBand: [10, 10 + BAND_WIDTH_THRESHOLD_CENTS + 0.01] });
    const exact = point(1, { importBand: [10, 10 + BAND_WIDTH_THRESHOLD_CENTS] });

    expect(findInstabilityWindows([wide, exact])).toEqual([{ from: 0, to: STEP, level: "wide-band" }]);
  });

  it("gives a run the worst level in it: spike over potential over wide-band", () => {
    const windows = findInstabilityWindows([
      point(0, { importBand: [0, 50] }),
      point(1, { importSpikeStatus: "potential" }),
      point(2, { importSpikeStatus: "spike" }),
    ]);

    expect(windows).toEqual([{ from: 0, to: 3 * STEP, level: "spike" }]);
  });

  it("skips points that already have an actual price", () => {
    expect(findInstabilityWindows([point(0, { actualImportCentsPerKwh: 30, importSpikeStatus: "spike" })])).toEqual([]);
  });
});

describe("windowSpanWithin", () => {
  const STEP = 300_000;
  const tss = [0, STEP, 2 * STEP, 3 * STEP];

  it("returns the first and last chart timestamps the window covers", () => {
    expect(windowSpanWithin({ from: STEP, to: 3 * STEP, level: "spike" }, tss)).toEqual({ x1: STEP, x2: 2 * STEP });
  });

  it("clips a window that runs past the visible range to what is visible", () => {
    expect(windowSpanWithin({ from: 2 * STEP, to: 10 * STEP, level: "spike" }, tss)).toEqual({ x1: 2 * STEP, x2: 3 * STEP });
    expect(windowSpanWithin({ from: -5 * STEP, to: STEP, level: "spike" }, tss)).toEqual({ x1: 0, x2: 0 });
  });

  it("returns null when no chart timestamp falls inside the window", () => {
    expect(windowSpanWithin({ from: 10 * STEP, to: 12 * STEP, level: "spike" }, tss)).toBeNull();
    expect(windowSpanWithin({ from: 0, to: STEP, level: "spike" }, [])).toBeNull();
  });
});
