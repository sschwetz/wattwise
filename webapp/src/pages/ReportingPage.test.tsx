import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import {
  energyCallPaths,
  LIVE_WINDOW_SPAN_THRESHOLD_MS,
  mockApi,
  queryParams,
  requestSpanMs,
} from "./dashboardTestUtils";
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

describe("ReportingPage historical chart and cost", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
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

    // Excludes /cost/billing-period: that endpoint computes its own start
    // from the active billing cycle and takes no start/end of its own.
    const costPaths = vi
      .mocked(apiFetch)
      .mock.calls.map(([path]) => path as string)
      .filter((path) => path.includes("/cost") && !path.includes("/cost/billing-period"));
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
    await waitFor(() => expect(energyCallPaths().length).toBeGreaterThanOrEqual(2));

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

});


describe("ReportingPage refetch loop regression", () => {
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
    await waitFor(() => expect(energyCallPaths().length).toBeGreaterThanOrEqual(2));
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


describe("ReportingPage date range selector", () => {
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
    await waitFor(() => expect(costSeriesBucket()).toBe("5min")); // Today: <= 1 day

    await userEvent.selectOptions(screen.getByLabelText("Date range"), "Yesterday");
    await waitFor(() => expect(costSeriesBucket()).toBe("5min")); // any 1-day period

    await userEvent.selectOptions(screen.getByLabelText("Date range"), "7 days");
    await waitFor(() => expect(costSeriesBucket()).toBe("hour")); // exactly 7 days: >= 7, < 30

    await userEvent.selectOptions(screen.getByLabelText("Date range"), "30 days");
    await waitFor(() => expect(costSeriesBucket()).toBe("6hour")); // exactly 30 days: >= 30
  });

  it("offers a Billing period range only when a billing cycle is active", async () => {
    mockApi({});
    renderPage();
    await screen.findByLabelText("Date range");
    await waitFor(() =>
      expect(vi.mocked(apiFetch).mock.calls.some(([p]) => (p as string).endsWith("/billing-period"))).toBe(true),
    );
    expect(screen.queryByRole("option", { name: "Billing period" })).not.toBeInTheDocument();
  });

  it("charts the whole billing period when Billing period is selected", async () => {
    const bounds = { start: "2026-10-01T00:00:00Z", end: "2026-11-01T00:00:00Z" };
    mockApi({}, undefined, undefined, undefined, undefined, null, null, bounds);
    renderPage();
    await screen.findByRole("option", { name: "Billing period" });

    await userEvent.selectOptions(screen.getByLabelText("Date range"), "Billing period");

    await waitFor(() => {
      const path = vi
        .mocked(apiFetch)
        .mock.calls.map(([p]) => p as string)
        .findLast((p) => p.includes("/cost/series"));
      const params = queryParams(path!);
      expect(params.get("start")).toBe("2026-10-01T00:00:00.000Z");
      expect(params.get("end")).toBe("2026-11-01T00:00:00.000Z");
      expect(params.get("bucket")).toBe("6hour"); // 31 days
    });
  });

  it("requests a 'Today' range that extends through the end of the day, not just up to now", async () => {
    mockApi({});
    const beforeRender = Date.now();
    renderPage();
    await waitFor(() => expect(energyCallPaths().length).toBeGreaterThanOrEqual(2));

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
    await waitFor(() => expect(energyCallPaths().length).toBeGreaterThanOrEqual(2));
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

