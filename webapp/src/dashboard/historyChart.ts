import type { CostBucket, CostSeriesPoint, DateRange } from "./useHistoricalData";
import type { Reading } from "./useLiveReadings";

/** One minute at N watts is N/60000 kWh -- matches core_api.cost.MINUTES_TO_KWH. */
const MINUTES_TO_KWH = 1 / 60_000;

const MINUTE_MS = 60 * 1000;
const HOUR_MS = 60 * MINUTE_MS;

export const BUCKET_STEP_MS: Record<CostBucket, number> = {
  "5min": 5 * MINUTE_MS,
  "15min": 15 * MINUTE_MS,
  hour: HOUR_MS,
  "6hour": 6 * HOUR_MS,
};

/** Truncates a reading/bucket timestamp down to the start of its bucket, in UTC
 * to match the backend's bucketing in core_api.cost.compute_cost_series. */
export function bucketStartMs(ts: string, bucket: CostBucket): number {
  return bucketStartFromMs(new Date(ts).getTime(), bucket);
}

export function bucketStartFromMs(ms: number, bucket: CostBucket): number {
  const date = new Date(ms);
  if (bucket === "5min") {
    date.setUTCMinutes(Math.floor(date.getUTCMinutes() / 5) * 5, 0, 0);
  } else if (bucket === "15min") {
    date.setUTCMinutes(Math.floor(date.getUTCMinutes() / 15) * 15, 0, 0);
  } else if (bucket === "hour") {
    date.setUTCMinutes(0, 0, 0);
  } else {
    date.setUTCHours(Math.floor(date.getUTCHours() / 6) * 6, 0, 0, 0);
  }
  return date.getTime();
}

/** Every bucket start in [range.from, range.to), including ones with no data yet
 * (e.g. the rest of today) -- those are zero-filled by buildHistoryChartData rather
 * than omitted, so the chart always shows the full requested span. */
export function enumerateBucketStarts(range: DateRange, bucket: CostBucket): number[] {
  const step = BUCKET_STEP_MS[bucket];
  const toMs = range.to.getTime();
  const starts: number[] = [];
  for (let current = bucketStartFromMs(range.from.getTime(), bucket); current < toMs; current += step) {
    starts.push(current);
  }
  return starts;
}

export interface HistoryChartPoint {
  ts: number;
  gridKwh: number;
  costDollars: number;
}

/** Builds one signed grid-flow bar per bucket -- positive while importing,
 * negative while exporting -- matching the cost line's same sign convention
 * (+ = buying, - = selling), plus the cost trend in dollars. Every bucket in
 * `range` is included even with no data (zero-filled), so e.g. a "Today" range
 * still shows the rest of the day's not-yet-happened hours as zero. */
export function buildHistoryChartData(
  importReadings: Reading[],
  exportReadings: Reading[],
  costSeries: CostSeriesPoint[],
  range: DateRange,
  bucket: CostBucket,
): HistoryChartPoint[] {
  const gridKwhByBucket = new Map<number, number>();
  for (const reading of importReadings) {
    const key = bucketStartMs(reading.ts, bucket);
    gridKwhByBucket.set(key, (gridKwhByBucket.get(key) ?? 0) + reading.value * MINUTES_TO_KWH);
  }
  for (const reading of exportReadings) {
    const key = bucketStartMs(reading.ts, bucket);
    gridKwhByBucket.set(key, (gridKwhByBucket.get(key) ?? 0) - reading.value * MINUTES_TO_KWH);
  }

  const costDollarsByBucket = new Map<number, number>();
  for (const point of costSeries) {
    const key = bucketStartMs(point.bucket_start, bucket);
    costDollarsByBucket.set(key, (costDollarsByBucket.get(key) ?? 0) + point.cost_c / 100);
  }

  return enumerateBucketStarts(range, bucket).map((ts) => ({
    ts,
    gridKwh: gridKwhByBucket.get(ts) ?? 0,
    costDollars: costDollarsByBucket.get(ts) ?? 0,
  }));
}

/** Full, precise label for the tooltip (as opposed to the sparser axis ticks
 * in buildAxisTickPlan below) -- always 24-hour time, never AM/PM. 5- and 15-min
 * buckets only ever cover short (<7 day) spans, so time-of-day alone is
 * unambiguous there; hour and 6-hour buckets cover multi-day spans, so the
 * date is included too. */
