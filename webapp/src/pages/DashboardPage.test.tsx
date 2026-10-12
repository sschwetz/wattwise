import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { ENERGY_METRICS, WEATHER_METRICS, type Reading } from "../dashboard/useLiveReadings";
import { DashboardPage } from "./DashboardPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sites/7"]}>
        <Routes>
          <Route path="/sites/:siteId" element={<DashboardPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function queryParams(path: string): URLSearchParams {
  return new URLSearchParams(path.split("?")[1] ?? "");
}

/**
 * The real Core API (GET /sites/{id}/readings/energy, /readings/weather, /prices) each take
 * a single required metric/channel plus `start`/`end`, and return only those rows. These
 * mocks reproduce that contract: every readings response is filtered down to what was asked.
 */
function mockApi(
  readingsByMetric: Record<string, Reading[]>,
  cost?: Record<string, unknown>,
  weatherByMetric?: Record<string, Reading[]>,
  priceReadingsByChannel?: Record<string, Reading[]>,
  costSeries?: Record<string, unknown>[],
) {
  vi.mocked(apiFetch).mockImplementation((path: string) => {
    if (path.includes("/readings/energy")) {
      const metric = queryParams(path).get("metric");
      return Promise.resolve(metric ? (readingsByMetric[metric] ?? []) : []);
    }
    if (path.includes("/readings/weather")) {
      const metric = queryParams(path).get("metric");
      return Promise.resolve(metric ? (weatherByMetric?.[metric] ?? []) : []);
    }
    if (path.includes("/prices")) {
      const channel = queryParams(path).get("channel");
      return Promise.resolve(channel ? (priceReadingsByChannel?.[channel] ?? []) : []);
    }
    if (path.includes("/cost/series")) {
      return Promise.resolve(costSeries ?? []);
    }
    if (path.includes("/cost")) {
      return Promise.resolve(cost ?? null);
    }
    return Promise.resolve([]);
  });
}

function energyCallPaths(): string[] {
  return vi
    .mocked(apiFetch)
    .mock.calls.map(([path]) => path as string)
    .filter((path) => path.includes("/readings/energy"));
}

function weatherCallPaths(): string[] {
  return vi
    .mocked(apiFetch)
    .mock.calls.map(([path]) => path as string)
    .filter((path) => path.includes("/readings/weather"));
}

function pricePaths(): string[] {
  return vi
    .mocked(apiFetch)
    .mock.calls.map(([path]) => path as string)
    .filter((path) => path.includes("/prices"));
}

/** The live grid-price tile and the today's-pricing-chart hit the identical
 * `/prices?channel=...` endpoint shape, distinguishable only by how wide a
 * span they request: the live tile polls a few minutes back, the chart (like
 * the history views) spans a full calendar day or more. */
function requestSpanMs(path: string): number | null {
  const params = queryParams(path);
  const start = params.get("start");
  const end = params.get("end");
  return start && end ? Date.parse(end) - Date.parse(start) : null;
}

const LIVE_WINDOW_SPAN_THRESHOLD_MS = 60 * 60_000;

describe("DashboardPage live tiles", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows the latest value for each known energy metric", async () => {
    mockApi({
      production_w: [{ ts: "2026-10-01T00:00:00Z", metric: "production_w", value: 2500 }],
      consumption_w: [{ ts: "2026-10-01T00:00:00Z", metric: "consumption_w", value: 1200 }],
    });
    renderPage();
    expect(await screen.findByText("2500 W")).toBeInTheDocument();
    expect(await screen.findByText("1200 W")).toBeInTheDocument();
  });

  it("rounds production/consumption/grid/battery-charge-discharge to the nearest whole number", async () => {
    mockApi({
      production_w: [{ ts: "2026-10-01T00:00:00Z", metric: "production_w", value: 1234.6 }],
      consumption_w: [{ ts: "2026-10-01T00:00:00Z", metric: "consumption_w", value: 1234.4 }],
      grid_export_w: [{ ts: "2026-10-01T00:00:00Z", metric: "grid_export_w", value: 99.5 }],
      battery_charge_w: [{ ts: "2026-10-01T00:00:00Z", metric: "battery_charge_w", value: 500.5 }],
    });
    renderPage();
    expect(await screen.findByText("1235 W")).toBeInTheDocument();
    expect(await screen.findByText("1234 W")).toBeInTheDocument();
    expect(await screen.findByText("100 W")).toBeInTheDocument();
    expect(await screen.findByText("501 W")).toBeInTheDocument();
  });

  it("floors production below 100W to 0 W, shown in neutral color with no glow", async () => {
    mockApi({
      production_w: [{ ts: "2026-10-01T00:00:00Z", metric: "production_w", value: 42 }],
    });
    renderPage();
    const value = await screen.findByText("0 W");
    expect(value.className).toContain("text-ink");
    expect(value.className).not.toContain("text-solar");
    expect(value.parentElement?.className).not.toContain("solar-glow");
  });

  it("shows an active (non-floored) production reading with its solar accent and glow", async () => {
    mockApi({
      production_w: [{ ts: "2026-10-01T00:00:00Z", metric: "production_w", value: 2500 }],
    });
    renderPage();
    const value = await screen.findByText("2500 W");
    expect(value.className).toContain("text-solar");
    expect(value.parentElement?.className).toContain("solar-glow");
  });

  it("floors battery charge/discharge below 100W to 0 W with no glow", async () => {
    mockApi({
      battery_charge_w: [{ ts: "2026-10-01T00:00:00Z", metric: "battery_charge_w", value: 50 }],
    });
    renderPage();
    const value = await screen.findByText("0 W");
    expect(value.className).toContain("text-ink");
    expect(value.className).not.toContain("text-flow");
    expect(value.parentElement?.className).not.toContain("flow-glow");
  });

  it("shows an active battery discharge reading in red with a danger glow", async () => {
    mockApi({
      battery_discharge_w: [{ ts: "2026-10-01T00:00:00Z", metric: "battery_discharge_w", value: 500 }],
    });
    renderPage();
    const value = await screen.findByText("500 W");
    expect(value.className).toContain("text-danger");
    expect(value.parentElement?.className).toContain("danger-glow");
  });

  it("shows an active battery charge reading in green (flow) with a flow glow", async () => {
    mockApi({
      battery_charge_w: [{ ts: "2026-10-01T00:00:00Z", metric: "battery_charge_w", value: 500 }],
    });
    renderPage();
    const value = await screen.findByText("500 W");
    expect(value.className).toContain("text-flow");
    expect(value.parentElement?.className).toContain("flow-glow");
  });

  it("shows grid import with a danger (red) glow", async () => {
    mockApi({
      grid_import_w: [{ ts: "2026-10-01T00:00:00Z", metric: "grid_import_w", value: 1094 }],
    });
    renderPage();
    const value = await screen.findByText("1094 W");
    expect(value.className).toContain("text-danger");
    expect(value.parentElement?.className).toContain("danger-glow");
  });

  it("shows grid export with a flow (green) glow", async () => {
    mockApi({
      grid_export_w: [{ ts: "2026-10-01T00:00:00Z", metric: "grid_export_w", value: 1094 }],
    });
    renderPage();
    const value = await screen.findByText("1094 W");
    expect(value.className).toContain("text-flow");
    expect(value.parentElement?.className).toContain("flow-glow");
  });

  it("shows the grid price tile using the buy price while importing, in red with a danger glow", async () => {
    mockApi(
      { grid_import_w: [{ ts: "2026-10-01T00:05:00Z", metric: "grid_import_w", value: 500 }] },
      undefined,
      undefined,
      { buy: [{ ts: "2026-10-01T00:09:00Z", metric: "buy", value: 32.1 }] },
    );
    renderPage();
    expect(await screen.findByText("Grid import price")).toBeInTheDocument();
    const value = screen.getByText("32.1c/kWh");
    expect(value.className).toContain("text-danger");
    expect(value.parentElement?.className).toContain("danger-glow");
  });

  it("shows the grid price tile using the sell price while exporting, sign dropped, in green with a flow glow", async () => {
    mockApi(
      { grid_export_w: [{ ts: "2026-10-01T00:05:00Z", metric: "grid_export_w", value: 500 }] },
      undefined,
      undefined,
      // Amber's raw feed-in sign: negative means the customer is paid 8.4c/kWh.
      { sell: [{ ts: "2026-10-01T00:09:00Z", metric: "sell", value: -8.4 }] },
    );
    renderPage();
    expect(await screen.findByText("Grid export price")).toBeInTheDocument();
    const value = screen.getByText("8.4c/kWh");
    expect(value.className).toContain("text-flow");
    expect(value.parentElement?.className).toContain("flow-glow");
  });

  it("shows the grid price tile's plain magnitude even during a rare negative-FiT event (raw sell price positive)", async () => {
    mockApi(
      { grid_export_w: [{ ts: "2026-10-01T00:05:00Z", metric: "grid_export_w", value: 500 }] },
      undefined,
      undefined,
      { sell: [{ ts: "2026-10-01T00:09:00Z", metric: "sell", value: 0.9 }] },
    );
    renderPage();
    const value = await screen.findByText("0.9c/kWh");
    expect(value.className).toContain("text-flow");
  });

  it("requests only the price channel matching the current grid direction, with start/end bounds", async () => {
    mockApi({ grid_import_w: [{ ts: "2026-10-01T00:05:00Z", metric: "grid_import_w", value: 500 }] });
    renderPage();
    // Exclude the today's-pricing-chart requests (a full calendar day, always
    // both channels) -- this test is only about the live grid-price tile,
    // which polls a short window and should track the current grid direction.
    function liveWindowPricePaths(): string[] {
      return pricePaths().filter((path) => {
        const span = requestSpanMs(path);
        return span !== null && span <= LIVE_WINDOW_SPAN_THRESHOLD_MS;
      });
    }
    await waitFor(() => expect(liveWindowPricePaths().length).toBeGreaterThan(0));
    const livePricePaths = liveWindowPricePaths();
    const requestedChannels = new Set(livePricePaths.map((path) => queryParams(path).get("channel")));
    expect(requestedChannels).toEqual(new Set(["buy"]));
    for (const path of livePricePaths) {
      const params = queryParams(path);
      expect(params.get("start")).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      expect(params.get("end")).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    }
  });

  it("shows an error when the grid price fails to load", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path.includes("/prices") && queryParams(path).get("channel") === "buy") {
        return Promise.reject(new Error("boom"));
      }
      if (path.includes("/readings/energy") && queryParams(path).get("metric") === "grid_import_w") {
        return Promise.resolve([{ ts: "2026-10-01T00:05:00Z", metric: "grid_import_w", value: 500 }]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    expect(await screen.findByText("Failed to load the grid import price.")).toBeInTheDocument();
    expect(screen.getByText("Failed to load the grid import price.")).toHaveAttribute("role", "alert");
  });

  it("shows today's running cost as a live tile, red with a danger glow for a net cost", async () => {
    mockApi({}, { total_cost_c: 150, total_import_kwh: 10, total_export_kwh: 4, minutes_missing_price: 0 });
    renderPage();
    const liveTiles = within(await screen.findByTestId("live-tiles"));
    expect(await liveTiles.findByText("Today's cost")).toBeInTheDocument();
    const value = liveTiles.getByText("$1.50");
    expect(value.className).toContain("text-danger");
    expect(value.parentElement?.className).toContain("danger-glow");
  });

  it("shows today's running cost as a credit tile in green with the sign dropped for a net credit", async () => {
    mockApi({}, { total_cost_c: -150, total_import_kwh: 4, total_export_kwh: 10, minutes_missing_price: 0 });
    renderPage();
    const liveTiles = within(await screen.findByTestId("live-tiles"));
    expect(await liveTiles.findByText("Today's credit")).toBeInTheDocument();
    const value = liveTiles.getByText("$1.50");
    expect(value.className).toContain("text-flow");
    expect(value.parentElement?.className).toContain("flow-glow");
  });

  it("keeps requesting today's cost at method=net even after the history card's range/method change", async () => {
    mockApi({}, { total_cost_c: 150, total_import_kwh: 10, total_export_kwh: 4, minutes_missing_price: 0 });
    renderPage();
    await within(await screen.findByTestId("live-tiles")).findByText("Today's cost");

    await userEvent.selectOptions(screen.getByLabelText("Date range"), "30 days");
    await userEvent.click(screen.getByLabelText("Gross"));
    await waitFor(() => expect(screen.getByLabelText("Gross")).toBeChecked());

    // Even after the history card switches to 30-days/gross, there must still
    // be a net-method cost request in flight -- it can only be today's tile.
    const netCostRequests = vi
      .mocked(apiFetch)
      .mock.calls.map(([path]) => path as string)
      .filter(
        (path) => path.includes("/cost") && !path.includes("/cost/series") && queryParams(path).get("method") === "net",
      );
    expect(netCostRequests.length).toBeGreaterThan(0);
  });

  it("does not round battery state of charge", async () => {
    mockApi({
      battery_soc_pct: [{ ts: "2026-10-01T00:00:00Z", metric: "battery_soc_pct", value: 72.5 }],
    });
    renderPage();
    expect(await screen.findByText("72.5 %")).toBeInTheDocument();
  });

