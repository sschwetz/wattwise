import { describe, expect, it } from "vitest";
import { formatRunTime, isStale, OUTCOME_LABEL, outcomeLabel, outcomeTone, runDescription, stepLines } from "./runFormat";
import type { ActorRun } from "./types";

const SYDNEY = "Australia/Sydney";
// Thursday 5 Nov 2026, 14:00 in Sydney (UTC+11), still 4 Nov in UTC-negative zones.
const now = new Date("2026-11-05T03:00:00Z");

function run(overrides: Partial<ActorRun> = {}): ActorRun {
  return {
    id: 1, schedule_id: 2, kind: "action", trigger_at: "2026-11-05T03:00:00Z", started_at: null, finished_at: null,
    outcome: "ok", message: null, attempts: 1, parent_run_id: null, snapshot: null, steps: null, ...overrides,
  };
}

describe("formatRunTime", () => {
  it("says Today for a time on the same day in the site zone, not the UTC day", () => {
    expect(formatRunTime("2026-11-04T23:30:00Z", SYDNEY, now)).toBe("Today 10:30");
  });
  it("says Yesterday for the previous day in the site zone", () => {
    expect(formatRunTime("2026-11-04T05:00:00Z", SYDNEY, now)).toBe("Yesterday 16:00");
  });
  it("uses a short weekday, date and time for older runs", () => {
    expect(formatRunTime("2026-11-01T05:00:00Z", SYDNEY, now)).toMatch(/Sun.*1 Nov.*16:00/);
  });
  it("computes the boundaries in the given zone", () => {
    // 03:00Z on 5 Nov is 19:00 on 4 Nov in Los Angeles.
    expect(formatRunTime("2026-11-05T03:00:00Z", "America/Los_Angeles", now)).toBe("Today 19:00");
    expect(formatRunTime("2026-11-05T03:00:00Z", "America/Los_Angeles", new Date("2026-11-05T20:00:00Z"))).toBe("Yesterday 19:00");
  });
});

describe("isStale", () => {
  it("is stale when never seen", () => expect(isStale(null, now)).toBe(true));
  it("is fresh at exactly 2 minutes and stale after", () => {
    expect(isStale("2026-11-05T02:58:00Z", now)).toBe(false);
    expect(isStale("2026-11-05T02:57:59Z", now)).toBe(true);
  });
});

describe("runDescription", () => {
  it("uses the action label and appends the message", () => {
    expect(runDescription(run({ message: "Set to 26" }), "Turn the system off")).toBe("Turn the system off: Set to 26");
  });
  it("uses only the label for a clean ok run", () => {
    expect(runDescription(run(), "Turn the system off")).toBe("Turn the system off");
  });
  it("labels restore and test runs", () => {
    expect(runDescription(run({ kind: "restore", message: "Back to cool" }), "x")).toBe("Restored: Back to cool");
    expect(runDescription(run({ kind: "test" }), "x")).toBe("Test run");
  });
  it("says a skipped or failed restore did not restore", () => {
    expect(runDescription(run({ kind: "restore", outcome: "skipped", message: "owner override" }), "x")).toBe("Restore skipped: owner override");
    expect(runDescription(run({ kind: "restore", outcome: "skipped" }), "x")).toBe("Restore skipped");
    expect(runDescription(run({ kind: "restore", outcome: "failed", message: "not confirmed" }), "x")).toBe("Restore failed: not confirmed");
  });
  it("falls back to a plain default by outcome", () => {
    expect(runDescription(run({ outcome: "skipped" }), "Turn off")).toBe("Turn off: Skipped");
    expect(runDescription(run({ outcome: "overridden" }), "Turn off")).toBe("Turn off: Changed by you");
  });
  it("adds the attempt count to a failed run that tried more than once", () => {
    expect(runDescription(run({ outcome: "failed", attempts: 3 }), "Turn off")).toBe("Turn off: Failed (after 3 tries)");
    expect(runDescription(run({ outcome: "failed", attempts: 1, message: "Timed out" }), "Turn off")).toBe("Turn off: Timed out");
  });
});

it("names every outcome", () => {
  expect(OUTCOME_LABEL).toEqual({
    ok: "OK", skipped: "Skipped", failed: "Failed", overridden: "You took over", pending: "Queued", running: "Running", partial: "Partly done",
  });
});

describe("unknown values from a newer server", () => {
  const odd = (o: Partial<Record<"kind" | "outcome" | "message", string>>) => run(o as unknown as Partial<ActorRun>);
  it("reads Run for an unknown kind, with the message", () => {
    expect(runDescription(odd({ kind: "migrate" }), "Turn off")).toBe("Run");
    expect(runDescription(odd({ kind: "migrate", message: "x" }), "Turn off")).toBe("Run: x");
  });
  it("shows the raw outcome with the neutral tone", () => {
    expect(outcomeLabel("cancelled")).toBe("cancelled");
    expect(outcomeTone("cancelled")).toBe(outcomeTone("pending"));
    expect(outcomeLabel("ok")).toBe("OK");
  });
});

describe("partial and steps", () => {
  const labelFor = (id: string) => ({ system_off: "Turn off", set_zone_temperature: "Set zone" })[id] ?? id;
  const step = (index: number, action_id: string, outcome: "ok" | "failed", extra: object = {}) =>
    ({ index, action_id, params: {}, outcome, message: null, attempts: 1, ...extra });

  it("partial has its own chip and tone", () => {
    expect(outcomeLabel("partial")).toBe("Partly done");
    expect(outcomeTone("partial")).toBe("bg-solar-soft text-solar");
    expect(runDescription(run({ outcome: "partial" }), "A, then B")).toBe("A, then B: Partly done");
  });

  it("stepLines puts failures first, in index order within each group", () => {
    const steps = [step(0, "system_off", "ok"), step(1, "set_zone_temperature", "failed", { message: "Too high" }), step(2, "system_off", "failed", { message: "No reply" })];
    expect(stepLines(run({ steps }), labelFor)).toEqual([
      { text: "Set zone: Too high", tone: "failed" },
      { text: "Turn off: No reply", tone: "failed" },
      { text: "Turn off", tone: "ok" },
    ]);
  });

  it("stepLines tells repeated actions apart by their params", () => {
    const steps = [
      step(0, "set_zone_temperature", "ok", { params: { zone: "Study", temperature: 24 } }),
      step(1, "set_zone_temperature", "ok", { params: { zone: "Lounge", temperature: 22 } }),
    ];
    expect(stepLines(run({ steps }), labelFor).map((l) => l.text)).toEqual(["Set zone (Study, 24)", "Set zone (Lounge, 22)"]);
  });

  it("stepLines is empty without steps", () => {
    expect(stepLines(run({ steps: null }), labelFor)).toEqual([]);
    expect(stepLines(run({ steps: [] }), labelFor)).toEqual([]);
  });
});
