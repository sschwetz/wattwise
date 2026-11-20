import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "../api/client";
import type { AdminUser } from "./useAdminUsers";
import { UserDetailPage } from "./UserDetailPage";

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
    totp_enabled: true,
    email_confirmed: true,
    has_amber_api_key: false,
    sites: [],
    ...extra,
  };
}

const ALICE = user(1, { display_name: "Alice Admin", is_admin: true, sites: [{ site_id: 1, site_name: "Home", role: "owner" }] });
const BOB = user(2, {
  display_name: "Bob Builder",
  email: "bob@example.com",
  has_amber_api_key: true,
  sites: [
    { site_id: 1, site_name: "Home", role: "owner" },
    { site_id: 2, site_name: "Shed", role: "manager" },
    { site_id: 3, site_name: "Office", role: "viewer" },
  ],
});
const CAROL = user(3, { sites: [{ site_id: 4, site_name: "Garage", role: "viewer" }] });

let users: AdminUser[];
let failWith: Record<string, ApiError>;

function calls(method: string, pathPart: string) {
  return vi.mocked(apiFetch).mock.calls.filter(([path, options]) => path.includes(pathPart) && options?.method === method);
}

function install() {
  vi.mocked(apiFetch).mockImplementation(async (path: string, options?: RequestInit) => {
    const key = `${options?.method ?? "GET"} ${path}`;
    if (failWith[key]) throw failWith[key];
    if (path === "/api/v1/admin/users") return users;
    if (path === "/api/v1/users/me") {
      return { id: 1, email: "user1@example.com", display_name: "Alice Admin", has_amber_api_key: false, has_avatar: false, is_admin: true };
    }
    const match = /^\/api\/v1\/admin\/users\/(\d+)$/.exec(path);
    if (match && !options?.method) return users.find((u) => u.id === Number(match[1]));
    return undefined;
  });
}

