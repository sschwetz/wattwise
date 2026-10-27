import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { AddSitePage } from "./AddSitePage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sites/new"]}>
        <Routes>
          <Route path="/sites/new" element={<AddSitePage />} />
          <Route path="/sites" element={<div>Site picker</div>} />
          <Route path="/sites/:siteId" element={<div>Dashboard for site</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AddSitePage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows a form to add another site", () => {
    renderPage();
    expect(screen.getByText("Add a new site")).toBeInTheDocument();
  });

  it("links back to the site picker", () => {
    renderPage();
    expect(screen.getByRole("link", { name: "Back" })).toHaveAttribute("href", "/sites");
  });

  it("creates the site and navigates to its dashboard", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ id: 9, owner_user_id: 1, name: "Cabin", timezone: "Australia/Hobart", enabled: true });
    renderPage();
    await userEvent.type(screen.getByPlaceholderText("Site name"), "Cabin");
    await userEvent.type(screen.getByPlaceholderText("Timezone (e.g. Australia/Sydney)"), "Australia/Hobart");
    await userEvent.click(screen.getByText("Create site"));
    expect(await screen.findByText("Dashboard for site")).toBeInTheDocument();
  });
});
