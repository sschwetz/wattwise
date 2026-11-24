import { describe, expect, it } from "vitest";
import { arrangeRows, layoutFromRows, moveRow, newSeparator, rowId } from "./scheduleLayout";
import type { ActorSchedule, ScheduleLayoutItem } from "./types";

function schedule(id: number): ActorSchedule {
  return {
    id, instance_id: 1, name: `S${id}`, enabled: true, trigger: { type: "time", time: "10:00", days_of_week: [1] },
    conditions: [], actions: [{ id: "system_off", params: {} }],
    restore: { enabled: false, when: "window_end", minutes: null, time: null, only_if: [], early_if: [] },
    retry: { max_attempts: 3, interval_seconds: 30, give_up_minutes: 15 }, limits: { min_gap_minutes: 0, run_again_after: null },
    notify_email: false,
  };
}

const schedules = [schedule(1), schedule(2), schedule(3), schedule(4)];
const sep = (key: string): ScheduleLayoutItem => ({ type: "separator", key, label: "" });
const sched = (id: number): ScheduleLayoutItem => ({ type: "schedule", id });
const ids = (items: ScheduleLayoutItem[] | null) => arrangeRows(schedules, items).map(rowId);

describe("arrangeRows", () => {
  it("is in id order when the list was never arranged", () => {
    expect(ids(null)).toEqual(["s:1", "s:2", "s:3", "s:4"]);
  });

  it("follows the layout, then appends the schedules it does not name in id order", () => {
    expect(ids([sched(3), sep("k1"), sched(1)])).toEqual(["s:3", "p:k1", "s:1", "s:2", "s:4"]);
  });

  it("drops an id that has no schedule", () => {
    expect(ids([sched(99), sched(2)])).toEqual(["s:2", "s:1", "s:3", "s:4"]);
  });

  it("shows a schedule once when the layout names it twice", () => {
    expect(ids([sched(2), sched(2)])).toEqual(["s:2", "s:1", "s:3", "s:4"]);
  });

  it("shows every schedule when the layout holds only separators or nothing", () => {
    expect(ids([sep("k1")])).toEqual(["p:k1", "s:1", "s:2", "s:3", "s:4"]);
    expect(ids([])).toEqual(["s:1", "s:2", "s:3", "s:4"]);
  });

  it("carries the separator label", () => {
    const [row] = arrangeRows(schedules, [{ type: "separator", key: "k1", label: "Kids" }]);
    expect(row).toEqual({ kind: "separator", key: "k1", label: "Kids" });
  });
});

describe("layoutFromRows", () => {
  it("round-trips: every schedule once, separators kept, in row order", () => {
    const rows = arrangeRows(schedules, [sched(3), { type: "separator", key: "k1", label: "Kids" }]);
    expect(layoutFromRows(rows)).toEqual([
      sched(3), { type: "separator", key: "k1", label: "Kids" }, sched(1), sched(2), sched(4),
    ]);
  });
});

describe("moveRow", () => {
  const rows = arrangeRows(schedules, [sched(1), sep("k1"), sched(2)]);

  it("puts the dragged row where the target row was", () => {
    expect(moveRow(rows, "s:3", "s:1").map(rowId)).toEqual(["s:3", "s:1", "p:k1", "s:2", "s:4"]);
    expect(moveRow(rows, "s:1", "s:2").map(rowId)).toEqual(["p:k1", "s:2", "s:1", "s:3", "s:4"]);
  });

  it("moves a separator like a schedule", () => {
    expect(moveRow(rows, "p:k1", "s:4").map(rowId)).toEqual(["s:1", "s:2", "s:3", "s:4", "p:k1"]);
  });

  it("returns the same rows for an unknown id or a drop on itself", () => {
    expect(moveRow(rows, "s:99", "s:1")).toBe(rows);
    expect(moveRow(rows, "s:1", "s:99")).toBe(rows);
    expect(moveRow(rows, "s:1", "s:1")).toBe(rows);
  });
});

describe("newSeparator", () => {
  it("is an unlabelled separator with a fresh key each time", () => {
    const a = newSeparator();
    const b = newSeparator();
    expect(a).toMatchObject({ kind: "separator", label: "" });
    expect(a.kind === "separator" && a.key.length > 0).toBe(true);
    expect(rowId(a)).not.toBe(rowId(b));
  });
});
