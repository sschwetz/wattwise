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

function renderPage(id: number | string = 2) {
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
  await screen.findByRole("heading", { name: "Administrator" });
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

  it("names the sites this user owns in the Amber key clear prompt", async () => {
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(within(screen.getByRole("alertdialog")).getByText(/sites this user owns/)).toBeInTheDocument();
  });

  it("disables Reset two-factor sign-in when it is not set up", async () => {
    users = [ALICE, user(2, { totp_enabled: false }), CAROL];
    await loaded();
    expect(screen.getByRole("button", { name: "Reset two-factor sign-in" })).toBeDisabled();
    expect(screen.getByText("Two-factor sign-in is not set up.")).toBeInTheDocument();
  });

  it("offers the two-factor reset when it is set up", async () => {
    await loaded();
    expect(screen.getByRole("button", { name: "Reset two-factor sign-in" })).toBeEnabled();
    expect(screen.queryByText("Two-factor sign-in is not set up.")).toBeNull();
  });

  it("drops the two-factor success text when another action starts", async () => {
    await loaded();
    await userEvent.click(screen.getByRole("button", { name: "Reset two-factor sign-in" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Reset two-factor" }));
    expect(await screen.findByText("Two-factor sign-in was reset.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Send password reset email" }));
    expect(screen.queryByText("Two-factor sign-in was reset.")).toBeNull();
  });

  it("shows User not found for an id the API does not know", async () => {
    failWith["GET /api/v1/admin/users/99"] = new ApiError(404, "user_not_found", "User not found", null);
    renderPage(99);
    expect(await screen.findByText("User not found")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("shows User not found for a non-numeric id without requesting it", async () => {
    renderPage("abc");
    expect(await screen.findByText("User not found")).toBeInTheDocument();
    expect(vi.mocked(apiFetch).mock.calls.some(([path]) => path.includes("NaN"))).toBe(false);
    expect(calls("GET", "/admin/users/")).toHaveLength(0);
  });

  it("shows a loading hint while the user loads", () => {
    vi.mocked(apiFetch).mockImplementation(() => new Promise(() => {}));
    renderPage();
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("shows an error with Retry in place of Site access when the user list fails", async () => {
    failWith["GET /api/v1/admin/users"] = new ApiError(500, "server_error", "list down", null);
    renderPage();
    await screen.findByRole("heading", { name: "Details" });
    expect(await screen.findByText("list down")).toBeInTheDocument();
    expect(screen.queryByLabelText("Add to site")).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove administrator" })).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Security" })).toBeInTheDocument();
  });

  it("disables a role select while its change is pending", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string, options?: RequestInit) => {
      if (options?.method === "PUT") return new Promise(() => {});
      if (path === "/api/v1/admin/users") return users;
      if (path === "/api/v1/admin/users/2") return BOB;
      return { id: 1, is_admin: true };
    });
    await loaded();
    const select = screen.getByLabelText("Role for Shed");
    await userEvent.selectOptions(select, "viewer");
    await waitFor(() => expect(select).toBeDisabled());
    expect(screen.getByLabelText("Role for Office")).toBeEnabled();
  });

  it("refetches the user after a mutation", async () => {
    await loaded();
    const userGets = () => vi.mocked(apiFetch).mock.calls.filter(([p, o]) => p === "/api/v1/admin/users/2" && !o?.method).length;
    const before = userGets();
    await userEvent.click(screen.getByRole("button", { name: "Make administrator" }));
    await waitFor(() => expect(userGets()).toBeGreaterThan(before));
  });

  it("refreshes the signed-in profile when a mutation targets the signed-in admin", async () => {
    users = [ALICE, user(2, { is_admin: true }), CAROL];
    await loaded(1);
    const meCalls = () => vi.mocked(apiFetch).mock.calls.filter(([p]) => p === "/api/v1/users/me").length;
    const before = meCalls();
    await userEvent.click(screen.getByRole("button", { name: "Remove administrator" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove admin" }));
    await waitFor(() => expect(calls("PATCH", "/admin/users/1")).toHaveLength(1));
    await waitFor(() => expect(meCalls()).toBeGreaterThan(before));
  });

  it("leaves the signed-in profile alone when a mutation targets someone else", async () => {
    await loaded();
    const meCalls = () => vi.mocked(apiFetch).mock.calls.filter(([p]) => p === "/api/v1/users/me").length;
    const before = meCalls();
    await userEvent.click(screen.getByRole("button", { name: "Make administrator" }));
    await waitFor(() => expect(calls("PATCH", "/admin/users/2")).toHaveLength(1));
    expect(meCalls()).toBe(before);
  });

  it("shows QueryError when the load fails", async () => {
    failWith["GET /api/v1/admin/users/2"] = new ApiError(500, "server_error", "boom", null);
    renderPage();
    expect(await screen.findByText("boom")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
