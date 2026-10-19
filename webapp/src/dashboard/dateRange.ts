import { BUCKET_STEP_MS } from "./historyChart";
import type { CostBucket, DateRange } from "./useHistoricalData";

export const DAY_MS = 24 * 60 * 60_000;

export function startOfDay(date: Date): Date {
  const start = new Date(date);
  start.setHours(0, 0, 0, 0);
  return start;
}

export function addDays(date: Date, days: number): Date {
  return new Date(date.getTime() + days * DAY_MS);
}

/** Local midnight to the next local midnight. */
export function todayRange(): DateRange {
  const from = startOfDay(new Date());
  return { from, to: addDays(from, 1) };
}

/** `zoomed` clipped to `day`, or the whole `day` when the zoom is unset or
 * lies outside it -- the Today chart can only show times inside today. */
export function clipToDay(zoomed: DateRange | null, day: DateRange): DateRange {
  if (!zoomed) {
    return day;
  }
  const from = Math.max(zoomed.from.getTime(), day.from.getTime());
  const to = Math.min(zoomed.to.getTime(), day.to.getTime());
  return from < to ? { from: new Date(from), to: new Date(to) } : day;
}

/** A drag selection covers its last bucket in full, so the zoom ends one
 * bucket after the last selected bucket's start. */
export function zoomRangeFor(fromTs: number, toTs: number, bucket: CostBucket): DateRange {
  return { from: new Date(fromTs), to: new Date(toTs + BUCKET_STEP_MS[bucket]) };
}