/** The battery tile's background must be the same diagonal soft-gradient
 * shape every other live tile uses (.solar-glow/.flow-glow/.danger-glow in
 * index.css: linear-gradient(120deg, color, surface 45%, color 90%)), just
 * with a computed color instead of a fixed accent token -- a flat solid
 * fill looks inconsistent with the rest of the dashboard. */
function batteryGradient(color: string): string {
  return `linear-gradient(120deg, ${color}, var(--color-surface) 45%, ${color} 90%)`;
}

describe("DashboardPage battery tile background", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("colors the battery tile pure danger at or below 10% charge", async () => {
    mockApi({
      battery_soc_pct: [{ ts: "2026-10-01T00:00:00Z", metric: "battery_soc_pct", value: 5 }],
    });
    renderPage();
    const value = await screen.findByText("5 %");
    expect(value.parentElement?.style.backgroundImage).toBe(
      batteryGradient("color-mix(in oklab, var(--color-solar-soft) 0%, var(--color-danger-soft) 100%)"),
    );
  });

  it("colors the battery tile pure solar (amber) at the midpoint of its charge range", async () => {
    mockApi({
      battery_soc_pct: [{ ts: "2026-10-01T00:00:00Z", metric: "battery_soc_pct", value: 55 }],
    });
    renderPage();
    const value = await screen.findByText("55 %");
    expect(value.parentElement?.style.backgroundImage).toBe(
      batteryGradient("color-mix(in oklab, var(--color-solar-soft) 100%, var(--color-danger-soft) 0%)"),
    );
  });

  it("colors the battery tile pure flow (green) at 100% charge", async () => {
    mockApi({
      battery_soc_pct: [{ ts: "2026-10-01T00:00:00Z", metric: "battery_soc_pct", value: 100 }],
    });
    renderPage();
    const value = await screen.findByText("100 %");
    expect(value.parentElement?.style.backgroundImage).toBe(
      batteryGradient("color-mix(in oklab, var(--color-flow-soft) 100%, var(--color-solar-soft) 0%)"),
    );
  });

  it("blends the battery tile background partway between danger and solar below the midpoint", async () => {
    mockApi({
      battery_soc_pct: [{ ts: "2026-10-01T00:00:00Z", metric: "battery_soc_pct", value: 30 }],
    });
    renderPage();
    const value = await screen.findByText("30 %");
    expect(value.parentElement?.style.backgroundImage).toBe(
      batteryGradient("color-mix(in oklab, var(--color-solar-soft) 44.4%, var(--color-danger-soft) 55.6%)"),
    );
  });
});

  it("requested the readings for the site id from the route", async () => {
    mockApi({});
    renderPage();
    await waitFor(() => expect(vi.mocked(apiFetch)).toHaveBeenCalled());
    const calledPaths = vi.mocked(apiFetch).mock.calls.map(([path]) => path as string);
    expect(calledPaths.some((p) => p.includes("/sites/7/readings/energy"))).toBe(true);
  });

  it("requests every energy metric individually with start/end bounds the backend accepts", async () => {
    mockApi({});
    renderPage();
    await waitFor(() => expect(energyCallPaths().length).toBeGreaterThanOrEqual(ENERGY_METRICS.length));

    const energyCalls = energyCallPaths();
    const requestedMetrics = new Set(energyCalls.map((path) => queryParams(path).get("metric")));
    for (const metric of ENERGY_METRICS) {
      expect(requestedMetrics.has(metric)).toBe(true);
    }

    for (const path of energyCalls) {
      const params = queryParams(path);
      // The backend requires start/end (NOT from/to) — a from/to request 422s.
      expect(params.get("start")).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      expect(params.get("end")).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      expect(params.has("from")).toBe(false);
      expect(params.has("to")).toBe(false);
    }
  });

  it("shows only the more recent of grid import/export when both have readings in the live window", async () => {
    mockApi({
      grid_import_w: [{ ts: "2026-10-01T00:05:00Z", metric: "grid_import_w", value: 14.085 }],
      grid_export_w: [{ ts: "2026-10-01T00:06:00Z", metric: "grid_export_w", value: 18.322 }],
    });
    renderPage();
    // Scoped to the live tiles: the history chart's legend always shows a static
    // "Grid import" label alongside "Grid export", regardless of live data.
    const liveTiles = within(screen.getByTestId("live-tiles"));
    expect(await liveTiles.findByText("Grid export")).toBeInTheDocument();
    expect(liveTiles.getByText("18 W")).toBeInTheDocument();
    expect(liveTiles.queryByText("Grid import")).not.toBeInTheDocument();
    expect(liveTiles.queryByText("14 W")).not.toBeInTheDocument();
  });

  it("shows only the more recent of battery charge/discharge when both have readings in the live window", async () => {
    mockApi({
      battery_charge_w: [{ ts: "2026-10-01T00:05:00Z", metric: "battery_charge_w", value: 2000 }],
      battery_discharge_w: [{ ts: "2026-10-01T00:06:00Z", metric: "battery_discharge_w", value: 150 }],
    });
    renderPage();
    expect(await screen.findByText("Battery discharge")).toBeInTheDocument();
    expect(screen.getByText("150 W")).toBeInTheDocument();
    expect(screen.queryByText("Battery charge")).not.toBeInTheDocument();
    expect(screen.queryByText("2000 W")).not.toBeInTheDocument();
  });

  it("shows an error when the live readings fail to load", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path.includes("/readings/energy")) {
        return Promise.reject(new Error("boom"));
      }
      return Promise.resolve([]);
    });
    renderPage();
    expect(await screen.findByText("Failed to load live readings.")).toBeInTheDocument();
    expect(screen.getByText("Failed to load live readings.")).toHaveAttribute("role", "alert");
  });

  it("shows the latest value for each known weather metric", async () => {
    mockApi({}, undefined, {
      temp_c: [{ ts: "2026-10-01T00:00:00Z", metric: "temp_c", value: 21.5 }],
      humidity_pct: [{ ts: "2026-10-01T00:00:00Z", metric: "humidity_pct", value: 55 }],
    });
    renderPage();
    expect(await screen.findByText("21.5 °C")).toBeInTheDocument();
    expect(await screen.findByText("55 %")).toBeInTheDocument();
  });

  it("rounds temperature and barometric pressure to one decimal place", async () => {
    mockApi({}, undefined, {
      temp_c: [{ ts: "2026-10-01T00:00:00Z", metric: "temp_c", value: 21.456 }],
      barometric_pressure_hpa: [{ ts: "2026-10-01T00:00:00Z", metric: "barometric_pressure_hpa", value: 1013.249 }],
    });
    renderPage();
    expect(await screen.findByText("21.5 °C")).toBeInTheDocument();
    expect(await screen.findByText("1013.2 hPa")).toBeInTheDocument();
  });

  it("combines wind speed and direction into a single tile with a compass abbreviation", async () => {
    mockApi({}, undefined, {
      wind_speed_kph: [{ ts: "2026-10-01T00:00:00Z", metric: "wind_speed_kph", value: 12.34 }],
      wind_direction_deg: [{ ts: "2026-10-01T00:00:00Z", metric: "wind_direction_deg", value: 45 }],
    });
    renderPage();
    expect(await screen.findByText("12.3 km/h NE")).toBeInTheDocument();
    expect(screen.queryByText("Wind direction")).not.toBeInTheDocument();
  });

  it("shows wind speed alone when no direction reading exists yet", async () => {
    mockApi({}, undefined, {
      wind_speed_kph: [{ ts: "2026-10-01T00:00:00Z", metric: "wind_speed_kph", value: 12.34 }],
    });
    renderPage();
    expect(await screen.findByText("12.3 km/h")).toBeInTheDocument();
  });

  it("requests every weather metric individually with start/end bounds the backend accepts", async () => {
    mockApi({});
    renderPage();
    await waitFor(() => expect(weatherCallPaths().length).toBeGreaterThanOrEqual(WEATHER_METRICS.length));

    const weatherCalls = weatherCallPaths();
    const requestedMetrics = new Set(weatherCalls.map((path) => queryParams(path).get("metric")));
    for (const metric of WEATHER_METRICS) {
      expect(requestedMetrics.has(metric)).toBe(true);
    }

    for (const path of weatherCalls) {
      const params = queryParams(path);
      expect(params.get("start")).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      expect(params.get("end")).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    }
  });

  it("shows an error when the live weather readings fail to load", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path.includes("/readings/weather")) {
        return Promise.reject(new Error("boom"));
      }
      return Promise.resolve([]);
    });
    renderPage();
    expect(await screen.findByText("Failed to load live weather.")).toBeInTheDocument();
    expect(screen.getByText("Failed to load live weather.")).toHaveAttribute("role", "alert");
  });
});

