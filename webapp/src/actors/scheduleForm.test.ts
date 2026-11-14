import { describe, expect, it } from "vitest";
import {
  addAction,
  addCondition,
  alignParams,
  applyRestorable,
  conditionReadings,
  emptyForm,
  fromSchedule,
  MAX_ACTIONS,
  moveAction,
  removeAction,
  resetActionParams,
  restoreOptions,
  setTriggerType,
  toPayload,
  type ConditionRow,
  type ReadingRow,
  type SchedulePayload,
  type ScheduleFormState,
} from "./scheduleForm";
import { validateForm } from "./scheduleValidation";
import { serverProblems } from "./serverRules.testHelper";
import type { Capabilities, ConditionSpec, TriggerSpec } from "./types";
import type { DemandWindow } from "../billing/useBilling";

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
      { reading: "outdoor_temp_c", op: "gt", value: 30, enabled: true },
      { reading: "zone_temp_c", op: "lt", value: 18.5, target: "Bed 1", enabled: true },
    ],
    actions: [{ id: "set_zones", params: { zones: ["Lounge"], enabled: false } }],
    restore: {
      enabled: true, when: "after_minutes", minutes: 45, time: null,
      only_if: [{ reading: "battery_soc_pct", op: "gt", value: 20, enabled: true }],
      early_if: [{ reading: "indoor_temp_c", op: "gt", value: 27, enabled: true }],
    },
    retry: { max_attempts: 4, interval_seconds: 20, give_up_minutes: 10 },
    limits: { min_gap_minutes: 0, run_again_after: null }, notify_email: true,
  };
}

const triggers: TriggerSpec[] = [
  { type: "window_start", window_id: 11 },
  { type: "window_end", window_id: 12 },
  { type: "time", time: "06:30", days_of_week: [0, 2, 4] },
  { type: "price_above", c_per_kwh: 45.5 },
  { type: "temperature", reading: "outdoor_temp_c", op: "lt", value: 12.5 },
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
    // A saved schedule is not given a suggested re-arm value, so it loads with rearmAuto off.
    expect(fromSchedule(toPayload(form, caps))).toEqual({ ...form, trigger: { ...form.trigger, rearmAuto: false } });
  });

  it("keeps a restore at a set time and a number param", () => {
    const p: SchedulePayload = {
      ...payload({ type: "time", time: "16:00", days_of_week: [0, 1, 2, 3, 4, 5, 6] }),
      actions: [{ id: "set_temperature", params: { temperature_c: 24 } }],
      restore: { enabled: false, when: "at_time", minutes: null, time: "21:00", only_if: [], early_if: [] },
    };
    expect(toPayload(fromSchedule(p), caps)).toEqual(p);
  });

  it("unticked rows are saved with enabled false", () => {
    const form = fromSchedule(payload(triggers[0]));
    form.conditions[0].use = false;
    form.restore.earlyIf[0].use = false;
    const body = toPayload(form, caps);
    expect(body.conditions).toEqual([
      { reading: "outdoor_temp_c", op: "gt", value: 30, enabled: false },
      { reading: "zone_temp_c", op: "lt", value: 18.5, target: "Bed 1", enabled: true },
    ]);
    expect(body.restore.early_if).toEqual([{ reading: "indoor_temp_c", op: "gt", value: 27, enabled: false }]);
  });

  it("fromSchedule loads the tick state and treats a missing flag as ticked", () => {
    const p = payload(triggers[0]);
    p.conditions = [
      { reading: "outdoor_temp_c", op: "gt", value: 30, target: null, enabled: false },
      { reading: "zone_temp_c", op: "lt", value: 18.5, target: "Bed 1" },
    ];
    p.restore.only_if = [{ reading: "battery_soc_pct", op: "gt", value: 20, target: null, enabled: false }];
    p.restore.early_if = [{ reading: "indoor_temp_c", op: "gt", value: 27 }];
    const form = fromSchedule(p);
    expect(form.conditions.map((r) => r.use)).toEqual([false, true]);
    expect(readingRow(form.conditions, 0).target).toBeNull();
    expect(form.restore.onlyIf[0].use).toBe(false);
    expect(form.restore.earlyIf[0]).toMatchObject({ use: true, target: null });
  });
});

