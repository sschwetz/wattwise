import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { ScheduleList } from "./ScheduleList";
import type { ActorInstance, ActorRunListItem, ActorSchedule, ScheduleLayoutItem } from "./types";

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
    conditions: [], actions: [{ id: "system_off", params: {} }],
    restore: { enabled: false, when: "window_end", minutes: null, time: null, only_if: [], early_if: [] },
    retry: { max_attempts: 3, interval_seconds: 30, give_up_minutes: 15 }, limits: { min_gap_minutes: 0, run_again_after: null }, notify_email: false,
  };
}

function runItem(id: number, scheduleId: number, outcome: ActorRunListItem["outcome"], trigger_at: string): ActorRunListItem {
  return {
    id, schedule_id: scheduleId, schedule_name: "x", kind: "action", trigger_at, started_at: null, finished_at: null,
    outcome, message: null, attempts: 1, parent_run_id: null, snapshot: null, steps: null,
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

describe("ScheduleList actions", () => {
  it("detail and list name every action", async () => {
    const multi = { ...schedule(1, "Peak off"), actions: [{ id: "system_off", params: {} }, { id: "set_mode", params: {} }, { id: "x", params: {} }] };
    const caps = { version: 1 as const, actions: [{ id: "system_off", label: "Turn the system off", restorable: true, params: [] }], readings: [] };
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ScheduleList
          site={{ id: 7, timezone: "UTC" }}
          data={{ instances: [{ ...instance, capabilities: caps }], schedules: [multi, schedule(2, "Single")] }}
          view={{ selected: null, onSelect: vi.fn(), canEdit: true }}
        />
      </QueryClientProvider>,
    );
    const row = screen.getByRole("button", { name: /Peak off/ });
    expect(within(row).getByText("Turn the system off +2 more")).toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: /Single/ })).getByText("Turn the system off")).toBeInTheDocument();
  });
});

