import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "../api/client";
import { queryParams } from "./dashboardTestUtils";
import { ReportingPage } from "./ReportingPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sites/7/reporting"]}>
        <Routes>
          <Route path="/sites/:siteId/reporting" element={<ReportingPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function day(date: string, overrides: Record<string, unknown> = {}) {
  return {
    start: `${date}T00:00:00Z`,
    date,
    hours: 24,
    avg_import_price_c: null,
    avg_export_price_c: null,
    grid_import_kwh: "3",
    grid_export_kwh: "1",
    battery_charge_kwh: "2",
    battery_discharge_kwh: "1",
    battery_soc_pct: "60",
    cost_c: "100",
    production_kwh: "10",
    consumption_kwh: "8",
    solar_to_battery_kwh: "2",
    grid_to_battery_kwh: "1",
    demand_charge_c: null,
    demand_charge_estimated: false,
    ...overrides,
  };
}

const BILLING_BOUNDS = { start: "2026-10-01T00:00:00Z", end: "2026-11-01T00:00:00Z" };

function mockDailyTotals(rows: Record<string, unknown>[], bounds: typeof BILLING_BOUNDS | null = BILLING_BOUNDS) {
  vi.mocked(apiFetch).mockImplementation((path: string) => {
    if (path.endsWith("/billing-period")) {
      return bounds ? Promise.resolve(bounds) : Promise.reject(new ApiError(404, "no_active_billing_cycle", "none", null));
    }
    if (path.includes("/totals")) {
      return Promise.resolve(rows);
    }
    return Promise.resolve([]);
  });
}

function dateColumn(): string[] {
  const table = within(screen.getByTestId("totals-table"));
  return table.getAllByRole("row").slice(1, -1).map((row) => within(row).getAllByRole("cell")[0].textContent ?? "");
}

function totalsPaths(): string[] {
  return vi
    .mocked(apiFetch)
    .mock.calls.map(([path]) => path as string)
    .filter((path) => path.includes("/totals"));
}

describe("ReportingPage totals", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("ends with a total row that sums the columns", async () => {
    mockDailyTotals([
      day("2026-10-01", { cost_c: "150", production_kwh: "12.345" }),
      day("2026-10-02", { cost_c: "-250" }),
    ]);
    renderPage();

    await screen.findByTestId("totals-table");
    const rows = within(screen.getByTestId("totals-table")).getAllByRole("row");
    expect(within(rows[rows.length - 1]).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "Total",
      "$-1.00",
      "22.34 kWh",
      "16.00 kWh",
      "4.00 kWh",
      "2.00 kWh",
      "–",
    ]);
  });

  it("lists each day newest first with every column formatted", async () => {
    mockDailyTotals([
      day("2026-10-01", { cost_c: "150", production_kwh: "12.345" }),
      day("2026-10-02", { cost_c: "-250" }),
    ]);
    renderPage();

    await screen.findByTestId("totals-table");
    expect(dateColumn()).toEqual(["2026-10-02", "2026-10-01"]);
    const oldest = within(screen.getByTestId("totals-table")).getAllByRole("row")[2];
    expect(within(oldest).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "2026-10-01",
      "$1.50",
      "12.35 kWh",
      "8.00 kWh",
      "2.00 kWh",
      "1.00 kWh",
      "–",
    ]);
  });

  it("marks an estimated demand charge with a warning triangle and leaves a final one bare", async () => {
    mockDailyTotals([
      day("2026-10-01", { demand_charge_c: "500", demand_charge_estimated: false }),
      day("2026-10-02", { demand_charge_c: "500", demand_charge_estimated: true }),
    ]);
    renderPage();

    await screen.findByTestId("totals-table");
    const [newest, oldest] = within(screen.getByTestId("totals-table")).getAllByRole("row").slice(1, -1);
    expect(within(newest).getByRole("img", { name: "Estimated" })).toBeInTheDocument();
    expect(within(newest).getAllByRole("cell")[6]).toHaveTextContent("$5.00");
    expect(within(oldest).queryByRole("img", { name: "Estimated" })).not.toBeInTheDocument();
  });

  it("re-sorts when a column header is clicked and flips direction on a second click", async () => {
    mockDailyTotals([
      day("2026-10-01", { cost_c: "300" }),
      day("2026-10-02", { cost_c: "100" }),
      day("2026-10-03", { cost_c: "200" }),
    ]);
    renderPage();
    await screen.findByTestId("totals-table");

    await userEvent.click(screen.getByRole("button", { name: "Cost" }));
    expect(dateColumn()).toEqual(["2026-10-01", "2026-10-03", "2026-10-02"]);
    expect(screen.getByRole("columnheader", { name: /Cost/ })).toHaveAttribute("aria-sort", "descending");

    await userEvent.click(screen.getByRole("button", { name: /Cost/ }));
    expect(dateColumn()).toEqual(["2026-10-02", "2026-10-03", "2026-10-01"]);
    expect(screen.getByRole("columnheader", { name: /Cost/ })).toHaveAttribute("aria-sort", "ascending");
  });

  it("asks for the billing period by default", async () => {
    mockDailyTotals([day("2026-10-01")]);
    renderPage();
    await screen.findByTestId("totals-table");

    const params = queryParams(totalsPaths()[0]);
    expect(params.get("start")).toBe("2026-10-01T00:00:00.000Z");
    // The billing period ends on 1 Nov 2026, before "today" is capped in.
    expect(new Date(params.get("end")!).getTime()).toBeLessThanOrEqual(new Date("2026-11-01T00:00:00Z").getTime());
    expect(params.get("method")).toBe("net");
  });

  it("shows the picked month and keeps the chosen sort", async () => {
    mockDailyTotals([day("2026-03-01", { cost_c: "100" }), day("2026-03-02", { cost_c: "300" })]);
    renderPage();
    await screen.findByTestId("totals-table");
    await userEvent.click(screen.getByRole("button", { name: "Cost" }));
    expect(dateColumn()).toEqual(["2026-03-02", "2026-03-01"]);

    await userEvent.click(screen.getByLabelText("Month"));
    await userEvent.click(await screen.findByText("Mar"));

    await waitFor(() => {
      const params = queryParams(totalsPaths().at(-1)!);
      expect(params.get("start")).toBe(new Date(new Date().getFullYear(), 2, 1).toISOString());
      expect(params.get("end")).toBe(new Date(new Date().getFullYear(), 3, 1).toISOString());
    });
    expect(screen.getByRole("columnheader", { name: /Cost/ })).toHaveAttribute("aria-sort", "descending");
  });

  it("falls back to the current month when the site has no billing cycle", async () => {
    mockDailyTotals([day("2026-11-01")], null);
    renderPage();
    await screen.findByTestId("totals-table");

    const now = new Date();
    const params = queryParams(totalsPaths()[0]);
    expect(params.get("start")).toBe(new Date(now.getFullYear(), now.getMonth(), 1).toISOString());
  });

  it("requests gross totals when Gross is selected", async () => {
    mockDailyTotals([day("2026-10-01")]);
    renderPage();
    await screen.findByTestId("totals-table");

    await userEvent.click(screen.getByLabelText("Gross"));

    await waitFor(() => expect(queryParams(totalsPaths().at(-1)!).get("method")).toBe("gross"));
  });

  it("shows an error when the totals query fails", async () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByText("Failed to load totals.")).toBeInTheDocument();
  });
});