describe("unticked rows", () => {
  const junk = { kind: "reading" as const, op: "gt" as const, use: false };

  it("an unticked incomplete row does not block saving and sends a server-valid body", () => {
    const form = valid();
    form.conditions.push({ ...junk, key: 5, reading: "gone_reading", value: "", target: "Garage" });
    form.conditions.push({ ...junk, key: 6, reading: "indoor_temp_c", value: "abc", target: null });
    form.restore.onlyIf.push({ ...junk, key: 5, reading: "zone_temp_c", value: "", target: "Garage" });
    expect(validateForm(form, caps, [])).toEqual({});
    const body = toPayload(form, caps);
    expect(body.conditions.slice(2)).toEqual([
      { reading: "gone_reading", op: "gt", value: 0, target: "Garage", enabled: false },
      { reading: "indoor_temp_c", op: "gt", value: 0, enabled: false },
    ]);
    expect(body.restore.only_if[1]).toEqual({ reading: "zone_temp_c", op: "gt", value: 0, target: "Garage", enabled: false });
    expect(serverProblems(caps, body)).toEqual([]);
  });

  it("a ticked row with an empty value still blocks saving", () => {
    const form = valid();
    readingRow(form.conditions, 0).value = "";
    readingRow(form.restore.earlyIf, 0).value = "";
    const errors = validateForm(form, caps, []);
    expect(errors["conditions.0"]).toBe("Enter a number for Outdoor temperature.");
    expect(errors["restore.earlyIf.0"]).toBe("Enter a number for Indoor temperature.");
  });

  it("a stale target on a ticked row blocks saving, and an unticked one does not", () => {
    const form = valid();
    readingRow(form.conditions, 1).target = "Garage";
    readingRow(form.restore.earlyIf, 0).target = "Lounge";
    const errors = validateForm(form, caps, []);
    expect(errors["conditions.1"]).toBe('Target "Garage" is no longer offered for Zone temperature. Pick another.');
    expect(errors["restore.earlyIf.0"]).toBe('Target "Lounge" is no longer offered for Indoor temperature. Pick another.');
    form.conditions[1].use = false;
    form.restore.earlyIf[0].use = false;
    expect(validateForm(form, caps, [])).toEqual({});
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
    expect(form.actions).toMatchObject([{ actionId: "system_off", params: {} }]);
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
    expect(form.actions[0].actionId).toBe("system_on");
    expect(toPayload({ ...form, name: "x" }, caps).actions).toEqual([{ id: "system_on", params: { mode: "COOL" } }]);
  });
});

describe("changing action resets params", () => {
  it("replaces the params with the new action's defaults", () => {
    const form = fromSchedule(payload(triggers[0]));
    const next = resetActionParams(form, form.actions[0].key, caps.actions[2], caps);
    expect(next.actions[0].actionId).toBe("set_temperature");
    expect(toPayload(next, caps).actions).toEqual([{ id: "set_temperature", params: { temperature_c: 20 } }]);
  });

  it("gives multi_choice no ticks and bool false", () => {
    const next = resetActionParams(emptyForm(caps, context), 0, caps.actions[3], caps);
    expect(toPayload(next, caps).actions[0].params).toEqual({ zones: [], enabled: false });
  });

  it("turns restore off for an action that cannot be restored", () => {
    const next = resetActionParams(emptyForm(caps, context), 0, caps.actions[4], caps);
    expect(next.restore.enabled).toBe(false);
  });

  it("keeps restore off when another card cannot be restored", () => {
    const base = emptyForm(caps, context);
    const two = { ...base, actions: [...base.actions, { key: 1, actionId: "beep", params: {} }], restore: { ...base.restore, enabled: true } };
    const next = resetActionParams(two, 0, caps.actions[1], caps);
    expect(next.restore.enabled).toBe(false);
  });

  it("leaves the form alone for an unknown key", () => {
    const form = { ...emptyForm(caps, context), restore: { ...emptyForm(caps, context).restore, enabled: true } };
    expect(resetActionParams(form, 99, caps.actions[4], caps)).toBe(form);
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
    expect(rows).toEqual([{ key: 0, use: true, kind: "reading", reading: "indoor_temp_c", op: "gt", value: "", target: null }]);
    expect(addCondition(rows, conditionReadings(caps))[1].key).toBe(1);
  });
});

