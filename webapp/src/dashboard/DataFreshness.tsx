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

function describeEnergyData(ts: string | null | undefined, isError: boolean, now: number, live: boolean): string {
  if (isError && ts === undefined) return "unavailable";
  if (ts === undefined) return "…";
  if (ts === null) return live ? "has no data yet" : "none yet";
  return `${live ? "last updated" : "changed"} ${formatElapsed(now - new Date(ts).getTime())} ago (${clockTime(ts)})`;
}

/**
 * A quiet line under the dashboard heading: how long ago the site's energy
 * data changed (the newest reading) and how long ago this page last fetched
 * it. While live data is flowing (`liveReceivedAt` is set) it instead shows
 * when the database was last updated and when the last live value arrived.
 * For checking whether the data is stale, so it is small and muted on purpose.
 */
export function DataFreshness({ siteId, liveReceivedAt }: { siteId: number; liveReceivedAt: number | null }) {
  const { data, isError, dataUpdatedAt } = useLatestEnergyTime(siteId);
  const now = useNow();
  if (liveReceivedAt !== null) {
    return (
      <p className="text-xs text-ink-muted/60">
        Energy DB {describeEnergyData(data?.ts, isError, now, true)} · live data received {formatElapsed(now - liveReceivedAt)} ago
      </p>
    );
  }
  const refreshed = dataUpdatedAt > 0 ? ` · refreshed ${formatElapsed(now - dataUpdatedAt)} ago` : "";
  return (
    <p className="text-xs text-ink-muted/60">
      Energy data {describeEnergyData(data?.ts, isError, now, false)}
      {refreshed}
    </p>
  );
}
