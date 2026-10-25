import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { DataFreshness } from "./DataFreshness";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderFreshness(liveReceivedAt: number | null = null) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DataFreshness siteId={7} liveReceivedAt={liveReceivedAt} />
    </QueryClientProvider>,
  );
}

const READING_TS = "2026-10-20T04:32:05Z";

describe("DataFreshness", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-10-20T04:34:05Z"));
    vi.mocked(apiFetch).mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows how long ago the energy data changed, its clock time, and how long since the last refresh", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ts: READING_TS });
    renderFreshness();

    const clock = new Date(READING_TS).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    expect(await screen.findByText(`Energy data changed 2 min ago (${clock}) · refreshed 0 sec ago`)).toBeInTheDocument();
    expect(vi.mocked(apiFetch).mock.calls[0][0]).toBe("/api/v1/sites/7/readings/energy/latest");
  });

  it("counts both ages up, and resets the refresh age when the data is polled again", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ts: READING_TS });
    renderFreshness();
    await screen.findByText(/Energy data changed/);

    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });
    expect(screen.getByText(/changed 2 min ago .* refreshed 30 sec ago/)).toBeInTheDocument();

    // The 60 s poll fires and returns a newer reading.
    vi.mocked(apiFetch).mockResolvedValue({ ts: "2026-10-20T04:35:00Z" });
    await act(async () => {
      vi.advanceTimersByTime(31_000);
    });
    expect(screen.getByText(/changed [5-9] sec ago .* refreshed [0-2] sec ago/)).toBeInTheDocument();
  });

  it("says so when the site has no energy data yet", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ts: null });
    renderFreshness();

    expect(await screen.findByText("Energy data none yet · refreshed 0 sec ago")).toBeInTheDocument();
  });

  it("says so when the last energy time cannot be loaded", async () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error("boom"));
    renderFreshness();

    expect(await screen.findByText("Energy data unavailable")).toBeInTheDocument();
  });

  it("shows the database age and the live data age, with no refresh age, while live data is flowing", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ts: READING_TS });
    renderFreshness(Date.now() - 4_000);

    const clock = new Date(READING_TS).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    expect(
      await screen.findByText(`Energy DB last updated 2 min ago (${clock}) · live data received 4 sec ago`),
    ).toBeInTheDocument();
  });

  it("says so in live mode when the database has no energy data yet", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ts: null });
    renderFreshness(Date.now());

    expect(await screen.findByText("Energy DB has no data yet · live data received 0 sec ago")).toBeInTheDocument();
  });
});
