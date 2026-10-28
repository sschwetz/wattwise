import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
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
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/settings/sites/:siteId" element={<SettingsPage />} />
          <Route path="/settings/sites/:siteId/:tab" element={<SettingsPage />} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return queryClient;
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

  it("keeps the tabs and typed changes when a background refetch fails", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path === "/api/v1/users/me") return { id: 1, email: "a@b.c" } as never;
      return [site(7, "Cabin")] as never;
    });
    const queryClient = renderAt("/settings/sites/7/general");
    const name = await screen.findByLabelText("Name");
    await waitFor(() => expect(name).toBeEnabled());
    await userEvent.clear(name);
    await userEvent.type(name, "Cabin two");

    vi.mocked(apiFetch).mockRejectedValue(new Error("offline"));
    await act(() => queryClient.refetchQueries({ queryKey: ["sites"] }));

    expect(await screen.findByText("Could not refresh your sites.")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Site settings" })).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Cabin two");
    expect(screen.queryByRole("link", { name: /your sites/i })).not.toBeInTheDocument();

    vi.mocked(apiFetch).mockResolvedValue([site(7, "Cabin")]);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByText("Could not refresh your sites.")).not.toBeInTheDocument());
    expect(screen.getByLabelText("Name")).toHaveValue("Cabin two");
  });

  it("sends a site URL with no tab to its General tab", async () => {
    renderAt("/settings/sites/9");
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/settings/sites/9/general");
  });

  it("gives the selected disabled chip the selected style as well as the disabled one", async () => {
    renderAt("/settings/sites/4/general");
    const chip = await screen.findByRole("link", { name: "Home (disabled)" });
    expect(chip).toHaveAttribute("aria-current", "page");
    expect(chip).toHaveClass("border-solar", "border-dashed");
    expect(screen.getByRole("link", { name: "Cabin" })).not.toHaveClass("border-solar");
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
      expect(screen.getAllByRole("link", { name: "Back" })).toHaveLength(1);
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    }

    function mockAs(meId: number) {
      vi.mocked(apiFetch).mockImplementation(async (path: string) => {
        if (path === "/api/v1/users/me") return { id: meId, email: "me@example.com" } as never;
        if (path.endsWith("/api/v1/sites")) return [site(3, "Farm")] as never;
        return (path.includes("/sources") ? [SOURCE] : []) as never;
      });
    }

    function writeControls(): HTMLElement[] {
      return Array.from(screen.getByTestId("settings-tab").querySelectorAll<HTMLElement>("button, input, select, textarea"));
    }

    it.each(["collectors", "tariffs", "billing"])("makes the %s tab read-only for a viewer", async (tab) => {
      mockAs(2);
      renderAt(`/settings/sites/3/${tab}`);
      expect(await screen.findByText("Only the owner can change this site's settings.")).toBeInTheDocument();
      await waitFor(() => expect(writeControls().length).toBeGreaterThan(0));
      for (const control of writeControls()) expect(control).toBeDisabled();
    });

    it.each(["collectors", "tariffs", "billing"])("leaves the %s tab editable for the owner", async (tab) => {
      mockAs(1);
      renderAt(`/settings/sites/3/${tab}`);
      await waitFor(() => expect(writeControls().some((control) => !control.matches(":disabled"))).toBe(true));
      expect(screen.queryByText("Only the owner can change this site's settings.")).not.toBeInTheDocument();
    });

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
    expect(await screen.findByRole("heading", { name: "Invitations" })).toBeInTheDocument();
  });
});