describe("DashboardPage historical chart and cost", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows a legend for the chart's grid-import/export and cost series", async () => {
    mockApi({});
    renderPage();
    expect(await screen.findByText("Grid import")).toBeInTheDocument();
    expect(screen.getByText("Grid export")).toBeInTheDocument();
    expect(screen.getByText("Cost")).toBeInTheDocument();
  });

  it("shows the computed cost for the selected net/gross method and re-fetches when toggled", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path.includes("/cost/series")) {
        return Promise.resolve([]);
      }
      if (path.includes("/cost")) {
        const method = queryParams(path).get("method");
        return Promise.resolve({
          total_cost_c: method === "net" ? 150 : 300,
          total_import_kwh: 10,
          total_export_kwh: 4,
          minutes_missing_price: 0,
        });
      }
      return Promise.resolve([]);
    });
    renderPage();
    // Scoped to the history card: the new "Today's cost" live tile (always
    // net) coincidentally shows the same "$1.50" when the selected range/
    // method happens to match today's net total.
    const historyCost = within(screen.getByTestId("history-cost"));
    expect(await historyCost.findByText("$1.50")).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Gross"));
    expect(await historyCost.findByText("$3.00")).toBeInTheDocument();
  });

  it("shows a positive (net cost) total in red with a danger glow", async () => {
    mockApi({}, { total_cost_c: 150, total_import_kwh: 10, total_export_kwh: 4, minutes_missing_price: 0 });
    renderPage();
    const value = await within(screen.getByTestId("history-cost")).findByText("$1.50");
    expect(value.className).toContain("text-danger");
    expect(value.parentElement?.className).toContain("danger-glow");
  });

  it("shows a negative (net credit) total in green with a flow glow", async () => {
    mockApi({}, { total_cost_c: -150, total_import_kwh: 4, total_export_kwh: 10, minutes_missing_price: 0 });
    renderPage();
    const value = await screen.findByText("$-1.50");
    expect(value.className).toContain("text-flow");
    expect(value.parentElement?.className).toContain("flow-glow");
  });

  it("requests the cost with start/end bounds, not from/to", async () => {
    mockApi({}, { total_cost_c: 150, total_import_kwh: 10, total_export_kwh: 4, minutes_missing_price: 0 });
    renderPage();
    await within(screen.getByTestId("history-cost")).findByText("$1.50");

    const costPaths = vi
      .mocked(apiFetch)
      .mock.calls.map(([path]) => path as string)
      .filter((path) => path.includes("/cost"));
    expect(costPaths.length).toBeGreaterThan(0);
    for (const path of costPaths) {
      const params = queryParams(path);
      expect(params.get("start")).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      expect(params.get("end")).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      expect(params.get("method")).toBe("net");
      expect(params.has("from")).toBe(false);
      expect(params.has("to")).toBe(false);
    }
  });

  it("requests both grid import and export for the history query", async () => {
    mockApi({
      grid_export_w: [{ ts: "2026-10-01T00:00:00Z", metric: "grid_export_w", value: 1000 }],
      grid_import_w: [{ ts: "2026-10-01T00:00:00Z", metric: "grid_import_w", value: 500 }],
    });
    renderPage();
    await waitFor(() => expect(energyCallPaths().length).toBeGreaterThan(ENERGY_METRICS.length));

    // The history query spans a day; the live queries span only five minutes.
    const historyPaths = energyCallPaths().filter((path) => {
      const span = requestSpanMs(path);
      return span !== null && span > LIVE_WINDOW_SPAN_THRESHOLD_MS;
    });
    const requestedMetrics = new Set(historyPaths.map((path) => queryParams(path).get("metric")));
    expect(requestedMetrics).toEqual(new Set(["grid_import_w", "grid_export_w"]));
  });

  it("shows errors when the history and cost queries fail", async () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByText("Failed to load cost.")).toBeInTheDocument();
    expect(await screen.findByText("Failed to load historical readings.")).toBeInTheDocument();
    const alerts = screen.getAllByRole("alert").map((node) => node.textContent);
    expect(alerts).toContain("Failed to load cost.");
    expect(alerts).toContain("Failed to load historical readings.");
  });

  it("shows nothing for the grid price tile when there is no recent grid reading to establish a direction", async () => {
    mockApi({}, { total_cost_c: 150, total_import_kwh: 10, total_export_kwh: 4, minutes_missing_price: 0 });
    renderPage();
    await within(screen.getByTestId("history-cost")).findByText("$1.50");
    expect(screen.queryByText(/c\/kWh/)).not.toBeInTheDocument();
  });
});

