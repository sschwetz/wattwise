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
  action: { id: "set_temperature", params: { temperature_c: 26 } },
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
    expect(edit((f) => ({ ...f, params: { temperature_c: { kind: "number", text: "25" } } }))).toBe(true);
    expect(edit((f) => ({ ...f, conditions: addCondition(f.conditions, [{ id: "indoor_temp_c", label: "", unit: null, targets: null }]) }))).toBe(true);
  });

  it("ignores spacing and the way a number is written", () => {
    expect(edit((f) => ({ ...f, name: "  Peak off " }))).toBe(false);
    expect(edit((f) => ({ ...f, retry: { ...f.retry, intervalSeconds: "30.0" } }))).toBe(false);
    expect(edit((f) => ({ ...f, retry: { ...f.retry, intervalSeconds: " 30 " } }))).toBe(false);
    expect(edit((f) => ({ ...f, params: { temperature_c: { kind: "number", text: "26.00" } } }))).toBe(false);
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

  it("is false again once the edit is undone", () => {
    const changed = { ...baseline, name: "Other" };
    expect(isDirty({ ...changed, name: "Peak off" }, baseline)).toBe(false);
  });
});
