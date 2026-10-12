import type { Reading } from "./useLiveReadings";

/** Amber's prices are already 5-minute intervals, fanned out by the backend
 * into one row per covered minute (see core-api's price ingestion) -- all
 * five rows in an interval carry the same value, so truncating to the
 * interval's start and keeping the first value seen is enough to dedupe back
 * down to one point per 5-minute interval, no averaging needed. */
function fiveMinBucketStart(ts: string): number {
  const date = new Date(ts);
  date.setUTCSeconds(0, 0);
  date.setUTCMinutes(Math.floor(date.getUTCMinutes() / 5) * 5);
  return date.getTime();
}

export interface PricePoint {
  ts: number;
  /** null outside the actual (elapsed) segment, so the two Line components
   * in the chart only draw their own halves. */
  actualCentsPerKwh: number | null;
  /** null outside the predicted (future) segment. Shares one point with
   * actualCentsPerKwh at the actual/predicted boundary so the two line
   * segments visually connect instead of leaving a gap. */
  predictedCentsPerKwh: number | null;
}

/** Builds one point per 5-minute interval, split into an actual (solid) and
 * predicted (dashed) segment purely by comparing each interval's timestamp to
 * `now` -- a reading timestamped later than now can only be forecast-origin,
 * since nothing else could have produced it yet (see useTodayPrices). */
export function buildPriceChartData(readings: Reading[], now: Date): PricePoint[] {
  const valueByBucket = new Map<number, number>();
  for (const reading of readings) {
    const key = fiveMinBucketStart(reading.ts);
    if (!valueByBucket.has(key)) {
      valueByBucket.set(key, reading.value);
    }
  }

  const sorted = Array.from(valueByBucket.entries()).sort((a, b) => a[0] - b[0]);
  const nowMs = now.getTime();
  let lastActualIndex = -1;
  for (let i = 0; i < sorted.length; i++) {
    if (sorted[i][0] <= nowMs) {
      lastActualIndex = i;
    }
  }

  return sorted.map(([ts, value], i) => ({
    ts,
    actualCentsPerKwh: i <= lastActualIndex ? value : null,
    predictedCentsPerKwh: i >= lastActualIndex ? value : null,
  }));
}

export interface TodayPricingPoint {
  ts: number;
  actualImportCentsPerKwh: number | null;
  predictedImportCentsPerKwh: number | null;
  actualExportCentsPerKwh: number | null;
  predictedExportCentsPerKwh: number | null;
}

/** Amber's feed-in (sell) price is stored negative in the normal case -- the
 * customer is paid to export -- so anywhere a price is shown as a plain rate
 * rather than a signed cost, it goes through this one place rather than each
 * call site re-deriving the same `Math.abs`. A no-op for the buy price,
 * which is already non-negative in the normal case. */
export function priceMagnitude(value: number): number;
export function priceMagnitude(value: number | null): number | null;
export function priceMagnitude(value: number | null): number | null {
  return value === null ? null : Math.abs(value);
}

/** Combines the import (buy) and export (sell) series into one per-bucket
 * dataset for a shared chart. Export values are shown as a plain positive
 * rate -- matching GridPriceTile's convention -- since Amber's feed-in price
 * is stored negative in the normal case (the customer is paid to export). */
export function mergeImportExportPrices(
  importPoints: PricePoint[],
  exportPoints: PricePoint[],
): TodayPricingPoint[] {
  const byTs = new Map<number, TodayPricingPoint>();

  function pointAt(ts: number): TodayPricingPoint {
    let point = byTs.get(ts);
    if (!point) {
      point = {
        ts,
        actualImportCentsPerKwh: null,
        predictedImportCentsPerKwh: null,
        actualExportCentsPerKwh: null,
        predictedExportCentsPerKwh: null,
      };
      byTs.set(ts, point);
    }
    return point;
  }

  for (const p of importPoints) {
    const point = pointAt(p.ts);
    point.actualImportCentsPerKwh = p.actualCentsPerKwh;
    point.predictedImportCentsPerKwh = p.predictedCentsPerKwh;
  }
  for (const p of exportPoints) {
    const point = pointAt(p.ts);
    point.actualExportCentsPerKwh = priceMagnitude(p.actualCentsPerKwh);
    point.predictedExportCentsPerKwh = priceMagnitude(p.predictedCentsPerKwh);
  }

  return Array.from(byTs.values()).sort((a, b) => a.ts - b.ts);
}