describe("DashboardPage refetch loop regression", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("does not keep issuing new fetches across render cycles when nothing user-controlled changes", async () => {
    // Resolve via a real timer tick (rather than an already-resolved Promise) so
    // actual wall-clock milliseconds elapse between renders, the same as a real
    // network request would take. This matters for reproducing the regression:
    // if every resolution landed in the same millisecond, `rangeForDays`'s
    // `new Date()` could coincidentally produce the same ISO string each time
    // and mask the bug.
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      return new Promise((resolve) => {
        setTimeout(() => {
          if (path.includes("/cost/series")) {
            resolve([]);
          } else if (path.includes("/cost")) {
            resolve({
              total_cost_c: 150,
              total_import_kwh: 10,
              total_export_kwh: 4,
              minutes_missing_price: 0,
            });
          } else {
            resolve([]);
          }
        }, 2);
      });
    });

    renderPage();

    // Let the initial queries resolve and the page settle.
    await within(screen.getByTestId("history-cost")).findByText("$1.50");
    await waitFor(() => expect(energyCallPaths().length).toBeGreaterThan(ENERGY_METRICS.length));
    const callCountAfterSettle = vi.mocked(apiFetch).mock.calls.length;

    // Give the component many more render cycles with real time elapsing between
    // them. If the date range were recomputed on every render (via `new Date()`
    // in `rangeForDays`), each cycle would build a brand-new, never-before-seen
    // query key and trigger another fetch, growing this count without bound.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 150));
    });

    expect(vi.mocked(apiFetch).mock.calls.length).toBe(callCountAfterSettle);
  });
});

