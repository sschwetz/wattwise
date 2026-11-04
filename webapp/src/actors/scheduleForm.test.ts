import { describe, expect, it } from "vitest";
import {
  addCondition,
  alignParams,
  conditionReadings,
  emptyForm,
  fromSchedule,
  resetActionParams,
  restoreOptions,
  setTriggerType,
  toPayload,
  validateForm,
  type SchedulePayload,
  type ScheduleFormState,
} from "./scheduleForm";
import { serverProblems } from "./serverRules.testHelper";
import type { Capabilities, TriggerSpec } from "./types";

const zones = [{ value: "Lounge", label: "Lounge" }, { value: "Bed 1", label: "Bed 1" }];

const caps: Capabilities = {
  version: 1,
  actions: [
    { id: "system_off", label: "Turn off", restorable: true, params: [] },
    {
      id: "system_on", label: "Turn on", restorable: true,
      params: [{ name: "mode", label: "Mode", type: "choice", min: null, max: null, unit: null, options: [{ value: "COOL", label: "Cool" }, { value: "HEAT", label: "Heat" }] }],
    },
    {
      id: "set_temperature", label: "Set temperature", restorable: true,
      params: [{ name: "temperature_c", label: "Temperature", type: "number", min: 10, max: 30, unit: "C", options: [] }],
    },
    {
      id: "set_zones", label: "Switch zones", restorable: true,
      params: [
        { name: "zones", label: "Zones", type: "multi_choice", min: null, max: null, unit: null, options: zones },
        { name: "enabled", label: "Zones on", type: "bool", min: null, max: null, unit: null, options: [] },
      ],
    },
    { id: "beep", label: "Beep", restorable: false, params: [] },
  ],
  readings: [
    { id: "indoor_temp_c", label: "Indoor temperature", unit: "C", type: "number", target_options: null },
    { id: "outdoor_temp_c", label: "Outdoor temperature", unit: "C", type: "number", target_options: null },
    { id: "zone_temp_c", label: "Zone temperature", unit: "C", type: "number", target_options: zones },
  ],
};

function payload(trigger: TriggerSpec): SchedulePayload {
  return {
    instance_id: 3,
    name: "Peak off",
    enabled: true,
    trigger,
    conditions: [
      { reading: "outdoor_temp_c", op: "gt", value: 30, target: null },
      { reading: "zone_temp_c", op: "lt", value: 18.5, target: "Bed 1" },
    ],
    action: { id: "set_zones", params: { zones: ["Lounge"], enabled: false } },
    restore: {
      enabled: true, when: "after_minutes", minutes: 45, time: null,
      only_if: [{ reading: "battery_soc_pct", op: "gt", value: 20, target: null }],
      early_if: [{ reading: "indoor_temp_c", op: "gt", value: 27, target: null }],
    },
    retry: { max_attempts: 4, interval_seconds: 20, give_up_minutes: 10 },
    notify_email: true,
  };
}

const triggers: TriggerSpec[] = [
  { type: "window_start", window_id: 11 },
  { type: "window_end", window_id: 12 },
  { type: "time", time: "06:30", days_of_week: [0, 2, 4] },
  { type: "price_above", c_per_kwh: 45.5 },
];

const context = { instanceId: 3, firstWindowId: 11 };

describe("toPayload and fromSchedule round-trip", () => {
  it.each(triggers)("for a $type trigger", (trigger) => {
    const p = payload(trigger);
    expect(toPayload(fromSchedule(p), caps)).toEqual(p);
    const form = fromSchedule(p);
    expect(fromSchedule(toPayload(form, caps))).toEqual(form);
  });

  it("for a new form", () => {
    const form = { ...emptyForm(caps, context), name: "New" };
    expect(fromSchedule(toPayload(form, caps))).toEqual(form);
  });

  it("keeps a restore at a set time and a number param", () => {
    const p: SchedulePayload = {
      ...payload({ type: "time", time: "16:00", days_of_week: [0, 1, 2, 3, 4, 5, 6] }),
      action: { id: "set_temperature", params: { temperature_c: 24 } },
      restore: { enabled: false, when: "at_time", minutes: null, time: "21:00", only_if: [], early_if: [] },
    };
    expect(toPayload(fromSchedule(p), caps)).toEqual(p);
  });

  it("leaves out unticked condition rows", () => {
    const form = fromSchedule(payload(triggers[0]));
    form.conditions[0].use = false;
    expect(toPayload(form, caps).conditions).toEqual([{ reading: "zone_temp_c", op: "lt", value: 18.5, target: "Bed 1" }]);
  });
});

