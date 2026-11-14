import { describe, expect, it } from "vitest";
import { isDirty } from "./scheduleDirty";
import { addCondition, emptyForm, fromSchedule, type SchedulePayload, type ScheduleFormState } from "./scheduleForm";
import type { Capabilities } from "./types";

const caps: Capabilities = {
  version: 1,
  actions: [
    { id: "set_temperature", label: "Set temperature", restorable: true, params: [{ name: "temperature_c", label: "Temperature", type: "number", min: 10, max: 30, unit: "C", options: [] }] },
  ],
  readings: [{ id: "indoor_temp_c", label: "Indoor temperature", unit: "C", type: "number", target_options: null }],
};

const saved: SchedulePayload = {
  instance_id: 3, name: "Peak off", enabled: true,
  trigger: { type: "time", time: "06:30", days_of_week: [0, 2, 4] },
  conditions: [{ reading: "indoor_temp_c", op: "gt", value: 30, enabled: true }],
  actions: [{ id: "set_temperature", params: { temperature_c: 26 } }],
  restore: { enabled: true, when: "after_minutes", minutes: 45, time: null, only_if: [], early_if: [] },
  retry: { max_attempts: 3, interval_seconds: 30, give_up_minutes: 15 },
  notify_email: false,
};

const baseline = fromSchedule(saved);

function edit(change: (form: ScheduleFormState) => ScheduleFormState): boolean {
  return isDirty(change(structuredClone(baseline)), baseline);
}