describe("DashboardPage date range selector", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows custom start/end pickers only once Custom range is selected", async () => {
    mockApi({});
    renderPage();
    await screen.findByLabelText("Date range");
    expect(screen.queryByLabelText("Custom range start")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Custom range end")).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Date range"), "Custom range");

    expect(screen.getByLabelText("Custom range start")).toBeInTheDocument();
    expect(screen.getByLabelText("Custom range end")).toBeInTheDocument();
  });

  function costSeriesBucket(): string | null {
    const path = vi
      .mocked(apiFetch)
      .mock.calls.map(([p]) => p as string)
      .findLast((p) => p.includes("/cost/series"));
    return path ? queryParams(path).get("bucket") : null;
  }

  it("uses progressively coarser buckets as the preset range grows", async () => {
    mockApi({});
    renderPage();
    await waitFor(() => expect(costSeriesBucket()).toBe("15min")); // Today: < 7 days

    await userEvent.selectOptions(screen.getByLabelText("Date range"), "7 days");
    await waitFor(() => expect(costSeriesBucket()).toBe("hour")); // exactly 7 days: >= 7, < 30

    await userEvent.selectOptions(screen.getByLabelText("Date range"), "30 days");
    await waitFor(() => expect(costSeriesBucket()).toBe("6hour")); // exactly 30 days: >= 30
  });

  it("requests a 'Today' range that extends through the end of the day, not just up to now", async () => {
    mockApi({});
    const beforeRender = Date.now();
    renderPage();
    await waitFor(() => expect(energyCallPaths().length).toBeGreaterThan(ENERGY_METRICS.length));

    const historyPaths = energyCallPaths().filter((path) => {
      const span = requestSpanMs(path);
      return span !== null && span > LIVE_WINDOW_SPAN_THRESHOLD_MS;
    });
    expect(historyPaths.length).toBeGreaterThan(0);
    for (const path of historyPaths) {
      const end = Date.parse(queryParams(path).get("end")!);
      // The old behavior ended exactly at "now"; the new behavior runs through
      // the end of the local calendar day, which is always later than "now".
      expect(end).toBeGreaterThan(beforeRender);
    }
  });

  it("offers a 'Yesterday' option requesting exactly yesterday's full calendar day", async () => {
    mockApi({});
    renderPage();
    await waitFor(() => expect(energyCallPaths().length).toBeGreaterThan(ENERGY_METRICS.length));
    // Only the requests triggered by selecting "Yesterday" matter here -- the
    // initial "Today" mount also issues 24h-span requests (deliberately
    // extending into the future), which would otherwise pollute this check.
    vi.mocked(apiFetch).mockClear();

    const beforeNow = Date.now();
    await userEvent.selectOptions(screen.getByLabelText("Date range"), "Yesterday");
    await waitFor(() => expect(energyCallPaths().length).toBeGreaterThan(0));

    const historyPaths = energyCallPaths().filter((path) => {
      const span = requestSpanMs(path);
      return span !== null && span > LIVE_WINDOW_SPAN_THRESHOLD_MS;
    });
    expect(historyPaths.length).toBeGreaterThan(0);
    for (const path of historyPaths) {
      const params = queryParams(path);
      const start = Date.parse(params.get("start")!);
      const end = Date.parse(params.get("end")!);
      expect(end - start).toBe(24 * 60 * 60_000);
      // Unlike "Today", "Yesterday" is fully elapsed -- it must not run past now.
      expect(end).toBeLessThanOrEqual(beforeNow);
    }
  });
});