describe("emptyForm picks defaults from capabilities", () => {
  it("uses the first window, the first action and window-end restore", () => {
    const form = emptyForm(caps, context);
    expect(form.instanceId).toBe(3);
    expect(form.enabled).toBe(true);
    expect(form.trigger.type).toBe("window_start");
    expect(form.trigger.windowId).toBe("11");
    expect(form.trigger.days).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(form.actionId).toBe("system_off");
    expect(form.params).toEqual({});
    expect(form.conditions).toEqual([]);
    expect(form.restore).toMatchObject({ enabled: true, when: "window_end" });
    expect(form.retry).toEqual({ maxAttempts: "3", intervalSeconds: "30", giveUpMinutes: "15" });
    expect(form.notifyEmail).toBe(false);
  });

  it("falls back to a set time and restore after 60 minutes with no windows", () => {
    const form = emptyForm(caps, { instanceId: 3, firstWindowId: null });
    expect(form.trigger.type).toBe("time");
    expect(form.restore).toMatchObject({ enabled: true, when: "after_minutes", minutes: "60" });
  });

  it("starts with the first action's default params", () => {
    const form = emptyForm({ ...caps, actions: caps.actions.slice(1) }, context);
    expect(form.actionId).toBe("system_on");
    expect(toPayload({ ...form, name: "x" }, caps).action).toEqual({ id: "system_on", params: { mode: "COOL" } });
  });
});

describe("changing action resets params", () => {
  it("replaces the params with the new action's defaults", () => {
    const form = fromSchedule(payload(triggers[0]));
    const next = resetActionParams(form, caps.actions[2]);
    expect(next.actionId).toBe("set_temperature");
    expect(toPayload(next, caps).action).toEqual({ id: "set_temperature", params: { temperature_c: 20 } });
  });

  it("gives multi_choice no ticks and bool false", () => {
    const next = resetActionParams(emptyForm(caps, context), caps.actions[3]);
    expect(toPayload(next, caps).action.params).toEqual({ zones: [], enabled: false });
  });

  it("turns restore off for an action that cannot be restored", () => {
    const next = resetActionParams(emptyForm(caps, context), caps.actions[4]);
    expect(next.restore.enabled).toBe(false);
  });
});

describe("trigger and restore options", () => {
  it("offers window end only for a window start trigger", () => {
    expect(restoreOptions("window_start")).toContain("window_end");
    expect(restoreOptions("window_end")).not.toContain("window_end");
    expect(restoreOptions("time")).not.toContain("window_end");
  });

  it("moves a window-end restore to after minutes when the trigger no longer has a window end", () => {
    const form = emptyForm(caps, context);
    expect(setTriggerType(form, "window_end").restore.when).toBe("after_minutes");
    expect(setTriggerType(form, "price_above").restore.when).toBe("after_minutes");
  });

  it("lists device readings then site readings without duplicates", () => {
    const ids = conditionReadings(caps).map((r) => r.id);
    expect(ids).toEqual(["indoor_temp_c", "outdoor_temp_c", "zone_temp_c", "battery_soc_pct", "price_c_per_kwh"]);
  });

  it("adds a row for the first reading with an empty value", () => {
    const rows = addCondition([], conditionReadings(caps));
    expect(rows).toEqual([{ key: 0, use: true, reading: "indoor_temp_c", op: "gt", value: "", target: null }]);
    expect(addCondition(rows, conditionReadings(caps))[1].key).toBe(1);
  });
});

function valid(): ScheduleFormState {
  return fromSchedule(payload(triggers[2]));
}

