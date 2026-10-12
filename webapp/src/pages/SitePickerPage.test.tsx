import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { SitePickerPage } from "./SitePickerPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn(), ApiError: actual.ApiError };
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sites"]}>
        <Routes>
          <Route path="/sites" element={<SitePickerPage />} />
          <Route path="/sites/:siteId" element={<div>Dashboard for site</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SitePickerPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows an onboarding form when the user has no sites", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]);
    renderPage();
    expect(await screen.findByText("Create your first site")).toBeInTheDocument();
  });

  it("creates a site from the onboarding form", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ id: 1, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney" });
    renderPage();
    await screen.findByText("Create your first site");
    await userEvent.type(screen.getByPlaceholderText("Site name"), "Home");
    await userEvent.type(screen.getByPlaceholderText("Timezone (e.g. Australia/Sydney)"), "Australia/Sydney");
    await userEvent.click(screen.getByText("Create site"));
    expect(await screen.findByText("Dashboard for site")).toBeInTheDocument();
  });

  it("shows an error on failed site creation and does not navigate", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockRejectedValueOnce(new ApiError(400, "invalid_site_name", "Site name is invalid", null));
    renderPage();
    await screen.findByText("Create your first site");
    await userEvent.type(screen.getByPlaceholderText("Site name"), "BadSite");
    await userEvent.type(screen.getByPlaceholderText("Timezone (e.g. Australia/Sydney)"), "Australia/Sydney");
    await userEvent.click(screen.getByText("Create site"));
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid");
    expect(screen.queryByText("Dashboard for site")).not.toBeInTheDocument();
  });

  it("redirects straight through when the user has exactly one site", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney" },
    ]);
    renderPage();
    expect(await screen.findByText("Dashboard for site")).toBeInTheDocument();
  });

  it("shows a switcher when the user has multiple sites", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      { id: 1, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney" },
      { id: 2, owner_user_id: 1, name: "Cabin", timezone: "Australia/Hobart" },
    ]);
    renderPage();
    expect(await screen.findByText("Home")).toBeInTheDocument();
    expect(screen.getByText("Cabin")).toBeInTheDocument();
  });
});
