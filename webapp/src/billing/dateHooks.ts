import { useState } from "react";
import { useSites } from "../sites/useSites";
import { endOfDayIso, localDate, startOfDayIso, suggestStart } from "./dates";

export interface DateRangeValue {
  start: string;
  end: string;
  onStartChange: (value: string) => void;
  onEndChange: (value: string) => void;
}

/** The site's time zone, or the browser's while the site list is loading. */
export function useSiteTimeZone(siteId: number): string {
  const { data } = useSites();
  const site = Array.isArray(data) ? data.find((s) => s.id === siteId) : undefined;
  return site?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
}

export function todayIn(timeZone: string): string {
  return localDate(new Date().toISOString(), timeZone);
}

/** The state behind DateRangeFields. The start date follows the day after the
 * latest end among `existing` items until the user types one. `toInput` gives
 * the API fields for the chosen dates. */
export function useDateRange(existing: { effective_to: string | null }[] | undefined, siteId: number) {
  const timeZone = useSiteTimeZone(siteId);
  const [typedStart, setStart] = useState<string | null>(null);
  const [end, setEnd] = useState("");
  const start = typedStart ?? suggestStart(existing ?? [], timeZone, todayIn(timeZone));
  const range: DateRangeValue = { start, end, onStartChange: setStart, onEndChange: setEnd };
  const toInput = () => ({
    effective_from: startOfDayIso(start, timeZone),
    ...(end ? { effective_to: endOfDayIso(end, timeZone) } : {}),
  });
  return { range, toInput };
}
