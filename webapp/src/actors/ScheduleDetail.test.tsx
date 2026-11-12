import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "../api/client";
import { ScheduleDetail } from "./ScheduleDetail";
import type { ActorInstance, ActorRun, ActorSchedule } from "./types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const instance: ActorInstance = {
  id: 3, site_id: 7, provider: "actron_neo", name: "Lounge AC", enabled: true,
  capabilities: { version: 1, actions: [{ id: "system_off", label: "Turn the system off", restorable: true, params: [] }], readings: [] },
  capabilities_updated_at: null, latest_readings: null, last_seen_at: null, auth_state: null,
};

const schedule: ActorSchedule = {
  id: 21, instance_id: 3, name: "Peak off", enabled: true,
  trigger: { type: "time", time: "16:00", days_of_week: [1] }, conditions: [], actions: [{ id: "system_off", params: {} }],
  restore: { enabled: false, when: "window_end", minutes: null, time: null, only_if: [], early_if: [] },
  retry: { max_attempts: 3, interval_seconds: 30, give_up_minutes: 15 }, notify_email: false,
};

function run(id: number, outcome: ActorRun["outcome"]): ActorRun {
  return {
    id, schedule_id: 21, kind: "action", trigger_at: "2026-11-05T03:00:00Z", started_at: null, finished_at: null,
    outcome, message: null, attempts: 1, parent_run_id: null, snapshot: null, steps: null,
  };
}

interface Api { runs: ActorRun[]; testResult?: () => Promise<ActorRun> }
let api: Api;

function mockApi(next: Api) {
  api = next;
  vi.mocked(apiFetch).mockImplementation((path: string, init?: RequestInit) => {
    if (init?.method === "POST" && path.endsWith("/test")) return api.testResult?.() ?? Promise.resolve(run(99, "pending"));
    if (path.endsWith("/runs")) return Promise.resolve(api.runs);
    if (path.endsWith("/tariffs")) return Promise.resolve([]);
    if (path.endsWith("/api/v1/sites")) return sitesResult();
    return Promise.resolve([]);
  });
}

let sitesResult: () => Promise<unknown> = () => Promise.resolve([{ id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true }]);

function renderDetail(sched: ActorSchedule | null, canEdit = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ScheduleDetail siteId={7} schedule={sched} instance={instance} canEdit={canEdit} />
    </QueryClientProvider>,
  );
}

const runCalls = () => vi.mocked(apiFetch).mock.calls.filter(([p, init]) => String(p).endsWith("/runs") && !init?.method);
const testButton = () => screen.findByRole("button", { name: "Test now" });

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  sitesResult = () => Promise.resolve([{ id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true }]);
  mockApi({ runs: [] });
});
afterEach(() => vi.useRealTimers());

describe("ScheduleDetail run history and Test now", () => {
  it("shows the unit state and run history under the editor", async () => {
    mockApi({ runs: [run(1, "ok")] });
    renderDetail(schedule);
    expect(await screen.findByRole("heading", { name: "Run history" })).toBeInTheDocument();
    expect(await within(screen.getByRole("list", { name: "Runs" })).findByText("Turn the system off")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Air conditioner" })).toBeInTheDocument();
  });

  it("detail and list name every action", async () => {
    const two = { ...schedule, actions: [{ id: "system_off", params: {} }, { id: "set_mode", params: {} }] };
    mockApi({ runs: [run(1, "ok")] });
    renderDetail(two);
    expect(await within(await screen.findByRole("list", { name: "Runs" })).findByText("Turn the system off, then set_mode")).toBeInTheDocument();
  });

  it("Test now posts and disables the button while pending", async () => {
    const user = userEvent.setup();
    let finish: (r: ActorRun) => void = () => {};
    mockApi({ runs: [], testResult: () => new Promise((resolve) => { finish = resolve; }) });
    renderDetail(schedule);
    await user.click(await testButton());
    expect(vi.mocked(apiFetch).mock.calls.some(([p, i]) => String(p).endsWith("/schedules/21/test") && i?.method === "POST")).toBe(true);
    expect(screen.getByRole("button", { name: "Test now" })).toBeDisabled();
    await act(async () => { finish(run(99, "pending")); });
    await waitFor(() => expect(screen.getByRole("button", { name: "Test now" })).toBeEnabled());
  });

  it("Test now success note and history refresh", async () => {
    const user = userEvent.setup();
    renderDetail(schedule);
    await user.click(await testButton());
    const note = await screen.findByText("Test queued. The actor starts it within about 30 seconds.");
    expect(note).toHaveAttribute("role", "status");
    await waitFor(() => expect(runCalls().length).toBeGreaterThan(1));
  });

  it("Test now error is shown", async () => {
    const user = userEvent.setup();
    mockApi({ runs: [], testResult: () => Promise.reject(new ApiError(409, "instance_disabled", "Enable the actor before testing a schedule", null)) });
    renderDetail(schedule);
    await user.click(await testButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("Enable the actor before testing a schedule");
    expect(screen.queryByText(/Test queued/)).not.toBeInTheDocument();
  });

  it("explains what a test does", async () => {
    renderDetail(schedule);
    await testButton();
    expect(screen.getByText(/ignoring its trigger and conditions/)).toBeInTheDocument();
  });

  it("viewer sees no Test now but still sees history", async () => {
    renderDetail(schedule, false);
    expect(await screen.findByRole("heading", { name: "Run history" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test now" })).not.toBeInTheDocument();
  });

  it("new schedule shows no history and no Test now", async () => {
    renderDetail(null);
    await screen.findByRole("combobox", { name: "Trigger" });
    expect(screen.queryByRole("heading", { name: "Run history" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test now" })).not.toBeInTheDocument();
  });

  it("shows the state panel for an actor that has never reported", async () => {
    const blank = { ...instance, capabilities: null, latest_readings: null };
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ScheduleDetail siteId={7} schedule={schedule} instance={blank} canEdit />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("No device state yet.")).toBeInTheDocument();
  });

  it("formats no times until the site time zone is known, and never falls back to UTC", async () => {
    let resolve: (v: unknown) => void = () => {};
    sitesResult = () => new Promise((r) => { resolve = r; });
    mockApi({ runs: [{ ...run(1, "ok"), trigger_at: "2026-11-04T23:30:00Z" }] });
    renderDetail(schedule);
    await screen.findByRole("button", { name: "Test now" });
    expect(screen.queryByRole("heading", { name: "Run history" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Air conditioner" })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("UTC");
    await act(async () => { resolve([{ id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true }]); });
    expect(await screen.findByRole("heading", { name: "Run history" })).toBeInTheDocument();
    expect(await screen.findByText(/10:30$/)).toBeInTheDocument();
  });

  it("history polls while a run is pending", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi({ runs: [run(1, "pending")] });
    renderDetail(schedule);
    await screen.findByText("Queued");
    const before = runCalls().length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(runCalls().length).toBeGreaterThan(before);
  });

  it("history does not poll when no run is active", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi({ runs: [run(1, "ok")] });
    renderDetail(schedule);
    await screen.findByText("OK");
    const before = runCalls().length;
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(runCalls().length).toBe(before);
  });
});