describe("ScheduleList order and separators", () => {
  const sep = (key: string, label = ""): ScheduleLayoutItem => ({ type: "separator", key, label });
  const sched = (id: number): ScheduleLayoutItem => ({ type: "schedule", id });
  const three = [schedule(1, "Peak off"), schedule(2, "Old"), schedule(3, "Never")];

  /** Layout reads return `layout`, layout writes echo the body (or fail), everything else is the run list. */
  function serve(layout: ScheduleLayoutItem[] | null, { failSave = false, failLoad = false } = {}) {
    vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
      if (!String(path).includes("schedule-layout")) return [];
      if (init?.method !== "PUT") {
        if (failLoad) throw new Error("down");
        return { items: layout };
      }
      if (failSave) throw new Error("no");
      return { items: JSON.parse(String(init.body)).items };
    });
  }

  function renderWith(opts: { instances?: ActorInstance[]; schedules?: ActorSchedule[]; canEdit?: boolean } = {}) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ScheduleList
          site={{ id: 7, timezone: "UTC" }}
          data={{ instances: opts.instances ?? [instance], schedules: opts.schedules ?? three }}
          view={{ selected: null, onSelect: vi.fn(), canEdit: opts.canEdit ?? true }}
        />
      </QueryClientProvider>,
    );
  }

  /** The Add separator button once the saved order has loaded and it is enabled. */
  async function addSeparator() {
    const button = await screen.findByRole("button", { name: "Add separator" });
    await waitFor(() => expect(button).toBeEnabled());
    return button;
  }

  const saved = () => vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === "PUT");
  const savedItems = (n: number) => JSON.parse(String(saved()[n][1]?.body)).items;
  /** Each list item's text, plus its input's value (a manager edits a separator's label in an input). */
  const names = () =>
    screen.getAllByRole("listitem").map((li) => `${li.textContent ?? ""} ${li.querySelector("input")?.value ?? ""}`);

  it("shows the rows in the saved order with the separator and its label between them", async () => {
    serve([sched(2), sep("k1", "Kids"), sched(1), sched(3)]);
    renderWith();
    expect(await screen.findByDisplayValue("Kids")).toBeInTheDocument();
    const text = names();
    expect(text[0]).toContain("Old");
    expect(text[1]).toContain("Kids");
    expect(text[2]).toContain("Peak off");
    expect(text[3]).toContain("Never");
  });

  it("puts a schedule the layout does not name after the last item", async () => {
    serve([sched(3), sep("k1", "Kids")]);
    renderWith();
    await screen.findByDisplayValue("Kids");
    const text = names();
    expect(text[0]).toContain("Never");
    expect(text[2]).toContain("Peak off");
    expect(text[3]).toContain("Old");
  });

  it("is one flat list across actors, with each row naming its actor and no actor heading", async () => {
    serve(null);
    const spare = { ...instance, id: 4, name: "Spare AC" };
    renderWith({ instances: [instance, spare], schedules: [schedule(1, "Peak off"), { ...schedule(2, "Old"), instance_id: 4 }] });
    expect(await screen.findByText("Lounge AC")).toBeInTheDocument();
    expect(screen.getByText("Spare AC")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 3 })).not.toBeInTheDocument();
  });

  it("names a device Device when it has no name", async () => {
    serve(null);
    renderWith({ instances: [{ ...instance, name: null }] });
    expect((await screen.findAllByText("Device")).length).toBeGreaterThan(0);
    expect(screen.queryByText("Air conditioner")).not.toBeInTheDocument();
  });

  it("adds an unlabelled separator at the end, keeping the current order", async () => {
    serve([sched(2)]);
    renderWith();
    await userEvent.click(await addSeparator());
    await waitFor(() => expect(saved()).toHaveLength(1));
    expect(savedItems(0)).toEqual([sched(2), sched(1), sched(3), { type: "separator", key: expect.any(String), label: "" }]);
  });

  it("renames a separator on Enter", async () => {
    serve([sched(1), sep("k1", "Kids"), sched(2), sched(3)]);
    renderWith();
    const input = await screen.findByRole("textbox", { name: "Separator label" });
    await userEvent.clear(input);
    await userEvent.type(input, "Night{Enter}");
    await waitFor(() => expect(saved()).toHaveLength(1));
    expect(savedItems(0)).toEqual([sched(1), sep("k1", "Night"), sched(2), sched(3)]);
  });

  it("removes a separator", async () => {
    serve([sched(1), sep("k1", "Kids"), sched(2), sched(3)]);
    renderWith();
    await userEvent.click(await screen.findByRole("button", { name: "Remove separator" }));
    await waitFor(() => expect(saved()).toHaveLength(1));
    expect(savedItems(0)).toEqual([sched(1), sched(2), sched(3)]);
  });

  it("gives a manager a handle on every row and every separator", async () => {
    serve([sched(1), sep("k1", "Kids"), sched(2), sched(3)]);
    renderWith();
    await screen.findByDisplayValue("Kids");
    expect(await screen.findAllByRole("button", { name: "Drag to reorder" })).toHaveLength(4);
  });

  it("shows a viewer the order and the separator, with no way to change them", async () => {
    serve([sched(2), sep("k1", "Kids"), sched(1), sched(3)]);
    renderWith({ canEdit: false });
    expect(await screen.findByText("Kids")).toBeInTheDocument();
    expect(names()[0]).toContain("Old");
    expect(screen.queryByRole("button", { name: "Add separator" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Drag to reorder" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove separator" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("turns editing off and offers a retry when the saved order does not load, so nothing overwrites it", async () => {
    serve([sched(2), sep("k1", "Kids")], { failLoad: true });
    renderWith();
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the saved order.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add separator" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Drag to reorder" })).not.toBeInTheDocument();
    expect(saved()).toHaveLength(0);
  });

  it("keeps Add separator off until the saved order has loaded", async () => {
    vi.mocked(apiFetch).mockImplementation(() => new Promise(() => {}));
    renderWith();
    expect(await screen.findByRole("button", { name: "Add separator" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Drag to reorder" })).not.toBeInTheDocument();
  });

  it("says so when a save fails, and shows the stored order again", async () => {
    serve([sched(1)], { failSave: true });
    renderWith();
    await userEvent.click(await addSeparator());
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save the order.");
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Separator label" })).not.toBeInTheDocument());
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
