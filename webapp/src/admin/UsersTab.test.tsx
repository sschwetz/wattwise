import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "../api/client";
import type { AdminUser } from "./useAdminUsers";
import { UsersTab } from "./UsersTab";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function user(id: number, extra: Partial<AdminUser> = {}): AdminUser {
  return {
    id,
    email: `user${id}@example.com`,
    display_name: null,
    is_admin: false,
    has_avatar: false,
    totp_enabled: false,
    email_confirmed: true,
    has_amber_api_key: false,
    sites: [],
    ...extra,
  };
}

const USERS: AdminUser[] = [
  user(1, {
    display_name: "Alice Admin",
    is_admin: true,
    sites: [
      { site_id: 1, site_name: "Home", role: "owner" },
      { site_id: 2, site_name: "Shed", role: "manager" },
      { site_id: 3, site_name: "Office", role: "viewer" },
    ],
  }),
  user(2, { display_name: "Bob Builder", email: "bob@example.com" }),
];

function renderTab() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <UsersTab />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function row(email: string) {
  return screen.getByRole("link", { name: (name) => name.includes(email) }).closest("tr") as HTMLElement;
}

describe("UsersTab", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path === "/api/v1/admin/users") return USERS;
      if (path === "/api/v1/users/me") {
        return { id: 1, email: "user1@example.com", display_name: "Alice Admin", has_amber_api_key: false, has_avatar: false, is_admin: true };
      }
      throw new Error(`unexpected ${path}`);
    });
  });

  it("shows each user's name, email, Admin badge and site chips", async () => {
    renderTab();
    expect(await screen.findByText("Bob Builder")).toBeInTheDocument();
    const alice = within(row("user1@example.com"));
    expect(alice.getByText("user1@example.com")).toBeInTheDocument();
    expect(alice.getByText("Admin")).toBeInTheDocument();
    expect(alice.getByRole("link")).not.toContainElement(alice.getByText("Admin"));
    expect(within(row("bob@example.com")).queryByText("Admin")).toBeNull();
    const manages = alice.getAllByRole("cell")[1];
    expect(within(manages).getByText("Home")).toBeInTheDocument();
    expect(within(manages).getByText("Shed")).toBeInTheDocument();
    expect(within(manages).queryByText("Office")).toBeNull();
    const views = alice.getAllByRole("cell")[2];
    expect(within(views).getByText("Office")).toBeInTheDocument();
  });

  it("filters by name or email as you type, ignoring case", async () => {
    renderTab();
    await screen.findByText("Bob Builder");
    const search = screen.getByRole("searchbox", { name: "Search users" });
    await userEvent.type(search, "BOB@");
    expect(screen.queryByText("Alice Admin")).toBeNull();
    expect(screen.getByText("Bob Builder")).toBeInTheDocument();
    await userEvent.clear(search);
    await userEvent.type(search, "alice");
    expect(screen.queryByText("Bob Builder")).toBeNull();
    expect(screen.getByText("Alice Admin")).toBeInTheDocument();
  });

  it("shows an empty message when nobody matches", async () => {
    renderTab();
    await screen.findByText("Bob Builder");
    await userEvent.type(screen.getByRole("searchbox", { name: "Search users" }), "zzz");
    expect(screen.getByText("No users match your search.")).toBeInTheDocument();
  });

  it("links each row to the user's detail page", async () => {
    renderTab();
    const link = await screen.findByRole("link", { name: /bob@example\.com/ });
    expect(link).toHaveAttribute("href", "/admin/users/2");
  });

  it("shows QueryError when the load fails", async () => {
    vi.mocked(apiFetch).mockRejectedValue(new ApiError(500, "server_error", "boom", null));
    renderTab();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
