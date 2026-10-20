import type { InstabilityWindow } from "./priceChart";

function clock(ts: number, timeZone: string | undefined): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false, timeZone });
}

/**
 * One quiet line above the pricing chart naming when the forecast looks
 * unreliable today. Renders nothing on a normal day. Times are in the viewer's
 * time zone, like the rest of the dashboard, unless `timeZone` is given.
 */
export function InstabilitySummary({ windows, timeZone }: { windows: InstabilityWindow[]; timeZone?: string }) {
  if (windows.length === 0) return null;
  const ranges = windows.map((w) => `${clock(w.from, timeZone)} to ${clock(w.to, timeZone)}`);
  return <p className="mb-2 text-xs text-ink-muted">Price instability expected {ranges.join(" and ")}</p>;
}
