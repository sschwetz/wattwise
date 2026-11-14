import { describe, expect, it } from "vitest";
import {
  defaultClockRef,
  emptyForm,
  fromSchedule,
  setTriggerType,
  sunRef,
  suggestRearm,
  toPayload,
  type ScheduleFormState,
  type TimeOfDayRow,
} from "./scheduleForm";
import { validateForm } from "./scheduleValidation";
import type { Capabilities } from "./types";

const caps: Capabilities = {
  version: 1,
  actions: [{ id: "system_off", label: "Turn off", restorable: true, params: [] }],
  readings: [],
};

function form(): ScheduleFormState {
  return { ...emptyForm(caps, { instanceId: 3, firstWindowId: null }), name: "Test" };
}

function withTrigger(patch: Partial<ScheduleFormState["trigger"]>): ScheduleFormState {
  const base = form();
  return { ...base, trigger: { ...base.trigger, ...patch } };
}

const row = (start: TimeOfDayRow["start"], end: TimeOfDayRow["end"], use = true): TimeOfDayRow => ({ key: 0, use, kind: "time_of_day", start, end });

describe("sun trigger", () => {
  const sun = withTrigger({ type: "sun", event: "sunset", offset: "-30", days: [5, 1] });

  it("is sent with its event, offset and sorted days, and loads back the same", () => {
    const payload = toPayload(sun, caps);
    expect(payload.trigger).toEqual({ type: "sun", event: "sunset", offset_minutes: -30, days_of_week: [1, 5] });
    expect(fromSchedule(payload).trigger).toMatchObject({ type: "sun", event: "sunset", offset: "-30", days: [1, 5] });
  });

  it("needs a whole offset within three hours and at least one day", () => {
    expect(validateForm(sun, caps, [])).toEqual({});
    expect(validateForm(withTrigger({ type: "sun", offset: "181" }), caps, [])["trigger.offset"]).toBeDefined();
    expect(validateForm(withTrigger({ type: "sun", offset: "1.5" }), caps, [])["trigger.offset"]).toBeDefined();
    expect(validateForm(withTrigger({ type: "sun", days: [] }), caps, [])["trigger.days"]).toBeDefined();
  });
});

describe("re-arm value", () => {
  it("suggests two degrees back inside the level", () => {
    expect(suggestRearm("30", "gt")).toBe("28");
    expect(suggestRearm("18.5", "lt")).toBe("20.5");
    expect(suggestRearm("", "gt")).toBe("");
  });

  it("is sent only when typed, on a temperature or price trigger", () => {
    const temp = withTrigger({ type: "temperature", level: "30", rearm: "28" });
    expect(toPayload(temp, caps).trigger).toMatchObject({ type: "temperature", value: 30, rearm: 28 });
    expect(toPayload(withTrigger({ type: "temperature", level: "30", rearm: "" }), caps).trigger).not.toHaveProperty("rearm");
    expect(toPayload(withTrigger({ type: "price_above", price: "40", rearm: "35" }), caps).trigger).toMatchObject({ rearm: 35 });
  });

  it("must lie back inside the level", () => {
    const errors = (patch: Partial<ScheduleFormState["trigger"]>) => validateForm(withTrigger(patch), caps, [])["trigger.rearm"];
    expect(errors({ type: "temperature", level: "30", op: "gt", rearm: "28" })).toBeUndefined();
    expect(errors({ type: "temperature", level: "30", op: "gt", rearm: "30" })).toBeDefined();
    expect(errors({ type: "temperature", level: "18", op: "lt", rearm: "16" })).toBeDefined();
    expect(errors({ type: "temperature", level: "18", op: "lt", rearm: "20" })).toBeUndefined();
    expect(errors({ type: "temperature", level: "30", rearm: "abc" })).toBeDefined();
    expect(errors({ type: "price_above", price: "40", rearm: "45" })).toBeDefined();
    expect(errors({ type: "price_above", price: "40", rearm: "" })).toBeUndefined();
  });

  it("a saved schedule loads with its value and without the suggestion switch", () => {
    const payload = toPayload(withTrigger({ type: "temperature", level: "30", rearm: "28" }), caps);
    expect(fromSchedule(payload).trigger).toMatchObject({ rearm: "28", rearmAuto: false });
  });
});

