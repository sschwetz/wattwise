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

/** A price reading as /prices returns it. `forecast_fallback` is true when
 * the reading sits in a forecast interval that arrived without Amber's
 * advancedPrice band, so its value is the plain perKwh forecast. */
export interface PriceReading extends Reading {
  forecast_fallback?: boolean;
  /** Amber's advancedPrice band for the reading's interval (raw, not adjusted
   * like `value` on the sell channel) and its spikeStatus. Null or absent for
   * settled intervals and fallback forecasts. */
  price_low?: number | null;
  price_high?: number | null;
  spike_status?: SpikeStatus;
}

export type SpikeStatus = "none" | "potential" | "spike";

/** A forecast band wider than this many c/kWh counts as unstable. A starting
 * value: on a normal day about 5% of general-channel bands are wider. */
export const BAND_WIDTH_THRESHOLD_CENTS = 20;

/** [min, max] of the band. Amber's low and high are ordered by confidence, not
 * by number, so on the export channel low can sit above high. */
function orderedBand(low: number | null | undefined, high: number | null | undefined): [number, number] | null {
  if (low == null || high == null) return null;
  return [Math.min(low, high), Math.max(low, high)];
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
  /** The fallback segment: future intervals priced from perKwh because Amber
   * sent no advancedPrice. Null elsewhere, except the last predicted point
   * before a fallback run, which both series share so the dotted segment
   * connects to the dashed one. */
  fallbackCentsPerKwh: number | null;
  /** The forecast's low-to-high band, ordered min then max; null when the
   * interval has none (settled intervals, fallback forecasts). */
  band: [number, number] | null;
  spikeStatus: SpikeStatus;
}

interface BucketValue {
  value: number;
  isFallback: boolean;
  band: [number, number] | null;
  spikeStatus: SpikeStatus;
}

/** Builds one point per 5-minute interval, split into an actual (solid) and
 * predicted (dashed) segment purely by comparing each interval's timestamp to
 * `now` -- a reading timestamped later than now can only be forecast-origin,
 * since nothing else could have produced it yet (see useTodayPrices).
 * Intervals the backend flags as fallback go on a third (dotted) segment. */
export function buildPriceChartData(readings: PriceReading[], now: Date): PricePoint[] {
  const valueByBucket = new Map<number, BucketValue>();
  for (const reading of readings) {
    const key = fiveMinBucketStart(reading.ts);
    if (!valueByBucket.has(key)) {
      valueByBucket.set(key, {
        value: reading.value,
        isFallback: reading.forecast_fallback === true,
        band: orderedBand(reading.price_low, reading.price_high),
        spikeStatus: reading.spike_status ?? "none",
      });
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

  return sorted.map(([ts, { value, isFallback, band, spikeStatus }], i) => {
    const nextIsFallback = sorted[i + 1]?.[1].isFallback === true;
    return {
      ts,
      actualCentsPerKwh: i <= lastActualIndex ? value : null,
      predictedCentsPerKwh: i >= lastActualIndex && !isFallback ? value : null,
      fallbackCentsPerKwh: isFallback || (nextIsFallback && i >= lastActualIndex) ? value : null,
      band,
      spikeStatus,
    };
  });
}

export interface TodayPricingPoint {
  ts: number;
  actualImportCentsPerKwh: number | null;
  predictedImportCentsPerKwh: number | null;
  fallbackImportCentsPerKwh: number | null;
  actualExportCentsPerKwh: number | null;
  predictedExportCentsPerKwh: number | null;
  fallbackExportCentsPerKwh: number | null;
  /** The import (buy) channel's forecast band and spike flag. Export has none:
   * its line is adjusted for incentives, so a raw band would not match it. */
  importBand: [number, number] | null;
  importSpikeStatus: SpikeStatus;
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
        fallbackImportCentsPerKwh: null,
        actualExportCentsPerKwh: null,
        predictedExportCentsPerKwh: null,
        fallbackExportCentsPerKwh: null,
        importBand: null,
        importSpikeStatus: "none",
      };
      byTs.set(ts, point);
    }
    return point;
  }

  for (const p of importPoints) {
    const point = pointAt(p.ts);
    point.actualImportCentsPerKwh = p.actualCentsPerKwh;
    point.predictedImportCentsPerKwh = p.predictedCentsPerKwh;
    point.fallbackImportCentsPerKwh = p.fallbackCentsPerKwh;
    point.importBand = p.band;
    point.importSpikeStatus = p.spikeStatus;
  }
  for (const p of exportPoints) {
    const point = pointAt(p.ts);
    point.actualExportCentsPerKwh = priceMagnitude(p.actualCentsPerKwh);
    point.predictedExportCentsPerKwh = priceMagnitude(p.predictedCentsPerKwh);
    point.fallbackExportCentsPerKwh = priceMagnitude(p.fallbackCentsPerKwh);
  }

  return Array.from(byTs.values()).sort((a, b) => a.ts - b.ts);
}

export type InstabilityLevel = "spike" | "potential" | "wide-band";

export interface InstabilityWindow {
  from: number;
  to: number;
  /** The worst level among the points in the window. */
  level: InstabilityLevel;
}

const FIVE_MINUTES_MS = 5 * 60 * 1000;
const LEVEL_SEVERITY: Record<InstabilityLevel, number> = { "wide-band": 0, potential: 1, spike: 2 };

function instabilityLevel(point: TodayPricingPoint): InstabilityLevel | null {
  if (point.importSpikeStatus !== "none") return point.importSpikeStatus;
  const band = point.importBand;
  return band !== null && band[1] - band[0] > BAND_WIDTH_THRESHOLD_CENTS ? "wide-band" : null;
}

/** Runs of consecutive unstable forecast points on the import channel, merged
 * into windows. A point is unstable when Amber flags it (potential or spike)
 * or its forecast band is wider than BAND_WIDTH_THRESHOLD_CENTS. Points that
 * already have an actual price are settled and skipped. A flagged point that
 * does not start where the previous window ends begins a new window. Expects
 * `points` sorted by ts. */
export function findInstabilityWindows(points: TodayPricingPoint[], intervalMs = FIVE_MINUTES_MS): InstabilityWindow[] {
  const windows: InstabilityWindow[] = [];
  for (const point of points) {
    const level = point.actualImportCentsPerKwh === null ? instabilityLevel(point) : null;
    if (level === null) continue;
    const last = windows[windows.length - 1];
    if (last && point.ts <= last.to) {
      last.to = point.ts + intervalMs;
      if (LEVEL_SEVERITY[level] > LEVEL_SEVERITY[last.level]) last.level = level;
    } else {
      windows.push({ from: point.ts, to: point.ts + intervalMs, level });
    }
  }
  return windows;
}