export function formatBucketTick(ts: number, bucket: CostBucket): string {
  const date = new Date(ts);
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  if (bucket === "5min" || bucket === "15min") {
    return time;
  }
  return `${date.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

const WEEKDAY_ABBREV = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const AXIS_DAY_MS = 24 * HOUR_MS;

function padTime(ts: number): string {
  const date = new Date(ts);
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

/** Picks roughly `target` evenly-spaced ticks out of `allTs` rather than one
 * per data point -- the fix for "the x axis is too busy". */
function thinnedTicks(allTs: number[], target: number): number[] {
  if (allTs.length <= target) {
    return allTs;
  }
  const step = Math.ceil(allTs.length / target);
  const picked: number[] = [];
  for (let i = 0; i < allTs.length; i += step) {
    picked.push(allTs[i]);
  }
  return picked;
}

export interface AxisTickPlan {
  ticks: number[];
  formatTick: (ts: number) => string;
}

/**
 * X-axis ticks and labels, chosen by how much time the chart actually spans --
 * not by bucket size -- so the axis reads sensibly at every zoom level:
 *  - <=24h: a handful of 24-hour times (e.g. "14:30"), thinned to stay readable.
 *  - 24h-7d: one labeled tick per local midnight (weekday abbreviation, e.g.
 *    "Mon") plus an unlabeled minor tick at each local noon for rhythm.
 *  - >7d: one tick per local calendar day (whichever bucket lands closest to
 *    that day's local midnight -- 6-hour UTC buckets rarely land on it
 *    exactly once the viewer's UTC offset isn't a multiple of 6 hours),
 *    labeled with the date and thinned if there would be too many days.
 * All local-time-of-day/day-boundary checks use the viewer's own timezone
 * (plain Date getters, not the UTC ones bucketStartFromMs uses to match the
 * backend) because these are for display, not for keying data.
 *
 * Takes any array of `{ ts }` points, not specifically HistoryChartPoint --
 * shared with the today-pricing chart in priceChart.ts, which has its own
 * point shape but the same axis needs.
 */
export function buildAxisTickPlan(points: { ts: number }[], range: DateRange): AxisTickPlan {
  const allTs = points.map((point) => point.ts);
  const spanMs = range.to.getTime() - range.from.getTime();

  if (spanMs <= AXIS_DAY_MS) {
    return { ticks: thinnedTicks(allTs, 8), formatTick: padTime };
  }

  if (spanMs <= 7 * AXIS_DAY_MS) {
    const midnights: number[] = [];
    const noons = new Set<number>();
    for (const ts of allTs) {
      const date = new Date(ts);
      if (date.getHours() === 0 && date.getMinutes() === 0) {
        midnights.push(ts);
      } else if (date.getHours() === 12 && date.getMinutes() === 0) {
        noons.add(ts);
      }
    }
    return {
      ticks: [...midnights, ...noons].sort((a, b) => a - b),
      formatTick: (ts) => (noons.has(ts) ? "" : WEEKDAY_ABBREV[new Date(ts).getDay()]),
    };
  }

  const closestToMidnightPerDay = new Map<string, { ts: number; diffMs: number }>();
  for (const ts of allTs) {
    const date = new Date(ts);
    const localMidnightMs = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
    const dayKey = String(localMidnightMs);
    const diffMs = Math.abs(ts - localMidnightMs);
    const existing = closestToMidnightPerDay.get(dayKey);
    if (!existing || diffMs < existing.diffMs) {
      closestToMidnightPerDay.set(dayKey, { ts, diffMs });
    }
  }
  const dailyTicks = Array.from(closestToMidnightPerDay.values())
    .map((entry) => entry.ts)
    .sort((a, b) => a - b);
  return {
    ticks: thinnedTicks(dailyTicks, 10),
    formatTick: (ts) => new Date(ts).toLocaleDateString([], { month: "short", day: "numeric" }),
  };
}

export function formatTooltipValue(value: number, name: string): [string, string] {
  if (name !== "Grid") {
    // Cost's sign is conveyed by color (red = charge, green = credit) rather
    // than a minus sign -- same reasoning as dropping it on the Grid row below.
    return [`$${Math.abs(value).toFixed(2)}`, name];
  }
  const direction = value < 0 ? "exported" : "imported";
  return [`${Math.abs(value).toFixed(2)} kWh ${direction}`, name];
}

const COST_STEPS_DOLLARS = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100];
const MAX_COST_INTERVALS = 5;

const roundToCents = (v: number) => Math.round(v * 100) / 100;

/** Cost axis ticks on whole multiples of a step that is never below 1c. */
export function buildCostAxis(points: HistoryChartPoint[]): { ticks: number[]; domain: [number, number] } {
  const values = points.map((p) => p.costDollars);
  const rawMin = Math.min(0, ...values);
  const rawMax = Math.max(0, ...values);
  const step =
    COST_STEPS_DOLLARS.find((s) => (rawMax - rawMin) / s <= MAX_COST_INTERVALS) ??
    COST_STEPS_DOLLARS[COST_STEPS_DOLLARS.length - 1];
  // Round to cents so float error doesn't push a tick off a whole multiple.
  const lo = roundToCents(Math.floor(rawMin / step + 1e-9) * step);
  const hi = Math.max(roundToCents(Math.ceil(rawMax / step - 1e-9) * step), lo + step);
  const ticks: number[] = [];
  for (let i = 0; lo + i * step <= hi + 1e-9; i++) {
    ticks.push(roundToCents(lo + i * step));
  }
  return { ticks, domain: [lo, hi] };
}