describe("DashboardPage today's pricing chart", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows the chart heading and an import/export actual/predicted legend", async () => {
    mockApi({}, undefined, undefined, { buy: [{ ts: "2026-10-01T00:00:00Z", metric: "buy", value: 25.5 }] });
    renderPage();
    expect(await screen.findByText("Today's Pricing")).toBeInTheDocument();
    expect(screen.getByText("Import actual")).toBeInTheDocument();
    expect(screen.getByText("Import predicted")).toBeInTheDocument();
    expect(screen.getByText("Export actual")).toBeInTheDocument();
    expect(screen.getByText("Export predicted")).toBeInTheDocument();
  });

  it("requests today's buy price spanning the full calendar day, including the future", async () => {
    mockApi({});
    renderPage();
    await waitFor(() =>
      expect(
        vi.mocked(apiFetch).mock.calls.some(([path]) => (path as string).includes("channel=buy")),
      ).toBe(true),
    );

    const beforeNow = Date.now();
    const buyPricePaths = vi
      .mocked(apiFetch)
      .mock.calls.map(([path]) => path as string)
      .filter((path) => path.includes("/prices") && queryParams(path).get("channel") === "buy");
    expect(buyPricePaths.length).toBeGreaterThan(0);
    for (const path of buyPricePaths) {
      const params = queryParams(path);
      const start = Date.parse(params.get("start")!);
      const end = Date.parse(params.get("end")!);
      expect(end - start).toBe(24 * 60 * 60_000);
      // Extends into the future (to pick up forecast rows), unlike the
      // live-tile buy/sell price hooks which only look a few minutes back.
      expect(end).toBeGreaterThan(beforeNow);
    }
  });

  it("requests today's sell price spanning the full calendar day, including the future", async () => {
    mockApi({});
    renderPage();
    await waitFor(() =>
      expect(
        vi.mocked(apiFetch).mock.calls.some(([path]) => (path as string).includes("channel=sell")),
      ).toBe(true),
    );

    const beforeNow = Date.now();
    const sellPricePaths = vi
      .mocked(apiFetch)
      .mock.calls.map(([path]) => path as string)
      .filter((path) => path.includes("/prices") && queryParams(path).get("channel") === "sell");
    expect(sellPricePaths.length).toBeGreaterThan(0);
    for (const path of sellPricePaths) {
      const params = queryParams(path);
      const start = Date.parse(params.get("start")!);
      const end = Date.parse(params.get("end")!);
      expect(end - start).toBe(24 * 60 * 60_000);
      expect(end).toBeGreaterThan(beforeNow);
    }
  });
});
