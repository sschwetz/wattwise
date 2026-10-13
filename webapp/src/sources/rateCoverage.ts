import type { RateChannel, RateWindow } from "./useRateWindows";

const DAY_MINUTES = 24 * 60;
const CHANNELS: RateChannel[] = ["buy", "sell", "controlled_load"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export interface CoverageGap {
  channel: RateChannel;
  /** One uncovered slot, such as "Saturday 00:00–07:00 in January". */
  example: string;
}

function toMinutes(time: string): number {
  const [hours, minutes] = time.split(":").map(Number);
  return hours * 60 + minutes;
}

function formatMinute(minute: number): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(minute / 60))}:${pad(minute % 60)}`;
}

/** The minutes of the day a window covers: start included, end excluded,
 * an end before the start wraps past midnight, equal times mean all day. */
function minutesOfDay(window: RateWindow): number[] {
  const start = toMinutes(window.start_time);
  const end = toMinutes(window.end_time);
  const all = (from: number, to: number) => Array.from({ length: to - from }, (_, i) => from + i);
  if (start === end) return all(0, DAY_MINUTES);
  if (start < end) return all(start, end);
  return [...all(start, DAY_MINUTES), ...all(0, end)];
}

/** In effect at `now`: started, and not yet ended. */
function isCurrent(window: RateWindow, now: Date): boolean {
  const started = new Date(window.effective_from) <= now;
  return started && (window.effective_to === null || new Date(window.effective_to) > now);
}

/** One flag per minute, laid out month by month, then weekday, then minute of day. */
function coveredMinutes(windows: RateWindow[]): Uint8Array {
  const covered = new Uint8Array(12 * 7 * DAY_MINUTES);
  for (const window of windows) {
    const minutes = minutesOfDay(window);
    for (const month of window.months) {
      for (const day of window.days_of_week) {
        const base = ((month - 1) * 7 + day) * DAY_MINUTES;
        for (const minute of minutes) covered[base + minute] = 1;
      }
    }
  }
  return covered;
}

/** Describes the uncovered run that starts at `index`, up to the end of that day. */
function describeGap(covered: Uint8Array, index: number): string {
  const slot = Math.floor(index / DAY_MINUTES);
  const start = index % DAY_MINUTES;
  let end = start;
  while (end < DAY_MINUTES && covered[slot * DAY_MINUTES + end] === 0) end++;
  const times = start === 0 && end === DAY_MINUTES ? "all day" : `${formatMinute(start)}–${formatMinute(end % DAY_MINUTES)}`;
  return `${WEEKDAYS[slot % 7]} ${times} in ${MONTHS[Math.floor(slot / 7)]}`;
}

/** The directions whose current windows leave some month, weekday and time of
 * day uncovered, each with the first uncovered slot as an example. A direction
 * with no current windows is not a gap: a tariff may have no controlled load or
 * no feed-in. Windows that have ended or not started yet are ignored. */
export function findCoverageGaps(windows: RateWindow[], now: Date = new Date()): CoverageGap[] {
  const current = windows.filter((w) => isCurrent(w, now));
  return CHANNELS.flatMap((channel) => {
    const forChannel = current.filter((w) => w.channel === channel);
    if (forChannel.length === 0) return [];
    const covered = coveredMinutes(forChannel);
    const first = covered.indexOf(0);
    return first === -1 ? [] : [{ channel, example: describeGap(covered, first) }];
  });
}
