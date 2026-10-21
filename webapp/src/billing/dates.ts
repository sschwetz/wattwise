// Calendar dates ("YYYY-MM-DD") against UTC instants in a site's time zone.
// A start date is 00:00 local on that day. An end date is the last day an
// item applies, so it is stored as 00:00 local on the following day.

const MS_PER_DAY = 86_400_000;

function zoneParts(ms: number, timeZone: string): Record<string, number> {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    second: "numeric",
  }).formatToParts(new Date(ms));
  const out: Record<string, number> = {};
  for (const part of parts) if (part.type !== "literal") out[part.type] = Number(part.value);
  return out;
}

/** How far the zone's wall clock is ahead of UTC at this instant. */
function offsetMs(ms: number, timeZone: string): number {
  const p = zoneParts(ms, timeZone);
  return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - Math.floor(ms / 1000) * 1000;
}

function parseDate(date: string): number {
  const [year, month, day] = date.split("-").map(Number);
  return Date.UTC(year, month - 1, day);
}

function formatDate(utcMidnightMs: number): string {
  return new Date(utcMidnightMs).toISOString().slice(0, 10);
}

export function addDays(date: string, days: number): string {
  return formatDate(parseDate(date) + days * MS_PER_DAY);
}

export function startOfDayIso(date: string, timeZone: string): string {
  const wallClockMs = parseDate(date);
  // The offset at the first guess can differ from the offset at the answer
  // on a daylight-saving day, so take it twice.
  const first = wallClockMs - offsetMs(wallClockMs, timeZone);
  return new Date(wallClockMs - offsetMs(first, timeZone)).toISOString();
}

export function endOfDayIso(date: string, timeZone: string): string {
  return startOfDayIso(addDays(date, 1), timeZone);
}

export function localDate(iso: string, timeZone: string): string {
  const p = zoneParts(Date.parse(iso), timeZone);
  return formatDate(Date.UTC(p.year, p.month - 1, p.day));
}

/** The last day an item applies, for a stored effective_to. A value at local
 * midnight is the start of the next day. Any other time (an item ended at the
 * moment of a click) shows its own local date. */
export function displayEndDate(iso: string, timeZone: string): string {
  const day = localDate(iso, timeZone);
  return iso === startOfDayIso(day, timeZone) ? addDays(day, -1) : day;
}

/** The start date to prefill: the day after the latest end among `items`, or
 * `today` when none has ended. */
export function suggestStart(items: { effective_to: string | null }[], timeZone: string, today: string): string {
  const ends = items.flatMap((item) => (item.effective_to ? [item.effective_to] : []));
  if (ends.length === 0) return today;
  const latest = ends.reduce((a, b) => (Date.parse(a) >= Date.parse(b) ? a : b));
  return addDays(displayEndDate(latest, timeZone), 1);
}
