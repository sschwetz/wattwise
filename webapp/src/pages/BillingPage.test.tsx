import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { BillingTab, TariffsTab } from "./BillingPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function BillingPageTabs() {
  return (
    <>
      <TariffsTab siteId={7} />
      <BillingTab siteId={7} />
    </>
  );
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sites/7/billing"]}>
        <Routes>
          <Route path="/sites/:siteId/billing" element={<BillingPageTabs />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function emptyListsFetch(_path: string) {
  return Promise.resolve([]);
}

describe("Billing tabs chrome", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("has no back link or page heading of its own", async () => {
    vi.mocked(apiFetch).mockImplementation(emptyListsFetch as never);
    renderPage();
    await screen.findByText("Billing cycle");
    expect(screen.queryByRole("link", { name: "Back" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });
});

const ACTIVE_TARIFF = {
  id: 1,
  site_id: 7,
  effective_from: "2026-01-01T00:00:00Z",
  effective_to: null,
  service_availability_c_per_day: "95",
};

const DEMAND_WINDOW = {
  id: 1,
  site_tariff_id: 1,
  effective_from: "2026-01-01T00:00:00Z",
  effective_to: null,
  label: "Summer peak",
  months: [12, 1, 2],
  days_of_week: [0, 1, 2, 3, 4],
  start_time: "16:00:00",
  end_time: "20:00:00",
  rate_c_per_kw_per_day: "45",
  measurement_interval_minutes: 30,
};

const EXPORT_ADJUSTMENT = {
  id: 1,
  site_tariff_id: 1,
  effective_from: "2026-01-01T00:00:00Z",
  effective_to: null,
  label: "Solar soak incentive",
  months: [11, 12, 1, 2],
  days_of_week: [0, 1, 2, 3, 4, 5, 6],
  start_time: "10:00:00",
  end_time: "15:00:00",
  adjustment_c_per_kwh: "5",
  daily_allowance_kwh: "0.0000",
};

const MONTHLY_FEE_DISCOUNT = {
  id: 1,
  site_tariff_id: 1,
  effective_from: "2026-01-01T00:00:00Z",
  effective_to: null,
  label: "New customer promo",
  starts_at: "2026-01-01T00:00:00Z",
  ends_at: "2026-04-01T00:00:00Z",
  discounted_fee_c: "0",
};

describe("Billing tabs", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("lists existing tariffs and creates a new one", async () => {
    vi.mocked(apiFetch).mockImplementation(emptyListsFetch);
    renderPage();
    await screen.findByText("No tariffs yet.");
    await userEvent.type(screen.getByPlaceholderText("Service availability (cents/day)"), "95");
    await userEvent.click(screen.getByText("Add tariff"));
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/tariffs",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("hints that demand windows live under a tariff when there are none yet", async () => {
    vi.mocked(apiFetch).mockImplementation(emptyListsFetch);
    renderPage();
    await screen.findByText("No tariffs yet.");
    expect(screen.getByText(/add a tariff first/i)).toBeInTheDocument();
  });

  it("does not show the no-tariffs hint once a tariff exists", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    expect(screen.queryByText(/add a tariff first/i)).not.toBeInTheDocument();
  });

  it("lists active tariffs before ended ones and minimises ended tariffs", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([
          { id: 1, site_id: 7, effective_from: "2025-01-01T00:00:00Z", effective_to: "2025-06-01T00:00:00Z", service_availability_c_per_day: "80" },
          { id: 2, site_id: 7, effective_from: "2026-01-01T00:00:00Z", effective_to: null, service_availability_c_per_day: "95" },
        ]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    const items = screen.getAllByRole("listitem");
    const tariffItems = items.filter((item) => /c\/day/.test(item.textContent ?? ""));
    expect(tariffItems[0]).toHaveTextContent(/95\.00c\/day/);
    expect(tariffItems[1]).toHaveTextContent(/80\.00c\/day/);
    expect(screen.getByText("End this tariff")).toBeInTheDocument();
    expect(screen.getByText(/until/)).toBeInTheDocument();
  });

  it("shows an ended tariff disabled with strikethrough, but still visible", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([
          { id: 1, site_id: 7, effective_from: "2025-01-01T00:00:00Z", effective_to: "2025-06-01T00:00:00Z", service_availability_c_per_day: "80" },
        ]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    const item = await screen.findByText(/80\.00c\/day/);
    expect(item.closest("li")).toHaveClass("line-through");
    // The bold c/day figure is its own span inside a <button> -- assert it
    // carries the class directly, since CSS text-decoration inherited from
    // an ancestor outside a <button> does not reliably paint through.
    expect(item).toHaveClass("line-through");
  });

  it("does not style an active tariff as disabled", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    const item = await screen.findByText(/95\.00c\/day/);
    expect(item.closest("li")).not.toHaveClass("line-through");
  });

  it("toggles an ended tariff's demand windows open and closed on click", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([
          {
            id: 1,
            site_id: 7,
            effective_from: "2025-01-01T00:00:00Z",
            effective_to: "2025-06-01T00:00:00Z",
            service_availability_c_per_day: "80",
          },
        ]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/demand-windows") {
        return Promise.resolve([{ ...DEMAND_WINDOW, label: "Winter peak", months: [6, 7, 8], rate_c_per_kw_per_day: "30" }]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    const toggle = await screen.findByText(/80\.00c\/day/);
    expect(screen.queryByText("Winter peak")).not.toBeInTheDocument();

    await userEvent.click(toggle);
    expect(await screen.findByText("Winter peak")).toBeInTheDocument();

    await userEvent.click(toggle);
    expect(screen.queryByText("Winter peak")).not.toBeInTheDocument();
  });

  it("renders an existing demand window's schedule in human-readable form", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/demand-windows") {
        return Promise.resolve([DEMAND_WINDOW]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    const item = await screen.findByText(/Summer peak/);
    expect(item.closest("li")).toHaveTextContent(/45\.00c\/kW\/day/);
    expect(item.closest("li")).toHaveTextContent(/16:00–20:00/);
    expect(item.closest("li")).toHaveTextContent(/Mon, Tue, Wed, Thu, Fri/);
    expect(item.closest("li")).toHaveTextContent(/Dec, Jan, Feb/);
    expect(item.closest("li")).toHaveTextContent(/every 30 min/);
  });

  it("creates a demand window under an existing tariff", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    await userEvent.type(screen.getByPlaceholderText("Window label"), "Summer peak");
    await userEvent.click(within(screen.getByRole("group", { name: "Months" })).getByLabelText("Dec"));
    await userEvent.click(within(screen.getByRole("group", { name: "Months" })).getByLabelText("Jan"));
    await userEvent.click(within(screen.getByRole("group", { name: "Months" })).getByLabelText("Feb"));
    await userEvent.click(within(screen.getByRole("group", { name: "Days of week" })).getByLabelText("Mon"));
    await userEvent.click(within(screen.getByRole("group", { name: "Days of week" })).getByLabelText("Tue"));
    await userEvent.click(within(screen.getByRole("group", { name: "Days of week" })).getByLabelText("Wed"));
    await userEvent.click(within(screen.getByRole("group", { name: "Days of week" })).getByLabelText("Thu"));
    await userEvent.click(within(screen.getByRole("group", { name: "Days of week" })).getByLabelText("Fri"));
    await userEvent.type(screen.getByPlaceholderText("Start time (HH:MM)"), "16:00");
    await userEvent.type(screen.getByPlaceholderText("End time (HH:MM)"), "20:00");
    await userEvent.type(screen.getByPlaceholderText("Rate (cents/kW/day)"), "45");
    await userEvent.click(screen.getByText("Add demand window"));
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/tariffs/1/demand-windows",
      expect.objectContaining({ method: "POST" }),
    );
    const postCall = vi
      .mocked(apiFetch)
      .mock.calls.find(
        ([path, options]) =>
          path === "/api/v1/sites/7/tariffs/1/demand-windows" && options?.method === "POST",
      );
    expect(postCall).toBeDefined();
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
      effective_from: expect.any(String),
      label: "Summer peak",
      months: [1, 2, 12],
      days_of_week: [0, 1, 2, 3, 4],
      start_time: "16:00",
      end_time: "20:00",
      rate_c_per_kw_per_day: 45,
    });
  });

  it("renders an existing export adjustment window with its signed rate", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/export-adjustments") {
        return Promise.resolve([EXPORT_ADJUSTMENT]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    const item = await screen.findByText(/Solar soak incentive/);
    expect(item.closest("li")).toHaveTextContent(/\+5\.00c\/kWh/);
    expect(item.closest("li")).toHaveTextContent(/10:00–15:00/);
    expect(item.closest("li")).toHaveTextContent(/every day/);
    expect(item.closest("li")).toHaveTextContent(/Nov, Dec, Jan, Feb/);
  });

  it("renders a disincentive export adjustment window's negative rate without a double sign", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/export-adjustments") {
        return Promise.resolve([{ ...EXPORT_ADJUSTMENT, label: "Grid congestion penalty", adjustment_c_per_kwh: "-3" }]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    const item = await screen.findByText(/Grid congestion penalty/);
    expect(item.closest("li")).toHaveTextContent(/-3\.00c\/kWh/);
  });

  it("renders an export adjustment window's daily allowance", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/export-adjustments") {
        return Promise.resolve([
          { ...EXPORT_ADJUSTMENT, label: "Export fee", adjustment_c_per_kwh: "-3", daily_allowance_kwh: "10.0000" },
        ]);
      }
      if (path === "/api/v1/sites/7/billing-cycle") {
        return Promise.resolve([{ id: 1, site_id: 7, effective_from: "2026-01-01T00:00:00Z", effective_to: null, cycle_months: 1, anchor_day: 1 }]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    const item = await screen.findByText("Export fee");
    expect(item.closest("li")).toHaveTextContent(/10 kWh\/day allowance/);
    // The note depends on the cycles response, so wait for the request and let
    // its result render before checking the note is absent.
    await waitFor(() => expect(vi.mocked(apiFetch)).toHaveBeenCalledWith("/api/v1/sites/7/billing-cycle"));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByText(/allowance is ignored/)).not.toBeInTheDocument();
  });

  it("warns that an allowance is ignored while no billing cycle is set", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/export-adjustments") {
        return Promise.resolve([{ ...EXPORT_ADJUSTMENT, daily_allowance_kwh: "10.0000" }]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    expect(await screen.findByText(/allowance is ignored until you set a billing cycle/i)).toBeInTheDocument();
  });

  it("sends the daily allowance when creating an export adjustment window", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    await userEvent.type(screen.getByPlaceholderText("Adjustment label"), "Export fee");
    await userEvent.click(within(screen.getByRole("group", { name: "Export months" })).getByLabelText("Jan"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Mon"));
    await userEvent.type(screen.getByPlaceholderText("Export start time (HH:MM)"), "00:00");
    await userEvent.type(screen.getByPlaceholderText("Export end time (HH:MM)"), "23:59");
    await userEvent.type(screen.getByPlaceholderText("Adjustment (+incentive / -disincentive, cents/kWh)"), "-3");
    await userEvent.type(screen.getByPlaceholderText("Daily allowance (kWh, 0 = none)"), "10");
    await userEvent.click(screen.getByText("Add export adjustment"));
    const postCall = vi
      .mocked(apiFetch)
      .mock.calls.find(
        ([path, options]) =>
          path === "/api/v1/sites/7/tariffs/1/export-adjustments" && options?.method === "POST",
      );
    expect(JSON.parse(String(postCall?.[1]?.body)).daily_allowance_kwh).toBe(10);
  });

  it("creates an export adjustment window under an existing tariff", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    await userEvent.type(screen.getByPlaceholderText("Adjustment label"), "Solar soak incentive");
    await userEvent.click(within(screen.getByRole("group", { name: "Export months" })).getByLabelText("Nov"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export months" })).getByLabelText("Dec"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export months" })).getByLabelText("Jan"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export months" })).getByLabelText("Feb"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Mon"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Tue"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Wed"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Thu"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Fri"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Sat"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Sun"));
    await userEvent.type(screen.getByPlaceholderText("Export start time (HH:MM)"), "10:00");
    await userEvent.type(screen.getByPlaceholderText("Export end time (HH:MM)"), "15:00");
    await userEvent.type(screen.getByPlaceholderText("Adjustment (+incentive / -disincentive, cents/kWh)"), "5");
    await userEvent.click(screen.getByText("Add export adjustment"));
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/tariffs/1/export-adjustments",
      expect.objectContaining({ method: "POST" }),
    );
    const postCall = vi
      .mocked(apiFetch)
      .mock.calls.find(
        ([path, options]) =>
          path === "/api/v1/sites/7/tariffs/1/export-adjustments" && options?.method === "POST",
      );
    expect(postCall).toBeDefined();
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
      effective_from: expect.any(String),
      label: "Solar soak incentive",
      months: [1, 2, 11, 12],
      days_of_week: [0, 1, 2, 3, 4, 5, 6],
      start_time: "10:00",
      end_time: "15:00",
      adjustment_c_per_kwh: 5,
      daily_allowance_kwh: 0,
    });
  });

  it("shows an error when creating an export adjustment window fails", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockImplementation((path: string, options?: RequestInit) => {
      if (path === "/api/v1/sites/7/tariffs" && (!options || options.method === undefined)) {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/export-adjustments" && options?.method === "POST") {
        return Promise.reject(new ApiError(400, "invalid_adjustment", "Adjustment window overlaps an existing one", null));
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    await userEvent.type(screen.getByPlaceholderText("Adjustment label"), "Solar soak incentive");
    await userEvent.click(within(screen.getByRole("group", { name: "Export months" })).getByLabelText("Nov"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export months" })).getByLabelText("Dec"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export months" })).getByLabelText("Jan"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export months" })).getByLabelText("Feb"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Mon"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Tue"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Wed"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Thu"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Fri"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Sat"));
    await userEvent.click(within(screen.getByRole("group", { name: "Export days of week" })).getByLabelText("Sun"));
    await userEvent.type(screen.getByPlaceholderText("Export start time (HH:MM)"), "10:00");
    await userEvent.type(screen.getByPlaceholderText("Export end time (HH:MM)"), "15:00");
    await userEvent.type(screen.getByPlaceholderText("Adjustment (+incentive / -disincentive, cents/kWh)"), "5");
    await userEvent.click(screen.getByText("Add export adjustment"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Adjustment window overlaps an existing one");
  });

  it("creates a tariff with an optional monthly fee entered in dollars", async () => {
    vi.mocked(apiFetch).mockImplementation(emptyListsFetch);
    renderPage();
    await screen.findByText("No tariffs yet.");
    await userEvent.type(screen.getByPlaceholderText("Service availability (cents/day)"), "95");
    await userEvent.type(screen.getByPlaceholderText("Monthly fee ($/month)"), "25.00");
    await userEvent.click(screen.getByText("Add tariff"));
    const postCall = vi
      .mocked(apiFetch)
      .mock.calls.find(([path, options]) => path === "/api/v1/sites/7/tariffs" && options?.method === "POST");
    expect(postCall).toBeDefined();
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual(
      expect.objectContaining({ monthly_fee_c: 2500 }),
    );
  });

  it("displays a tariff's monthly fee in dollars when set", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([{ ...ACTIVE_TARIFF, monthly_fee_c: "2500" }]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    const tariffRow = await screen.findByText(/95\.00c\/day/);
    expect(tariffRow.closest("li")).toHaveTextContent(/\$25\.00\/month/);
  });

  it("renders an existing monthly fee discount window as a negative dollar amount", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/monthly-fee-discounts") {
        return Promise.resolve([{ ...MONTHLY_FEE_DISCOUNT, discounted_fee_c: "1000" }]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    const item = await screen.findByText(/New customer promo/);
    expect(item.closest("li")).toHaveTextContent(/-\$10\.00\/month/);
    // The page formats dates in the viewer's locale, so build the expectation the same way.
    expect(item.closest("li")).toHaveTextContent(new Date(MONTHLY_FEE_DISCOUNT.starts_at).toLocaleDateString());
    expect(item.closest("li")).toHaveTextContent(new Date(MONTHLY_FEE_DISCOUNT.ends_at).toLocaleDateString());
  });

  it("does not style a current fee discount window as disabled", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/monthly-fee-discounts") {
        return Promise.resolve([{ ...MONTHLY_FEE_DISCOUNT, ends_at: "2999-01-01T00:00:00Z" }]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    const item = await screen.findByText(/New customer promo/);
    expect(item.closest("li")).not.toHaveClass("line-through");
  });

  it("keeps an expired fee discount window visible but shows it disabled with strikethrough", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/monthly-fee-discounts") {
        return Promise.resolve([{ ...MONTHLY_FEE_DISCOUNT, ends_at: "2020-01-01T00:00:00Z" }]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    const item = await screen.findByText(/New customer promo/);
    expect(item.closest("li")).toHaveClass("line-through");
    expect(item).toHaveClass("line-through");
    const amount = await screen.findByText(/-\$0\.00\/month/);
    expect(amount).toHaveClass("line-through");
  });

  it("creates a monthly fee discount window with a dollar amount under an existing tariff", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    await userEvent.type(screen.getByPlaceholderText("Discount label"), "New customer promo");
    await userEvent.type(screen.getByPlaceholderText("Discount starts (YYYY-MM-DD)"), "2026-01-01");
    await userEvent.type(screen.getByPlaceholderText("Discount ends (YYYY-MM-DD)"), "2026-04-01");
    await userEvent.type(screen.getByPlaceholderText("Discounted fee ($/month)"), "10.00");
    await userEvent.click(screen.getByText("Add fee discount"));
    const postCall = vi
      .mocked(apiFetch)
      .mock.calls.find(
        ([path, options]) =>
          path === "/api/v1/sites/7/tariffs/1/monthly-fee-discounts" && options?.method === "POST",
      );
    expect(postCall).toBeDefined();
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
      label: "New customer promo",
      starts_at: "2026-01-01T00:00:00Z",
      ends_at: "2026-04-01T00:00:00Z",
      discounted_fee_c: 1000,
    });
  });

  it("shows an error when creating a monthly fee discount window fails", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockImplementation((path: string, options?: RequestInit) => {
      if (path === "/api/v1/sites/7/tariffs" && (!options || options.method === undefined)) {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/monthly-fee-discounts" && options?.method === "POST") {
        return Promise.reject(new ApiError(400, "invalid_window", "Discount window overlaps an existing one", null));
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    await userEvent.type(screen.getByPlaceholderText("Discount label"), "New customer promo");
    await userEvent.type(screen.getByPlaceholderText("Discount starts (YYYY-MM-DD)"), "2026-01-01");
    await userEvent.type(screen.getByPlaceholderText("Discount ends (YYYY-MM-DD)"), "2026-04-01");
    await userEvent.type(screen.getByPlaceholderText("Discounted fee ($/month)"), "0.00");
    await userEvent.click(screen.getByText("Add fee discount"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Discount window overlaps an existing one");
  });

  it("lists existing billing cycles and creates a new one", async () => {
    vi.mocked(apiFetch).mockImplementation(emptyListsFetch);
    renderPage();
    await screen.findByText("No billing cycles yet.");
    await userEvent.type(screen.getByPlaceholderText("Cycle length (months)"), "3");
    await userEvent.type(screen.getByPlaceholderText("Anchor day (1-31)"), "1");
    await userEvent.click(screen.getByText("Add billing cycle"));
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/billing-cycle",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows an explicit empty state for demand charges, not an error", async () => {
    vi.mocked(apiFetch).mockImplementation(emptyListsFetch);
    renderPage();
    expect(await screen.findByText(/No demand charges yet/)).toBeInTheDocument();
  });

  it("shows an error when creating a tariff fails", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockImplementation((path: string, options?: RequestInit) => {
      if (path === "/api/v1/sites/7/tariffs" && options?.method === "POST") {
        return Promise.reject(new ApiError(400, "invalid_tariff", "Service availability is invalid", null));
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText("No tariffs yet.");
    await userEvent.type(screen.getByPlaceholderText("Service availability (cents/day)"), "95");
    await userEvent.click(screen.getByText("Add tariff"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Service availability is invalid");
  });

  it("shows an error when ending a tariff fails", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockImplementation((path: string, options?: RequestInit) => {
      if (path === "/api/v1/sites/7/tariffs" && (!options || options.method === undefined)) {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1" && options?.method === "PATCH") {
        return Promise.reject(new ApiError(500, "server_error", "Could not end tariff", null));
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    await userEvent.click(screen.getByText("End this tariff"));
    await userEvent.click(screen.getByText("Confirm end"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not end tariff");
  });

  it("shows an error when creating a demand window fails", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockImplementation((path: string, options?: RequestInit) => {
      if (path === "/api/v1/sites/7/tariffs" && (!options || options.method === undefined)) {
        return Promise.resolve([ACTIVE_TARIFF]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/demand-windows" && options?.method === "POST") {
        return Promise.reject(new ApiError(400, "invalid_window", "Demand window overlaps an existing one", null));
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    await userEvent.type(screen.getByPlaceholderText("Window label"), "Summer peak");
    await userEvent.click(within(screen.getByRole("group", { name: "Months" })).getByLabelText("Dec"));
    await userEvent.click(within(screen.getByRole("group", { name: "Months" })).getByLabelText("Jan"));
    await userEvent.click(within(screen.getByRole("group", { name: "Months" })).getByLabelText("Feb"));
    await userEvent.click(within(screen.getByRole("group", { name: "Days of week" })).getByLabelText("Mon"));
    await userEvent.click(within(screen.getByRole("group", { name: "Days of week" })).getByLabelText("Tue"));
    await userEvent.click(within(screen.getByRole("group", { name: "Days of week" })).getByLabelText("Wed"));
    await userEvent.click(within(screen.getByRole("group", { name: "Days of week" })).getByLabelText("Thu"));
    await userEvent.click(within(screen.getByRole("group", { name: "Days of week" })).getByLabelText("Fri"));
    await userEvent.type(screen.getByPlaceholderText("Start time (HH:MM)"), "16:00");
    await userEvent.type(screen.getByPlaceholderText("End time (HH:MM)"), "20:00");
    await userEvent.type(screen.getByPlaceholderText("Rate (cents/kW/day)"), "45");
    await userEvent.click(screen.getByText("Add demand window"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Demand window overlaps an existing one");
  });

  it("shows an error when creating a billing cycle fails", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockImplementation((path: string, options?: RequestInit) => {
      if (path === "/api/v1/sites/7/billing-cycle" && options?.method === "POST") {
        return Promise.reject(new ApiError(400, "invalid_cycle", "Anchor day is invalid", null));
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText("No billing cycles yet.");
    await userEvent.type(screen.getByPlaceholderText("Cycle length (months)"), "3");
    await userEvent.type(screen.getByPlaceholderText("Anchor day (1-31)"), "1");
    await userEvent.click(screen.getByText("Add billing cycle"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Anchor day is invalid");
  });

  it("shows an error when ending a billing cycle fails", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockImplementation((path: string, options?: RequestInit) => {
      if (path === "/api/v1/sites/7/billing-cycle" && (!options || options.method === undefined)) {
        return Promise.resolve([
          { id: 1, site_id: 7, effective_from: "2026-01-01T00:00:00Z", effective_to: null, cycle_months: 3, anchor_day: 1 },
        ]);
      }
      if (path === "/api/v1/sites/7/billing-cycle/1" && options?.method === "PATCH") {
        return Promise.reject(new ApiError(500, "server_error", "Could not end billing cycle", null));
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/Every 3 month/);
    await userEvent.click(screen.getByText("End this cycle"));
    await userEvent.click(screen.getByText("Confirm end"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not end billing cycle");
  });

  it("groups existing demand windows, bonuses, and discounts together before any of the add-new forms", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([{ ...ACTIVE_TARIFF, monthly_fee_c: "2500" }]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/demand-windows") {
        return Promise.resolve([DEMAND_WINDOW]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/export-adjustments") {
        return Promise.resolve([EXPORT_ADJUSTMENT]);
      }
      if (path === "/api/v1/sites/7/tariffs/1/monthly-fee-discounts") {
        return Promise.resolve([MONTHLY_FEE_DISCOUNT]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95\.00c\/day/);
    const discountItem = await screen.findByText(/New customer promo/);
    const addDemandButton = screen.getByText("Add demand window");
    const addExportButton = screen.getByText("Add export adjustment");

    // The discount window belongs to the last section, but every existing
    // item (across all three categories) must still be grouped above every
    // add-form (across all three categories) -- not interleaved per section.
    expect(discountItem.compareDocumentPosition(addDemandButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(discountItem.compareDocumentPosition(addExportButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("names the tariff section after the site's pricing source", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/sources") {
        return Promise.resolve([
          { id: 5, site_id: 7, kind: "pricing", provider: "local", name: "Origin Fixed", enabled: true, effective_to: null, config: {} },
        ]);
      }
      return emptyListsFetch(path);
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "Origin Fixed Tariffs" })).toBeInTheDocument();
  });

  it("falls back to Tariffs with no pricing source", async () => {
    vi.mocked(apiFetch).mockImplementation(emptyListsFetch);
    renderPage();

    expect(await screen.findByRole("heading", { name: "Tariffs" })).toBeInTheDocument();
  });
});
