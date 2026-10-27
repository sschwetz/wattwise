import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { BillingTab, TariffsTab } from "./BillingPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const SITE = { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true };
const TARIFF = {
  id: 1,
  site_id: 7,
  effective_from: "2026-01-01T00:00:00Z",
  effective_to: null,
  service_availability_c_per_day: "95",
  monthly_fee_c: null,
};
const JUN_30_END = "2026-06-30T14:00:00.000Z"; // 00:00 on 1 July in Sydney
const PATTERN = { months: [1], days_of_week: [0], start_time: "16:00:00", end_time: "20:00:00" };

const KINDS = [
  {
    name: "demand",
    path: "demand-windows",
    nameInLabel: "Demand window",
    add: "Add demand window",
    window: { ...PATTERN, label: "Peak", rate_c_per_kw_per_day: "45", measurement_interval_minutes: 30 },
  },
  {
    name: "export adjustment",
    path: "export-adjustments",
    nameInLabel: "Export adjustment",
    add: "Add export adjustment",
    window: { ...PATTERN, label: "Peak", adjustment_c_per_kwh: "-2", daily_allowance_kwh: "0" },
  },
  {
    name: "conditional credit",
    path: "conditional-credits",
    nameInLabel: "Credit",
    add: "Add conditional credit",
    window: { ...PATTERN, label: "Peak", max_avg_import_w: "100", credit_c: "50" },
  },
];

function windowRow(kind: (typeof KINDS)[number], effective_from: string, effective_to: string | null) {
  return { id: 5, site_tariff_id: 1, effective_from, effective_to, ...kind.window };
}

function mockApi(lists: Record<string, unknown[]>) {
  vi.mocked(apiFetch).mockImplementation(((path: string, options?: RequestInit) => {
    if (path === "/api/v1/sites") return Promise.resolve([SITE]);
    if (path === "/api/v1/sites/7/tariffs" && !options?.method) return Promise.resolve([TARIFF]);
    if (options?.method === "PATCH" || options?.method === "POST") return Promise.resolve({});
    return Promise.resolve(lists[path] ?? []);
  }) as never);
}

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

describe.each(KINDS)("$name window dates", (kind) => {
  const listPath = `/api/v1/sites/7/tariffs/1/${kind.path}`;
  beforeEach(() => vi.mocked(apiFetch).mockReset());

  it("suggests the day after the latest ended window as the start date", async () => {
    mockApi({ [listPath]: [windowRow(kind, "2026-01-01T00:00:00Z", JUN_30_END)] });
    renderPage();
    await screen.findByText(/until/);

    expect(screen.getByLabelText(`${kind.nameInLabel} start date`)).toHaveValue("2026-07-01");
  });

  it("shows an ended window struck through with its last day", async () => {
    mockApi({ [listPath]: [windowRow(kind, "2026-01-01T00:00:00Z", JUN_30_END)] });
    renderPage();
    const until = await screen.findByText(new RegExp(`until ${new Date("2026-06-30T00:00:00").toLocaleDateString()}`));

    expect(until.closest("li")).toHaveClass("line-through");
  });

  it("ends a window on the chosen last day", async () => {
    mockApi({ [listPath]: [windowRow(kind, "2026-01-01T00:00:00Z", null)] });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "End Peak" }));
    fireEvent.change(screen.getByLabelText("End date"), { target: { value: "2026-06-30" } });
    await userEvent.click(screen.getByText("Confirm end"));

    expect(apiFetch).toHaveBeenCalledWith(
      `${listPath}/5`,
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ effective_to: JUN_30_END }) }),
    );
  });

  it("sends the chosen start and end dates when adding a window", async () => {
    mockApi({});
    renderPage();
    await screen.findByText(kind.add);
    fireEvent.change(screen.getByLabelText(`${kind.nameInLabel} start date`), { target: { value: "2026-07-01" } });
    fireEvent.change(screen.getByLabelText(`${kind.nameInLabel} end date (optional)`), { target: { value: "2027-06-30" } });
    const form = screen.getByText(kind.add).closest("form") as HTMLFormElement;
    // Required fields are not under test here; submit the form directly.
    fireEvent.submit(form);

    const posted = () => vi.mocked(apiFetch).mock.calls.find(([p, o]) => p === listPath && o?.method === "POST");
    await waitFor(() => expect(posted()).toBeDefined());
    const body = JSON.parse(String(posted()?.[1]?.body));
    expect(body.effective_from).toBe(JUN_30_END);
    expect(body.effective_to).toBe("2027-06-30T14:00:00.000Z");
    expect(within(form).getByText(kind.add)).toBeInTheDocument();
  });
});