function slot(start: Date, overrides: Record<string, unknown> = {}) {
  return day("2026-10-02", {
    start: start.toISOString(),
    hours: 1,
    avg_import_price_c: "25.5",
    avg_export_price_c: "-8",
    ...overrides,
  });
}

/** Days for the month list, hours for a day and minutes for an hour, keyed by the bucket asked for. */
function mockDrillDown() {
  vi.mocked(apiFetch).mockImplementation((path: string) => {
    if (path.endsWith("/billing-period")) {
      return Promise.resolve(BILLING_BOUNDS);
    }
    const bucket = queryParams(path).get("bucket");
    if (bucket === "hour") {
      return Promise.resolve([
        slot(new Date(2026, 9, 2, 12), { cost_c: "500" }),
        slot(new Date(2026, 9, 2, 13), { cost_c: "100" }),
      ]);
    }
    if (bucket === "minute") {
      return Promise.resolve([slot(new Date(2026, 9, 2, 13, 0)), slot(new Date(2026, 9, 2, 13, 1))]);
    }
    return Promise.resolve([day("2026-10-01", { cost_c: "300" }), day("2026-10-02", { cost_c: "200" })]);
  });
}

function renderDrilledInto(search = "") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/sites/7/reporting${search}`]}>
        <Routes>
          <Route path="/sites/:siteId/reporting" element={<ReportingPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ReportingPage drill-down", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows a chart above the table on the day list", async () => {
    mockDrillDown();
    renderPage();
    await screen.findByTestId("totals-table");
    const chart = screen.getByTestId("totals-chart");
    expect(chart.compareDocumentPosition(screen.getByTestId("totals-table"))).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    for (const label of ["Production", "Consumption", "Grid import", "Grid export", "Battery import", "Battery export"]) {
      expect(within(chart).getByText(`${label} (kW)`)).toBeInTheDocument();
    }
    expect(within(chart).getByText("Battery charge (%)")).toBeInTheDocument();
  });

  it("opens a day by the hour, with average prices after the cost", async () => {
    mockDrillDown();
    renderPage();
    await screen.findByTestId("totals-table");

    await userEvent.click(screen.getByText("2026-10-02"));

    await screen.findByRole("button", { name: "Avg import price" });
    const headers = within(screen.getByTestId("totals-table")).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers.slice(0, 4)).toEqual(["Hour \u25BC", "Cost", "Avg import price", "Avg export price"]);
    expect(headers).not.toContain("Total demand charge");
    const params = queryParams(totalsPaths().at(-1)!);
    expect(params.get("bucket")).toBe("hour");
    expect(params.get("start")).toBe(new Date(2026, 9, 2).toISOString());
    expect(params.get("end")).toBe(new Date(2026, 9, 3).toISOString());
    expect(screen.getAllByText("25.5 c/kWh")).toHaveLength(3);
    expect(screen.getByTestId("totals-chart")).toBeInTheDocument();
    expect(dateColumn()).toEqual(["13:00", "12:00"]);
  });

  it("opens an hour by the minute, and minute rows do not drill further", async () => {
    mockDrillDown();
    renderPage();
    await screen.findByTestId("totals-table");
    await userEvent.click(screen.getByText("2026-10-02"));
    await userEvent.click(await screen.findByText("13:00"));

    await waitFor(() => expect(queryParams(totalsPaths().at(-1)!).get("bucket")).toBe("minute"));
    const params = queryParams(totalsPaths().at(-1)!);
    expect(params.get("start")).toBe(new Date(2026, 9, 2, 13).toISOString());
    expect(params.get("end")).toBe(new Date(2026, 9, 2, 14).toISOString());
    await waitFor(() => expect(dateColumn()).toEqual(["13:01", "13:00"]));
    const calls = totalsPaths().length;
    await userEvent.click(screen.getByText("13:01"));
    expect(totalsPaths().length).toBe(calls);
    expect(screen.getByTestId("totals-chart")).toBeInTheDocument();
  });

  it("goes back one level at a time with the back arrow", async () => {
    mockDrillDown();
    renderPage();
    await screen.findByTestId("totals-table");
    await userEvent.click(screen.getByText("2026-10-02"));
    await userEvent.click(await screen.findByText("13:00"));
    await waitFor(() => expect(dateColumn()).toEqual(["13:01", "13:00"]));

    await userEvent.click(screen.getByRole("link", { name: "Back" }));
    await waitFor(() => expect(dateColumn()).toEqual(["13:00", "12:00"]));

    await userEvent.click(screen.getByRole("link", { name: "Back" }));
    await waitFor(() => expect(dateColumn()).toEqual(["2026-10-02", "2026-10-01"]));
    expect(screen.queryByRole("link", { name: "Back" })).not.toBeInTheDocument();
  });

  it("carries the chosen sort into the next level", async () => {
    mockDrillDown();
    renderPage();
    await screen.findByTestId("totals-table");
    await userEvent.click(screen.getByRole("button", { name: "Cost" }));
    expect(dateColumn()).toEqual(["2026-10-01", "2026-10-02"]);

    await userEvent.click(screen.getByText("2026-10-01"));

    await waitFor(() => expect(dateColumn()).toEqual(["12:00", "13:00"]));
    expect(screen.getByRole("columnheader", { name: /Cost/ })).toHaveAttribute("aria-sort", "descending");
  });

  it("falls back to time order when the sorted column does not exist at the next level", async () => {
    mockDrillDown();
    renderPage();
    await screen.findByTestId("totals-table");
    await userEvent.click(screen.getByRole("button", { name: "Total demand charge" }));

    await userEvent.click(screen.getByText("2026-10-01"));

    await waitFor(() => expect(dateColumn()).toEqual(["13:00", "12:00"]));
    expect(screen.getByRole("columnheader", { name: /Hour/ })).toHaveAttribute("aria-sort", "descending");
  });

  it("opens straight onto a day from its link", async () => {
    mockDrillDown();
    renderDrilledInto("?month=2026-10&day=2026-10-02");
    await screen.findByRole("button", { name: "Avg export price" });
    expect(queryParams(totalsPaths()[0]).get("bucket")).toBe("hour");
    expect(screen.getByRole("link", { name: "Back" })).toHaveAttribute("href", "/sites/7/reporting?month=2026-10");
  });
});


describe("ReportingPage refetch loop regression", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("does not keep issuing new fetches across render cycles when nothing user-controlled changes", async () => {
    // Resolve on a real timer tick so wall-clock time passes between renders,
    // as it would for a network request. A range recomputed from `new Date()`
    // on every render would then build a new query key each time.
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      return new Promise((resolve) => {
        setTimeout(() => {
          if (path.endsWith("/billing-period")) {
            resolve(BILLING_BOUNDS);
          } else {
            resolve([day("2026-10-01")]);
          }
        }, 2);
      });
    });

    renderPage();
    await screen.findByTestId("totals-table");
    const callCountAfterSettle = vi.mocked(apiFetch).mock.calls.length;

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 150));
    });

    expect(vi.mocked(apiFetch).mock.calls.length).toBe(callCountAfterSettle);
  });
});
