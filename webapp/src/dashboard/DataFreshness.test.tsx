import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { DataFreshness } from "./DataFreshness";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderFreshness() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DataFreshness siteId={7} />
    </QueryClientProvider>,
  );
}

describe("DataFreshness", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(apiFetch).mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the time of the last energy reading and how long the page has been loaded", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ts: "2026-10-20T04:32:05Z" });
    renderFreshness();

    const expectedTime = new Date("2026-10-20T04:32:05Z").toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    expect(await screen.findByText(new RegExp(`Energy data ${expectedTime}`))).toBeInTheDocument();
    expect(screen.getByText(/page loaded 0 sec ago/)).toBeInTheDocument();
    expect(vi.mocked(apiFetch).mock.calls[0][0]).toBe("/api/v1/sites/7/readings/energy/latest");
  });

  it("counts up in seconds, then minutes, then hours", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ts: "2026-10-20T04:32:05Z" });
    renderFreshness();
    await screen.findByText(/Energy data/);

    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });
    expect(screen.getByText(/page loaded 30 sec ago/)).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(90_000);
    });
    expect(screen.getByText(/page loaded 2 min ago/)).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(3 * 3_600_000);
    });
    expect(screen.getByText(/page loaded 3 hours ago/)).toBeInTheDocument();
  });

  it("says so when the site has no energy data yet", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ts: null });
    renderFreshness();

    expect(await screen.findByText(/Energy data none yet/)).toBeInTheDocument();
  });

  it("says so when the last energy time cannot be loaded, and still shows the page age", async () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error("boom"));
    renderFreshness();

    expect(await screen.findByText(/Energy data unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/page loaded 0 sec ago/)).toBeInTheDocument();
  });
});