function renderPage(id = 2) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/admin/users/${id}`]}>
        <Routes>
          <Route path="/admin/users/:userId" element={<UserDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function loaded(id = 2) {
  renderPage(id);
  await screen.findByRole("heading", { name: "Details" });
}

function siteRow(name: string) {
  return screen.getByText(name, { selector: "li *, li" }).closest("li") as HTMLElement;
}

describe("UserDetailPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    users = [ALICE, BOB, CAROL];
    failWith = {};
    install();
  });

  it("shows the four sections and a link back to the Users tab", async () => {
    await loaded();
    for (const name of ["Details", "Site access", "Security", "Administrator"]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: "Back" })).toHaveAttribute("href", "/admin/users");
  });

  it("sends one PATCH with only the changed fields", async () => {
    await loaded();
    const name = screen.getByLabelText("Display name");
    await userEvent.clear(name);
    await userEvent.type(name, "Robert");
    const email = screen.getByLabelText("Email");
    await userEvent.clear(email);
    await userEvent.type(email, "rob@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PATCH", "/admin/users/2")).toHaveLength(1));
    expect(JSON.parse(calls("PATCH", "/admin/users/2")[0][1]?.body as string)).toEqual({
      display_name: "Robert",
      email: "rob@example.com",
    });
  });

  it("sends only the display name when the email is untouched", async () => {
    await loaded();
    const name = screen.getByLabelText("Display name");
    await userEvent.clear(name);
    await userEvent.type(name, "Robert");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PATCH", "/admin/users/2")).toHaveLength(1));
    expect(JSON.parse(calls("PATCH", "/admin/users/2")[0][1]?.body as string)).toEqual({ display_name: "Robert" });
  });

  it("keeps Save disabled until something changes", async () => {
    await loaded();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("shows email_taken on the email field", async () => {
    failWith["PATCH /api/v1/admin/users/2"] = new ApiError(409, "email_taken", "An account with this email already exists", null);
    await loaded();
    const email = screen.getByLabelText("Email");
    await userEvent.clear(email);
    await userEvent.type(email, "carol@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const message = await screen.findByText("An account with this email already exists");
    expect(email).toHaveAccessibleDescription("An account with this email already exists");
    expect(message).toBeInTheDocument();
  });

  it("removes the avatar only when the user has one", async () => {
    users = [ALICE, user(2, { has_avatar: true }), CAROL];
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Remove photo" }));
    await waitFor(() => expect(calls("DELETE", "/admin/users/2/avatar")).toHaveLength(1));
  });

  it("offers no avatar removal when there is none", async () => {
    await loaded();
    expect(screen.queryByRole("button", { name: "Remove photo" })).toBeNull();
  });

  it("lists only sites the user is not on in Add to site, and PUTs the chosen role", async () => {
    await loaded();
    const select = screen.getByLabelText("Add to site");
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Choose a site", "Garage"]);
    await userEvent.selectOptions(select, "Garage");
    await userEvent.selectOptions(screen.getByLabelText("Role for new site"), "viewer");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls("PUT", "/admin/users/2/sites/4")).toHaveLength(1));
    expect(JSON.parse(calls("PUT", "/admin/users/2/sites/4")[0][1]?.body as string)).toEqual({ role: "viewer" });
  });

  it("shows the owner's row with no selector and no Remove", async () => {
    await loaded();
    const home = within(siteRow("Home"));
    expect(home.getByText("Owner")).toBeInTheDocument();
    expect(home.queryByRole("combobox")).toBeNull();
    expect(home.queryByRole("button")).toBeNull();
  });

  it("changes a role with PUT", async () => {
    await loaded();
    await userEvent.selectOptions(screen.getByLabelText("Role for Shed"), "viewer");
    await waitFor(() => expect(calls("PUT", "/admin/users/2/sites/2")).toHaveLength(1));
    expect(JSON.parse(calls("PUT", "/admin/users/2/sites/2")[0][1]?.body as string)).toEqual({ role: "viewer" });
  });

  it("asks before removing site access, and does nothing on cancel", async () => {
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Remove Shed" }));
    const dialog = screen.getByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(calls("DELETE", "/sites/2")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Remove Shed" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove access" }));
    await waitFor(() => expect(calls("DELETE", "/admin/users/2/sites/2")).toHaveLength(1));
  });

  it("says the reset email was requested, not sent", async () => {
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Send password reset email" }));
    await waitFor(() => expect(calls("POST", "/admin/users/2/password-reset")).toHaveLength(1));
    expect(await screen.findByRole("button", { name: "Reset email requested" })).toBeInTheDocument();
    expect(screen.queryByText(/sent/i)).toBeNull();
  });

  it("asks before resetting two-factor sign-in, and does nothing on cancel", async () => {
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Reset two-factor sign-in" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(calls("POST", "/totp/reset")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Reset two-factor sign-in" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Reset two-factor" }));
    await waitFor(() => expect(calls("POST", "/admin/users/2/totp/reset")).toHaveLength(1));
  });

  it("shows the Amber key as set without ever showing a value", async () => {
    await loaded();
    expect(screen.getByText("Amber API key: set")).toBeInTheDocument();
    expect(screen.queryByLabelText("New Amber API key")).toBeNull();
  });

  it("shows not set when the user has no Amber key", async () => {
    await loaded(3);
    expect(screen.getByText("Amber API key: not set")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  });

  it("changes the Amber key through an input", async () => {
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Change" }));
    await userEvent.type(screen.getByLabelText("New Amber API key"), "secret-key");
    await userEvent.click(screen.getByRole("button", { name: "Save key" }));
    await waitFor(() => expect(calls("PUT", "/admin/users/2/amber-key")).toHaveLength(1));
    expect(JSON.parse(calls("PUT", "/admin/users/2/amber-key")[0][1]?.body as string)).toEqual({ amber_api_key: "secret-key" });
  });

  it("asks before clearing the Amber key", async () => {
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(calls("DELETE", "/amber-key")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Clear key" }));
    await waitFor(() => expect(calls("DELETE", "/admin/users/2/amber-key")).toHaveLength(1));
  });

  it("makes a user an administrator without asking", async () => {
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Make administrator" }));
    await waitFor(() => expect(calls("PATCH", "/admin/users/2")).toHaveLength(1));
    expect(JSON.parse(calls("PATCH", "/admin/users/2")[0][1]?.body as string)).toEqual({ is_admin: true });
  });

  it("asks before clearing the admin flag, and does nothing on cancel", async () => {
    users = [ALICE, user(2, { is_admin: true }), CAROL];
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Remove administrator" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(calls("PATCH", "/admin/users/2")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Remove administrator" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove admin" }));
    await waitFor(() => expect(calls("PATCH", "/admin/users/2")).toHaveLength(1));
    expect(JSON.parse(calls("PATCH", "/admin/users/2")[0][1]?.body as string)).toEqual({ is_admin: false });
  });

  it("disables the toggle with an explanation when this user is the only admin", async () => {
    users = [user(1, { is_admin: true }), BOB];
    await loaded(1);
    expect(screen.getByRole("button", { name: "Remove administrator" })).toBeDisabled();
    expect(screen.getByText("This is the only administrator, so the flag cannot be removed.")).toBeInTheDocument();
  });

  it("shows the API message and disables the toggle after a last_admin 409", async () => {
    users = [ALICE, user(2, { is_admin: true }), CAROL];
    failWith["PATCH /api/v1/admin/users/2"] = new ApiError(409, "last_admin", "The last administrator cannot lose the admin flag", null);
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Remove administrator" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove admin" }));
    expect(await screen.findByText("The last administrator cannot lose the admin flag")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove administrator" })).toBeDisabled();
  });

  it("shows QueryError when the load fails", async () => {
    failWith["GET /api/v1/admin/users/2"] = new ApiError(500, "server_error", "boom", null);
    renderPage();
    expect(await screen.findByText("boom")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