function readingRow(rows: ConditionRow[], index: number): ReadingRow {
  const row = rows[index];
  if (row.kind !== "reading") throw new Error(`row ${index} is not a reading row`);
  return row;
}

function valid(): ScheduleFormState {
  return fromSchedule(payload(triggers[2]));
}

describe("validateForm", () => {
  it("accepts a valid form", () => {
    expect(validateForm(valid(), caps, [])).toEqual({});
  });

  it("rejects out-of-range numbers", () => {
    const form = resetActionParams(valid(), 0, caps.actions[2], caps);
    form.actions[0].params.temperature_c = { kind: "number", text: "31" };
    expect(validateForm(form, caps, [])["actions.0.param.temperature_c"]).toBe("Temperature must be between 10 and 30.");
    form.actions[0].params.temperature_c = { kind: "number", text: "9.5" };
    expect(validateForm(form, caps, [])["actions.0.param.temperature_c"]).toBe("Temperature must be between 10 and 30.");
  });

  it("rejects empty required params", () => {
    const form = resetActionParams(valid(), 0, caps.actions[2], caps);
    form.actions[0].params.temperature_c = { kind: "number", text: "" };
    expect(validateForm(form, caps, [])["actions.0.param.temperature_c"]).toBe("Enter a number for Temperature.");
    const zonesForm = resetActionParams(valid(), 0, caps.actions[3], caps);
    expect(validateForm(zonesForm, caps, [])["actions.0.param.zones"]).toBe("Tick at least one option for Zones.");
  });

  it("rejects a bad HH:MM", () => {
    const form = valid();
    form.trigger.time = "24:00";
    expect(validateForm(form, caps, [])["trigger.time"]).toBe("Enter the time as HH:MM, for example 16:00.");
    form.trigger.time = "6:30";
    expect(validateForm(form, caps, [])["trigger.time"]).toBeDefined();
    const restore = valid();
    restore.restore.when = "at_time";
    restore.restore.time = "";
    expect(validateForm(restore, caps, [])["restore.time"]).toBe("Enter the restore time as HH:MM, for example 21:00.");
  });

  it("rejects values below the retry floors", () => {
    const form = valid();
    form.retry = { maxAttempts: "0", intervalSeconds: "4", giveUpMinutes: "0" };
    expect(validateForm(form, caps, [])).toEqual({
      "retry.maxAttempts": "Attempts must be a whole number of at least 1.",
      "retry.intervalSeconds": "Seconds between tries must be a whole number of at least 5.",
      "retry.giveUpMinutes": "Give up after must be a whole number of minutes, at least 1.",
    });
    form.retry = { maxAttempts: "2.5", intervalSeconds: "30", giveUpMinutes: "15" };
    expect(validateForm(form, caps, [])["retry.maxAttempts"]).toBeDefined();
  });

  it("rejects an empty name, no days, a missing window and price", () => {
    const form = valid();
    form.name = " ";
    form.trigger.days = [];
    const errors = validateForm(form, caps, []);
    expect(errors.name).toBe("Enter a name for this schedule.");
    expect(errors["trigger.days"]).toBe("Tick at least one day.");
    expect(validateForm({ ...valid(), trigger: { ...valid().trigger, type: "window_start", windowId: "" } }, caps, [])["trigger.windowId"]).toBe("Pick a demand window.");
    expect(validateForm({ ...valid(), trigger: { ...valid().trigger, type: "price_above", price: "abc" } }, caps, [])["trigger.price"]).toBe("Enter a price in c/kWh.");
  });

  it("rejects a ticked condition with no value and restore minutes below 1", () => {
    const form = valid();
    readingRow(form.conditions, 1).value = "";
    form.restore.minutes = "0";
    const errors = validateForm(form, caps, []);
    expect(errors["conditions.1"]).toBe("Enter a number for Zone temperature.");
    expect(errors["restore.minutes"]).toBe("Enter a whole number of minutes, at least 1.");
  });

  it("skips restore fields while restore is off", () => {
    const form = valid();
    form.restore.enabled = false;
    form.restore.minutes = "";
    readingRow(form.restore.earlyIf, 0).value = "";
    expect(validateForm(form, caps, [])).toEqual({});
  });

  it("asks for an early condition when restoring only on conditions", () => {
    const form = valid();
    form.restore.when = "conditions_only";
    form.restore.earlyIf = [];
    expect(validateForm(form, caps, [])["restore.earlyIf"]).toBe("Add a condition under Restore early when, or pick another time to restore.");
  });

  it("rejects an action the actor does not declare", () => {
    const form = valid();
    form.actions[0].actionId = "gone";
    expect(validateForm(form, caps, [])["actions.0.action"]).toBe("Pick an action this device supports.");
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
    ["undeclared reading", (f) => { readingRow(f.restore.onlyIf, 0).reading = "gone_reading"; }],
    ["stale target", (f) => { f.restore.earlyIf = [{ key: 0, use: true, kind: "reading", reading: "zone_temp_c", op: "gt", value: "20", target: "Garage" }]; }],
    ["target on a reading without targets", (f) => { readingRow(f.restore.earlyIf, 0).target = "Lounge"; }],
    ["target on a site reading", (f) => { readingRow(f.restore.onlyIf, 0).target = "Lounge"; }],
    ["no value", (f) => { readingRow(f.restore.earlyIf, 0).value = "abc"; }],
    ["unticked row with an undeclared reading", (f) => { f.restore.onlyIf[0] = { ...readingRow(f.restore.onlyIf, 0), use: false, reading: "gone_reading", value: "" }; }],
  ];

  it.each(garbage)("%s", (_name, spoil) => {
    const form = valid();
    spoil(form);
    form.restore.enabled = false;
    expect(validateForm(form, caps, [])).toEqual({});
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
    fromSchedule({ ...payload(triggers[2]), actions: [{ id, params }] });

  it("drops a param the actor no longer declares", () => {
    const form = alignParams(stored({ zones: ["Lounge"], enabled: true, speed: "HIGH" }), caps);
    expect(toPayload(form, caps).actions[0].params).toEqual({ zones: ["Lounge"], enabled: true });
  });

  it("fills in the default for a newly declared param", () => {
    const form = alignParams(stored({ zones: ["Lounge"] }), caps);
    expect(toPayload(form, caps).actions[0].params).toEqual({ zones: ["Lounge"], enabled: false });
  });

  it("uses the default when a stored value has the wrong type", () => {
    const form = alignParams(stored({ temperature_c: "warm" }, "set_temperature"), caps);
    expect(toPayload(form, caps).actions[0].params).toEqual({ temperature_c: 20 });
  });

  it("keeps a choice that is no longer offered so validateForm can flag it", () => {
    const form = alignParams(stored({ mode: "DRY" }, "system_on"), caps);
    expect(form.actions[0].params.mode).toEqual({ kind: "value", value: "DRY" });
    expect(validateForm(form, caps, [])["actions.0.param.mode"]).toBe('Mode "DRY" is no longer offered by the device. Pick another option.');
  });

  it("drops zones the device no longer has", () => {
    const form = alignParams(stored({ zones: ["Lounge", "Garage"], enabled: true }), caps);
    expect(toPayload(form, caps).actions[0].params).toEqual({ zones: ["Lounge"], enabled: true });
  });

  it("leaves an undeclared action as stored", () => {
    const form = stored({ level: 3 }, "gone");
    expect(alignParams(form, caps)).toEqual(form);
  });

  it("toPayload sends only declared param names", () => {
    const form = stored({ zones: ["Lounge"], enabled: true, speed: "HIGH" });
    expect(Object.keys(toPayload(form, caps).actions[0].params)).toEqual(["zones", "enabled"]);
  });
});

describe("a schedule with a list of actions", () => {
  const two = (): SchedulePayload => ({
    ...payload(triggers[2]),
    actions: [
      { id: "set_temperature", params: { temperature_c: 24 } },
      { id: "set_zones", params: { zones: ["Lounge"], enabled: true } },
      { id: "system_off", params: {} },
    ],
  });
  const keys = (form: ScheduleFormState) => form.actions.map((a) => a.key);
  const ids = (form: ScheduleFormState) => form.actions.map((a) => a.actionId);

  it("test_form_from_schedule_builds_one_card_per_action", () => {
    const form = fromSchedule(two());
    expect(ids(form)).toEqual(["set_temperature", "set_zones", "system_off"]);
    expect(new Set(keys(form)).size).toBe(3);
    expect(form.actions[0].params.temperature_c).toEqual({ kind: "number", text: "24" });
  });

  it("test_payload_sends_actions_in_order", () => {
    const p = two();
    const body = toPayload(fromSchedule(p), caps);
    expect(body.actions).toEqual(p.actions);
    expect(body).not.toHaveProperty("action");
    expect(serverProblems(caps, body)).toEqual([]);
  });

  it("test_add_remove_move_keep_stable_keys", () => {
    const form = fromSchedule(two());
    const [a, b, c] = keys(form);
    const added = addAction(form, caps);
    const d = added.actions[3].key;
    expect([a, b, c]).not.toContain(d);
    expect(added.actions[3]).toMatchObject({ actionId: "system_off", params: {} });
    const moved = moveAction(added, c, -1);
    expect(keys(moved)).toEqual([a, c, b, d]);
    expect(ids(moved)).toEqual(["set_temperature", "system_off", "set_zones", "system_off"]);
    expect(keys(moveAction(moved, a, -1))).toEqual([a, c, b, d]);
    expect(keys(moveAction(moved, d, 1))).toEqual([a, c, b, d]);
    expect(keys(removeAction(moved, c))).toEqual([a, b, d]);
    expect(keys(removeAction(moved, 99))).toEqual([a, c, b, d]);
  });

  it("test_cannot_remove_the_last_action", () => {
    const form = emptyForm(caps, context);
    expect(removeAction(form, form.actions[0].key)).toBe(form);
  });

  it("test_cannot_add_a_ninth_action", () => {
    let form = emptyForm(caps, context);
    for (let i = 1; i < MAX_ACTIONS; i += 1) form = addAction(form, caps);
    expect(form.actions).toHaveLength(MAX_ACTIONS);
    expect(addAction(form, caps)).toBe(form);
  });

  it("test_validation_reports_the_card_with_the_bad_param", () => {
    const form = fromSchedule(two());
    const second = form.actions[1].key;
    form.actions[1].params.zones = { kind: "value", value: [] };
    const errors = validateForm(form, caps, []);
    expect(Object.keys(errors)).toEqual([`actions.${second}.param.zones`]);
  });

  it("flags an empty list under the actions key", () => {
    expect(validateForm({ ...emptyForm(caps, context), name: "x", actions: [] }, caps, []).actions).toBeDefined();
  });

  it("keeps restore on only when every action can be restored", () => {
    const form = { ...fromSchedule(two()), actions: [...fromSchedule(two()).actions, { key: 9, actionId: "beep", params: {} }] };
    expect(applyRestorable(form, caps).restore.enabled).toBe(false);
    expect(applyRestorable(fromSchedule(two()), caps).restore.enabled).toBe(true);
  });

  it("the server rules refuse no actions, nine actions and a bad entry, naming the entry", () => {
    const p = two();
    expect(serverProblems(caps, { ...p, actions: [] })).not.toEqual([]);
    expect(serverProblems(caps, { ...p, actions: Array(9).fill(p.actions[2]) })).not.toEqual([]);
    const bad = { ...p, actions: [p.actions[0], { id: "set_temperature", params: { temperature_c: 99 } }] };
    expect(serverProblems(caps, bad).filter((m) => m.startsWith("actions[1]"))).toHaveLength(1);
  });
});

describe("calendar and demand window rows", () => {
  const calendar: ConditionSpec[] = [
    { kind: "months", op: "in", values: [12, 1, 2], enabled: true },
    { kind: "days", op: "not_in", values: [5, 6], enabled: true },
    { kind: "week", parity: "odd", enabled: true },
    { kind: "demand_window", op: "not_active", window_id: null, enabled: true },
    { kind: "demand_window", op: "active", window_id: 7, enabled: false },
  ];
  const window7 = { id: 7, label: "Peak" } as DemandWindow;

  function withCalendar(): ScheduleFormState {
    const base = valid();
    return fromSchedule({ ...toPayload(base, caps), conditions: calendar });
  }

  it("round trips through the form with months and days sorted", () => {
    const body = toPayload(withCalendar(), caps);
    expect(body.conditions).toEqual([
      { kind: "months", op: "in", values: [1, 2, 12], enabled: true },
      ...calendar.slice(1),
    ]);
  });

  it("sends reading rows without a kind", () => {
    const body = toPayload(valid(), caps);
    expect(body.conditions.every((c) => !("kind" in c))).toBe(true);
  });

  it("sends an unticked months row with nothing picked, and drops it ticked", () => {
    const form = valid();
    form.conditions = [{ key: 0, use: false, kind: "months", op: "in", values: [] }];
    expect(toPayload(form, caps).conditions).toEqual([{ kind: "months", op: "in", values: [], enabled: false }]);
    form.conditions = [{ key: 0, use: true, kind: "months", op: "in", values: [] }];
    expect(toPayload(form, caps).conditions).toEqual([]);
  });

  it("sends a ticked demand window row for any window as null", () => {
    const form = valid();
    form.conditions = [{ key: 0, use: true, kind: "demand_window", op: "not_active", windowId: "" }];
    expect(toPayload(form, caps).conditions).toEqual([{ kind: "demand_window", op: "not_active", window_id: null, enabled: true }]);
  });

  it("matches the server's rules", () => {
    expect(serverProblems(caps, toPayload(withCalendar(), caps))).toEqual([]);
  });

  it("blocks a ticked months or days row with nothing picked, but not an unticked one", () => {
    const form = valid();
    form.conditions = [
      { key: 0, use: true, kind: "months", op: "in", values: [] },
      { key: 1, use: true, kind: "days", op: "in", values: [] },
      { key: 2, use: false, kind: "days", op: "in", values: [] },
    ];
    const errors = validateForm(form, caps, [window7]);
    expect(errors["conditions.0"]).toBe("Pick at least one month.");
    expect(errors["conditions.1"]).toBe("Pick at least one day.");
    expect(errors["conditions.2"]).toBeUndefined();
  });

  it("blocks a ticked demand window row naming a window that is gone", () => {
    const form = valid();
    form.conditions = [{ key: 0, use: true, kind: "demand_window", op: "active", windowId: "9" }];
    expect(validateForm(form, caps, [window7])["conditions.0"]).toBe("This demand window is no longer available. Pick another.");
    form.conditions = [{ key: 0, use: true, kind: "demand_window", op: "active", windowId: "7" }];
    expect(validateForm(form, caps, [window7])).toEqual({});
  });
});
