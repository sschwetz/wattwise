import type { CostBucket, DateRange } from "./useHistoricalData";
import { addDays, DAY_MS, startOfDay, todayRange } from "./dateRange";

export type RangeMode = "today" | "yesterday" | "7d" | "30d" | "billing" | "custom";

export const RANGE_MODE_LABELS: Record<RangeMode, string> = {
  today: "Today",
  yesterday: "Yesterday",
  "7d": "7 days",
  "30d": "30 days",
  billing: "Billing period",
  custom: "Custom range",
};

/** Today/7 days/30 days all start at local midnight and run through the end of
 * today (even the part that hasn't happened yet) rather than a rolling N*24h
 * window, so a day's bars/line always cover the same fixed calendar span no
 * matter what time "now" is -- the not-yet-happened part reads as zero.
 * Yesterday is the one fully-elapsed exception: it stops at today's midnight
 * rather than running into the future. Billing period is the site's current
 * cycle, start to end, as reported by core-api. */
export function rangeForMode(mode: RangeMode, custom: DateRange, billing: DateRange | null | undefined): DateRange {
  if (mode === "custom") {
    return custom;
  }
  if (mode === "billing") {
    // Falls back to today while the period bounds load or if no cycle is active.
    return billing ?? todayRange();
  }
  if (mode === "yesterday") {
    const from = addDays(startOfDay(new Date()), -1);
    return { from, to: addDays(from, 1) };
  }
  const daysBack = mode === "today" ? 0 : mode === "7d" ? 6 : 29;
  const to = addDays(startOfDay(new Date()), 1);
  const from = addDays(to, -1 - daysBack);
  return { from, to };
}

const SEVEN_DAYS_MS = 7 * DAY_MS;
const THIRTY_DAYS_MS = 30 * DAY_MS;

/** Finer resolution for shorter spans, coarser for longer ones, so the chart
 * stays both detailed and readable at every zoom level. Anything up to a day
 * (including a zoomed-in selection) is always 5-minute intervals. */
export function bucketForRange(range: DateRange): CostBucket {
  const durationMs = range.to.getTime() - range.from.getTime();
  if (durationMs <= DAY_MS) {
    return "5min";
  }
  if (durationMs < SEVEN_DAYS_MS) {
    return "15min";
  }
  if (durationMs < THIRTY_DAYS_MS) {
    return "hour";
  }
  return "6hour";
}