describe("isDirty", () => {
  it("is false for the untouched form", () => {
    expect(isDirty(baseline, baseline)).toBe(false);
    expect(isDirty(structuredClone(baseline), baseline)).toBe(false);
  });

  it("is false for an untouched new form", () => {
    const fresh = emptyForm(caps, { instanceId: 3, firstWindowId: null });
    expect(isDirty(emptyForm(caps, { instanceId: 3, firstWindowId: null }), fresh)).toBe(false);
  });

  it("is true once a field changes", () => {
    expect(edit((f) => ({ ...f, name: "Peak on" }))).toBe(true);
    expect(edit((f) => ({ ...f, enabled: false }))).toBe(true);
    expect(edit((f) => ({ ...f, trigger: { ...f.trigger, time: "07:00" } }))).toBe(true);
    expect(edit((f) => ({ ...f, retry: { ...f.retry, maxAttempts: "4" } }))).toBe(true);
    expect(edit((f) => ({ ...f, actions: [{ ...f.actions[0], params: { temperature_c: { kind: "number", text: "25" } } }] }))).toBe(true);
    expect(edit((f) => ({ ...f, conditions: addCondition(f.conditions, [{ id: "indoor_temp_c", label: "", unit: null, type: "number", targets: null }]) }))).toBe(true);
  });

  it("ignores spacing and the way a number is written", () => {
    expect(edit((f) => ({ ...f, name: "  Peak off " }))).toBe(false);
    expect(edit((f) => ({ ...f, retry: { ...f.retry, intervalSeconds: "30.0" } }))).toBe(false);
    expect(edit((f) => ({ ...f, retry: { ...f.retry, intervalSeconds: " 30 " } }))).toBe(false);
    expect(edit((f) => ({ ...f, actions: [{ ...f.actions[0], params: { temperature_c: { kind: "number", text: "26.00" } } }] }))).toBe(false);
    expect(edit((f) => ({ ...f, conditions: f.conditions.map((c) => ({ ...c, value: "3e1" })) }))).toBe(false);
  });

  it("treats a blank number as a change from zero", () => {
    const zero = { ...baseline, retry: { ...baseline.retry, maxAttempts: "0" } };
    expect(isDirty({ ...zero, retry: { ...zero.retry, maxAttempts: "" } }, zero)).toBe(true);
  });

  it("ignores the order the days were ticked in and the row keys", () => {
    expect(edit((f) => ({ ...f, trigger: { ...f.trigger, days: [4, 0, 2] } }))).toBe(false);
    expect(edit((f) => ({ ...f, conditions: f.conditions.map((c) => ({ ...c, key: 9 })) }))).toBe(false);
  });

  it("keeps a name that looks like a number as typed", () => {
    const named = { ...baseline, name: "007" };
    expect(isDirty({ ...named, name: "7" }, named)).toBe(true);
  });

  it("counts each kind of real edit", () => {
    expect(edit((f) => ({ ...f, conditions: f.conditions.map((c) => ({ ...c, use: false })) }))).toBe(true);
    expect(edit((f) => ({ ...f, trigger: { ...f.trigger, days: [0, 2, 4, 6] } }))).toBe(true);
    expect(edit((f) => ({ ...f, trigger: { ...f.trigger, days: [0, 2] } }))).toBe(true);
    expect(edit((f) => ({ ...f, notifyEmail: true }))).toBe(true);
    expect(edit((f) => ({ ...f, enabled: !f.enabled }))).toBe(true);
    expect(edit((f) => ({ ...f, retry: { ...f.retry, giveUpMinutes: "20" } }))).toBe(true);
    expect(edit((f) => ({ ...f, retry: { ...f.retry, intervalSeconds: "31" } }))).toBe(true);
    expect(edit((f) => ({ ...f, name: "Peak of" }))).toBe(true);
  });

  it("does not count re-typing the same value", () => {
    expect(edit((f) => ({ ...f, name: "Peak off" }))).toBe(false);
    expect(edit((f) => ({ ...f, retry: { ...f.retry, maxAttempts: "3" } }))).toBe(false);
    expect(edit((f) => ({ ...f, conditions: f.conditions.map((c) => ({ ...c, value: "30.0" })) }))).toBe(false);
    expect(edit((f) => ({ ...f, name: "Peak off\t" }))).toBe(false);
  });

  it("counts a zone toggled on or off, and not a zone toggled off and back on", () => {
    const zoned = fromSchedule({ ...saved, actions: [{ id: "set_zones", params: { zones: ["Lounge", "Study"] } }] });
    const zones = (value: string[]) => ({ ...zoned, actions: [{ ...zoned.actions[0], params: { zones: { kind: "value" as const, value } } }] });
    expect(isDirty(zones(["Lounge"]), zoned)).toBe(true);
    expect(isDirty(zones(["Lounge", "Study", "Bed 1"]), zoned)).toBe(true);
    expect(isDirty(zones(["Study", "Lounge"]), zoned)).toBe(false);
  });

  it("counts a change to a param named key", () => {
    const keyed = fromSchedule({ ...saved, actions: [{ id: "custom", params: { key: "a" } }] });
    expect(isDirty({ ...keyed, actions: [{ ...keyed.actions[0], params: { key: { kind: "value", value: "b" } } }] }, keyed)).toBe(true);
  });

  it("test_dirty_check_sees_a_reordered_list", () => {
    const list = fromSchedule({
      ...saved,
      actions: [{ id: "set_temperature", params: { temperature_c: 26 } }, { id: "system_off", params: {} }],
    });
    const reordered = { ...list, actions: [list.actions[1], list.actions[0]] };
    expect(isDirty(reordered, list)).toBe(true);
    expect(isDirty(structuredClone(list), list)).toBe(false);
    const rekeyed = { ...list, actions: list.actions.map((a) => ({ ...a, key: a.key + 10 })) };
    expect(isDirty(rekeyed, list)).toBe(false);
  });

  it("is false again once the edit is undone", () => {
    const changed = { ...baseline, name: "Other" };
    expect(isDirty({ ...changed, name: "Peak off" }, baseline)).toBe(false);
  });
});

describe("isDirty with calendar rows", () => {
  const withMonths = fromSchedule({ ...saved, conditions: [{ kind: "months", op: "in", values: [12, 1], enabled: true }] });

  it("notices a changed month list and ignores the order picked", () => {
    const reordered = structuredClone(withMonths);
    const row = reordered.conditions[0];
    if (row.kind === "months") row.values = [1, 12];
    expect(isDirty(reordered, withMonths)).toBe(false);
    if (row.kind === "months") row.values = [1];
    expect(isDirty(reordered, withMonths)).toBe(true);
  });

  it("ignores a changed row key", () => {
    const rekeyed = structuredClone(withMonths);
    rekeyed.conditions[0].key = 9;
    expect(isDirty(rekeyed, withMonths)).toBe(false);
  });
});
