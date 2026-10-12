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
    vi.mocked(apiFetch).mockResolvedValue([]);
    renderAt("/sites/7");
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sources" })).toHaveAttribute("href", "/sites/7/sources");
    expect(screen.getByRole("link", { name: "Billing" })).toHaveAttribute("href", "/sites/7/billing");
  });

  it("renders the sources page inside the site layout at /sites/:siteId/sources", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch).mockResolvedValue([]);
    renderAt("/sites/7/sources");
    expect(await screen.findByRole("heading", { name: "Sources" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/sites/7");
  });

  it("renders the billing page inside the site layout at /sites/:siteId/billing", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch).mockResolvedValue([]);
    renderAt("/sites/7/billing");
    expect(await screen.findByRole("heading", { name: "Billing" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Switch site" })).toHaveAttribute("href", "/sites");
  });

  it("renders the account page at /account", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch).mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false });
    renderAt("/account");
    expect(await screen.findByRole("heading", { name: "Account" })).toBeInTheDocument();
  });

  it("lets a logged-in user reach Account from the dashboard nav", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false });
    renderAt("/sites/7");
    await screen.findByRole("heading", { name: "Dashboard" });
    await userEvent.click(screen.getByRole("link", { name: "Account" }));
    expect(await screen.findByRole("heading", { name: "Account" })).toBeInTheDocument();
  });

  it("lets a logged-in user reach Sources from the dashboard nav", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch).mockResolvedValue([]);
    renderAt("/sites/7");
    await screen.findByRole("heading", { name: "Dashboard" });
    await userEvent.click(screen.getByRole("link", { name: "Sources" }));
    expect(await screen.findByRole("heading", { name: "Sources" })).toBeInTheDocument();
  });
});
