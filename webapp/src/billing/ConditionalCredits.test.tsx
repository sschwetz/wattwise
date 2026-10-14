import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { ConditionalCreditForm, ConditionalCreditList } from "./ConditionalCredits";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const PATH = "/api/v1/sites/7/tariffs/3/conditional-credits";

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("ConditionalCredits", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("lists a credit with its threshold, amount and pattern", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      {
        id: 1,
        site_tariff_id: 3,
        label: "Low evening import",
        months: [1, 2, 3],
        days_of_week: [0, 1, 2, 3, 4],
        start_time: "17:00:00",
        end_time: "21:00:00",
        max_avg_import_w: "300.0000",
        credit_c: "50.0000",
      },
    ]);
    renderWithClient(<ConditionalCreditList siteId={7} tariffId={3} />);

    expect(await screen.findByText("Low evening import")).toBeInTheDocument();
    expect(screen.getByText(/50\.00c credit/)).toBeInTheDocument();
    expect(screen.getByText(/17:00–21:00/)).toBeInTheDocument();
    expect(screen.getByText(/average import below 300 W/i)).toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledWith(PATH);
  });

  it("posts the parsed form values", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({});
    renderWithClient(<ConditionalCreditForm siteId={7} tariffId={3} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Credit label"), "Low evening import");
    await user.type(screen.getByLabelText("Credit months"), "1, 2,3");
    await user.type(screen.getByLabelText("Credit days of week"), "0,1,2,3,4");
    await user.type(screen.getByLabelText("Credit start time"), "17:00");
    await user.type(screen.getByLabelText("Credit end time"), "21:00");
    await user.type(screen.getByLabelText("Average import below (W)"), "300");
    await user.type(screen.getByLabelText("Credit (cents)"), "50");
    await user.click(screen.getByRole("button", { name: "Add conditional credit" }));

    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path, init] = vi.mocked(apiFetch).mock.calls[0];
    expect(path).toBe(PATH);
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      label: "Low evening import",
      months: [1, 2, 3],
      days_of_week: [0, 1, 2, 3, 4],
      start_time: "17:00",
      end_time: "21:00",
      max_avg_import_w: 300,
      credit_c: 50,
    });
  });

  it("shows the API error message when the create fails", async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("start_time must be before end_time"));
    renderWithClient(<ConditionalCreditForm siteId={7} tariffId={3} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Credit label"), "x");
    await user.type(screen.getByLabelText("Credit months"), "1");
    await user.type(screen.getByLabelText("Credit days of week"), "0");
    await user.type(screen.getByLabelText("Credit start time"), "21:00");
    await user.type(screen.getByLabelText("Credit end time"), "17:00");
    await user.type(screen.getByLabelText("Average import below (W)"), "300");
    await user.type(screen.getByLabelText("Credit (cents)"), "50");
    await user.click(screen.getByRole("button", { name: "Add conditional credit" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("start_time must be before end_time");
  });
});
