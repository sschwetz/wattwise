import type { TotalsBucket, TotalsRow } from "../dashboard/useHistoricalData";

const pad = (n: number) => String(n).padStart(2, "0");

/** A row's time in the device's timezone: the date, the hour or the minute. */
export function formatRowTime(row: TotalsRow, bucket: TotalsBucket): string {
  const start = new Date(row.start);
  if (bucket === "day") {
    return row.date;
  }
  return bucket === "hour" ? `${pad(start.getHours())}:00` : `${pad(start.getHours())}:${pad(start.getMinutes())}`;
}
