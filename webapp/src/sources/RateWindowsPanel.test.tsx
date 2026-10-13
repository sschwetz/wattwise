import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { RateWindowsPanel } from "./RateWindowsPanel";
import type { RateWindow } from "./useRateWindows";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RateWindowsPanel siteId={7} sourceId={3} />
    </QueryClientProvider>,
  );
}

function win(overrides: Partial<RateWindow>): RateWindow {
  return {
    id: 1,
    source_id: 3,
    channel: "buy",
    label: "Peak",
    effective_from: "2026-01-01T00:00:00Z",
    effective_to: null,
    months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    days_of_week: [0, 1, 2, 3, 4, 5, 6],
    start_time: "00:00:00",
    end_time: "00:00:00",
    rate_c_per_kwh: "32.5000",
    ...overrides,
  };
}

describe("RateWindowsPanel", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows empty Import, Export and Controlled load tables and no warning for a new source", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]);
    renderPanel();

    expect(await screen.findByRole("heading", { name: "Import" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Export (feed-in)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Controlled load" })).toBeInTheDocument();
    expect(screen.queryByText(/not covered/i)).not.toBeInTheDocument();
  });

  it("lists a window under its channel with the rate", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      win({ channel: "sell", label: "Feed-in", rate_c_per_kwh: "5.0000" }),
    ]);
    renderPanel();

    const exportSection = (await screen.findByRole("heading", { name: "Export (feed-in)" })).closest("section")!;
    expect(within(exportSection).getByText("Feed-in")).toBeInTheDocument();
    expect(within(exportSection).getByText(/5c\/kWh/)).toBeInTheDocument();
  });

  it("warns about uncovered time", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([win({ start_time: "07:00:00", end_time: "22:00:00" })]);
    renderPanel();

    expect(await screen.findByText(/not covered/i)).toBeInTheDocument();
  });

  it("adds a flat import window with sensible defaults", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]).mockResolvedValueOnce(win({})).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("heading", { name: "Import" });
    await user.type(screen.getByLabelText("Label"), "Flat");
    await user.type(screen.getByLabelText("Rate (c/kWh)"), "32.5");
    await user.click(screen.getByRole("button", { name: "Add window" }));

    const post = vi.mocked(apiFetch).mock.calls[1];
    expect(post[0]).toBe("/api/v1/sites/7/sources/3/rate-windows");
    const body = JSON.parse((post[1] as RequestInit).body as string);
    expect(body).toMatchObject({
      channel: "buy",
      label: "Flat",
      months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
      days_of_week: [0, 1, 2, 3, 4, 5, 6],
      start_time: "00:00:00",
      end_time: "00:00:00",
      rate_c_per_kwh: 32.5,
      effective_to: null,
    });
  });

  it("shows the server's message when a window overlaps", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockRejectedValueOnce(new Error('This window overlaps "Flat" (id 1) on the same channel'));
    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("heading", { name: "Import" });
    await user.type(screen.getByLabelText("Label"), "Peak");
    await user.type(screen.getByLabelText("Rate (c/kWh)"), "40");
    await user.click(screen.getByRole("button", { name: "Add window" }));

    expect(await screen.findByRole("alert")).toHaveTextContent('overlaps "Flat"');
  });

  it("ends a window by sending it back with an end date", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([win({})]).mockResolvedValueOnce(win({})).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("button", { name: "End Peak" }));

    const patch = vi.mocked(apiFetch).mock.calls[1];
    expect(patch[0]).toBe("/api/v1/sites/7/sources/3/rate-windows/1");
    const body = JSON.parse((patch[1] as RequestInit).body as string);
    expect(body.effective_to).not.toBeNull();
    expect(body.rate_c_per_kwh).toBe(32.5);
  });

  it("says so when the windows fail to load", async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("boom"));
    renderPanel();

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load rate windows.");
    expect(screen.queryByText("No windows.")).not.toBeInTheDocument();
  });

  it("deletes a window", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([win({})]).mockResolvedValueOnce(undefined).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("button", { name: "Delete Peak" }));

    expect(vi.mocked(apiFetch).mock.calls[1][0]).toBe("/api/v1/sites/7/sources/3/rate-windows/1");
    expect((vi.mocked(apiFetch).mock.calls[1][1] as RequestInit).method).toBe("DELETE");
  });
});
