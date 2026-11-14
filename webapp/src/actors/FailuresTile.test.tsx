import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { FailuresTile } from "./FailuresTile";
import type { ActorFailure, AlertSeverity } from "./types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const site = { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true };

function failure(n: number, overrides: Partial<ActorFailure> = {}): ActorFailure {
  return {
    kind: "run_failed", instance_id: 3, schedule_id: n, schedule_name: `Schedule ${n}`,
    message: `Message ${n}`, since: `2026-10-0${n}T14:30:00Z`, subject: null,
    severity: "failure", alert_id: null, cleared_at: null, sticky: false, ...overrides,
  };
}

function alert(n: number, overrides: Partial<ActorFailure> = {}): ActorFailure {
  return failure(n, {
    kind: "alert", instance_id: null, schedule_id: null, schedule_name: null,
    subject: "Source", message: `Message ${n}`, alert_id: n, ...overrides,
  });
}

interface Data {
  instances?: unknown[];
  failures?: ActorFailure[] | Error;
  sites?: Promise<unknown[]>;
}

function mockData({ instances = [{ id: 3, auth_state: null }], failures = [], sites }: Data) {
  vi.mocked(apiFetch).mockImplementation((path: string = "") => {
    if (path.endsWith("/instances")) return Promise.resolve(instances);
    if (path.endsWith("/failures")) return failures instanceof Error ? Promise.reject(failures) : Promise.resolve(failures);
    if (path.endsWith("/sites")) return sites ?? Promise.resolve([site]);
    return Promise.resolve([]);
  });
}

function renderTile(queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <FailuresTile siteId={7} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.mocked(apiFetch).mockReset());

