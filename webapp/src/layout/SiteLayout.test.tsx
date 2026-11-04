import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { SiteLayout } from "./SiteLayout";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderLayout(initialPath = "/sites/7") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/sites/new" element={<p>Add site page</p>} />
          <Route path="/sites/:siteId" element={<SiteLayout />}>
            <Route index element={<p>Dashboard content</p>} />
            <Route path="reporting" element={<p>Reporting content</p>} />
            <Route path="help/:slug?" element={<p>Help content</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SiteLayout", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockResolvedValue([
      { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true },
      { id: 9, owner_user_id: 1, name: "Cabin", timezone: "Australia/Hobart", enabled: true },
    ]);
  });

  it("renders the matched child route inside its chrome", () => {
    renderLayout();
    expect(screen.getByText("Dashboard content")).toBeInTheDocument();
  });

  it("links to the dashboard and reporting for the current site id", () => {
    renderLayout();
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/sites/7");
    expect(screen.getByRole("link", { name: "Reporting" })).toHaveAttribute("href", "/sites/7/reporting");
  });

  it("shows only Dashboard and Reporting in the side rail", () => {
    renderLayout();
    const nav = screen.getByRole("navigation", { name: "Site" });
    expect(Array.from(nav.querySelectorAll("a")).map((a) => a.textContent)).toEqual(["Dashboard", "Reporting"]);
  });

  it("navigates back to the dashboard from a sibling page", async () => {
    renderLayout("/sites/7/reporting");
    expect(screen.getByText("Reporting content")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "Dashboard" }));
    expect(await screen.findByText("Dashboard content")).toBeInTheDocument();
  });

  async function chipLabels() {
    const nav = await screen.findByRole("navigation", { name: "Sites" });
    return Array.from(nav.querySelectorAll("a")).map((a) => a.textContent);
  }

  it("lists every site plus an Add chip", async () => {
    renderLayout();
    expect(await chipLabels()).toEqual(["Home", "Cabin", "Add"]);
  });

  it("hides disabled sites in the chips until Show disabled is clicked", async () => {
    vi.mocked(apiFetch).mockResolvedValue([
      { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true },
      { id: 9, owner_user_id: 1, name: "Cabin", timezone: "Australia/Hobart", enabled: false },
    ]);
    renderLayout();
    expect(await chipLabels()).toEqual(["Home", "Add"]);
    await userEvent.click(screen.getByRole("button", { name: "Show disabled" }));
    expect(await chipLabels()).toEqual(["Home", "Cabin", "Add"]);
    expect(screen.getByRole("link", { name: "Cabin (disabled)" })).toBeInTheDocument();
  });

  it("shows no toggle when every site is enabled", async () => {
    renderLayout();
    await chipLabels();
    expect(screen.queryByRole("button", { name: /disabled/i })).not.toBeInTheDocument();
  });

  it("switches to another site when its chip is clicked", async () => {
    renderLayout();
    await userEvent.click(await screen.findByRole("link", { name: "Cabin" }));
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/sites/9");
  });

  it("keeps the reporting page when switching site from it", async () => {
    renderLayout("/sites/7/reporting");
    expect(await screen.findByRole("link", { name: "Cabin" })).toHaveAttribute("href", "/sites/9/reporting");
  });

  it("navigates to the add-site page from the Add chip", async () => {
    renderLayout();
    await userEvent.click(await screen.findByRole("link", { name: "Add" }));
    expect(await screen.findByText("Add site page")).toBeInTheDocument();
  });

  it("keeps the sidebar on a help route and highlights none of its links", () => {
    renderLayout("/sites/7/help/07-billing");
    expect(screen.getByText("Help content")).toBeInTheDocument();
    for (const name of ["Dashboard", "Reporting"]) {
      expect(screen.getByRole("link", { name })).not.toHaveAttribute("aria-current");
    }
  });

  it("fills its parent's height instead of forcing its own full viewport height", () => {
    const { container } = renderLayout();
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("flex-1");
    expect(root.className).not.toContain("min-h-screen");
  });

  it("shows a disabled banner linking to the General settings tab", async () => {
    vi.mocked(apiFetch).mockResolvedValue([
      { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: false },
    ]);
    renderLayout();
    expect(await screen.findByText(/this site is disabled/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /settings/i })).toHaveAttribute("href", "/settings/sites/7/general");
  });

  it("shows no banner for an enabled site", async () => {
    renderLayout();
    // The site names only render once the sites response has been processed.
    expect(await screen.findByRole("link", { name: "Home" })).toBeInTheDocument();
    expect(screen.queryByText(/disabled/i)).not.toBeInTheDocument();
  });
});
