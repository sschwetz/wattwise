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
  return dayRangeOffset(0);
}

/** The local calendar day `offset` days from today (-1 yesterday, 1 tomorrow). */
export function dayRangeOffset(offset: number, now = new Date()): DateRange {
  const from = startOfDay(now);
  from.setDate(from.getDate() + offset);
  const to = new Date(from);
  to.setDate(to.getDate() + 1);
  return { from, to };
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

/** Tomorrow's prices appear from this local hour (8pm). */
const TOMORROW_FROM_HOUR = 20;
/** Yesterday's prices stay until this local hour (4am). */
const YESTERDAY_UNTIL_HOUR = 4;

/** Which day a pricing chart shows: yesterday, today or tomorrow. */
export type PricingDayOffset = -1 | 0 | 1;

/** Which pricing days to show at `now`: yesterday until 4am, today always,
 * tomorrow from 8pm. */
export function pricingDayOffsets(now: Date): PricingDayOffset[] {
  const hour = now.getHours();
  return [...(hour < YESTERDAY_UNTIL_HOUR ? [-1 as const] : []), 0, ...(hour >= TOMORROW_FROM_HOUR ? [1 as const] : [])];
}
