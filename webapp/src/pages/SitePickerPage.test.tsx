import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { SitePickerPage } from "./SitePickerPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn(), ApiError: actual.ApiError };
});

interface Routes_ {
  sites?: unknown[];
  pending?: unknown[];
}

function mockApi({ sites = [], pending = [] }: Routes_) {
  vi.mocked(apiFetch).mockImplementation(async (path: string) => {
    if (path === "/api/v1/sites") return sites;
    if (path === "/api/v1/invites/pending") return pending;
    return {};
  });
}

const home = { id: 1, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true };
const cabin = { id: 2, owner_user_id: 1, name: "Cabin", timezone: "Australia/Hobart", enabled: false };
const pendingInvite = {
  id: 5,
  site_id: 9,
  site_name: "Beach House",
  inviter_email: "owner@example.com",
  created_at: "2026-10-01T00:00:00Z",
};

function renderPage() {
  return renderRouted("/sites");
}

function renderRouted(entry: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/sites" element={<SitePickerPage />} />
          <Route path="/settings" element={<div>Settings page</div>} />
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
    mockApi({});
    const created = { id: 1, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true };
    const base = vi.mocked(apiFetch).getMockImplementation()!;
    vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) =>
      init?.method === "POST" ? created : base(path, init),
    );
    renderPage();
    await screen.findByText("Create your first site");
    await userEvent.type(screen.getByPlaceholderText("Site name"), "Home");
    await userEvent.type(screen.getByPlaceholderText("Timezone (e.g. Australia/Sydney)"), "Australia/Sydney");
    await userEvent.click(screen.getByText("Create site"));
    expect(await screen.findByText("Dashboard for site")).toBeInTheDocument();
  });

  it("shows an error on failed site creation and does not navigate", async () => {
    const { ApiError } = await import("../api/client");
    mockApi({});
    const base = vi.mocked(apiFetch).getMockImplementation()!;
    vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") throw new ApiError(400, "invalid_site_name", "Site name is invalid", null);
      return base(path, init);
    });
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
      { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true },
    ]);
    renderPage();
    expect(await screen.findByText("Dashboard for site")).toBeInTheDocument();
  });

  it("shows a switcher when the user has multiple sites", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      { id: 1, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true },
      { id: 2, owner_user_id: 1, name: "Cabin", timezone: "Australia/Hobart", enabled: true },
    ]);
    renderPage();
    expect(await screen.findByText("Home")).toBeInTheDocument();
    expect(screen.getByText("Cabin")).toBeInTheDocument();
  });

  it("lists only enabled sites and redirects when one is enabled", async () => {
    mockApi({ sites: [home, cabin] });
    renderPage();
    expect(await screen.findByText("Dashboard for site")).toBeInTheDocument();
  });

  it("redirects to settings when every site is disabled", async () => {
    mockApi({ sites: [{ ...home, enabled: false }, cabin] });
    renderRouted("/sites");
    expect(await screen.findByText("Settings page")).toBeInTheDocument();
  });

  it("shows the create form when there are no sites and no pending invites", async () => {
    mockApi({});
    renderPage();
    expect(await screen.findByText("Create your first site")).toBeInTheDocument();
    expect(screen.queryByText("Pending invitations")).not.toBeInTheDocument();
  });

  it("shows pending invitations when the user has no sites", async () => {
    mockApi({ pending: [pendingInvite] });
    renderPage();
    expect(await screen.findByText("Pending invitations")).toBeInTheDocument();
    expect(screen.getByText("Beach House")).toBeInTheDocument();
    expect(screen.getByText(/owner@example.com/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Decline" })).toBeInTheDocument();
  });

  it("shows pending invitations beside several enabled sites", async () => {
    mockApi({ sites: [home, { ...cabin, enabled: true }], pending: [pendingInvite] });
    renderPage();
    expect(await screen.findByText("Pending invitations")).toBeInTheDocument();
    expect(screen.getByText("Cabin")).toBeInTheDocument();
  });

  it("accepts an invitation and refetches sites", async () => {
    mockApi({ pending: [pendingInvite] });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Accept" }));
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/invites/5/accept", { method: "POST" });
    await waitFor(() =>
      expect(vi.mocked(apiFetch).mock.calls.filter(([p]) => p === "/api/v1/sites").length).toBeGreaterThan(1),
    );
  });

  it("declines an invitation", async () => {
    mockApi({ pending: [pendingInvite] });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Decline" }));
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/invites/5/decline", { method: "POST" });
  });

  it("shows an inline error when accepting fails", async () => {
    const { ApiError } = await import("../api/client");
    mockApi({ pending: [pendingInvite] });
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path === "/api/v1/invites/pending") return [pendingInvite];
      if (path === "/api/v1/sites") return [];
      throw new ApiError(409, "invite_not_pending", "Invite is no longer pending", null);
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Accept" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("fills its parent's height instead of forcing its own full viewport height while loading", () => {
    vi.mocked(apiFetch).mockImplementationOnce(() => new Promise(() => {}));
    const { container } = renderPage();
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("flex-1");
    expect(root.className).not.toContain("min-h-screen");
  });

  it("fills its parent's height instead of forcing its own full viewport height during onboarding", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]);
    const { container } = renderPage();
    await screen.findByText("Create your first site");
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("flex-1");
    expect(root.className).not.toContain("min-h-screen");
  });

  it("fills its parent's height instead of forcing its own full viewport height in the switcher", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      { id: 1, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true },
      { id: 2, owner_user_id: 1, name: "Cabin", timezone: "Australia/Hobart", enabled: true },
    ]);
    const { container } = renderPage();
    await screen.findByText("Home");
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("flex-1");
    expect(root.className).not.toContain("min-h-screen");
  });
});
