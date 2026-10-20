import type { Reading } from "./useLiveReadings";

export const AVERAGE_BUCKET_MS = 5 * 60_000;

/** Live tiles that show a 5-minute average instead of the latest reading. */
export const AVERAGED_METRICS: ReadonlySet<string> = new Set([
  "production_w",
  "consumption_w",
  "battery_discharge_w",
  "grid_import_w",
]);

/**
 * Replaces the latest reading of each averaged metric with its running mean
 * over the current clock-aligned 5-minute bucket (e.g. at 0:07 the mean covers
 * 0:05 to now; at 0:10 it restarts). Other metrics pass through unchanged.
 *
 * The collector emits only one of import/export (and charge/discharge) per
 * poll, so a metric is missing from cycles where the other side was active.
 * Those cycles count as 0 W: the divisor is the number of distinct poll
 * timestamps in the bucket, not the number of rows for that metric.
 *
 * When the bucket has no row for a metric (for example just after the bucket
 * rolls over, before the next poll), that metric keeps its raw latest reading. The averaged reading keeps the
 * raw latest timestamp so the import/export and charge/discharge collapsing
 * still picks whichever side is newer.
 */
export function averageLiveReadings(readings: Reading[], now: number): Reading[] {
  const bucketStart = Math.floor(now / AVERAGE_BUCKET_MS) * AVERAGE_BUCKET_MS;

  const pollTimes = new Set<string>();
  const sums = new Map<string, number>();
  const hasBucketRow = new Set<string>();
  for (const reading of readings) {
    const time = Date.parse(reading.ts);
    if (time < bucketStart || time > now) {
      continue;
    }
    pollTimes.add(reading.ts);
    if (AVERAGED_METRICS.has(reading.metric)) {
      sums.set(reading.metric, (sums.get(reading.metric) ?? 0) + reading.value);
      hasBucketRow.add(reading.metric);
    }
  }

  const latestAveraged = new Map<string, Reading>();
  const passthrough: Reading[] = [];
  for (const reading of readings) {
    if (!AVERAGED_METRICS.has(reading.metric)) {
      passthrough.push(reading);
      continue;
    }
    const existing = latestAveraged.get(reading.metric);
    if (!existing || reading.ts > existing.ts) {
      latestAveraged.set(reading.metric, reading);
    }
  }

  const averaged = Array.from(latestAveraged.values()).map((latest) =>
    hasBucketRow.has(latest.metric)
      ? { ...latest, value: (sums.get(latest.metric) ?? 0) / pollTimes.size }
      : latest,
  );
  return [...passthrough, ...averaged];
}
