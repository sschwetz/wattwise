import type { DateRange, TotalsBucket } from "../dashboard/useHistoricalData";

/** Where the Reporting page is drilled to. Kept in the URL so the back arrow
 * and the browser's back button both return to the level above. */
export interface ReportingView {
  /** "YYYY-MM"; null for the current billing period. */
  month: string | null;
  /** "YYYY-MM-DD"; null for the day list. */
  day: string | null;
  /** 0 to 23; null for the hour list. Only meaningful with a day. */
  hour: number | null;
}

const MONTH = /^(\d{4})-(\d{2})$/;
const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

export function parseView(params: URLSearchParams): ReportingView {
  const month = params.get("month");
  const day = params.get("day");
  const hour = Number(params.get("hour"));
  const validDay = day !== null && DAY.test(day);
  return {
    month: month !== null && MONTH.test(month) ? month : null,
    day: validDay ? day : null,
    hour: validDay && params.get("hour") !== null && Number.isInteger(hour) && hour >= 0 && hour < 24 ? hour : null,
  };
}

export function viewSearch(view: ReportingView): string {
  const params = new URLSearchParams();
  if (view.month) {
    params.set("month", view.month);
  }
  if (view.day) {
    params.set("day", view.day);
    if (view.hour !== null) {
      params.set("hour", String(view.hour));
    }
  }
  return params.toString();
}

/** What each row of the view's table covers. */
export function bucketOf(view: ReportingView): TotalsBucket {
  if (view.day === null) {
    return "day";
  }
  return view.hour === null ? "hour" : "minute";
}

/** The view one level up. */
export function parentView(view: ReportingView): ReportingView {
  return view.hour !== null ? { ...view, hour: null } : { ...view, day: null, hour: null };
}

export function monthKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

export function dayDate(day: string): Date {
  const [, year, month, date] = DAY.exec(day)!;
  return new Date(Number(year), Number(month) - 1, Number(date));
}

export function monthDate(month: string): Date {
  const [, year, number] = MONTH.exec(month)!;
  return new Date(Number(year), Number(number) - 1, 1);
}

function calendarMonth(date: Date): DateRange {
  return { from: new Date(date.getFullYear(), date.getMonth(), 1), to: new Date(date.getFullYear(), date.getMonth() + 1, 1) };
}

function fullRange(view: ReportingView, billingRange: DateRange | null, now: Date): DateRange {
  if (view.day !== null) {
    const day = dayDate(view.day);
    const [year, month, date] = [day.getFullYear(), day.getMonth(), day.getDate()];
    return view.hour === null
      ? { from: day, to: new Date(year, month, date + 1) }
      : { from: new Date(year, month, date, view.hour), to: new Date(year, month, date, view.hour + 1) };
  }
  if (view.month !== null) {
    return calendarMonth(monthDate(view.month));
  }
  return billingRange ?? calendarMonth(now);
}

/** The end of the bucket `now` falls in, so rows never reach into the future. */
function endOfCurrent(bucket: TotalsBucket, now: Date): Date {
  const [year, month, date, hour, minute] = [now.getFullYear(), now.getMonth(), now.getDate(), now.getHours(), now.getMinutes()];
  if (bucket === "day") {
    return new Date(year, month, date + 1);
  }
  return bucket === "hour" ? new Date(year, month, date, hour + 1) : new Date(year, month, date, hour, minute + 1);
}

/** The time span the view lists: the day list covers the picked month, or the
 * billing period when none is picked (the current calendar month when the
 * site has no billing cycle). */
export function rangeForView(view: ReportingView, billingRange: DateRange | null, now: Date): DateRange {
  const full = fullRange(view, billingRange, now);
  const cap = endOfCurrent(bucketOf(view), now);
  return { from: full.from, to: full.to.getTime() > cap.getTime() ? cap : full.to };
}
