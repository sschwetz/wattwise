import { describe, expect, it } from "vitest";
import {
  applyLiveMessage,
  backoffMs,
  freshLiveReadings,
  LIVE_FRESH_MS,
  overlayLive,
  parseLiveMessage,
  type LiveMessage,
  type LiveValues,
} from "./liveStream";

const msg = (ts: string, values: Record<string, number>, kind: "energy" | "weather" = "energy"): LiveMessage => ({
  kind,
  ts,
  values,
});

describe("parseLiveMessage", () => {
  it("accepts a valid message", () => {
    const data = { kind: "energy", ts: "2026-01-01T00:00:00Z", values: { production_w: 5 } };
    expect(parseLiveMessage(data)).toEqual(data);
  });

  it("rejects bad shapes", () => {
    expect(parseLiveMessage({ ts: "t", values: {} })).toBeNull();
    expect(parseLiveMessage({ kind: "other", ts: "t", values: {} })).toBeNull();
    expect(parseLiveMessage({ kind: "energy", ts: "t", values: { a: "1" } })).toBeNull();
    expect(parseLiveMessage(null)).toBeNull();
    expect(parseLiveMessage("text")).toBeNull();
    expect(parseLiveMessage([])).toBeNull();
  });
});

describe("applyLiveMessage", () => {
  it("ignores an older ts and accepts a newer one without mutating", () => {
    const first = applyLiveMessage(new Map(), msg("2026-01-01T00:00:10Z", { production_w: 1 }), 100);
    const older = applyLiveMessage(first, msg("2026-01-01T00:00:05Z", { production_w: 9 }), 200);
    expect(older.get("production_w")?.value).toBe(1);
    const newer = applyLiveMessage(first, msg("2026-01-01T00:00:20Z", { production_w: 2 }), 300);
    expect(newer.get("production_w")).toEqual({ value: 2, ts: "2026-01-01T00:00:20Z", receivedAt: 300 });
    expect(first.get("production_w")?.value).toBe(1);
    expect(newer).not.toBe(first);
  });
});

describe("freshLiveReadings", () => {
  const live: LiveValues = new Map([["production_w", { value: 3, ts: "t1", receivedAt: 1000 }]]);

  it("keeps a value at 14 999 ms and drops one at 15 000 ms", () => {
    expect(freshLiveReadings(live, 1000 + LIVE_FRESH_MS - 1)).toEqual([
      { ts: "t1", metric: "production_w", value: 3 },
    ]);
    expect(freshLiveReadings(live, 1000 + LIVE_FRESH_MS)).toEqual([]);
  });

  it("returns both grid flows with the export newer", () => {
    let state: LiveValues = applyLiveMessage(new Map(), msg("2026-01-01T00:00:01Z", { grid_import_w: 100 }), 10);
    state = applyLiveMessage(state, msg("2026-01-01T00:00:02Z", { grid_export_w: 50 }), 20);
    const readings = freshLiveReadings(state, 30);
    const imp = readings.find((r) => r.metric === "grid_import_w");
    const exp = readings.find((r) => r.metric === "grid_export_w");
    expect(readings).toHaveLength(2);
    expect(exp && imp && exp.ts > imp.ts).toBe(true);
  });
});

describe("overlayLive", () => {
  it("replaces same-metric readings and passes the rest through in order", () => {
    const polled = [
      { ts: "a", metric: "m1", value: 1 },
      { ts: "a", metric: "m2", value: 2 },
    ];
    const live = [
      { ts: "b", metric: "m2", value: 20 },
      { ts: "b", metric: "m3", value: 30 },
    ];
    expect(overlayLive(polled, live)).toEqual([
      { ts: "a", metric: "m1", value: 1 },
      { ts: "b", metric: "m2", value: 20 },
      { ts: "b", metric: "m3", value: 30 },
    ]);
  });
});

describe("backoffMs", () => {
  it("doubles up to 30 s", () => {
    expect(backoffMs(0)).toBe(1000);
    expect(backoffMs(3)).toBe(8000);
    expect(backoffMs(10)).toBe(30000);
  });
});
