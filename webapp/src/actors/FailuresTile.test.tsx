import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { FailuresTile } from "./FailuresTile";
import type { ActorFailure } from "./types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const site = { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true };

function failure(n: number, overrides: Partial<ActorFailure> = {}): ActorFailure {
  return {
    kind: "run_failed", instance_id: 3, schedule_id: n, schedule_name: `Schedule ${n}`,
    message: `Message ${n}`, since: `2026-10-0${n}T14:30:00Z`, ...overrides,
  };
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

  it("shows All schedules OK", async () => {
    mockData({});
    renderTile();
    const tile = await screen.findByRole("region", { name: "Schedule status" });
    expect(within(tile).getByText("All schedules OK")).toBeInTheDocument();
    expect(within(tile).getByRole("link", { name: /schedule tab/i })).toHaveAttribute("href", "/settings/sites/7/schedule");
    expect(tile.className).not.toContain("danger-glow");
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
    const tile = await screen.findByRole("region", { name: "Needs attention" });
    expect(within(tile).getByText(/Needs attention/)).toHaveTextContent("3");
    const rows = within(within(tile).getByRole("list")).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Still off");
    expect(rows[0]).toHaveTextContent("Schedule 1");
    expect(rows[0]).toHaveTextContent("Message 1");
    expect(rows[1]).toHaveTextContent("Actor not reporting");
    expect(rows[1]).not.toHaveTextContent("null");
    expect(rows[2]).toHaveTextContent("Needs sign-in");
    expect(within(tile).getByRole("link", { name: "Open the Schedule tab" })).toHaveAttribute("href", "/settings/sites/7/schedule");
    expect(within(tile).getByRole("status")).toHaveAttribute("aria-live", "polite");
  });

  it("uses the danger style only when failures exist", async () => {
    mockData({ failures: [failure(1)] });
    const { unmount } = renderTile();
    expect((await screen.findByRole("region", { name: "Needs attention" })).className).toContain("danger-glow");
    unmount();
    mockData({});
    renderTile();
    expect((await screen.findByRole("region", { name: "Schedule status" })).className).not.toContain("danger-glow");
  });

  it("shows the five oldest first and an and N more line", async () => {
    const all = [7, 6, 5, 4, 3, 2, 1].map((n) => failure(n));
    mockData({ failures: all });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Needs attention" });
    const rows = within(tile).getAllByRole("listitem");
    expect(rows.map((r) => /Schedule (\d)/.exec(r.textContent ?? "")?.[1])).toEqual(["1", "2", "3", "4", "5"]);
    const more = within(tile).getByRole("link", { name: "and 2 more" });
    expect(more).toHaveAttribute("href", "/settings/sites/7/schedule");
  });

  it("shows the muted unavailable line when the failures request errors while instances exist", async () => {
    mockData({ failures: new Error("boom") });
    renderTile();
    expect(await screen.findByText("Schedule status unavailable")).toBeInTheDocument();
    expect(screen.queryByText("All schedules OK")).not.toBeInTheDocument();
  });

  it("keeps showing cached failures when a background refetch errors", async () => {
    mockData({ failures: [failure(1)] });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderTile(queryClient);
    await screen.findByRole("region", { name: "Needs attention" });
    mockData({ failures: new Error("boom") });
    await queryClient.refetchQueries();
    await vi.waitFor(() => expect(queryClient.getQueryCache().getAll().some((q) => q.state.status === "error")).toBe(true));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByRole("region", { name: "Needs attention" })).toHaveTextContent("Message 1");
    expect(screen.queryByText("Schedule status unavailable")).not.toBeInTheDocument();
  });

  it("waits for the site time zone before formatting times", async () => {
    let resolveSites: (s: unknown[]) => void = () => {};
    mockData({ failures: [failure(1)], sites: new Promise((r) => { resolveSites = r; }) });
    const { container } = renderTile();
    const tile = await screen.findByRole("region", { name: "Needs attention" });
    expect(container.querySelector("time")).toBeNull();
    expect(tile.textContent).not.toMatch(/UTC/);
    expect(tile.textContent).not.toMatch(/2026|00:30|14:30/);
    resolveSites([site]);
    const time = await screen.findByText(/2 Oct.*00:30/);
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", "2026-10-01T14:30:00Z");
    expect(tile.textContent).not.toMatch(/UTC/);
  });

  it("a needs_sign_in row shows no time", async () => {
    mockData({ failures: [failure(1, { kind: "needs_sign_in", since: "2026-11-05T03:00:00Z" })] });
    const { container } = renderTile();
    const tile = await screen.findByRole("region", { name: "Needs attention" });
    await screen.findByText("Needs sign-in");
    await within(tile).findByText("Message 1");
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector("time")).toBeNull();
    expect(tile.textContent).not.toMatch(/Today|Yesterday|since/i);
  });

  it("other kinds still show their time", async () => {
    mockData({ failures: [failure(1, { kind: "still_off" }), failure(2, { kind: "needs_sign_in" })] });
    const { container } = renderTile();
    await screen.findByText(/2 Oct.*00:30/);
    expect(container.querySelectorAll("time")).toHaveLength(1);
  });

  it("never lets a needs_sign_in row push out an older failure", async () => {
    const signIn = failure(9, { kind: "needs_sign_in", since: new Date().toISOString() });
    mockData({ failures: [signIn, ...[5, 4, 3, 2, 1].map((n) => failure(n))] });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Needs attention" });
    const rows = within(tile).getAllByRole("listitem");
    expect(rows).toHaveLength(5);
    expect(tile.textContent).not.toContain("Schedule 9");
    expect(within(tile).getByRole("link", { name: "and 1 more" })).toBeInTheDocument();
  });

  it("viewer sees the tile", async () => {
    mockData({ failures: [failure(1)] });
    renderTile();
    const tile = await screen.findByRole("region", { name: "Needs attention" });
    expect(within(tile).getByText("Message 1")).toBeInTheDocument();
    expect(within(tile).queryByRole("button")).not.toBeInTheDocument();
    expect(within(tile).getByRole("link", { name: "Open the Schedule tab" })).toBeInTheDocument();
  });
});
