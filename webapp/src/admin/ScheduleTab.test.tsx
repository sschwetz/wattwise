import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "../api/client";
import { ScheduleTab } from "./ScheduleTab";
import type { AdminJob, AdminJobList } from "./useAdminJobs";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const DEMAND: AdminJob = {
  id: "demand_interval",
  label: "Demand poll",
  kind: "interval",
  timing: { interval_minutes: 5, hour: null, minute: null },
  default: { interval_minutes: 5, hour: null, minute: null },
  is_default: true,
  min_minutes: 1,
  next_run: "2026-11-24T01:00:00Z",
  last_run: null,
};
const DAILY: AdminJob = {
  id: "daily_rollup",
  label: "Daily rollup",
  kind: "daily",
  timing: { interval_minutes: null, hour: 2, minute: 30 },
  default: { interval_minutes: null, hour: 3, minute: 0 },
  is_default: false,
  min_minutes: null,
  next_run: null,
  last_run: "2026-11-23T02:30:00Z",
};

function listOf(next: Partial<AdminJobList> = {}): AdminJobList {
  return { timezone: "Australia/Brisbane", scheduler_running: true, jobs: [DEMAND, DAILY], ...next };
}

function setup(list: AdminJobList | Error = listOf()) {
  vi.mocked(apiFetch).mockImplementation(async (_path: string, init?: RequestInit) => {
    if (init?.method) return DEMAND;
    if (list instanceof Error) throw list;
    return list;
  });
}

function renderTab() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ScheduleTab />
    </QueryClientProvider>,
  );
}

function row(label: string) {
  return within(screen.getByRole("group", { name: label }));
}

describe("ScheduleTab", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("lists every job with its timing and the timezone for daily jobs", async () => {
    setup();
    renderTab();
    expect(await screen.findByRole("group", { name: "Demand poll" })).toBeInTheDocument();
    expect(row("Demand poll").getByLabelText("Interval in minutes")).toHaveValue(5);
    expect(row("Daily rollup").getByLabelText("Time of day")).toHaveValue("02:30");
    expect(row("Daily rollup").getByText("Australia/Brisbane")).toBeInTheDocument();
    expect(row("Demand poll").queryByText("Australia/Brisbane")).not.toBeInTheDocument();
  });

  it("saves a new interval", async () => {
    setup();
    renderTab();
    const input = await screen.findByLabelText("Interval in minutes");
    await userEvent.clear(input);
    await userEvent.type(input, "10");
    await userEvent.click(row("Demand poll").getByRole("button", { name: "Save" }));
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/admin/jobs/demand_interval", {
      method: "PUT",
      body: JSON.stringify({ kind: "interval", interval_minutes: 10 }),
    });
  });

  it("saves a new daily time", async () => {
    setup();
    renderTab();
    const input = await screen.findByLabelText("Time of day");
    fireEvent.change(input, { target: { value: "01:05" } });
    await userEvent.click(row("Daily rollup").getByRole("button", { name: "Save" }));
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/admin/jobs/daily_rollup", {
      method: "PUT",
      body: JSON.stringify({ kind: "daily", hour: 1, minute: 5 }),
    });
  });

  it("offers Reset to default only when the timing differs from the default", async () => {
    setup();
    renderTab();
    await screen.findByRole("group", { name: "Daily rollup" });
    expect(row("Demand poll").queryByRole("button", { name: "Reset to default" })).not.toBeInTheDocument();
    await userEvent.click(row("Daily rollup").getByRole("button", { name: "Reset to default" }));
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/admin/jobs/daily_rollup", { method: "DELETE" });
  });

  it("shows the server message on a 422 and keeps the typed value", async () => {
    setup();
    renderTab();
    const input = await screen.findByLabelText("Interval in minutes");
    vi.mocked(apiFetch).mockRejectedValueOnce(new ApiError(422, "invalid_timing", "Interval must be at least 1 minute", null));
    await userEvent.clear(input);
    await userEvent.type(input, "0");
    await userEvent.click(row("Demand poll").getByRole("button", { name: "Save" }));
    expect(await row("Demand poll").findByText("Interval must be at least 1 minute")).toBeInTheDocument();
    expect(input).toHaveValue(0);
  });

  it("says the scheduler is off when it is not running", async () => {
    setup(listOf({ scheduler_running: false }));
    renderTab();
    expect(await screen.findByText(/scheduler is off/i)).toHaveTextContent("apply when it is enabled");
  });

  it("does not mention the scheduler when it is running", async () => {
    setup();
    renderTab();
    await screen.findByRole("group", { name: "Demand poll" });
    expect(screen.queryByText(/scheduler is off/i)).not.toBeInTheDocument();
  });

  it("shows QueryError with a retry when the load fails", async () => {
    setup(new ApiError(500, "boom", "Jobs broke", null));
    renderTab();
    expect(await screen.findByText("Jobs broke")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
