import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
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

function pricePaths(channel: "buy" | "sell"): URLSearchParams[] {
  return vi
    .mocked(apiFetch)
    .mock.calls.map(([path]) => path as string)
    .filter((path) => path.includes("/prices"))
    .map((path) => new URLSearchParams(path.split("?")[1]))
    .filter((params) => params.get("channel") === channel);
}

describe("ReportingPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockResolvedValue([]);
  });

  it("shows the pricing chart with an import/export actual/predicted legend", async () => {
    renderPage();
    expect(await screen.findByText("Today's Pricing")).toBeInTheDocument();
    for (const label of ["Import actual", "Import predicted", "Export actual", "Export predicted"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it.each(["buy", "sell"] as const)(
    "requests today's %s price spanning the full calendar day, including the future",
    async (channel) => {
      renderPage();
      await waitFor(() => expect(pricePaths(channel).length).toBeGreaterThan(0));

      const now = Date.now();
      for (const params of pricePaths(channel)) {
        const start = Date.parse(params.get("start")!);
        const end = Date.parse(params.get("end")!);
        expect(end - start).toBe(24 * 60 * 60_000);
        expect(end).toBeGreaterThan(now);
      }
    },
  );
});
