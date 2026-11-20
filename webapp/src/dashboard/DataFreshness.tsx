import { useEffect, useState } from "react";
import { formatElapsed } from "./elapsedFormat";
import { type LatestReadingTime, useLatestEnergyTime, useLatestWeatherTime } from "./useLiveReadings";

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

function describeData(ts: string | null | undefined, isError: boolean, now: number, live: boolean): string {
  if (isError && ts === undefined) return "unavailable";
  if (ts === undefined) return "…";
  if (ts === null) return live ? "has no data yet" : "none yet";
  return `${live ? "last updated" : "changed"} ${formatElapsed(now - new Date(ts).getTime())} ago (${clockTime(ts)})`;
}

interface Latest {
  data: LatestReadingTime | undefined;
  isError: boolean;
  dataUpdatedAt: number;
}

/**
 * One line for one kind of data: how long ago it changed (the newest reading)
 * and how long ago this page last fetched it. While live data is flowing
 * (`liveReceivedAt` is set) it instead shows when the database was last
 * updated and when the last live value arrived.
 */
function FreshnessLine({
  label,
  latest,
  liveReceivedAt,
  now,
}: {
  label: string;
  latest: Latest;
  liveReceivedAt: number | null;
  now: number;
}) {
  const { data, isError, dataUpdatedAt } = latest;
  if (liveReceivedAt !== null) {
    return (
      <p className="text-xs text-ink-muted/60">
        {label} DB {describeData(data?.ts, isError, now, true)} · live data received {formatElapsed(now - liveReceivedAt)} ago
      </p>
    );
  }
  const refreshed = dataUpdatedAt > 0 ? ` · refreshed ${formatElapsed(now - dataUpdatedAt)} ago` : "";
  return (
    <p className="text-xs text-ink-muted/60">
      {label} data {describeData(data?.ts, isError, now, false)}
      {refreshed}
    </p>
  );
}

/**
 * Quiet lines under the dashboard heading, one for the site's energy data and
 * one for its weather data, for checking whether either is stale. Each
 * switches to its live form on its own when its live values are flowing, so
 * they are small and muted on purpose.
 */
export function DataFreshness({
  siteId,
  energyReceivedAt,
  weatherReceivedAt,
}: {
  siteId: number;
  energyReceivedAt: number | null;
  weatherReceivedAt: number | null;
}) {
  const energy = useLatestEnergyTime(siteId);
  const weather = useLatestWeatherTime(siteId);
  const now = useNow();
  return (
    <>
      <FreshnessLine label="Energy" latest={energy} liveReceivedAt={energyReceivedAt} now={now} />
      <FreshnessLine label="Weather" latest={weather} liveReceivedAt={weatherReceivedAt} now={now} />
    </>
  );
}
