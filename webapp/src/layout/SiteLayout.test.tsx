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

  it("shows only Dashboard and Reporting in the nav", () => {
    renderLayout();
    const nav = screen.getByRole("navigation");
    expect(Array.from(nav.querySelectorAll("a")).map((a) => a.textContent)).toEqual(["Dashboard", "Reporting"]);
  });

  it("navigates back to the dashboard from a sibling page", async () => {
    renderLayout("/sites/7/reporting");
    expect(screen.getByText("Reporting content")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "Dashboard" }));
    expect(await screen.findByText("Dashboard content")).toBeInTheDocument();
  });

  it("lists every site plus an option to add a new one in the switcher", async () => {
    renderLayout();
    const select = await screen.findByRole("combobox", { name: "Switch site" });
    await screen.findByText("Home");
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Home", "Cabin", "Add new site"]);
    expect(select).toHaveValue("7");
  });

  it("omits disabled sites from the switcher", async () => {
    vi.mocked(apiFetch).mockResolvedValue([
      { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true },
      { id: 9, owner_user_id: 1, name: "Cabin", timezone: "Australia/Hobart", enabled: false },
    ]);
    renderLayout();
    await screen.findByText("Home");
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Home", "Add new site"]);
  });

  it("switches to another site when it's picked from the dropdown", async () => {
    renderLayout();
    await screen.findByText("Home");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Switch site" }), "Cabin");
    expect(await screen.findByText("Dashboard content")).toBeInTheDocument();
  });

  it("navigates to the add-site page when 'Add new site' is picked", async () => {
    renderLayout();
    await screen.findByText("Home");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Switch site" }), "Add new site");
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
});