describe("FailuresTile", () => {
  it("renders nothing without instances", async () => {
    mockData({ instances: [], failures: [failure(1)] });
    const { container } = renderTile();
    await vi.waitFor(() => expect(apiFetch).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
  });

  it("shows an alert on a site with no actor, linking to the Collectors tab", async () => {
    mockData({
      instances: [],
      failures: [failure(1, { kind: "alert", instance_id: null, schedule_id: null, schedule_name: null, subject: "Roof Envoy", message: "Certificate changed" })],
    });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule log" });
    expect(within(tile).getByText("Roof Envoy - Certificate changed")).toBeInTheDocument();
    expect(within(tile).getByRole("link", { name: "Open Collectors" })).toHaveAttribute("href", "/settings/sites/7/collectors");
    expect(within(tile).queryByRole("link", { name: "Open Schedule" })).not.toBeInTheDocument();
  });

  it("links to both tabs when schedule failures and alerts are present", async () => {
    mockData({
      failures: [failure(1), failure(2, { kind: "alert", instance_id: null, schedule_id: null, schedule_name: null, subject: "Site", message: "Email failed" })],
    });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule log" });
    expect(within(tile).getByRole("link", { name: "Open Schedule" })).toBeInTheDocument();
    expect(within(tile).getByRole("link", { name: "Open Collectors" })).toBeInTheDocument();
  });

  it("failures tile includes a partial run", async () => {
    mockData({ failures: [failure(1, { message: "The action partly failed" })] });
    renderTile();
    const row = await screen.findByText("Schedule 1 - Run failed");
    expect(row).toHaveAttribute("title", "Schedule 1 - The action partly failed");
  });

  it("renders nothing when the log is empty", async () => {
    mockData({});
    const { container } = renderTile();
    await vi.waitFor(() => expect(apiFetch).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
  });

  it("logs a successful run as an info row without a link", async () => {
    mockData({ failures: [failure(1, { kind: "run_ok", severity: "notice", message: "The action ran" }), failure(2)] });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule log" });
    const rows = within(tile).getAllByRole("listitem");
    expect(rows[1]).toHaveTextContent("Schedule 1 - Ran OK");
    expect(within(rows[1]).queryByRole("link")).not.toBeInTheDocument();
    expect(within(rows[0]).getByRole("link", { name: "Open Schedule" })).toBeInTheDocument();
  });

  it("does not colour the tile for successful runs alone", async () => {
    mockData({ failures: [failure(1, { kind: "run_ok", severity: "notice" })] });
    renderTile();
    expect((await screen.findByRole("region", { name: "Schedule log" })).className).not.toMatch(/-glow/);
  });

  it("lists failures with a link to the Schedule page", async () => {
    mockData({
      failures: [
        failure(1, { kind: "still_off" }),
        failure(2, { kind: "actor_silent", schedule_id: null, schedule_name: null }),
        failure(3, { kind: "needs_sign_in" }),
      ],
    });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule log" });
    const rows = within(within(tile).getByRole("list")).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[2]).toHaveTextContent("Schedule 3 - Needs sign-in");
    expect(rows[0]).toHaveTextContent("Not reporting");
    expect(rows[0]).not.toHaveTextContent("null");
    expect(rows[1]).toHaveTextContent("Schedule 1 - Still off");
    expect(within(tile).getAllByRole("link", { name: "Open Schedule" })[0]).toHaveAttribute("href", "/settings/sites/7/schedule");
    expect(within(tile).getByRole("status")).toHaveAttribute("aria-live", "polite");
  });

  it("uses the danger style only when failures exist", async () => {
    mockData({ failures: [failure(1)] });
    const { unmount } = renderTile();
    expect((await screen.findByRole("region", { name: "Schedule log" })).className).toContain("danger-glow");
    unmount();
  });

  it("shows the five newest first and an and N more line", async () => {
    const all = [1, 2, 3, 4, 5, 6, 7].map((n) => failure(n));
    mockData({ failures: all });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule log" });
    const rows = within(tile).getAllByRole("listitem");
    expect(rows.map((r) => /Schedule (\d)/.exec(r.textContent ?? "")?.[1])).toEqual(["7", "6", "5", "4", "3"]);
    const more = within(tile).getByRole("link", { name: "and 2 more" });
    expect(more).toHaveAttribute("href", "/settings/sites/7/schedule");
  });

  it("shows the muted unavailable line when the failures request errors while instances exist", async () => {
    mockData({ failures: new Error("boom") });
    renderTile();
    expect(await screen.findByText("Schedule status unavailable")).toBeInTheDocument();
  });

  it("keeps showing cached failures when a background refetch errors", async () => {
    mockData({ failures: [failure(1)] });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderTile(queryClient);
    await screen.findByRole("region", { name: "Schedule log" });
    mockData({ failures: new Error("boom") });
    await queryClient.refetchQueries();
    await vi.waitFor(() => expect(queryClient.getQueryCache().getAll().some((q) => q.state.status === "error")).toBe(true));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByRole("region", { name: "Schedule log" })).toHaveTextContent("Schedule 1 - Run failed");
    expect(screen.queryByText("Schedule status unavailable")).not.toBeInTheDocument();
  });

  it("waits for the site time zone before formatting times", async () => {
    let resolveSites: (s: unknown[]) => void = () => {};
    mockData({ failures: [failure(1)], sites: new Promise((r) => { resolveSites = r; }) });
    const { container } = renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule log" });
    expect(container.querySelector("time")).toBeNull();
    expect(tile.textContent).not.toMatch(/UTC/);
    expect(tile.textContent).not.toMatch(/2026|00:30|14:30/);
    resolveSites([site]);
    const time = await screen.findByText("2026-10-02 00:30");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", "2026-10-01T14:30:00Z");
    expect(tile.textContent).not.toMatch(/UTC/);
  });

  it("a needs_sign_in row shows no time", async () => {
    mockData({ failures: [failure(1, { kind: "needs_sign_in", since: "2026-11-05T03:00:00Z" })] });
    const { container } = renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule log" });
    await within(tile).findByText("Schedule 1 - Needs sign-in");
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector("time")).toBeNull();
    expect(tile.textContent).not.toMatch(/Today|Yesterday|since/i);
  });

  it("other kinds still show their time", async () => {
    mockData({ failures: [failure(1, { kind: "still_off" }), failure(2, { kind: "needs_sign_in" })] });
    const { container } = renderTile();
    await screen.findByText("2026-10-02 00:30");
    expect(container.querySelectorAll("time")).toHaveLength(1);
  });

  it("never lets a needs_sign_in row push out an older failure", async () => {
    const signIn = failure(9, { kind: "needs_sign_in", since: new Date().toISOString() });
    mockData({ failures: [signIn, ...[5, 4, 3, 2, 1].map((n) => failure(n))] });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule log" });
    expect(within(tile).getAllByRole("listitem")).toHaveLength(5);
    expect(tile.textContent).not.toContain("Schedule 9");
    expect(within(tile).getByRole("link", { name: "and 1 more" })).toBeInTheDocument();
  });

  it("names each Dismiss button after its row", async () => {
    mockData({ failures: [alert(1, { sticky: true, alert_id: 9, subject: "Roof Envoy", message: "Certificate changed" })] });
    renderTile();
    expect(await screen.findByRole("button", { name: "Dismiss: Roof Envoy - Certificate changed" })).toBeInTheDocument();
  });

  it("viewer sees the tile", async () => {
    mockData({ failures: [failure(1)] });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule log" });
    expect(within(tile).getByText("Schedule 1 - Run failed")).toBeInTheDocument();
    expect(within(tile).queryByRole("button")).not.toBeInTheDocument();
    expect(within(tile).getByRole("link", { name: "Open Schedule" })).toBeInTheDocument();
  });

  it("shows one line per alert: time, source and message", async () => {
    mockData({ failures: [alert(1, { subject: "Weatherlink Live", message: "Cannot connect to the API", since: "2026-10-01T04:30:00Z" })] });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule log" });
    expect(await within(tile).findByText("2026-10-01 14:30")).toBeInTheDocument();
    expect(within(tile).getByText("Weatherlink Live - Cannot connect to the API")).toBeInTheDocument();
    expect(within(tile).queryByText("Problem")).not.toBeInTheDocument();
  });

  it("shows Dismiss for sticky alerts and calls the dismiss route", async () => {
    mockData({ failures: [alert(1, { sticky: true, alert_id: 9, severity: "notice" })] });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule log" });
    expect(within(tile).queryByRole("link", { name: "Open Collectors" })).not.toBeInTheDocument();
    await userEvent.click(within(tile).getByRole("button", { name: /^Dismiss/ }));
    await vi.waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/api/v1/sites/7/actors/alerts/9", { method: "DELETE" }));
  });

  it("keeps the row and re-enables Dismiss when the request fails", async () => {
    mockData({ failures: [alert(1, { sticky: true, alert_id: 9 })] });
    const base = vi.mocked(apiFetch).getMockImplementation()!;
    vi.mocked(apiFetch).mockImplementation((path: string = "", init?: RequestInit) =>
      init?.method === "DELETE" ? Promise.reject(new Error("boom")) : base(path, init));
    renderTile();
    const button = await screen.findByRole("button", { name: /^Dismiss/ });
    await userEvent.click(button);
    await vi.waitFor(() => expect(apiFetch).toHaveBeenCalledWith(expect.stringContaining("/alerts/9"), { method: "DELETE" }));
    await vi.waitFor(() => expect(screen.getByRole("button", { name: /^Dismiss/ })).toBeEnabled());
    expect(screen.getByText(/Message 1/)).toBeInTheDocument();
  });

  it("tints the tile red, amber or blue by the highest unresolved bulb", async () => {
    const cases: [AlertSeverity[], string][] = [
      [["notice", "partial", "failure"], "danger-glow"],
      [["notice", "partial"], "partial-glow"],
      [["notice"], "notice-glow"],
    ];
    for (const [severities, glow] of cases) {
      mockData({ failures: severities.map((s, i) => alert(i + 1, { severity: s })) });
      const { unmount } = renderTile();
      expect((await screen.findByRole("region", { name: "Schedule log" })).className).toContain(glow);
      unmount();
    }
  });

  it("a cleared alert is neutral and does not count", async () => {
    mockData({ failures: [alert(1, { cleared_at: "2026-10-02T00:00:00Z", severity: "failure" })] });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule log" });
    expect(within(tile).queryByRole("heading")).not.toBeInTheDocument();
    expect(tile.className).not.toMatch(/-glow/);
  });

  it("orders unresolved by severity, cleared last", async () => {
    mockData({
      failures: [
        alert(1, { message: "cleared", cleared_at: "2026-10-05T00:00:00Z", severity: "failure" }),
        alert(2, { message: "blue", severity: "notice" }),
        alert(3, { message: "amber", severity: "partial" }),
        alert(4, { message: "red", severity: "failure" }),
      ],
    });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule log" });
    const order = within(tile).getAllByRole("listitem").map((r) => /(red|amber|blue|cleared)/.exec(r.textContent ?? "")?.[1]);
    expect(order).toEqual(["red", "amber", "blue", "cleared"]);
  });
});