describe("validateForm", () => {
  it("accepts a valid form", () => {
    expect(validateForm(valid(), caps)).toEqual({});
  });

  it("rejects out-of-range numbers", () => {
    const form = resetActionParams(valid(), caps.actions[2]);
    form.params.temperature_c = { kind: "number", text: "31" };
    expect(validateForm(form, caps)["param.temperature_c"]).toBe("Temperature must be between 10 and 30.");
    form.params.temperature_c = { kind: "number", text: "9.5" };
    expect(validateForm(form, caps)["param.temperature_c"]).toBe("Temperature must be between 10 and 30.");
  });

  it("rejects empty required params", () => {
    const form = resetActionParams(valid(), caps.actions[2]);
    form.params.temperature_c = { kind: "number", text: "" };
    expect(validateForm(form, caps)["param.temperature_c"]).toBe("Enter a number for Temperature.");
    const zonesForm = resetActionParams(valid(), caps.actions[3]);
    expect(validateForm(zonesForm, caps)["param.zones"]).toBe("Tick at least one option for Zones.");
  });

  it("rejects a bad HH:MM", () => {
    const form = valid();
    form.trigger.time = "24:00";
    expect(validateForm(form, caps)["trigger.time"]).toBe("Enter the time as HH:MM, for example 16:00.");
    form.trigger.time = "6:30";
    expect(validateForm(form, caps)["trigger.time"]).toBeDefined();
    const restore = valid();
    restore.restore.when = "at_time";
    restore.restore.time = "";
    expect(validateForm(restore, caps)["restore.time"]).toBe("Enter the restore time as HH:MM, for example 21:00.");
  });

  it("rejects values below the retry floors", () => {
    const form = valid();
    form.retry = { maxAttempts: "0", intervalSeconds: "4", giveUpMinutes: "0" };
    expect(validateForm(form, caps)).toEqual({
      "retry.maxAttempts": "Attempts must be a whole number of at least 1.",
      "retry.intervalSeconds": "Seconds between tries must be a whole number of at least 5.",
      "retry.giveUpMinutes": "Give up after must be a whole number of minutes, at least 1.",
    });
    form.retry = { maxAttempts: "2.5", intervalSeconds: "30", giveUpMinutes: "15" };
    expect(validateForm(form, caps)["retry.maxAttempts"]).toBeDefined();
  });

  it("rejects an empty name, no days, a missing window and price", () => {
    const form = valid();
    form.name = " ";
    form.trigger.days = [];
    const errors = validateForm(form, caps);
    expect(errors.name).toBe("Enter a name for this schedule.");
    expect(errors["trigger.days"]).toBe("Tick at least one day.");
    expect(validateForm({ ...valid(), trigger: { ...valid().trigger, type: "window_start", windowId: "" } }, caps)["trigger.windowId"]).toBe("Pick a demand window.");
    expect(validateForm({ ...valid(), trigger: { ...valid().trigger, type: "price_above", price: "abc" } }, caps)["trigger.price"]).toBe("Enter a price in c/kWh.");
  });

  it("rejects a ticked condition with no value and restore minutes below 1", () => {
    const form = valid();
    form.conditions[1].value = "";
    form.restore.minutes = "0";
    const errors = validateForm(form, caps);
    expect(errors["conditions.1"]).toBe("Enter a number for Zone temperature.");
    expect(errors["restore.minutes"]).toBe("Enter a whole number of minutes, at least 1.");
  });

  it("skips restore fields while restore is off", () => {
    const form = valid();
    form.restore.enabled = false;
    form.restore.minutes = "";
    form.restore.earlyIf[0].value = "";
    expect(validateForm(form, caps)).toEqual({});
  });

  it("asks for an early condition when restoring only on conditions", () => {
    const form = valid();
    form.restore.when = "conditions_only";
    form.restore.earlyIf = [];
    expect(validateForm(form, caps)["restore.earlyIf"]).toBe("Add a condition under Restore early when, or pick another time to restore.");
  });

  it("rejects an action the actor does not declare", () => {
    const form = valid();
    form.actionId = "gone";
    expect(validateForm(form, caps).action).toBe("Pick an action this device supports.");
  });
});

