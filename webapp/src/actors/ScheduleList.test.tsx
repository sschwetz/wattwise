import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { ScheduleList } from "./ScheduleList";
import type { ActorInstance, ActorRunListItem, ActorSchedule } from "./types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const instance: ActorInstance = {
  id: 3, site_id: 7, provider: "actron_neo", name: "Lounge AC", enabled: true, capabilities: null,
  capabilities_updated_at: null, latest_readings: null, last_seen_at: null, auth_state: null,
};

function schedule(id: number, name: string): ActorSchedule {
  return {
    id, instance_id: 3, name, enabled: true, trigger: { type: "time", time: "10:00", days_of_week: [1] },
    conditions: [], action: { id: "system_off", params: {} },
    restore: { enabled: false, when: "window_end", minutes: null, time: null, only_if: [], early_if: [] },
    retry: { max_attempts: 3, interval_seconds: 30, give_up_minutes: 15 }, notify_email: false,
  };
}

function runItem(id: number, scheduleId: number, outcome: ActorRunListItem["outcome"], trigger_at: string): ActorRunListItem {
  return {
    id, schedule_id: scheduleId, schedule_name: "x", kind: "action", trigger_at, started_at: null, finished_at: null,
    outcome, message: null, attempts: 1, parent_run_id: null, snapshot: null,
  };
}

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
  vi.mocked(apiFetch).mockResolvedValue([
    runItem(3, 1, "failed", "2026-11-05T03:00:00Z"),
    runItem(2, 1, "ok", "2026-11-04T03:00:00Z"),
    runItem(1, 2, "skipped", "2020-01-01T03:00:00Z"),
  ]);
});

function renderList() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ScheduleList
        site={{ id: 7, timezone: "UTC" }}
        data={{ instances: [instance], schedules: [schedule(1, "Peak off"), schedule(2, "Old"), schedule(3, "Never")] }}
        view={{ selected: null, onSelect: vi.fn(), canEdit: true }}
      />
    </QueryClientProvider>,
  );
}

describe("ScheduleList last run", () => {
  it("shows the newest run's time and outcome under each name, or No runs yet", async () => {
    renderList();
    const peak = screen.getByRole("button", { name: /Peak off/ });
    expect(await within(peak).findByText(/Last run .* · Failed/)).toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: /Old/ })).getByText(/Last run .* · Skipped/)).toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: /Never/ })).getByText("No runs yet")).toBeInTheDocument();
  });

  it("asks for the site's runs once, not once per schedule", async () => {
    renderList();
    await screen.findByText(/Last run .* · Failed/);
    const runCalls = vi.mocked(apiFetch).mock.calls.filter(([p]) => String(p).includes("/runs"));
    expect(runCalls).toHaveLength(1);
    expect(String(runCalls[0][0])).toContain("limit=50");
  });
});

describe("ScheduleList polling", () => {
  afterEach(() => vi.useRealTimers());

  it("moves the line from Queued to OK as the poll returns a finished run", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const queued = [runItem(5, 1, "pending", "2026-11-05T03:00:00Z")];
    const done = [runItem(5, 1, "ok", "2026-11-05T03:00:00Z")];
    vi.mocked(apiFetch).mockResolvedValueOnce(queued).mockResolvedValue(done);
    renderList();
    expect(await screen.findByText(/Last run .* · Queued/)).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(await screen.findByText(/Last run .* · OK/)).toBeInTheDocument();
  });

  it("shows the raw outcome of an unknown one, never undefined", async () => {
    vi.mocked(apiFetch).mockResolvedValue([runItem(5, 1, "cancelled" as ActorRunListItem["outcome"], "2026-11-05T03:00:00Z")]);
    renderList();
    expect(await screen.findByText(/Last run .* · cancelled/)).toBeInTheDocument();
  });
});
