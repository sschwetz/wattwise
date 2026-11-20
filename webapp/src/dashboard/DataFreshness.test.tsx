import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { DataFreshness } from "./DataFreshness";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderFreshness(energyReceivedAt: number | null = null, weatherReceivedAt: number | null = null) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DataFreshness siteId={7} energyReceivedAt={energyReceivedAt} weatherReceivedAt={weatherReceivedAt} />
    </QueryClientProvider>,
  );
}

const READING_TS = "2026-10-20T04:32:05Z";

function clock(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

/** Answers each latest-reading request by its path, so the two lines get their own value. */
function mockLatest(energy: string | null | Error, weather: string | null | Error = energy) {
  vi.mocked(apiFetch).mockImplementation((path: string) => {
    const answer = path.endsWith("/readings/weather/latest") ? weather : energy;
    return answer instanceof Error ? Promise.reject(answer) : Promise.resolve({ ts: answer });
  });
}

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
    mockLatest(READING_TS);
    renderFreshness();

    expect(await screen.findByText(`Energy data changed 2 min ago (${clock(READING_TS)}) · refreshed 0 sec ago`)).toBeInTheDocument();
    expect(vi.mocked(apiFetch).mock.calls.map(([path]) => path)).toContain("/api/v1/sites/7/readings/energy/latest");
  });

  it("shows the same line for the weather data, from its own endpoint", async () => {
    mockLatest("2026-10-20T04:30:05Z", READING_TS);
    renderFreshness();

    expect(await screen.findByText(`Weather data changed 2 min ago (${clock(READING_TS)}) · refreshed 0 sec ago`)).toBeInTheDocument();
    expect(await screen.findByText(/^Energy data changed 4 min ago/)).toBeInTheDocument();
    expect(vi.mocked(apiFetch).mock.calls.map(([path]) => path)).toContain("/api/v1/sites/7/readings/weather/latest");
  });

  it("counts both ages up, and resets the refresh age when the data is polled again", async () => {
    mockLatest(READING_TS);
    renderFreshness();
    await screen.findByText(/Energy data changed/);

    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });
    expect(screen.getByText(/Energy data changed 2 min ago .* refreshed 30 sec ago/)).toBeInTheDocument();
    expect(screen.getByText(/Weather data changed 2 min ago .* refreshed 30 sec ago/)).toBeInTheDocument();

    // The 60 s poll fires and returns a newer reading.
    mockLatest("2026-10-20T04:35:00Z");
    await act(async () => {
      vi.advanceTimersByTime(31_000);
    });
    expect(screen.getByText(/Energy data changed [5-9] sec ago .* refreshed [0-2] sec ago/)).toBeInTheDocument();
    expect(screen.getByText(/Weather data changed [5-9] sec ago .* refreshed [0-2] sec ago/)).toBeInTheDocument();
  });

  it("says so when the site has no energy data yet", async () => {
    mockLatest(null);
    renderFreshness();

    expect(await screen.findByText("Energy data none yet · refreshed 0 sec ago")).toBeInTheDocument();
  });

  it("says so when the site has no weather data yet", async () => {
    mockLatest(READING_TS, null);
    renderFreshness();

    expect(await screen.findByText("Weather data none yet · refreshed 0 sec ago")).toBeInTheDocument();
    expect(await screen.findByText(/^Energy data changed/)).toBeInTheDocument();
  });

  it("says so when the last energy time cannot be loaded", async () => {
    mockLatest(new Error("boom"), READING_TS);
    renderFreshness();

    expect(await screen.findByText("Energy data unavailable")).toBeInTheDocument();
    expect(await screen.findByText(/^Weather data changed/)).toBeInTheDocument();
  });

  it("says so when the last weather time cannot be loaded, without hiding the energy line", async () => {
    mockLatest(READING_TS, new Error("boom"));
    renderFreshness();

    expect(await screen.findByText("Weather data unavailable")).toBeInTheDocument();
    expect(await screen.findByText(/^Energy data changed/)).toBeInTheDocument();
  });

  it("shows the database age and the live data age, with no refresh age, while live energy data is flowing", async () => {
    mockLatest(READING_TS);
    renderFreshness(Date.now() - 4_000);

    expect(
      await screen.findByText(`Energy DB last updated 2 min ago (${clock(READING_TS)}) · live data received 4 sec ago`),
    ).toBeInTheDocument();
  });

  it("shows the same live form for weather, in the same format", async () => {
    mockLatest(READING_TS);
    renderFreshness(null, Date.now() - 2_000);

    expect(
      await screen.findByText(`Weather DB last updated 2 min ago (${clock(READING_TS)}) · live data received 2 sec ago`),
    ).toBeInTheDocument();
  });

  it("switches each line to its live form on its own", async () => {
    mockLatest(READING_TS);
    renderFreshness(Date.now() - 4_000, null);

    expect(await screen.findByText(/^Energy DB last updated .* · live data received 4 sec ago$/)).toBeInTheDocument();
    expect(await screen.findByText(/^Weather data changed .* · refreshed 0 sec ago$/)).toBeInTheDocument();
  });

  it("shows both live forms when both streams are flowing", async () => {
    mockLatest(READING_TS);
    renderFreshness(Date.now() - 4_000, Date.now() - 2_000);

    expect(await screen.findByText(/^Energy DB last updated .* · live data received 4 sec ago$/)).toBeInTheDocument();
    expect(await screen.findByText(/^Weather DB last updated .* · live data received 2 sec ago$/)).toBeInTheDocument();
  });

  it("says so in live mode when the database has no energy data yet", async () => {
    mockLatest(null);
    renderFreshness(Date.now());

    expect(await screen.findByText("Energy DB has no data yet · live data received 0 sec ago")).toBeInTheDocument();
  });

  it("says so in live mode when the database has no weather data yet", async () => {
    mockLatest(READING_TS, null);
    renderFreshness(null, Date.now());

    expect(await screen.findByText("Weather DB has no data yet · live data received 0 sec ago")).toBeInTheDocument();
  });
});
