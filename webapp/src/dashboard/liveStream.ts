import type { Reading } from "./useLiveReadings";

/** A live value stops counting as current this long after it arrived. */
export const LIVE_FRESH_MS = 15_000;

export interface LiveMessage {
  kind: "energy" | "weather";
  ts: string;
  values: Record<string, number>;
}

export interface LiveValue {
  value: number;
  ts: string;
  receivedAt: number;
}

export type LiveValues = ReadonlyMap<string, LiveValue>;

export function parseLiveMessage(data: unknown): LiveMessage | null {
  if (typeof data !== "object" || data === null || Array.isArray(data)) return null;
  const { kind, ts, values } = data as Record<string, unknown>;
  if (kind !== "energy" && kind !== "weather") return null;
  if (typeof ts !== "string") return null;
  if (typeof values !== "object" || values === null || Array.isArray(values)) return null;
  const entries = Object.entries(values);
  if (!entries.every(([, v]) => typeof v === "number")) return null;
  return { kind, ts, values: Object.fromEntries(entries) as Record<string, number> };
}

export function applyLiveMessage(current: LiveValues, message: LiveMessage, receivedAt: number): LiveValues {
  const next = new Map(current);
  for (const [metric, value] of Object.entries(message.values)) {
    const held = current.get(metric);
    // ISO timestamps from the server compare correctly as strings.
    if (held && message.ts < held.ts) continue;
    next.set(metric, { value, ts: message.ts, receivedAt });
  }
  return next;
}

export function freshLiveReadings(live: LiveValues, now: number): Reading[] {
  const readings: Reading[] = [];
  for (const [metric, v] of live) {
    if (now - v.receivedAt < LIVE_FRESH_MS) readings.push({ ts: v.ts, metric, value: v.value });
  }
  return readings;
}

/** When the newest still-fresh value arrived (ms since epoch), or null when none is fresh. */
export function latestFreshReceivedAt(live: LiveValues, now: number): number | null {
  let latest: number | null = null;
  for (const v of live.values()) {
    if (now - v.receivedAt >= LIVE_FRESH_MS) continue;
    if (latest === null || v.receivedAt > latest) latest = v.receivedAt;
  }
  return latest;
}

export function overlayLive(polled: Reading[], live: Reading[]): Reading[] {
  const liveByMetric = new Map(live.map((r) => [r.metric, r]));
  const merged = polled.map((r) => liveByMetric.get(r.metric) ?? r);
  const seen = new Set(polled.map((r) => r.metric));
  return [...merged, ...live.filter((r) => !seen.has(r.metric))];
}

export function backoffMs(attempt: number): number {
  return Math.min(1000 * 2 ** attempt, 30_000);
}