describe("with restore off, hidden fields never make the payload rejectable", () => {
  const garbage: [string, (f: ScheduleFormState) => void][] = [
    ["minutes 0", (f) => { f.restore.when = "after_minutes"; f.restore.minutes = "0"; }],
    ["minutes -5", (f) => { f.restore.when = "after_minutes"; f.restore.minutes = "-5"; }],
    ["minutes empty", (f) => { f.restore.when = "after_minutes"; f.restore.minutes = ""; }],
    ["minutes 2.5", (f) => { f.restore.when = "after_minutes"; f.restore.minutes = "2.5"; }],
    ["bad time", (f) => { f.restore.when = "at_time"; f.restore.time = "25:99"; }],
    ["empty time", (f) => { f.restore.when = "at_time"; f.restore.time = ""; }],
    ["undeclared reading", (f) => { f.restore.onlyIf[0].reading = "gone_reading"; }],
    ["stale target", (f) => { f.restore.earlyIf = [{ key: 0, use: true, reading: "zone_temp_c", op: "gt", value: "20", target: "Garage" }]; }],
    ["target on a reading without targets", (f) => { f.restore.earlyIf[0].target = "Lounge"; }],
    ["target on a site reading", (f) => { f.restore.onlyIf[0].target = "Lounge"; }],
    ["no value", (f) => { f.restore.earlyIf[0].value = "abc"; }],
  ];

  it.each(garbage)("%s", (_name, spoil) => {
    const form = valid();
    spoil(form);
    form.restore.enabled = false;
    expect(validateForm(form, caps)).toEqual({});
    const body = toPayload(form, caps);
    expect(body.restore.enabled).toBe(false);
    expect(serverProblems(caps, body)).toEqual([]);
  });

  it("keeps the valid hidden values", () => {
    const form = valid();
    form.restore.enabled = false;
    expect(toPayload(form, caps).restore).toEqual({ ...payload(triggers[2]).restore, enabled: false });
  });
});

describe("alignParams matches the stored params to the declaration", () => {
  const stored = (params: Record<string, string | number | boolean | string[]>, id = "set_zones") =>
    fromSchedule({ ...payload(triggers[2]), action: { id, params } });

  it("drops a param the actor no longer declares", () => {
    const form = alignParams(stored({ zones: ["Lounge"], enabled: true, speed: "HIGH" }), caps);
    expect(toPayload(form, caps).action.params).toEqual({ zones: ["Lounge"], enabled: true });
  });

  it("fills in the default for a newly declared param", () => {
    const form = alignParams(stored({ zones: ["Lounge"] }), caps);
    expect(toPayload(form, caps).action.params).toEqual({ zones: ["Lounge"], enabled: false });
  });

  it("uses the default when a stored value has the wrong type", () => {
    const form = alignParams(stored({ temperature_c: "warm" }, "set_temperature"), caps);
    expect(toPayload(form, caps).action.params).toEqual({ temperature_c: 20 });
  });

  it("keeps a choice that is no longer offered so validateForm can flag it", () => {
    const form = alignParams(stored({ mode: "DRY" }, "system_on"), caps);
    expect(form.params.mode).toEqual({ kind: "value", value: "DRY" });
    expect(validateForm(form, caps)["param.mode"]).toBe('Mode "DRY" is no longer offered by the device. Pick another option.');
  });

  it("drops zones the device no longer has", () => {
    const form = alignParams(stored({ zones: ["Lounge", "Garage"], enabled: true }), caps);
    expect(toPayload(form, caps).action.params).toEqual({ zones: ["Lounge"], enabled: true });
  });

  it("leaves an undeclared action as stored", () => {
    const form = stored({ level: 3 }, "gone");
    expect(alignParams(form, caps)).toBe(form);
  });

  it("toPayload sends only declared param names", () => {
    const form = stored({ zones: ["Lounge"], enabled: true, speed: "HIGH" });
    expect(Object.keys(toPayload(form, caps).action.params)).toEqual(["zones", "enabled"]);
  });
});
