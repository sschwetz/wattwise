import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { BillingPage } from "./BillingPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const SITE = { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney" };
const JUN_30_END = "2026-06-30T14:00:00.000Z"; // 00:00 on 1 July in Sydney

function tariff(id: number, effective_from: string, effective_to: string | null) {
  return { id, site_id: 7, effective_from, effective_to, service_availability_c_per_day: "95", monthly_fee_c: null };
}

function mockApi(lists: Record<string, unknown[]>) {
  vi.mocked(apiFetch).mockImplementation(((path: string, options?: RequestInit) => {
    if (path === "/api/v1/sites") return Promise.resolve([SITE]);
    if (options?.method === "PATCH" || options?.method === "POST") return Promise.resolve({});
    return Promise.resolve(lists[path] ?? []);
  }) as never);
}

function bodyOf(path: string, method: string): Record<string, unknown> {
  const call = vi.mocked(apiFetch).mock.calls.find(([p, o]) => p === path && o?.method === method);
  return JSON.parse(String(call?.[1]?.body));
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sites/7/billing"]}>
        <Routes>
          <Route path="/sites/:siteId/billing" element={<BillingPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("BillingPage tariff and cycle dates", () => {
  beforeEach(() => vi.mocked(apiFetch).mockReset());

  it("adds a tariff starting on the chosen date, open ended", async () => {
    mockApi({});
    renderPage();
    await screen.findByText("No tariffs yet.");
    await userEvent.type(screen.getByPlaceholderText("Service availability (cents/day)"), "95");
    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-07-01" } });
    await userEvent.click(screen.getByText("Add tariff"));

    const body = bodyOf("/api/v1/sites/7/tariffs", "POST");
    expect(body.effective_from).toBe(JUN_30_END);
    expect(body).not.toHaveProperty("effective_to");
  });

  it("adds a tariff with an end date, which is the last day it applies", async () => {
    mockApi({});
    renderPage();
    await screen.findByText("No tariffs yet.");
    await userEvent.type(screen.getByPlaceholderText("Service availability (cents/day)"), "95");
    fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-04-01" } });
    fireEvent.change(screen.getByLabelText("End date (optional)"), { target: { value: "2026-06-30" } });
    await userEvent.click(screen.getByText("Add tariff"));

    const body = bodyOf("/api/v1/sites/7/tariffs", "POST");
    expect(body.effective_from).toBe("2026-03-31T13:00:00.000Z");
    expect(body.effective_to).toBe(JUN_30_END);
  });

  it("suggests the day after the latest ended tariff as the start date", async () => {
    mockApi({ "/api/v1/sites/7/tariffs": [tariff(1, "2026-01-01T00:00:00Z", JUN_30_END)] });
    renderPage();
    await screen.findByText(/until/);

    expect(screen.getByLabelText("Start date")).toHaveValue("2026-07-01");
  });

  it("shows an ended tariff's last day, not the instant after it", async () => {
    mockApi({ "/api/v1/sites/7/tariffs": [tariff(1, "2026-01-01T00:00:00Z", JUN_30_END)] });
    renderPage();

    expect(await screen.findByText(new RegExp(`until ${new Date("2026-06-30T00:00:00").toLocaleDateString()}`))).toBeInTheDocument();
  });

  it("ends a tariff on the chosen last day", async () => {
    mockApi({ "/api/v1/sites/7/tariffs": [tariff(1, "2026-01-01T00:00:00Z", null)] });
    renderPage();
    await userEvent.click(await screen.findByText("End this tariff"));
    fireEvent.change(screen.getByLabelText("End date"), { target: { value: "2026-06-30" } });
    await userEvent.click(screen.getByText("Confirm end"));

    expect(bodyOf("/api/v1/sites/7/tariffs/1", "PATCH")).toEqual({ effective_to: JUN_30_END });
  });

  it("adds a billing cycle starting on the chosen date", async () => {
    mockApi({});
    renderPage();
    await screen.findByText("No billing cycles yet.");
    await userEvent.type(screen.getByPlaceholderText("Cycle length (months)"), "3");
    await userEvent.type(screen.getByPlaceholderText("Anchor day (1-31)"), "1");
    fireEvent.change(screen.getByLabelText("Cycle start date"), { target: { value: "2026-07-01" } });
    await userEvent.click(screen.getByText("Add billing cycle"));

    expect(bodyOf("/api/v1/sites/7/billing-cycle", "POST").effective_from).toBe(JUN_30_END);
  });

  it("ends a billing cycle on the chosen last day", async () => {
    const cycle = { id: 1, site_id: 7, effective_from: "2026-01-01T00:00:00Z", effective_to: null, cycle_months: 3, anchor_day: 1 };
    mockApi({ "/api/v1/sites/7/billing-cycle": [cycle] });
    renderPage();
    await userEvent.click(await screen.findByText("End this cycle"));
    fireEvent.change(screen.getByLabelText("End date"), { target: { value: "2026-06-30" } });
    await userEvent.click(screen.getByText("Confirm end"));

    expect(bodyOf("/api/v1/sites/7/billing-cycle/1", "PATCH")).toEqual({ effective_to: JUN_30_END });
  });
});
