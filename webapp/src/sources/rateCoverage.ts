import type { RateChannel, RateWindow } from "./useRateWindows";

const DAY_MINUTES = 24 * 60;
const CHANNELS: RateChannel[] = ["buy", "sell", "controlled_load"];

function toMinutes(time: string): number {
  const [hours, minutes] = time.split(":").map(Number);
  return hours * 60 + minutes;
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

function isCurrent(window: RateWindow, now: Date): boolean {
  return window.effective_to === null || new Date(window.effective_to) > now;
}

function channelHasGap(windows: RateWindow[]): boolean {
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
  return covered.includes(0);
}

/** True when a channel that has at least one current window leaves some month,
 * weekday and time of day uncovered. A channel with no windows is not a gap:
 * a tariff may have no controlled load or no feed-in. */
export function hasCoverageGap(windows: RateWindow[], now: Date = new Date()): boolean {
  const current = windows.filter((w) => isCurrent(w, now));
  return CHANNELS.some((channel) => {
    const forChannel = current.filter((w) => w.channel === channel);
    return forChannel.length > 0 && channelHasGap(forChannel);
  });
}
