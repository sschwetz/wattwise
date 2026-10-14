import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

const LIST_PATH = "/api/v1/sites/7/sources/3/rate-windows";

function postBody(): Record<string, unknown> {
  const post = vi.mocked(apiFetch).mock.calls[1];
  return JSON.parse((post[1] as RequestInit).body as string);
}

async function expectRefetch() {
  await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(3));
  expect(vi.mocked(apiFetch).mock.calls[2][0]).toBe(LIST_PATH);
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

    expect(await screen.findByText(/not covered/i)).toHaveTextContent(
      "Not covered: Import (for example Monday 00:00–07:00 in January). Those minutes will have no price.",
    );
  });

  it("names every direction with a gap in the warning", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      win({ id: 1, start_time: "07:00:00", end_time: "22:00:00" }),
      win({ id: 2, channel: "sell", label: "Feed-in", days_of_week: [0, 1, 2, 3, 4, 5] }),
    ]);
    renderPanel();

    expect(await screen.findByText(/not covered/i)).toHaveTextContent(
      "Not covered: Import (for example Monday 00:00–07:00 in January); " +
        "Export (feed-in) (for example Sunday all day in January). Those minutes will have no price.",
    );
  });

  it("still warns when a window that has not started would fill the gap", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      win({ id: 1, start_time: "07:00:00", end_time: "22:00:00" }),
      win({ id: 2, label: "Next year", effective_from: "2099-01-01T00:00:00Z" }),
    ]);
    renderPanel();

    expect(await screen.findByText(/not covered/i)).toHaveTextContent("Import (for example Monday 00:00–07:00 in January)");
  });

  it("disables every window action while a change is in flight", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([win({})]).mockReturnValueOnce(new Promise(() => {}));
    const user = userEvent.setup();
    renderPanel();

    await user.type(await screen.findByLabelText("Window label"), "Flat");
    await user.type(screen.getByLabelText("Rate (c/kWh)"), "30");
    await user.click(screen.getByRole("button", { name: "End Peak" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Add window" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "End Peak" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete Peak" })).toBeDisabled();
  });

  it("adds a flat import window with sensible defaults", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]).mockResolvedValueOnce(win({})).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("heading", { name: "Import" });
    await user.type(screen.getByLabelText("Window label"), "Flat");
    await user.type(screen.getByLabelText("Rate (c/kWh)"), "32.5");
    await user.click(screen.getByRole("button", { name: "Add window" }));

    expect(vi.mocked(apiFetch).mock.calls[1][0]).toBe(LIST_PATH);
    expect(postBody()).toMatchObject({
      channel: "buy",
      label: "Flat",
      months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
      days_of_week: [0, 1, 2, 3, 4, 5, 6],
      start_time: "00:00:00",
      end_time: "00:00:00",
      rate_c_per_kwh: 32.5,
      effective_to: null,
    });
    await expectRefetch();
    expect(screen.getByLabelText("Window label")).toHaveValue("");
    expect(screen.getByLabelText("Rate (c/kWh)")).toHaveValue(null);
  });

  it("starts a window added for today at the current time, not at midnight", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]).mockResolvedValueOnce(win({})).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("heading", { name: "Import" });
    await user.type(screen.getByLabelText("Window label"), "New rate");
    await user.type(screen.getByLabelText("Rate (c/kWh)"), "30");
    const before = Date.now();
    await user.click(screen.getByRole("button", { name: "Add window" }));

    const effectiveFrom = Date.parse(postBody().effective_from as string);
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    expect(effectiveFrom).not.toBe(midnight.getTime());
    expect(effectiveFrom).toBeGreaterThanOrEqual(before);
  });

  it("starts a window added for a future date at local midnight", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]).mockResolvedValueOnce(win({})).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("heading", { name: "Import" });
    await user.type(screen.getByLabelText("Window label"), "Next year");
    await user.type(screen.getByLabelText("Rate (c/kWh)"), "30");
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2099-01-15" } });
    await user.click(screen.getByRole("button", { name: "Add window" }));

    expect(postBody().effective_from).toBe(new Date("2099-01-15T00:00:00").toISOString());
  });

  it("will not add a window whose label is only spaces", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("heading", { name: "Import" });
    await user.type(screen.getByLabelText("Window label"), "   ");
    await user.type(screen.getByLabelText("Rate (c/kWh)"), "30");

    expect(screen.getByRole("button", { name: "Add window" })).toBeDisabled();
  });

  it("shows the server's message when a window overlaps", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockRejectedValueOnce(new Error('This window overlaps "Flat" (id 1) on the same channel'));
    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("heading", { name: "Import" });
    await user.type(screen.getByLabelText("Window label"), "Peak");
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
    await expectRefetch();
  });

  it("offers Delete but not End for a window that has not started", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([win({ effective_from: "2099-01-01T00:00:00Z" })]);
    renderPanel();

    expect(await screen.findByRole("button", { name: "Delete Peak" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "End Peak" })).not.toBeInTheDocument();
  });

  it("shows only the latest failure when End and then Delete fail", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([win({})])
      .mockRejectedValueOnce(new Error("Ending failed"))
      .mockRejectedValueOnce(new Error("Deleting failed"));
    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("button", { name: "End Peak" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Ending failed");

    await user.click(screen.getByRole("button", { name: "Delete Peak" }));
    await screen.findByText("Deleting failed");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent("Deleting failed");
  });

  it("says so when the windows fail to load", async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("boom"));
    renderPanel();

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load rate windows.");
    expect(screen.queryByText("No windows.")).not.toBeInTheDocument();
  });

  describe("Edit rate", () => {
    async function openEditor(window: RateWindow = win({})) {
      const user = userEvent.setup();
      renderPanel();
      await user.click(await screen.findByRole("button", { name: `Edit rate of ${window.label}` }));
      return user;
    }

    it("opens prefilled with the current rate", async () => {
      vi.mocked(apiFetch).mockResolvedValueOnce([win({})]);
      await openEditor();

      expect(screen.getByLabelText("New rate (c/kWh)")).toHaveValue(32.5);
    });

    it("sends the window back unchanged apart from the new rate, then closes", async () => {
      const window = win({ effective_to: "2026-09-01T00:00:00Z", months: [1, 2], days_of_week: [5, 6], start_time: "07:00:00", end_time: "22:00:00" });
      vi.mocked(apiFetch).mockResolvedValueOnce([window]).mockResolvedValueOnce(window).mockResolvedValueOnce([window]);
      const user = await openEditor(window);

      await user.clear(screen.getByLabelText("New rate (c/kWh)"));
      await user.type(screen.getByLabelText("New rate (c/kWh)"), "35");
      await user.click(screen.getByRole("button", { name: "Save" }));

      const patch = vi.mocked(apiFetch).mock.calls[1];
      expect(patch[0]).toBe("/api/v1/sites/7/sources/3/rate-windows/1");
      expect((patch[1] as RequestInit).method).toBe("PATCH");
      expect(JSON.parse((patch[1] as RequestInit).body as string)).toEqual({
        channel: "buy",
        label: "Peak",
        effective_from: "2026-01-01T00:00:00Z",
        effective_to: "2026-09-01T00:00:00Z",
        months: [1, 2],
        days_of_week: [5, 6],
        start_time: "07:00:00",
        end_time: "22:00:00",
        rate_c_per_kwh: 35,
      });
      await expectRefetch();
      await waitFor(() => expect(screen.queryByLabelText("New rate (c/kWh)")).not.toBeInTheDocument());
    });

    it("cannot save an unchanged, negative or empty rate", async () => {
      vi.mocked(apiFetch).mockResolvedValueOnce([win({})]);
      const user = await openEditor();
      const input = screen.getByLabelText("New rate (c/kWh)");
      const save = screen.getByRole("button", { name: "Save" });

      expect(save).toBeDisabled();
      await user.clear(input);
      expect(save).toBeDisabled();
      await user.type(input, "-1");
      expect(save).toBeDisabled();
      await user.clear(input);
      await user.type(input, "40");
      expect(save).toBeEnabled();
    });

    it("closes on Cancel without calling the API", async () => {
      vi.mocked(apiFetch).mockResolvedValueOnce([win({})]);
      const user = await openEditor();

      await user.click(screen.getByRole("button", { name: "Cancel" }));

      expect(screen.queryByLabelText("New rate (c/kWh)")).not.toBeInTheDocument();
      expect(apiFetch).toHaveBeenCalledTimes(1);
    });

    it("shows the server's message in the one alert when saving fails", async () => {
      vi.mocked(apiFetch).mockResolvedValueOnce([win({})]).mockRejectedValueOnce(new Error("Rate rejected"));
      const user = await openEditor();

      await user.clear(screen.getByLabelText("New rate (c/kWh)"));
      await user.type(screen.getByLabelText("New rate (c/kWh)"), "40");
      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("Rate rejected");
      expect(screen.getAllByRole("alert")).toHaveLength(1);
    });

    it("is disabled while another change is in flight", async () => {
      vi.mocked(apiFetch).mockResolvedValueOnce([win({})]).mockReturnValueOnce(new Promise(() => {}));
      const user = userEvent.setup();
      renderPanel();

      await user.click(await screen.findByRole("button", { name: "End Peak" }));

      await waitFor(() => expect(screen.getByRole("button", { name: "Edit rate of Peak" })).toBeDisabled());
    });

    it("is offered on an ended window", async () => {
      vi.mocked(apiFetch).mockResolvedValueOnce([win({ effective_to: "2026-03-01T00:00:00Z" })]);
      renderPanel();

      expect(await screen.findByRole("button", { name: "Edit rate of Peak" })).toHaveTextContent("Edit rate");
      expect(screen.queryByRole("button", { name: "End Peak" })).not.toBeInTheDocument();
    });
  });

  it("deletes a window", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([win({})]).mockResolvedValueOnce(undefined).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("button", { name: "Delete Peak" }));

    expect(vi.mocked(apiFetch).mock.calls[1][0]).toBe("/api/v1/sites/7/sources/3/rate-windows/1");
    expect((vi.mocked(apiFetch).mock.calls[1][1] as RequestInit).method).toBe("DELETE");
    await expectRefetch();
  });
});
