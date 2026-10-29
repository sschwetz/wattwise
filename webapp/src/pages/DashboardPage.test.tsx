import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, configureApiClient } from "../api/client";
import { FakeWebSocket } from "../dashboard/fakeWebSocket";
import { ENERGY_METRICS, WEATHER_METRICS } from "../dashboard/useLiveReadings";
import { DashboardPage } from "./DashboardPage";
import {
  energyCallPaths,
  LIVE_WINDOW_SPAN_THRESHOLD_MS,
  mockApi,
  pricePaths,
  queryParams,
  requestSpanMs,
  weatherCallPaths,
} from "./dashboardTestUtils";

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

describe("DashboardPage data freshness", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows when the energy collectors last wrote and the refresh age under the Dashboard heading", async () => {
    mockApi({}, undefined, undefined, undefined, undefined, null, null, null, "2026-10-20T04:32:05Z");
    renderPage();

    const heading = screen.getByRole("heading", { name: "Dashboard" });
    const line = await screen.findByText(/Energy data changed .*\(\d\d:\d\d:\d\d\)/);
    expect(line).toHaveTextContent(/refreshed \d+ sec ago/);
    expect(heading.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

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

  it("floors production below 100W to Idle, shown in neutral color with no glow", async () => {
    mockApi({
      production_w: [{ ts: "2026-10-01T00:00:00Z", metric: "production_w", value: 42 }],
    });
    renderPage();
    const value = await screen.findByText("Idle");
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

  it("floors battery charge/discharge below 100W to Idle with no glow", async () => {
    mockApi({
      battery_charge_w: [{ ts: "2026-10-01T00:00:00Z", metric: "battery_charge_w", value: 50 }],
    });
    renderPage();
    const value = await screen.findByText("Idle");
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

  it("shows the billing period's running cost as a full-width live tile, red with a danger glow for a net cost", async () => {
    mockApi(
      {},
      { total_cost_c: 150, total_import_kwh: 10, total_export_kwh: 4, minutes_missing_price: 0 },
      undefined,
      undefined,
      undefined,
      { total_cost_c: 300, total_import_kwh: 20, total_export_kwh: 8, minutes_missing_price: 0 },
    );
    renderPage();
    const liveTiles = within(await screen.findByTestId("live-tiles"));
    expect(await liveTiles.findByText("Billing Period")).toBeInTheDocument();
    const value = liveTiles.getByText("$3.00");
    expect(value.className).toContain("text-danger");
    expect(value.parentElement?.className).toContain("danger-glow");
  });

  it("shows the billing period's running cost as a credit tile in green with the sign dropped for a net credit", async () => {
    mockApi(
      {},
      undefined,
      undefined,
      undefined,
      undefined,
      { total_cost_c: -300, total_import_kwh: 8, total_export_kwh: 20, minutes_missing_price: 0 },
    );
    renderPage();
    const liveTiles = within(await screen.findByTestId("live-tiles"));
    expect(await liveTiles.findByText("Billing Period")).toBeInTheDocument();
    const value = liveTiles.getByText("$3.00");
    expect(value.className).toContain("text-flow");
    expect(value.parentElement?.className).toContain("flow-glow");
  });

  it("adds an Including Discounts tile with the gross fee discount total", async () => {
    mockApi(
      {},
      undefined,
      undefined,
      undefined,
      undefined,
      { total_cost_c: 300, total_import_kwh: 20, total_export_kwh: 8, minutes_missing_price: 0, monthly_fee_discounts_c: 2500 },
    );
    renderPage();
    const liveTiles = within(await screen.findByTestId("live-tiles"));
    expect(await liveTiles.findByText("Including Discounts")).toBeInTheDocument();
    expect(liveTiles.getByText("$25.00").className).toContain("text-flow");
    const billingWrapper = liveTiles.getByText("Billing Period").parentElement?.parentElement;
    expect(billingWrapper?.className).toContain("sm:col-span-2");
    expect(billingWrapper?.parentElement?.className).toContain("sm:grid-cols-3");
  });

  it("hides the Including Discounts tile when no discount covers the period", async () => {
    mockApi(
      {},
      undefined,
      undefined,
      undefined,
      undefined,
      { total_cost_c: 300, total_import_kwh: 20, total_export_kwh: 8, minutes_missing_price: 0, monthly_fee_discounts_c: 0 },
    );
    renderPage();
    const liveTiles = within(await screen.findByTestId("live-tiles"));
    expect(await liveTiles.findByText("Billing Period")).toBeInTheDocument();
    expect(liveTiles.queryByText("Including Discounts")).not.toBeInTheDocument();
  });

  it("shows nothing for the billing period tile when there's no active billing cycle", async () => {
    mockApi({});
    renderPage();
    const liveTiles = within(await screen.findByTestId("live-tiles"));
    await liveTiles.findByText("Weather");
    expect(liveTiles.queryByText("Billing Period")).not.toBeInTheDocument();
    expect(liveTiles.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows an error alert for the billing period tile when it fails for a reason other than no active cycle", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path.includes("/cost/billing-period")) {
        return Promise.reject(new ApiError(500, "internal_error", "boom", null));
      }
      return Promise.resolve([]);
    });
    renderPage();
    expect(await screen.findByText("Failed to load the billing period cost.")).toBeInTheDocument();
    expect(screen.getByText("Failed to load the billing period cost.")).toHaveAttribute("role", "alert");
  });

  it("requests today's cost with proration disabled, so the live tile shows the full service charge immediately", async () => {
    mockApi({}, { total_cost_c: 150, total_import_kwh: 10, total_export_kwh: 4, minutes_missing_price: 0 });
    renderPage();
    await within(await screen.findByTestId("live-tiles")).findByText("Today's cost");

    const todayCostRequest = vi
      .mocked(apiFetch)
      .mock.calls.map(([path]) => path as string)
      .find((path) => path.includes("/cost") && !path.includes("/cost/series") && !path.includes("/cost/billing-period"));
    expect(todayCostRequest).toBeDefined();
    expect(queryParams(todayCostRequest!).get("prorate_fees")).toBe("false");
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
    expect(await screen.findByText("Battery Discharging")).toBeInTheDocument();
    expect(screen.getByText("150 W")).toBeInTheDocument();
    expect(screen.queryByText("Battery Charging")).not.toBeInTheDocument();
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

  it("shows total production today as the per-minute production readings summed to kWh", async () => {
    mockApi({
      production_w: [
        { ts: "2026-10-01T00:00:00Z", metric: "production_w", value: 60000 },
        { ts: "2026-10-01T00:01:00Z", metric: "production_w", value: 60000 },
        { ts: "2026-10-01T00:02:00Z", metric: "production_w", value: 60000 },
      ],
    });
    renderPage();
    expect(await screen.findByText("3.00 kWh")).toBeInTheDocument();
    expect(screen.getByText("Total Production")).toBeInTheDocument();
  });

  it("shows today's rain as the sum of the rain readings since midnight", async () => {
    mockApi({}, undefined, {
      rain_mm: [
        { ts: "2026-10-01T00:00:00Z", metric: "rain_mm", value: 0.2 },
        { ts: "2026-10-01T00:01:00Z", metric: "rain_mm", value: 0.1 },
        { ts: "2026-10-01T00:02:00Z", metric: "rain_mm", value: 0 },
      ],
    });
    renderPage();
    expect(await screen.findByText("0.3 mm")).toBeInTheDocument();
    expect(screen.getByText("Rain today")).toBeInTheDocument();
  });

  it("keeps showing today's rain after the rain stops", async () => {
    mockApi({}, undefined, {
      rain_mm: [
        { ts: "2026-10-01T00:00:00Z", metric: "rain_mm", value: 0.2 },
        { ts: "2026-10-01T00:01:00Z", metric: "rain_mm", value: 0 },
        { ts: "2026-10-01T00:02:00Z", metric: "rain_mm", value: 0 },
      ],
    });
    renderPage();
    expect(await screen.findByText("0.2 mm")).toBeInTheDocument();
  });

  it("rounds the rain total to one decimal place", async () => {
    mockApi({}, undefined, {
      rain_mm: [
        { ts: "2026-10-01T00:00:00Z", metric: "rain_mm", value: 0.1 },
        { ts: "2026-10-01T00:01:00Z", metric: "rain_mm", value: 0.2 },
      ],
    });
    renderPage();
    expect(await screen.findByText("0.3 mm")).toBeInTheDocument();
  });

  it("asks for rain from local midnight, not the live 5-minute window", async () => {
    mockApi({});
    renderPage();
    await waitFor(() => expect(weatherCallPaths().some((p) => queryParams(p).get("metric") === "rain_mm")).toBe(true));

    const rainPath = weatherCallPaths().find((p) => queryParams(p).get("metric") === "rain_mm") as string;
    const start = new Date(queryParams(rainPath).get("start") as string);
    expect([start.getHours(), start.getMinutes(), start.getSeconds()]).toEqual([0, 0, 0]);
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

describe("DashboardPage today's charts", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  afterEach(() => vi.useRealTimers());

  it("shows a loading indicator on the chart until its data arrives", async () => {
    vi.mocked(apiFetch).mockImplementation(() => new Promise(() => {}));
    renderPage();
    const todayChart = await screen.findByTestId("today-chart");
    expect(within(todayChart).getByRole("status")).toHaveTextContent("Loading chart");
  });

  it("shows today's grid and cost chart with a legend", async () => {
    mockApi({});
    renderPage();
    const todayChart = await screen.findByTestId("today-chart");
    for (const label of ["Grid import", "Grid export", "Cost"]) {
      expect(within(todayChart).getByText(label)).toBeInTheDocument();
    }
  });

  it("requests today's grid and cost series at 5-minute buckets for a full calendar day", async () => {
    mockApi({});
    renderPage();
    await waitFor(() =>
      expect(vi.mocked(apiFetch).mock.calls.some(([p]) => (p as string).includes("/cost/series"))).toBe(true),
    );
    const seriesPaths = vi
      .mocked(apiFetch)
      .mock.calls.map(([p]) => p as string)
      .filter((p) => p.includes("/cost/series"));
    for (const path of seriesPaths) {
      const params = queryParams(path);
      expect(params.get("bucket")).toBe("5min");
      expect(params.get("method")).toBe("net");
      expect(Date.parse(params.get("end")!) - Date.parse(params.get("start")!)).toBe(24 * 60 * 60_000);
    }
  });

  describe("pricing charts by time of day", () => {
    afterEach(() => vi.useRealTimers());

    async function renderAt(hour: number) {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date(2026, 9, 20, hour, 30));
      mockApi({});
      renderPage();
      await screen.findByText("Today's Pricing");
    }

    it("shows only today at midday", async () => {
      await renderAt(12);
      expect(screen.queryByText("Tomorrow's Pricing")).not.toBeInTheDocument();
      expect(screen.queryByText("Yesterday's Pricing")).not.toBeInTheDocument();
    });

    it("adds tomorrow's chart below today's from 8pm", async () => {
      await renderAt(20);
      const today = screen.getByText("Today's Pricing");
      const tomorrow = screen.getByText("Tomorrow's Pricing");
      expect(today.compareDocumentPosition(tomorrow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it("shows yesterday's chart above today's until 4am", async () => {
      await renderAt(2);
      const yesterday = screen.getByText("Yesterday's Pricing");
      const today = screen.getByText("Today's Pricing");
      expect(yesterday.compareDocumentPosition(today) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(screen.queryByText("Tomorrow's Pricing")).not.toBeInTheDocument();
    });
  });

  it("shows the pricing chart with an import/export actual/predicted legend", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 9, 20, 12));
    mockApi({}, undefined, undefined, { buy: [{ ts: "2026-10-01T00:00:00Z", metric: "buy", value: 25.5 }] });
    renderPage();
    expect(await screen.findByText("Today's Pricing")).toBeInTheDocument();
    for (const label of ["Import actual", "Import predicted", "Export actual", "Export predicted"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("shows the pricing chart above the grid and cost chart", async () => {
    mockApi({});
    renderPage();
    const heading = await screen.findByText("Today's Pricing");
    const todayChart = await screen.findByTestId("today-chart");
    expect(heading.compareDocumentPosition(todayChart) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it.each(["buy", "sell"] as const)(
    "requests today's %s price spanning the full calendar day, including the future",
    async (channel) => {
      // Pinned to midday: before 4am the page also shows yesterday's chart,
      // whose window ends in the past.
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date(2026, 9, 20, 12));
      mockApi({});
      renderPage();
      const chartPaths = () =>
        pricePaths().filter((p) => queryParams(p).get("channel") === channel && (requestSpanMs(p) ?? 0) > LIVE_WINDOW_SPAN_THRESHOLD_MS);
      await waitFor(() => expect(chartPaths().length).toBeGreaterThan(0));

      const now = Date.now();
      for (const path of chartPaths()) {
        const params = queryParams(path);
        expect(Date.parse(params.get("end")!) - Date.parse(params.get("start")!)).toBe(24 * 60 * 60_000);
        expect(Date.parse(params.get("end")!)).toBeGreaterThan(now);
      }
    },
  );
});

describe("DashboardPage demand window notice", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows a red 'DEMAND WINDOW ACTIVE' notice after the heading when a window is active", async () => {
    mockApi({}, undefined, undefined, undefined, undefined, undefined, {
      id: 1,
      site_tariff_id: 1,
      effective_from: "2026-01-01T00:00:00Z",
      effective_to: null,
      label: "Peak",
      months: [1],
      days_of_week: [0],
      start_time: "00:00:00",
      end_time: "23:59:00",
      rate_c_per_kw_per_day: "45.0",
      measurement_interval_minutes: 30,
    });
    renderPage();
    const notice = await screen.findByText("- DEMAND WINDOW ACTIVE");
    expect(notice.className).toContain("text-danger");
    // The heading's own accessible name stays plain, unaffected by the
    // adjacent notice.
    expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });

  it("shows no notice when no demand window is currently active", async () => {
    mockApi({});
    renderPage();
    await screen.findByRole("heading", { name: "Dashboard" });
    expect(screen.queryByText(/DEMAND WINDOW ACTIVE/)).not.toBeInTheDocument();
  });
});

describe("DashboardPage battery chart", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows the Battery chart when the site has battery readings today", async () => {
    mockApi({
      battery_soc_pct: [{ ts: new Date().toISOString(), metric: "battery_soc_pct", value: 60 }],
    });
    renderPage();
    expect(await screen.findByTestId("battery-chart")).toBeInTheDocument();
  });

  it("hides the Battery chart when the site has no battery readings", async () => {
    mockApi({});
    renderPage();
    await screen.findByTestId("today-chart");
    expect(screen.queryByTestId("battery-chart")).not.toBeInTheDocument();
  });
});

describe("DashboardPage live stream", () => {
  const original = globalThis.WebSocket;
  const last = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  const push = (msg: unknown) =>
    act(() => {
      last().open();
      last().message(msg);
    });
  const iso = () => new Date().toISOString();
  const energyMsg = (values: Record<string, number>) => ({ kind: "energy", ts: iso(), values });
  const weatherMsg = (values: Record<string, number>) => ({ kind: "weather", ts: iso(), values });
  const polledProduction = (value: number) => ({
    production_w: [{ ts: iso(), metric: "production_w", value }],
  });

  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    FakeWebSocket.instances = [];
    globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
    localStorage.setItem("access_token", "tok");
    configureApiClient(() => localStorage.getItem("access_token"), () => undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.WebSocket = original;
    localStorage.clear();
  });

  it("shows a pushed live value on the tile before the next poll", async () => {
    mockApi(polledProduction(1000));
    renderPage();
    expect(await screen.findByText("1000 W")).toBeInTheDocument();
    push(energyMsg({ production_w: 2500 }));
    expect(await screen.findByText("2500 W")).toBeInTheDocument();
  });

  it("keeps a grid tile that arrives live between consumption and battery", async () => {
    mockApi({
      consumption_w: [{ ts: iso(), metric: "consumption_w", value: 1200 }],
      battery_charge_w: [{ ts: iso(), metric: "battery_charge_w", value: 500 }],
    });
    renderPage();
    const tiles = within(await screen.findByTestId("live-tiles"));
    await tiles.findByText("Battery Charging");
    push(energyMsg({ grid_export_w: 300 }));
    const grid = await tiles.findByText("Grid export");
    const battery = tiles.getByText("Battery Charging");
    const consumption = tiles.getByText("Consumption");
    expect(consumption.compareDocumentPosition(grid) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(grid.compareDocumentPosition(battery) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows the instantaneous live value, not the 5-minute average", async () => {
    // 12:07:30 UTC: now-120s and now-60s both fall in the bucket starting 12:05:00.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(Date.UTC(2026, 9, 20, 12, 7, 30)));
    const now = Date.now();
    mockApi({
      production_w: [
        { ts: new Date(now - 120_000).toISOString(), metric: "production_w", value: 1000 },
        { ts: new Date(now - 60_000).toISOString(), metric: "production_w", value: 3000 },
      ],
    });
    renderPage();
    expect(await screen.findByText("2000 W")).toBeInTheDocument();
    push(energyMsg({ production_w: 3700 }));
    expect(await screen.findByText("3700 W")).toBeInTheDocument();
  });

  it("falls back to the polled value after 15 seconds of silence", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi(polledProduction(1000));
    renderPage();
    expect(await screen.findByText("1000 W")).toBeInTheDocument();
    push(energyMsg({ production_w: 2500 }));
    expect(await screen.findByText("2500 W")).toBeInTheDocument();
    act(() => void vi.advanceTimersByTime(16_000));
    expect(await screen.findByText("1000 W")).toBeInTheDocument();
  });

  it("weather tiles update from a live weather message", async () => {
    mockApi({}, undefined, {
      temp_c: [{ ts: iso(), metric: "temp_c", value: 21.5 }],
    });
    renderPage();
    expect(await screen.findByText("21.5 °C")).toBeInTheDocument();
    push(weatherMsg({ temp_c: 24.5 }));
    expect(await screen.findByText("24.5 °C")).toBeInTheDocument();
  });

  it("rain today stays on stored data", async () => {
    mockApi({}, undefined, {
      rain_mm: [{ ts: iso(), metric: "rain_mm", value: 0.3 }],
    });
    renderPage();
    expect(await screen.findByText("0.3 mm")).toBeInTheDocument();
    push(weatherMsg({ rain_mm: 5, temp_c: 30.5 }));
    expect(await screen.findByText("30.5 °C")).toBeInTheDocument();
    expect(screen.getByText("0.3 mm")).toBeInTheDocument();
    expect(screen.queryByText("5 mm")).not.toBeInTheDocument();
  });

  it("tiles are unchanged when the socket never opens", async () => {
    globalThis.WebSocket = original;
    mockApi(polledProduction(1000));
    renderPage();
    expect(await screen.findByText("1000 W")).toBeInTheDocument();
  });
});
