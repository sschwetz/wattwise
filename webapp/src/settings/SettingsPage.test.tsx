import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { SettingsPage } from "./SettingsPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function LocationProbe() {
  return <p data-testid="path">{useLocation().pathname}</p>;
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/settings/sites/:siteId/:tab" element={<SettingsPage />} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const SOURCE = { id: 1, site_id: 3, kind: "energy", provider: "envoy", name: null, enabled: true, config: {} };

const site = (id: number, name: string, enabled = true) => ({
  id,
  owner_user_id: 1,
  name,
  timezone: "Australia/Sydney",
  enabled,
});

describe("SettingsPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockResolvedValue([site(4, "Home", false), site(7, "Cabin"), site(9, "Shed")]);
  });

  it("redirects /settings to the General tab of the first enabled site", async () => {
    renderAt("/settings");
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/settings/sites/7/general");
  });

  it("falls back to the first site when every site is disabled", async () => {
    vi.mocked(apiFetch).mockResolvedValue([site(4, "Home", false), site(7, "Cabin", false)]);
    renderAt("/settings");
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/settings/sites/4/general");
  });

  it("links to the add-site page when the user has no sites", async () => {
    vi.mocked(apiFetch).mockResolvedValue([]);
    renderAt("/settings");
    expect(await screen.findByRole("link", { name: /add a site/i })).toHaveAttribute("href", "/sites/new");
    expect(screen.queryByRole("link", { name: "View invitations" })).not.toBeInTheDocument();
  });

  it("points to invitations when there are no sites but invites are pending", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) =>
      Promise.resolve(path.includes("/invites/pending") ? [{ id: 1, site_id: 3, site_name: "Farm", inviter_email: "a@b.c", created_at: "" }] : []),
    );
    renderAt("/settings");
    expect(await screen.findByRole("link", { name: "View invitations" })).toHaveAttribute("href", "/sites");
    expect(screen.getByRole("link", { name: /add a site/i })).toHaveAttribute("href", "/sites/new");
  });

  it("shows an error with Retry and a link to /sites when the sites fail to load", async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("boom"));
    renderAt("/settings");
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /your sites/i })).toHaveAttribute("href", "/sites");
    vi.mocked(apiFetch).mockResolvedValue([site(7, "Cabin")]);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
  });

  it("redirects an unknown site id to the default site, keeping a valid tab", async () => {
    renderAt("/settings/sites/99/billing");
    expect(await screen.findByRole("heading", { name: "Billing cycle" })).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/settings/sites/7/billing");
  });

  it("redirects a non-numeric site id to the default site", async () => {
    renderAt("/settings/sites/abc/general");
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/settings/sites/7/general");
  });

  it("falls back to General when both site and tab are unknown", async () => {
    renderAt("/settings/sites/99/nope");
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/settings/sites/7/general");
  });

  it("redirects an unknown tab to General for that site", async () => {
    renderAt("/settings/sites/9/nope");
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/settings/sites/9/general");
  });

  describe("collectors, tariffs and billing tabs", () => {
    beforeEach(() => {
      vi.mocked(apiFetch).mockImplementation((path: string) =>
        Promise.resolve(path.endsWith("/api/v1/sites") ? [site(3, "Farm")] : path.includes("/sources") ? [SOURCE] : []),
      );
    });

    function expectNoPageChrome() {
      expect(screen.queryByRole("link", { name: "Back" })).not.toBeInTheDocument();
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    }

    it("renders the sources list for the site on the collectors tab", async () => {
      renderAt("/settings/sites/3/collectors");
      expect(await within(await screen.findByRole("list")).findByText("envoy")).toBeInTheDocument();
      expect(apiFetch).toHaveBeenCalledWith(expect.stringContaining("/sites/3/sources"));
      expectNoPageChrome();
    });

    it("shows the tariff section and not the billing cycle on the tariffs tab", async () => {
      renderAt("/settings/sites/3/tariffs");
      expect(await screen.findByRole("heading", { name: /tariff/i })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Billing cycle" })).not.toBeInTheDocument();
      expectNoPageChrome();
    });

    it("shows billing cycle and demand charges and no tariff form on the billing tab", async () => {
      renderAt("/settings/sites/3/billing");
      expect(await screen.findByRole("heading", { name: "Billing cycle" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Demand charges" })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: /tariff/i })).not.toBeInTheDocument();
      expectNoPageChrome();
    });
  });

  it("marks the active tab and the selected chip with aria-current", async () => {
    renderAt("/settings/sites/9/tariffs");
    expect(await screen.findByRole("link", { name: "Tariffs" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "General" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Shed" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Cabin" })).not.toHaveAttribute("aria-current");
  });

  it("shows a chip per site including disabled ones, and an Add chip last", async () => {
    renderAt("/settings/sites/7/general");
    const chips = await screen.findByRole("navigation", { name: "Sites" });
    const links = Array.from(chips.querySelectorAll("a"));
    expect(links.map((a) => a.getAttribute("aria-label") ?? a.textContent)).toEqual([
      "Home (disabled)",
      "Cabin",
      "Shed",
      "Add",
    ]);
    expect(links[3]).toHaveAttribute("href", "/sites/new");
  });

  it("changes the siteId in the URL when a chip is clicked, keeping the tab", async () => {
    renderAt("/settings/sites/7/billing");
    await userEvent.click(await screen.findByRole("link", { name: "Shed" }));
    expect(screen.getByTestId("path")).toHaveTextContent("/settings/sites/9/billing");
  });

  it("lists the five tabs in order", async () => {
    renderAt("/settings/sites/7/general");
    const rail = await screen.findByRole("navigation", { name: "Site settings" });
    expect(Array.from(rail.querySelectorAll("a")).map((a) => a.textContent)).toEqual([
      "General",
      "Collectors",
      "Tariffs",
      "Billing",
      "Sharing",
    ]);
  });

  it("renders the content for the tab in the URL", async () => {
    renderAt("/settings/sites/7/sharing");
    expect(await screen.findByText("Tab: sharing")).toBeInTheDocument();
  });
});
