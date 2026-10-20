import { useEffect, useState } from "react";
import { formatElapsed } from "./elapsedFormat";
import { useLatestEnergyTime } from "./useLiveReadings";

const TICK_MS = 1_000;

/** Re-renders every second and returns the current time, so the ages below tick up. */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, []);
  return now;
}

function clockTime(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function describeEnergyData(ts: string | null | undefined, isError: boolean, now: number): string {
  if (isError && ts === undefined) return "unavailable";
  if (ts === undefined) return "…";
  if (ts === null) return "none yet";
  return `changed ${formatElapsed(now - new Date(ts).getTime())} ago (${clockTime(ts)})`;
}

/**
 * A quiet line under the dashboard heading: how long ago the site's energy
 * data last changed (the newest reading, which is what the live tiles show),
 * and how long ago this page last fetched it. For checking whether the data
 * is stale, so it is small and muted on purpose.
 */
export function DataFreshness({ siteId }: { siteId: number }) {
  const { data, isError, dataUpdatedAt } = useLatestEnergyTime(siteId);
  const now = useNow();
  const refreshed = dataUpdatedAt > 0 ? ` · refreshed ${formatElapsed(now - dataUpdatedAt)} ago` : "";
  return (
    <p className="text-xs text-ink-muted/60">
      Energy data {describeEnergyData(data?.ts, isError, now)}
      {refreshed}
    </p>
  );
}
