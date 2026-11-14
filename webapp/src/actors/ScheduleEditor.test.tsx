import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "../api/client";
import { ScheduleDetail } from "./ScheduleDetail";
import { serverProblems } from "./serverRules.testHelper";
import type { ActorInstance, ActorSchedule, Capabilities } from "./types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const zones = [{ value: "Lounge", label: "Lounge" }, { value: "Bed 1", label: "Bed 1" }, { value: "Study", label: "Study" }];
const none = { min: null, max: null, unit: null, options: [] };

const caps: Capabilities = {
  version: 1,
  actions: [
    { id: "system_off", label: "Turn off", restorable: true, params: [] },
    {
      id: "system_on", label: "Turn on", restorable: true,
      params: [{ ...none, name: "mode", label: "Mode", type: "choice", options: [{ value: "COOL", label: "Cool" }, { value: "HEAT", label: "Heat" }] }],
    },
    { id: "set_temperature", label: "Set temperature", restorable: true, params: [{ ...none, name: "temperature_c", label: "Temperature", type: "number", min: 10, max: 30, unit: "C" }] },
    {
      id: "set_zones", label: "Switch zones", restorable: true,
      params: [
        { ...none, name: "zones", label: "Zones", type: "multi_choice", options: zones },
        { ...none, name: "enabled", label: "Zones on", type: "bool" },
      ],
    },
    {
      id: "set_zone_temperature", label: "Set zone temperature", restorable: true,
      params: [
        { ...none, name: "zones", label: "Zones", type: "multi_choice", options: zones },
        { ...none, name: "temperature_c", label: "Zone target", type: "number", min: 10, max: 30, unit: "C" },
      ],
    },
    { id: "beep", label: "Beep", restorable: false, params: [] },
  ],
  readings: [
    { id: "indoor_temp_c", label: "Indoor temperature", unit: "C", type: "number", target_options: null },
    { id: "outdoor_temp_c", label: "Outdoor temperature", unit: "C", type: "number", target_options: null },
    { id: "humidity_pct", label: "Indoor humidity", unit: "%", type: "number", target_options: null },
    { id: "is_on", label: "Unit", unit: null, type: "bool", target_options: null },
    { id: "zone_temp_c", label: "Zone temperature", unit: "C", type: "number", target_options: zones },
  ],
};

const instance: ActorInstance = {
  id: 3, site_id: 7, provider: "actron_neo", name: "Lounge AC", enabled: true, capabilities: caps,
  capabilities_updated_at: null, latest_readings: null, last_seen_at: null, auth_state: null,
};

const tariff = { id: 5, site_id: 7, effective_from: "2026-01-01T00:00:00Z", effective_to: null, service_availability_c_per_day: "100", monthly_fee_c: null };
const window11 = {
  id: 11, site_tariff_id: 5, effective_from: "2026-01-01T00:00:00Z", effective_to: null, label: "Weekday peak",
  months: [11, 12, 1, 2, 3], days_of_week: [0, 1, 2, 3, 4], start_time: "16:00:00", end_time: "21:00:00",
  rate_c_per_kw_per_day: "40", measurement_interval_minutes: 30,
};

const saved: ActorSchedule = {
  id: 21, instance_id: 3, name: "Peak off", enabled: true,
  trigger: { type: "window_start", window_id: 11 },
  conditions: [{ reading: "outdoor_temp_c", op: "gt", value: 30, target: null }],
  actions: [{ id: "set_temperature", params: { temperature_c: 26 } }],
  restore: { enabled: true, when: "window_end", minutes: null, time: null, only_if: [], early_if: [] },
  retry: { max_attempts: 3, interval_seconds: 30, give_up_minutes: 15 },
  limits: { min_gap_minutes: 0, run_again_after: null }, notify_email: false,
};

interface Api {
  windows?: unknown[];
  /** The sites list; left out, the editor does not know the site's time zone. */
  sites?: unknown[];
  failWith?: Error;
  /** Answer a PATCH with the schedule as the body leaves it, as the server does. */
  echo?: boolean;
}

let api: Api = {};

function mockApi(next: Api = {}) {
  api = next;
  vi.mocked(apiFetch).mockImplementation((path: string, init?: RequestInit) => {
    const { windows = [window11], failWith, echo, sites } = api;
    if (init?.method && failWith) return Promise.reject(failWith);
    if (init?.method === "POST") return Promise.resolve({ ...saved, id: 99 });
    if (init?.method === "PATCH") return Promise.resolve(echo ? { ...saved, ...JSON.parse(String(init.body)) } : saved);
    if (init?.method === "DELETE") return Promise.resolve(undefined);
    if (path === "/api/v1/sites" && sites) return Promise.resolve(sites);
    if (path.endsWith("/tariffs")) return Promise.resolve([tariff]);
    if (path.endsWith("/demand-windows")) return Promise.resolve(windows);
    return Promise.resolve([]);
  });
}