describe("limits", () => {
  it("start at no gap and no lockout", () => {
    expect(toPayload(form(), caps).limits).toEqual({ min_gap_minutes: 0, run_again_after: null });
  });

  it("choosing a temperature trigger starts the gap at 30 minutes, once", () => {
    const next = setTriggerType(form(), "temperature");
    expect(next.limits.minGap).toBe("30");
    const edited = { ...next, limits: { ...next.limits, minGap: "10" } };
    expect(setTriggerType(edited, "temperature").limits.minGap).toBe("10");
  });

  it("round-trip the gap and the lockout time", () => {
    const base = form();
    const limits = { minGap: "45", againAfter: sunRef("sunrise") };
    const payload = toPayload({ ...base, limits }, caps);
    expect(payload.limits).toEqual({ min_gap_minutes: 45, run_again_after: { kind: "sun", event: "sunrise", offset_minutes: 0 } });
    expect(fromSchedule(payload).limits).toEqual(limits);
  });

  it("need a whole gap of 0 to 1440 minutes and a valid lockout time", () => {
    const errors = (limits: ScheduleFormState["limits"]) =>
      Object.fromEntries(Object.entries(validateForm({ ...form(), limits }, caps, [])).filter(([key]) => key.startsWith("limits.")));
    expect(errors({ minGap: "30", againAfter: defaultClockRef("06:00") })).toEqual({});
    expect(errors({ minGap: "-1", againAfter: null })["limits.minGap"]).toBeDefined();
    expect(errors({ minGap: "1441", againAfter: null })["limits.minGap"]).toBeDefined();
    expect(errors({ minGap: "2.5", againAfter: null })["limits.minGap"]).toBeDefined();
    expect(errors({ minGap: "0", againAfter: defaultClockRef("6:00") })["limits.againAfter"]).toBeDefined();
  });
});

describe("time-of-day row", () => {
  it("is sent with both times and loads back the same", () => {
    const base = form();
    const rows = [row({ kind: "sun", event: "sunset", offset: "-30" }, defaultClockRef("23:00"))];
    const payload = toPayload({ ...base, conditions: rows }, caps);
    expect(payload.conditions).toEqual([
      { kind: "time_of_day", start: { kind: "sun", event: "sunset", offset_minutes: -30 }, end: { kind: "clock", time: "23:00" }, enabled: true },
    ]);
    expect(fromSchedule(payload).conditions).toMatchObject([{ kind: "time_of_day", start: { event: "sunset", offset: "-30" }, end: { time: "23:00" }, use: true }]);
  });

  it("flags a ticked row with a bad time or identical ends, and ignores an unticked one", () => {
    const errorsFor = (r: TimeOfDayRow) => validateForm({ ...form(), conditions: [r] }, caps, [])["conditions.0"];
    expect(errorsFor(row(defaultClockRef("18:00"), defaultClockRef("23:00")))).toBeUndefined();
    expect(errorsFor(row(defaultClockRef("25:00"), defaultClockRef("23:00")))).toBeDefined();
    expect(errorsFor(row(defaultClockRef("23:00"), defaultClockRef("23:00")))).toBeDefined();
    expect(errorsFor(row(defaultClockRef(""), defaultClockRef("23:00"), false))).toBeUndefined();
  });

  it("drops a row that cannot be sent rather than sending bad times", () => {
    const payload = toPayload({ ...form(), conditions: [row(defaultClockRef("x"), defaultClockRef("23:00"), false)] }, caps);
    expect(payload.conditions).toEqual([]);
  });
});
