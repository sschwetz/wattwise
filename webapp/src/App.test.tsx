import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "./api/client";
import { App } from "./App";
import { AuthProvider } from "./auth/AuthContext";

vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

// Matches every endpoint with an empty/absent result except
// /demand-window/active, whose real 404-for-"none active" contract a bare
// [] would misrepresent as an active window (truthy), wrongly appending
// " - DEMAND WINDOW ACTIVE" to the dashboard heading these tests assert on.
function resolveEmpty(path: string): Promise<unknown> {
  return Promise.resolve(path.includes("/demand-window/active") ? null : []);
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("App routing", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    localStorage.clear();
  });

  it("redirects an unauthenticated visit to / over to /login", async () => {
    renderAt("/");
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });

  it("renders the login page at /login", async () => {
    renderAt("/login");
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });

  it("renders the register page at /register", async () => {
    renderAt("/register");
    expect(await screen.findByText("Create an account")).toBeInTheDocument();
  });

  it("sends an authenticated visit to / through the site picker", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch).mockResolvedValue([]);
    renderAt("/");
    expect(await screen.findByText("Create your first site")).toBeInTheDocument();
  });

  it("renders the dashboard inside the site layout at /sites/:siteId", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch).mockImplementation(resolveEmpty);
    renderAt("/sites/7");
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Reporting" })).toHaveAttribute("href", "/sites/7/reporting");
    expect(screen.queryByRole("link", { name: "Sources" })).not.toBeInTheDocument();
  });

  it("redirects /sites/:siteId/sources to the Collectors settings tab", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch).mockResolvedValue([
      { id: 5, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true },
    ]);
    renderAt("/sites/5/sources");
    expect(await screen.findByText("Tab: collectors")).toBeInTheDocument();
  });

  it("redirects /sites/:siteId/billing to the Billing settings tab", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch).mockResolvedValue([
      { id: 5, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true },
    ]);
    renderAt("/sites/5/billing");
    expect(await screen.findByText("Tab: billing")).toBeInTheDocument();
  });

  it("navigates to the add-site page at /sites/new", async () => {
    localStorage.setItem("access_token", "a-token");
    renderAt("/sites/new");
    expect(await screen.findByText("Add a new site")).toBeInTheDocument();
  });

  it("renders the account page at /account", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch).mockResolvedValueOnce({ id: 1, email: "me@example.com", has_avatar: false, has_amber_api_key: false });
    renderAt("/account");
    expect(await screen.findByRole("heading", { name: "Account" })).toBeInTheDocument();
  });

  it("lets a logged-in user reach Account from the header dropdown", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
      .mockImplementation(resolveEmpty);
    renderAt("/sites/7");
    await screen.findByRole("heading", { name: "Dashboard" });
    await userEvent.click(await screen.findByRole("button", { name: "me@example.com" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Account settings" }));
    expect(await screen.findByRole("heading", { name: "Account" })).toBeInTheDocument();
  });

  it("lets a logged-in user reach Site settings from the header dropdown", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch).mockImplementation((path: string) =>
      path === "/api/v1/users/me"
        ? Promise.resolve({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
        : path === "/api/v1/sites"
          ? Promise.resolve([{ id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true }])
          : resolveEmpty(path),
    );
    renderAt("/sites/7");
    await screen.findByRole("heading", { name: "Dashboard" });
    await userEvent.click(await screen.findByRole("button", { name: "me@example.com" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Site settings" }));
    expect(await screen.findByRole("heading", { name: "Site settings" })).toBeInTheDocument();
  });

  it("opens Help from the header menu inside the site layout, without a reload", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch).mockImplementation((path: string) =>
      path === "/api/v1/users/me"
        ? Promise.resolve({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
        : resolveEmpty(path),
    );
    renderAt("/sites/7");
    await screen.findByRole("heading", { name: "Dashboard" });
    await userEvent.click(await screen.findByRole("button", { name: "me@example.com" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Help" }));
    expect(await screen.findByRole("heading", { level: 1, name: "WattWise User Guide" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Reporting" })).toHaveAttribute("href", "/sites/7/reporting");
    expect(screen.getByRole("button", { name: "me@example.com" })).toBeInTheDocument();
  });

  it("shows the help guide with the header only at /help", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch).mockResolvedValue([]);
    renderAt("/help");
    expect(await screen.findByRole("heading", { level: 1, name: "WattWise User Guide" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Reporting" })).not.toBeInTheDocument();
  });
});
