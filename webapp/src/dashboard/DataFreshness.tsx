import { useEffect, useState } from "react";
import { formatElapsed } from "./elapsedFormat";
import { useLatestEnergyTime } from "./useLiveReadings";

const TICK_MS = 1_000;

function useElapsedSincePageLoad(): number {
  const [loadedAt] = useState(() => Date.now());
  const [now, setNow] = useState(loadedAt);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, []);
  return now - loadedAt;
}

function describeEnergyTime(ts: string | null | undefined, isError: boolean): string {
  if (isError) return "unavailable";
  if (ts === undefined) return "…";
  if (ts === null) return "none yet";
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

/**
 * A quiet line under the dashboard heading: when the site's energy collectors
 * last wrote a reading, and how long this page has been loaded. For checking
 * whether the data is stale, so it is small and muted on purpose.
 */
export function DataFreshness({ siteId }: { siteId: number }) {
  const { data, isError } = useLatestEnergyTime(siteId);
  const elapsed = useElapsedSincePageLoad();
  return (
    <p className="text-xs text-ink-muted/60">
      Energy data {describeEnergyTime(data?.ts, isError)} · page loaded {formatElapsed(elapsed)} ago
    </p>
  );
}