function renderDetail(schedule: ActorSchedule | null, canEdit = true, shown: ActorInstance = instance) {
  const onSelect = vi.fn();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ScheduleDetail siteId={7} schedule={schedule} instance={shown} canEdit={canEdit} onSelect={onSelect} />
    </QueryClientProvider>,
  );
  return { onSelect };
}

function writes(method: string) {
  return vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === method);
}

function body(method: string) {
  const call = writes(method)[0];
  return JSON.parse(String(call[1]?.body));
}

async function ready() {
  return screen.findByRole("combobox", { name: "Trigger" });
}

const step = (name: string) => screen.getByRole("heading", { name, level: 3 }).closest("section") as HTMLElement;

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  mockApi();
});

describe("ScheduleEditor", () => {
  it("action list comes only from declared capabilities", async () => {
    renderDetail(null);
    await ready();
    const options = within(screen.getByRole("combobox", { name: "Action" })).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["Turn off", "Turn on", "Set temperature", "Switch zones", "Set zone temperature", "Beep"]);
  });

  it("changing action resets params", async () => {
    const user = userEvent.setup();
    renderDetail(saved);
    await ready();
    expect(screen.getByRole("spinbutton", { name: /Temperature/ })).toHaveValue(26);
    await user.selectOptions(screen.getByRole("combobox", { name: "Action" }), "system_on");
    expect(screen.queryByRole("spinbutton", { name: /Temperature/ })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Mode" })).toHaveValue("COOL");
    await user.selectOptions(screen.getByRole("combobox", { name: "Action" }), "set_temperature");
    expect(screen.getByRole("spinbutton", { name: /Temperature/ })).toHaveValue(20);
  });

  it("restore step shows window wording only for window triggers", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    expect(within(step("When it ends")).getByRole("option", { name: "When the window ends" })).toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "Trigger" }), "time");
    expect(within(step("When it ends")).queryByText(/window/i)).not.toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "Trigger" }), "price_above");
    expect(within(step("When it ends")).queryByText(/window/i)).not.toBeInTheDocument();
  });

  it("a window_end trigger does not offer the window-end restore option", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.selectOptions(screen.getByRole("combobox", { name: "Trigger" }), "window_end");
    const when = screen.getByRole("combobox", { name: "When to put it back" });
    expect(within(when).queryByRole("option", { name: /window ends/i })).not.toBeInTheDocument();
    expect(when).toHaveValue("after_minutes");
  });

  it("restore off hides the restore controls", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.click(screen.getByRole("checkbox", { name: "Put it back how it was before" }));
    expect(screen.queryByRole("combobox", { name: "When to put it back" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Only restore if" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Restore early when" })).not.toBeInTheDocument();
  });

  it("multi_choice zones render one tick box per option", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.selectOptions(screen.getByRole("combobox", { name: "Action" }), "set_zones");
    const group = screen.getByRole("group", { name: "Zones" });
    expect(within(group).getAllByRole("checkbox").map((c) => c.getAttribute("aria-label") ?? c.closest("label")?.textContent))
      .toEqual(["Lounge", "Bed 1", "Study"]);
    expect(screen.getByRole("checkbox", { name: "Zones on" })).not.toBeChecked();
  });

  it("a target select appears for a reading that declares target_options", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    const onlyIf = screen.getByRole("group", { name: "Only if" });
    await user.click(within(onlyIf).getByRole("button", { name: "Add condition" }));
    expect(within(onlyIf).queryByRole("combobox", { name: "Target 1" })).not.toBeInTheDocument();
    await user.selectOptions(within(onlyIf).getByRole("combobox", { name: "Reading 1" }), "zone_temp_c");
    const target = within(onlyIf).getByRole("combobox", { name: "Target 1" });
    expect(within(target).getAllByRole("option").map((o) => o.textContent)).toEqual(["Lounge", "Bed 1", "Study"]);
    expect(target).toHaveValue("Lounge");
  });

  it("an on/off reading shows 'is' and an On/Off select instead of a comparison and a value", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Only when on");
    const onlyIf = screen.getByRole("group", { name: "Only if" });
    await user.click(within(onlyIf).getByRole("button", { name: "Add condition" }));
    await user.selectOptions(within(onlyIf).getByRole("combobox", { name: "Reading 1" }), "is_on");
    expect(within(onlyIf).queryByRole("combobox", { name: "Comparison 1" })).not.toBeInTheDocument();
    expect(within(onlyIf).queryByRole("spinbutton", { name: "Value 1" })).not.toBeInTheDocument();
    const state = within(onlyIf).getByRole("combobox", { name: "State 1" });
    expect(state).toHaveValue("on");
    await user.selectOptions(state, "off");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(body("POST").conditions).toEqual([{ reading: "is_on", op: "lt", value: 1, enabled: true }]);
  });

  it("a temperature trigger saves the reading, direction and level", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Hot day");
    await user.selectOptions(screen.getByRole("combobox", { name: "Trigger" }), "temperature");
    await user.selectOptions(screen.getByRole("combobox", { name: "Temperature" }), "indoor_temp_c");
    await user.selectOptions(screen.getByRole("combobox", { name: "Crosses" }), "lt");
    await user.type(screen.getByRole("spinbutton", { name: "Level (C)" }), "18");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    // The re-arm value starts two degrees back inside the level, and the gap starts at 30 minutes.
    expect(body("POST").trigger).toEqual({ type: "temperature", reading: "indoor_temp_c", op: "lt", value: 18, rearm: 20 });
    expect(body("POST").limits).toEqual({ min_gap_minutes: 30, run_again_after: null });
  });

  it("the suggested re-arm value follows the level until it is typed over", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.selectOptions(screen.getByRole("combobox", { name: "Trigger" }), "temperature");
    const rearm = screen.getByRole("spinbutton", { name: /Can fire again once it is back/ });
    await user.type(screen.getByRole("spinbutton", { name: "Level (C)" }), "30");
    expect(rearm).toHaveValue(28);
    await user.clear(rearm);
    await user.type(rearm, "25");
    await user.clear(screen.getByRole("spinbutton", { name: "Level (C)" }));
    await user.type(screen.getByRole("spinbutton", { name: "Level (C)" }), "32");
    expect(rearm).toHaveValue(25);
  });

  it("a sun trigger saves its event, offset and days", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Evening");
    await user.selectOptions(screen.getByRole("combobox", { name: "Trigger" }), "sun");
    await user.selectOptions(screen.getByRole("combobox", { name: "Event" }), "sunrise");
    await user.type(screen.getByRole("spinbutton", { name: "Minutes after (negative is before)" }), "{Control>}a{/Control}-20");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(body("POST").trigger).toEqual({ type: "sun", event: "sunrise", offset_minutes: -20, days_of_week: [0, 1, 2, 3, 4, 5, 6] });
  });

  it("says sun times are estimated while the site has no location", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.selectOptions(screen.getByRole("combobox", { name: "Trigger" }), "sun");
    expect(screen.getByText(/estimated from the site's time zone/)).toBeInTheDocument();
  });

  it("a time-of-day row saves its two times", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Evenings");
    await user.selectOptions(screen.getByRole("combobox", { name: "Trigger" }), "sun");
    await user.click(within(step("Only if")).getByRole("button", { name: "Add condition" }));
    await user.selectOptions(within(step("Only if")).getByRole("combobox", { name: "Reading 1" }), "time_of_day");
    await user.selectOptions(screen.getByRole("combobox", { name: "Start 1 type" }), "clock");
    await user.type(screen.getByRole("textbox", { name: "Start 1 time" }), "18:00");
    await user.selectOptions(screen.getByRole("combobox", { name: "End 1 type" }), "clock");
    await user.type(screen.getByRole("textbox", { name: "End 1 time" }), "23:00");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(body("POST").conditions).toContainEqual({
      kind: "time_of_day", start: { kind: "clock", time: "18:00" }, end: { kind: "clock", time: "23:00" }, enabled: true,
    });
  });

  it("the How often step saves the gap and the run-again time", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Limited");
    await user.selectOptions(screen.getByRole("combobox", { name: "Trigger" }), "sun");
    const how = step("How often");
    await user.clear(within(how).getByRole("spinbutton", { name: "Minutes between runs" }));
    await user.type(within(how).getByRole("spinbutton", { name: "Minutes between runs" }), "45");
    await user.click(within(how).getByRole("checkbox", { name: "Do not run again until" }));
    await user.selectOptions(within(how).getByRole("combobox", { name: "Run again after type" }), "sunrise");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(body("POST").limits).toEqual({ min_gap_minutes: 45, run_again_after: { kind: "sun", event: "sunrise", offset_minutes: 0 } });
  });

  it("flags a bad gap in the How often step", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Limited");
    await user.selectOptions(screen.getByRole("combobox", { name: "Trigger" }), "sun");
    await user.clear(screen.getByRole("spinbutton", { name: "Minutes between runs" }));
    await user.type(screen.getByRole("spinbutton", { name: "Minutes between runs" }), "2000");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(screen.getByText(/from 0 to 1440/)).toBeInTheDocument();
    expect(writes("POST")).toHaveLength(0);
  });

  it("a temperature trigger without a level is not saved", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Hot day");
    await user.selectOptions(screen.getByRole("combobox", { name: "Trigger" }), "temperature");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(screen.getByText("Enter a temperature in C.")).toBeInTheDocument();
    expect(writes("POST")).toHaveLength(0);
  });

  it("save posts the payload and shows Saved", async () => {
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Peak off");
    const onlyIf = screen.getByRole("group", { name: "Only if" });
    await user.click(within(onlyIf).getByRole("button", { name: "Add condition" }));
    await user.selectOptions(within(onlyIf).getByRole("combobox", { name: "Reading 1" }), "outdoor_temp_c");
    await user.type(within(onlyIf).getByRole("spinbutton", { name: "Value 1" }), "30");
    await user.selectOptions(screen.getByRole("combobox", { name: "Action" }), "set_zones");
    await user.click(screen.getByRole("checkbox", { name: "Bed 1" }));
    await user.click(screen.getByRole("checkbox", { name: "Email me if it still fails" }));
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    const [path] = writes("POST")[0];
    expect(path).toBe("/api/v1/sites/7/actors/schedules");
    expect(body("POST")).toEqual({
      instance_id: 3,
      name: "Peak off",
      enabled: true,
      trigger: { type: "window_start", window_id: 11 },
      conditions: [{ reading: "outdoor_temp_c", op: "gt", value: 30, enabled: true }],
      actions: [{ id: "set_zones", params: { zones: ["Bed 1"], enabled: false } }],
      restore: { enabled: true, when: "window_end", minutes: null, time: null, only_if: [], early_if: [] },
      retry: { max_attempts: 3, interval_seconds: 30, give_up_minutes: 15 },
      limits: { min_gap_minutes: 0, run_again_after: null },
      notify_email: true,
    });
  });

  it("create calls onSelect with the new id", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderDetail(null);
    await ready();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Peak off");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith({ kind: "schedule", id: 99 }));
  });

  it("update sends the full body", async () => {
    const user = userEvent.setup();
    renderDetail(saved);
    await ready();
    const temperature = screen.getByRole("spinbutton", { name: /Temperature/ });
    await user.clear(temperature);
    await user.type(temperature, "24");
    await user.click(screen.getByRole("switch", { name: "Enabled" }));
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    const [path] = writes("PATCH")[0];
    expect(path).toBe("/api/v1/sites/7/actors/schedules/21");
    const { id: _id, instance_id: _instance, ...rest } = saved;
    expect(body("PATCH")).toEqual({
      ...rest,
      enabled: false,
      conditions: [{ reading: "outdoor_temp_c", op: "gt", value: 30, enabled: true }],
      actions: [{ id: "set_temperature", params: { temperature_c: 24 } }],
    });
  });

  it("server 422 message is shown", async () => {
    mockApi({ failWith: new ApiError(422, "invalid_schedule", "Window 11 is not a demand window of this site", null) });
    const user = userEvent.setup();
    renderDetail(saved);
    await ready();
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Window 11 is not a demand window of this site");
  });

  it("client validation blocks the request", async () => {
    const user = userEvent.setup();
    renderDetail(saved);
    await ready();
    const temperature = screen.getByRole("spinbutton", { name: /Temperature/ });
    await user.clear(temperature);
    await user.type(temperature, "35");
    await user.clear(screen.getByRole("textbox", { name: "Name" }));
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    const alerts = screen.getAllByRole("alert").map((a) => a.textContent);
    expect(alerts).toContain("Temperature must be between 10 and 30.");
    expect(alerts).toContain("Enter a name for this schedule.");
    expect(writes("PATCH")).toHaveLength(0);
    expect(screen.getByRole("spinbutton", { name: /Temperature/ })).toHaveAttribute("aria-invalid", "true");
  });

  it("viewer sees values read-only without buttons", async () => {
    renderDetail(saved, false);
    await ready();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Peak off");
    expect(screen.getByRole("textbox", { name: "Name" })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "Action" })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: /Temperature/ })).toHaveValue(26);
    expect(screen.queryByRole("button", { name: "Save schedule" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete schedule" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add condition" })).not.toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Enabled" })).toBeDisabled();
  });

  it("delete asks for confirmation then calls the hook and calls onSelect(null)", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderDetail(saved);
    await ready();
    await user.click(screen.getByRole("button", { name: "Delete schedule" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete this schedule?" });
    expect(writes("DELETE")).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(null));
    expect(writes("DELETE")[0][0]).toBe("/api/v1/sites/7/actors/schedules/21");
  });

  it("a 409 on delete shows the server's message", async () => {
    mockApi({ failWith: new ApiError(409, "unit_held_off", "The unit is still held off; wait for the restore", null) });
    const user = userEvent.setup();
    const { onSelect } = renderDetail(saved);
    await ready();
    await user.click(screen.getByRole("button", { name: "Delete schedule" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The unit is still held off; wait for the restore");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("a 409 on save shows the server's message", async () => {
    const message = "This schedule is holding the unit off. Change its trigger, action or restore after the unit is put back.";
    mockApi({ failWith: new ApiError(409, "unit_held_off", message, null) });
    const user = userEvent.setup();
    renderDetail(saved);
    await ready();
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
  });

  it("names the Enabled switch from its visible label", async () => {
    renderDetail(saved);
    await ready();
    const toggle = screen.getByRole("switch", { name: "Enabled" });
    expect(toggle).not.toHaveAttribute("aria-label");
    expect(document.getElementById(toggle.getAttribute("aria-labelledby") ?? "")).toHaveTextContent("Enabled");
  });

  it("links the days error to the days group", async () => {
    const user = userEvent.setup();
    renderDetail({ ...saved, trigger: { type: "time", time: "16:00", days_of_week: [0] }, restore: { ...saved.restore, when: "after_minutes", minutes: 60 } });
    await ready();
    const days = screen.getByRole("group", { name: "Days" });
    await user.click(within(days).getByRole("checkbox", { name: "Mon" }));
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    const error = await screen.findByText("Tick at least one day.");
    expect(days).toHaveAttribute("aria-describedby", error.id);
    expect(days).not.toHaveAttribute("aria-invalid");
  });

  it("says a conditions-only restore falls back to 24 hours", async () => {
    const user = userEvent.setup();
    renderDetail(saved);
    await ready();
    await user.selectOptions(screen.getByRole("combobox", { name: "When to put it back" }), "conditions_only");
    expect(screen.getByText(/If it never happens, the unit is put back after 24 hours\./)).toBeInTheDocument();
  });

  it("offers windows of the current tariff in words", async () => {
    renderDetail(saved);
    await ready();
    expect(screen.getByRole("combobox", { name: "Demand window" })).toHaveDisplayValue("Weekday peak (Nov-Mar, Mon-Fri, 16:00-21:00)");
  });

  it("disables window triggers when the tariff has no demand windows", async () => {
    mockApi({ windows: [] });
    renderDetail(null);
    const trigger = await ready();
    expect(trigger).toHaveValue("time");
    expect(within(trigger).getByRole("option", { name: "A demand window starts" })).toBeDisabled();
    expect(screen.getByText("This site's tariff has no demand windows.")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "When to put it back" })).toHaveValue("after_minutes");
  });

  it("condition rows can be removed", async () => {
    const user = userEvent.setup();
    renderDetail(saved);
    await ready();
    const onlyIf = screen.getByRole("group", { name: "Only if" });
    expect(within(onlyIf).getByRole("combobox", { name: "Reading 1" })).toHaveValue("outdoor_temp_c");
    await user.click(within(onlyIf).getByRole("button", { name: "Remove condition 1" }));
    expect(within(onlyIf).queryByRole("combobox", { name: "Reading 1" })).not.toBeInTheDocument();
  });

  it("restore off with bad hidden fields still saves a body the server accepts", async () => {
    const user = userEvent.setup();
    const stale: ActorSchedule = {
      ...saved,
      restore: {
        enabled: true, when: "after_minutes", minutes: 30, time: null,
        only_if: [{ reading: "gone_reading", op: "gt", value: 1, target: null }],
        early_if: [{ reading: "zone_temp_c", op: "gt", value: 26, target: "Garage" }],
      },
    };
    renderDetail(stale);
    await ready();
    const minutes = screen.getByRole("spinbutton", { name: "Minutes after it runs" });
    await user.clear(minutes);
    await user.type(minutes, "0");
    await user.click(screen.getByRole("checkbox", { name: "Put it back how it was before" }));
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    const sent = { ...body("PATCH"), instance_id: 3 };
    expect(sent.restore.enabled).toBe(false);
    expect(serverProblems(caps, sent)).toEqual([]);
  });

  it("a schedule with a removed param saves without the stale key", async () => {
    const user = userEvent.setup();
    renderDetail({ ...saved, actions: [{ id: "set_temperature", params: { temperature_c: 26, fan_mode: "HIGH" } }] });
    await ready();
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(body("PATCH").actions).toEqual([{ id: "set_temperature", params: { temperature_c: 26 } }]);
  });

  it("a schedule with an added param shows the default and saves it", async () => {
    const user = userEvent.setup();
    renderDetail({ ...saved, actions: [{ id: "system_on", params: {} }] });
    await ready();
    expect(screen.getByRole("combobox", { name: "Mode" })).toHaveValue("COOL");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(body("PATCH").actions).toEqual([{ id: "system_on", params: { mode: "COOL" } }]);
  });

  it("a stored choice that is no longer offered is shown as such and blocks saving", async () => {
    const user = userEvent.setup();
    renderDetail({ ...saved, actions: [{ id: "system_on", params: { mode: "DRY" } }] });
    await ready();
    const mode = screen.getByRole("combobox", { name: "Mode" });
    expect(mode).toHaveValue("DRY");
    expect(mode).toHaveDisplayValue("DRY (no longer offered)");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(screen.getByRole("alert")).toHaveTextContent('Mode "DRY" is no longer offered by the device. Pick another option.');
    expect(writes("PATCH")).toHaveLength(0);
  });

  it("an undeclared action still renders and blocks saving", async () => {
    const user = userEvent.setup();
    renderDetail({ ...saved, actions: [{ id: "gone", params: { level: 3 } }] });
    await ready();
    expect(screen.getByRole("combobox", { name: "Action" })).toHaveDisplayValue("gone (not offered by this device)");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Pick an action this device supports.");
    expect(writes("PATCH")).toHaveLength(0);
  });

  it("a stale target on a ticked row is shown as no longer offered and blocks saving, and clears after picking a valid one", async () => {
    const user = userEvent.setup();
    renderDetail({ ...saved, conditions: [{ reading: "zone_temp_c", op: "gt", value: 26, target: "Garage", enabled: true }] });
    await ready();
    const onlyIf = screen.getByRole("group", { name: "Only if" });
    const target = within(onlyIf).getByRole("combobox", { name: "Target 1" });
    expect(target).toHaveValue("Garage");
    expect(target).toHaveDisplayValue("Garage (no longer offered)");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    const message = 'Target "Garage" is no longer offered for Zone temperature. Pick another.';
    expect(within(onlyIf).getByText(message)).toBeInTheDocument();
    expect(writes("PATCH")).toHaveLength(0);
    await user.selectOptions(target, "Study");
    expect(within(onlyIf).queryByText(message)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(body("PATCH").conditions).toEqual([{ reading: "zone_temp_c", op: "gt", value: 26, target: "Study", enabled: true }]);
  });

  it("a target on a reading that no longer takes one is shown and can be cleared", async () => {
    const user = userEvent.setup();
    renderDetail({ ...saved, conditions: [{ reading: "indoor_temp_c", op: "gt", value: 26, target: "Lounge", enabled: true }] });
    await ready();
    const onlyIf = screen.getByRole("group", { name: "Only if" });
    const target = within(onlyIf).getByRole("combobox", { name: "Target 1" });
    expect(target).toHaveDisplayValue("Lounge (no longer offered)");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(within(onlyIf).getByText('Target "Lounge" is no longer offered for Indoor temperature. Pick another.')).toBeInTheDocument();
    await user.selectOptions(target, "");
    expect(within(onlyIf).queryByRole("combobox", { name: "Target 1" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(body("PATCH").conditions).toEqual([{ reading: "indoor_temp_c", op: "gt", value: 26, enabled: true }]);
  });

  it("the editor shows an unticked row after Save and after re-opening the schedule", async () => {
    mockApi({ echo: true });
    const user = userEvent.setup();
    renderDetail(saved);
    await ready();
    const onlyIf = () => screen.getByRole("group", { name: "Only if" });
    const tick = () => within(onlyIf()).getByRole("checkbox", { name: "Use condition 1" });
    expect(tick()).toBeChecked();
    await user.click(tick());
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(tick()).not.toBeChecked();
    const stored: ActorSchedule = { ...saved, ...body("PATCH") };
    cleanup();
    renderDetail(stored);
    await ready();
    expect(tick()).not.toBeChecked();
    expect(within(onlyIf()).getByRole("combobox", { name: "Reading 1" })).toHaveValue("outdoor_temp_c");
    expect(within(onlyIf()).getByRole("spinbutton", { name: "Value 1" })).toHaveValue(30);
  });

  describe("action list", () => {
    const cards = () => screen.getAllByRole("group", { name: /^Action \d+$/ });
    const card = (n: number) => screen.getByRole("group", { name: `Action ${n}` });
    const save = (user: ReturnType<typeof userEvent.setup>) => user.click(screen.getByRole("button", { name: "Save schedule" }));

    it("adds a second action card", async () => {
      const user = userEvent.setup();
      renderDetail(saved);
      await ready();
      expect(cards()).toHaveLength(1);
      await user.click(screen.getByRole("button", { name: "Add action" }));
      expect(cards()).toHaveLength(2);
      expect(within(card(2)).getByRole("combobox", { name: "Action" })).toHaveValue("system_off");
    });

    it("removes a card but not the last", async () => {
      const user = userEvent.setup();
      renderDetail(saved);
      await ready();
      expect(screen.getByRole("button", { name: "Remove action 1" })).toBeDisabled();
      await user.click(screen.getByRole("button", { name: "Add action" }));
      await user.click(screen.getByRole("button", { name: "Remove action 2" }));
      expect(cards()).toHaveLength(1);
      expect(screen.getByRole("button", { name: "Remove action 1" })).toBeDisabled();
    });

    it("moves a card down and the payload order follows", async () => {
      const user = userEvent.setup();
      renderDetail(saved);
      await ready();
      await user.click(screen.getByRole("button", { name: "Add action" }));
      expect(screen.getByRole("button", { name: "Move action 1 up" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Move action 2 down" })).toBeDisabled();
      await user.click(screen.getByRole("button", { name: "Move action 1 down" }));
      expect(within(card(1)).getByRole("combobox", { name: "Action" })).toHaveValue("system_off");
      await save(user);
      expect(await screen.findByText(/Saved/)).toBeInTheDocument();
      expect(body("PATCH").actions).toEqual([{ id: "system_off", params: {} }, { id: "set_temperature", params: { temperature_c: 26 } }]);
    });

    it("shows the error on the card it belongs to", async () => {
      const user = userEvent.setup();
      renderDetail(saved);
      await ready();
      await user.click(screen.getByRole("button", { name: "Add action" }));
      await user.selectOptions(within(card(2)).getByRole("combobox", { name: "Action" }), "set_temperature");
      await user.clear(within(card(2)).getByRole("spinbutton", { name: /Temperature/ }));
      await save(user);
      expect(within(card(2)).getByText("Enter a number for Temperature.")).toBeInTheDocument();
      expect(within(card(1)).queryByText(/Enter a number/)).not.toBeInTheDocument();
      expect(writes("PATCH")).toHaveLength(0);
    });

    it("shows the allowed range hint for a zone temperature card", async () => {
      const user = userEvent.setup();
      const readings = { zone_ranges_c: { Lounge: [18, 26.5] } } as unknown as ActorInstance["latest_readings"];
      renderDetail(saved, true, { ...instance, latest_readings: readings });
      await ready();
      await user.selectOptions(screen.getByRole("combobox", { name: "Action" }), "set_zone_temperature");
      expect(screen.queryByText(/Allowed now/)).not.toBeInTheDocument();
      await user.click(screen.getByRole("checkbox", { name: "Lounge" }));
      expect(screen.getByText("Allowed now: 18–26.5°C")).toBeInTheDocument();
      expect(screen.getByText("Order matters: an earlier temperature change moves this range.")).toBeInTheDocument();
      await user.click(screen.getByRole("checkbox", { name: "Study" }));
      expect(screen.queryByText(/Allowed now/)).not.toBeInTheDocument();
      expect(screen.getByText(/Order matters/)).toBeInTheDocument();
    });

    it("shows the range all ticked zones allow", async () => {
      const user = userEvent.setup();
      const readings = { zone_ranges_c: { Lounge: [18, 26.5], Study: [20, 25] } } as unknown as ActorInstance["latest_readings"];
      renderDetail(saved, true, { ...instance, latest_readings: readings });
      await ready();
      await user.selectOptions(screen.getByRole("combobox", { name: "Action" }), "set_zone_temperature");
      await user.click(screen.getByRole("checkbox", { name: "Lounge" }));
      await user.click(screen.getByRole("checkbox", { name: "Study" }));
      expect(screen.getByText("Allowed now: 20–25°C")).toBeInTheDocument();
    });

    it("shows only the order line when there are no readings", async () => {
      const user = userEvent.setup();
      renderDetail(saved);
      await ready();
      await user.selectOptions(screen.getByRole("combobox", { name: "Action" }), "set_zone_temperature");
      expect(screen.queryByText(/Allowed now/)).not.toBeInTheDocument();
      expect(screen.getByText(/Order matters/)).toBeInTheDocument();
    });

    it("hides Add action at eight cards", async () => {
      const user = userEvent.setup();
      renderDetail(saved);
      await ready();
      for (let i = 1; i < 8; i += 1) await user.click(screen.getByRole("button", { name: "Add action" }));
      expect(cards()).toHaveLength(8);
      expect(screen.queryByRole("button", { name: "Add action" })).not.toBeInTheDocument();
    });

    it("keeps the error for an invalid second card visible after Save", async () => {
      const user = userEvent.setup();
      renderDetail(saved);
      await ready();
      await user.click(screen.getByRole("button", { name: "Add action" }));
      await user.selectOptions(within(card(2)).getByRole("combobox", { name: "Action" }), "set_zones");
      await save(user);
      expect(within(card(2)).getByText("Tick at least one option for Zones.")).toBeInTheDocument();
      expect(writes("PATCH")).toHaveLength(0);
    });

    it("turns restore off and disables it when a card cannot be restored", async () => {
      const user = userEvent.setup();
      renderDetail(saved);
      await ready();
      const restore = () => screen.getByRole("checkbox", { name: "Put it back how it was before" });
      expect(restore()).toBeChecked();
      await user.click(screen.getByRole("button", { name: "Add action" }));
      await user.selectOptions(within(card(2)).getByRole("combobox", { name: "Action" }), "beep");
      expect(restore()).toBeDisabled();
      expect(restore()).not.toBeChecked();
      await save(user);
      expect(await screen.findByText(/Saved/)).toBeInTheDocument();
      expect(body("PATCH").restore.enabled).toBe(false);
    });
  });
});

describe("calendar and demand window conditions", () => {
  const sydney = { id: 7, name: "Home", timezone: "Australia/Sydney" };

  async function openNew(on = "2026-11-14T02:00:00Z") {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date(on) });
    mockApi({ sites: [sydney] });
    const user = userEvent.setup();
    renderDetail(null);
    await ready();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Summer cool");
    return { user, onlyIf: screen.getByRole("group", { name: "Only if" }) };
  }

  async function addRow(user: ReturnType<typeof userEvent.setup>, group: HTMLElement, kind: string, n = 1) {
    await user.click(within(group).getByRole("button", { name: "Add condition" }));
    await user.selectOptions(within(group).getByRole("combobox", { name: `Reading ${n}` }), kind);
  }

  const submit = (user: ReturnType<typeof userEvent.setup>) => user.click(screen.getByRole("button", { name: "Save schedule" }));

  afterEach(() => vi.useRealTimers());

  it("a month row sends the months picked", async () => {
    const { user, onlyIf } = await openNew();
    await addRow(user, onlyIf, "months");
    for (const name of ["Dec", "Jan", "Feb"]) await user.click(within(onlyIf).getByRole("button", { name }));
    expect(within(onlyIf).getByRole("button", { name: "Jan" })).toHaveAttribute("aria-pressed", "true");
    await submit(user);
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(body("POST").conditions).toEqual([{ kind: "months", op: "in", values: [1, 2, 12], enabled: true }]);
  });

  it("a ticked month row with nothing picked blocks the save", async () => {
    const { user, onlyIf } = await openNew();
    await addRow(user, onlyIf, "months");
    await submit(user);
    expect(await within(onlyIf).findByText("Pick at least one month.")).toBeInTheDocument();
    expect(writes("POST")).toHaveLength(0);
  });

  it("a day row sends 'is not' with the days picked", async () => {
    const { user, onlyIf } = await openNew();
    await addRow(user, onlyIf, "days");
    await user.selectOptions(within(onlyIf).getByRole("combobox", { name: "Day comparison 1" }), "not_in");
    for (const name of ["Sat", "Sun"]) await user.click(within(onlyIf).getByRole("button", { name }));
    await submit(user);
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(body("POST").conditions).toEqual([{ kind: "days", op: "not_in", values: [5, 6], enabled: true }]);
  });

  it("a week row starts on the parity of the current week at the site", async () => {
    const odd = await openNew("2026-11-14T02:00:00Z");
    await addRow(odd.user, odd.onlyIf, "week");
    expect(within(odd.onlyIf).getByRole("combobox", { name: "Week parity 1" })).toHaveValue("odd");
    await submit(odd.user);
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(body("POST").conditions).toEqual([{ kind: "week", parity: "odd", enabled: true }]);
  });

  it("a week row is even in an even week, by the site's calendar day", async () => {
    const even = await openNew("2026-11-15T14:00:00Z");
    await addRow(even.user, even.onlyIf, "week");
    expect(within(even.onlyIf).getByRole("combobox", { name: "Week parity 1" })).toHaveValue("even");
  });

  it("a demand window row sends any window as null and a chosen window as its id", async () => {
    const { user, onlyIf } = await openNew();
    await addRow(user, onlyIf, "demand_window");
    expect(within(onlyIf).getByRole("combobox", { name: "Window state 1" })).toHaveValue("not_active");
    await submit(user);
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(body("POST").conditions).toEqual([{ kind: "demand_window", op: "not_active", window_id: null, enabled: true }]);
    await user.selectOptions(within(onlyIf).getByRole("combobox", { name: "Window 1" }), "11");
    await user.selectOptions(within(onlyIf).getByRole("combobox", { name: "Window state 1" }), "active");
    await submit(user);
    await waitFor(() => expect(writes("POST").length + writes("PATCH").length).toBe(2));
    const sent = [...writes("POST"), ...writes("PATCH")].map(([, init]) => JSON.parse(String(init?.body)));
    expect(sent.map((b) => b.conditions[0])).toContainEqual({ kind: "demand_window", op: "active", window_id: 11, enabled: true });
  });

  it("saves the summer example in order", async () => {
    const { user, onlyIf } = await openNew();
    await addRow(user, onlyIf, "months");
    for (const name of ["Dec", "Jan", "Feb"]) await user.click(within(onlyIf).getByRole("button", { name }));
    await addRow(user, onlyIf, "outdoor_temp_c", 2);
    await user.type(within(onlyIf).getByRole("spinbutton", { name: "Value 2" }), "30");
    await addRow(user, onlyIf, "demand_window", 3);
    await submit(user);
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(body("POST").conditions).toEqual([
      { kind: "months", op: "in", values: [1, 2, 12], enabled: true },
      { reading: "outdoor_temp_c", op: "gt", value: 30, enabled: true },
      { kind: "demand_window", op: "not_active", window_id: null, enabled: true },
    ]);
  });

  it("opens a saved days row with its buttons pressed", async () => {
    mockApi({ sites: [sydney] });
    renderDetail({ ...saved, conditions: [{ kind: "days", op: "in", values: [0, 2], enabled: true }] });
    await ready();
    const onlyIf = screen.getByRole("group", { name: "Only if" });
    expect(within(onlyIf).getByRole("button", { name: "Mon" })).toHaveAttribute("aria-pressed", "true");
    expect(within(onlyIf).getByRole("button", { name: "Tue" })).toHaveAttribute("aria-pressed", "false");
    expect(within(onlyIf).getByRole("button", { name: "Wed" })).toHaveAttribute("aria-pressed", "true");
  });

  it("the restore conditions offer the calendar rows too", async () => {
    mockApi({ sites: [sydney] });
    renderDetail(saved);
    await ready();
    const restoreIf = screen.getByRole("group", { name: "Only restore if" });
    await addRow(userEvent.setup(), restoreIf, "week");
    expect(within(restoreIf).getByRole("combobox", { name: "Week parity 1" })).toBeInTheDocument();
  });
});
