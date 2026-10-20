const SECOND_MS = 1_000;
const MINUTE_MS = 60 * SECOND_MS;
const HOUR_MS = 60 * MINUTE_MS;

/** "30 sec", "2 min", "1 hour", "3 hours": the elapsed time in its largest
 * whole unit, rounded down. A negative span (the clock stepped backwards)
 * shows as 0 sec. */
export function formatElapsed(ms: number): string {
  const elapsed = Math.max(0, ms);
  if (elapsed < MINUTE_MS) {
    return `${Math.floor(elapsed / SECOND_MS)} sec`;
  }
  if (elapsed < HOUR_MS) {
    return `${Math.floor(elapsed / MINUTE_MS)} min`;
  }
  const hours = Math.floor(elapsed / HOUR_MS);
  return `${hours} ${hours === 1 ? "hour" : "hours"}`;
}
