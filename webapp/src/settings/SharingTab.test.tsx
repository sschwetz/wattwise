import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../account/useAccount";
import { ApiError, apiFetch } from "../api/client";
import type { Invite } from "../sites/useInvites";
import type { SitePerson } from "../sites/useSitePeople";
import type { Site } from "../sites/useSites";
import { SharingTab } from "./SharingTab";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const site: Site = { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true, latitude: null, longitude: null, my_role: "owner" };

function invite(id: number, status: Invite["status"], extra: Partial<Invite> = {}): Invite {
  return {
    id,
    site_id: 7,
    email: `p${id}@example.com`,
    status,
    created_at: "2026-03-01T12:00:00Z",
    accepted_at: null,
    declined_at: null,
    revoked_at: null,
    inviter_user_id: 1,
    inviter_email: "owner@example.com",
    ...extra,
  };
}

const INVITES: Invite[] = [
  invite(1, "invited"),
  invite(2, "accepted", { accepted_at: "2026-03-10T12:00:00Z" }),
  invite(3, "declined", { declined_at: "2026-03-11T12:00:00Z" }),
  invite(4, "revoked", { revoked_at: "2026-03-12T12:00:00Z" }),
];

const OWNER: SitePerson = { user_id: 1, email: "owner@example.com", display_name: null, role: "owner" };
const PEOPLE: SitePerson[] = [
  OWNER,
  { user_id: 2, email: "boss@example.com", display_name: "Boss", role: "manager" },
  { user_id: 3, email: "watcher@example.com", display_name: null, role: "viewer" },
];

const OWNER_ME: Me = {
  id: 1,
  email: "owner@example.com",
  display_name: null,
  has_amber_api_key: false,
  has_avatar: false,
  is_admin: false,
};

let invites: Invite[];
let people: SitePerson[];
let me: Me;

const isPeoplePath = (path: string) => path === "/api/v1/sites/7/access";

function renderTab(s: Site = site) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <SharingTab site={s} />
    </QueryClientProvider>,
  );
}

function calls(method: string) {
  return vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === method);
}

function row(email: string) {
  return screen.getByText(email).closest("li") as HTMLElement;
}

describe("SharingTab", () => {
  beforeEach(() => {
    invites = INVITES;
    people = [OWNER];
    me = OWNER_ME;
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") return invite(9, "invited") as never;
      if (init?.method === "DELETE") return undefined as never;
      if (isPeoplePath(path)) return people as never;
      if (path === "/api/v1/users/me") return me as never;
      return invites as never;
    });
  });

  describe("revoke on a manager's accepted invite", () => {
    const SELF: SitePerson = { user_id: 5, email: "Me@Example.com", display_name: null, role: "manager" };

    beforeEach(() => {
      people = [...PEOPLE, SELF];
      invites = [
        invite(11, "accepted", { email: "boss@example.com", accepted_at: "2026-03-10T12:00:00Z" }),
        invite(12, "accepted", { email: "me@example.com", accepted_at: "2026-03-10T12:00:00Z" }),
        invite(13, "accepted", { email: "watcher@example.com", accepted_at: "2026-03-10T12:00:00Z" }),
      ];
    });

    const revokeButton = (email: string) => screen.queryByRole("button", { name: `Revoke ${email}` });

    it("gives the owner Revoke on every accepted invite", async () => {
      renderTab();
      await screen.findByText("Me@Example.com");
      expect(revokeButton("boss@example.com")).toBeInTheDocument();
      expect(revokeButton("me@example.com")).toBeInTheDocument();
      expect(revokeButton("watcher@example.com")).toBeInTheDocument();
    });

    it("hides Revoke from a manager on another manager's accepted invite", async () => {
      me = { ...me, id: 5, email: "me@example.com" };
      renderTab({ ...site, my_role: "manager" });
      // The people list and the signed-in user are both loaded once the
      // list shows and the manager's own Revoke is there.
      expect(await screen.findByText("Boss")).toBeInTheDocument();
      await waitFor(() => expect(revokeButton("me@example.com")).toBeInTheDocument());
      expect(revokeButton("boss@example.com")).toBeNull();
    });

    it("keeps Revoke for a manager on their own accepted invite", async () => {
      me = { ...me, id: 5, email: "me@example.com" };
      renderTab({ ...site, my_role: "manager" });
      expect(await screen.findByRole("button", { name: "Revoke me@example.com" })).toBeInTheDocument();
    });

    it("keeps Revoke for a manager on a viewer's accepted invite", async () => {
      me = { ...me, id: 5, email: "me@example.com" };
      renderTab({ ...site, my_role: "manager" });
      await waitFor(() => expect(revokeButton("me@example.com")).toBeInTheDocument());
      expect(revokeButton("watcher@example.com")).toBeInTheDocument();
    });

    it("refreshes the people list after a revoke", async () => {
      renderTab();
      await userEvent.click(await screen.findByRole("button", { name: "Revoke watcher@example.com" }));
      const peopleGets = () => vi.mocked(apiFetch).mock.calls.filter(([p, i]) => isPeoplePath(p) && !i?.method);
      const before = peopleGets().length;
      await userEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));
      await waitFor(() => expect(calls("POST")).toHaveLength(1));
      expect(calls("POST")[0][0]).toBe("/api/v1/sites/7/invites/13/revoke");
      await waitFor(() => expect(peopleGets().length).toBeGreaterThan(before));
    });
  });

  it("sends an invite, clears the field and refetches the list", async () => {
    renderTab();
    const field = await screen.findByLabelText("Email");
    await userEvent.type(field, "new@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Send invite" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(calls("POST")[0][0]).toBe("/api/v1/sites/7/invites");
    expect(JSON.parse(String(calls("POST")[0][1]?.body))).toEqual({ email: "new@example.com" });
    expect(await screen.findByText("Invitation sent")).toBeInTheDocument();
    expect(field).toHaveValue("");
    const listGets = vi.mocked(apiFetch).mock.calls.filter(([p, i]) => p === "/api/v1/sites/7/invites" && !i?.method);
    expect(listGets.length).toBeGreaterThan(1);
  });

  it("shows email, status label and the date of the last status change", async () => {
    renderTab();
    await screen.findByText("p1@example.com");
    const day = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString();
    expect(within(row("p1@example.com")).getByText("Invited")).toBeInTheDocument();
    expect(within(row("p1@example.com")).getByText(day("2026-03-01"))).toBeInTheDocument();
    expect(within(row("p2@example.com")).getByText("Accepted")).toBeInTheDocument();
    expect(within(row("p2@example.com")).getByText(day("2026-03-10"))).toBeInTheDocument();
    expect(within(row("p3@example.com")).getByText("Declined")).toBeInTheDocument();
    expect(within(row("p3@example.com")).getByText(day("2026-03-11"))).toBeInTheDocument();
    expect(within(row("p4@example.com")).getByText("Revoked")).toBeInTheDocument();
    expect(within(row("p4@example.com")).getByText(day("2026-03-12"))).toBeInTheDocument();
  });

  describe("dates", () => {
    const savedTz = process.env.TZ;
    afterEach(() => {
      process.env.TZ = savedTz;
    });

    // 03:00 UTC on 2 March is still 1 March in Los Angeles but 2 March in Sydney.
    const NEAR_MIDNIGHT = "2026-03-02T03:00:00Z";

    it.each([
      ["America/Los_Angeles", 1],
      ["Australia/Sydney", 2],
    ])("formats the date in the viewer's time zone (%s), not the site's", async (zone, dayOfMonth) => {
      process.env.TZ = zone;
      invites = [invite(1, "invited", { created_at: NEAR_MIDNIGHT })];
      renderTab({ ...site, timezone: "Asia/Tokyo" });
      await screen.findByText("p1@example.com");
      const expected = new Date(2026, 2, dayOfMonth).toLocaleDateString();
      expect(within(row("p1@example.com")).getByText(expected)).toBeInTheDocument();
    });
  });

  it("shows who sent each invite", async () => {
    invites = [invite(1, "invited", { inviter_email: "alice@example.com" })];
    renderTab();
    await screen.findByText("p1@example.com");
    expect(within(row("p1@example.com")).getByText("Invited by alice@example.com")).toBeInTheDocument();
  });

  it("names the address on each Revoke button", async () => {
    renderTab();
    await screen.findByText("p1@example.com");
    expect(screen.getByRole("button", { name: "Revoke p1@example.com" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke p2@example.com" })).toBeInTheDocument();
  });

  it("offers revoke only on invited and accepted rows, and never delete", async () => {
    renderTab();
    await screen.findByText("p1@example.com");
    expect(within(row("p1@example.com")).getByRole("button", { name: /revoke/i })).toBeInTheDocument();
    expect(within(row("p2@example.com")).getByRole("button", { name: /revoke/i })).toBeInTheDocument();
    expect(within(row("p3@example.com")).queryByRole("button")).toBeNull();
    expect(within(row("p4@example.com")).queryByRole("button")).toBeNull();
    expect(screen.queryByRole("button", { name: /delete|remove/i })).toBeNull();
  });

  it("asks for confirmation before revoking", async () => {
    renderTab();
    await screen.findByText("p1@example.com");
    await userEvent.click(within(row("p1@example.com")).getByRole("button", { name: /revoke/i }));
    expect(calls("POST")).toHaveLength(0);
    await userEvent.click(within(row("p1@example.com")).getByRole("button", { name: "Cancel" }));
    expect(calls("POST")).toHaveLength(0);
    await userEvent.click(within(row("p1@example.com")).getByRole("button", { name: /revoke/i }));
    await userEvent.click(within(row("p1@example.com")).getByRole("button", { name: "Confirm revoke" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(calls("POST")[0][0]).toBe("/api/v1/sites/7/invites/1/revoke");
  });

  it("shows a revoke error near the row", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") throw new ApiError(409, "conflict", "Cannot revoke", null);
      if (isPeoplePath(path)) return people as never;
      return invites as never;
    });
    renderTab();
    await screen.findByText("p1@example.com");
    await userEvent.click(within(row("p1@example.com")).getByRole("button", { name: /revoke/i }));
    await userEvent.click(within(row("p1@example.com")).getByRole("button", { name: "Confirm revoke" }));
    expect(await within(row("p1@example.com")).findByText("Cannot revoke")).toBeInTheDocument();
  });

  it("shows an already_invited error under the field", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") throw new ApiError(409, "already_invited", "That address is already invited", null);
      if (isPeoplePath(path)) return people as never;
      return invites as never;
    });
    renderTab();
    await userEvent.type(await screen.findByLabelText("Email"), "p1@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Send invite" }));
    expect(await screen.findByText("That address is already invited")).toBeInTheDocument();
    expect(screen.queryByText("Invitation sent")).toBeNull();
  });

  it("gives a viewer no form and a revoke button on their accepted row", async () => {
    invites = [invite(2, "accepted", { accepted_at: "2026-03-10T00:00:00Z" })];
    renderTab({ ...site, my_role: "viewer" });
    await screen.findByText("p2@example.com");
    expect(screen.queryByLabelText("Email")).toBeNull();
    expect(screen.queryByRole("button", { name: "Send invite" })).toBeNull();
    expect(within(row("p2@example.com")).getByRole("button", { name: /revoke/i })).toBeInTheDocument();
  });

  it("shows the invite form and revoke on invited rows to a manager", async () => {
    renderTab({ ...site, my_role: "manager" });
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send invite" })).toBeInTheDocument();
    await screen.findByText("p1@example.com");
    expect(within(row("p1@example.com")).getByRole("button", { name: /revoke/i })).toBeInTheDocument();
  });

  it("hides the form and the revoke button on invited rows from a viewer", async () => {
    renderTab({ ...site, my_role: "viewer" });
    await screen.findByText("p1@example.com");
    expect(screen.queryByLabelText("Email")).toBeNull();
    expect(within(row("p1@example.com")).queryByRole("button")).toBeNull();
  });

  it("treats a missing role as read-only", async () => {
    renderTab({ ...site, my_role: undefined } as unknown as Site);
    await screen.findByText("p1@example.com");
    expect(screen.queryByLabelText("Email")).toBeNull();
    expect(within(row("p1@example.com")).queryByRole("button")).toBeNull();
  });

  it("shows an error with Retry when the list fails to load", async () => {
    let fail = true;
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (isPeoplePath(path)) return people as never;
      if (fail) throw new Error("List down");
      return invites as never;
    });
    renderTab();
    expect(await screen.findByText("List down")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("p1@example.com")).toBeInTheDocument();
  });

  describe("people with access", () => {
    beforeEach(() => {
      people = PEOPLE;
    });

    function personRow(email: string) {
      return screen.getByText(email).closest("li") as HTMLElement;
    }

    it("lists everyone with a role badge, for the owner", async () => {
      renderTab();
      expect(await screen.findByRole("heading", { name: "People with access" })).toBeInTheDocument();
      await screen.findByText("owner@example.com");
      expect(within(personRow("owner@example.com")).getByText("Owner")).toBeInTheDocument();
      expect(within(personRow("boss@example.com")).getByText("Manager")).toBeInTheDocument();
      expect(within(personRow("boss@example.com")).getByText("Boss")).toBeInTheDocument();
      expect(within(personRow("watcher@example.com")).getByText("Viewer")).toBeInTheDocument();
    });

    it("gives the owner Remove on manager and viewer rows only", async () => {
      renderTab();
      await screen.findByText("boss@example.com");
      expect(screen.getByRole("button", { name: "Remove boss@example.com" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Remove watcher@example.com" })).toBeInTheDocument();
      expect(within(personRow("owner@example.com")).queryByRole("button")).toBeNull();
    });

    it("removes only after confirmation, and Cancel does nothing", async () => {
      renderTab();
      await userEvent.click(await screen.findByRole("button", { name: "Remove watcher@example.com" }));
      const dialog = screen.getByRole("alertdialog");
      expect(within(dialog).getByText("watcher@example.com will lose access to this site.")).toBeInTheDocument();
      await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
      expect(calls("DELETE")).toHaveLength(0);
      await userEvent.click(screen.getByRole("button", { name: "Remove watcher@example.com" }));
      await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }));
      await waitFor(() => expect(calls("DELETE")).toHaveLength(1));
      expect(calls("DELETE")[0][0]).toBe("/api/v1/sites/7/access/3");
    });

    it("shows the list read-only to a manager", async () => {
      renderTab({ ...site, my_role: "manager" });
      await screen.findByText("boss@example.com");
      expect(screen.getByText("watcher@example.com")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Remove/ })).toBeNull();
    });

    it("does not show or fetch the list for a viewer", async () => {
      renderTab({ ...site, my_role: "viewer" });
      await screen.findByText("p1@example.com");
      expect(screen.queryByRole("heading", { name: "People with access" })).toBeNull();
      expect(vi.mocked(apiFetch).mock.calls.some(([path]) => isPeoplePath(path))).toBe(false);
    });

    it("shows a remove error", async () => {
      vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
        if (init?.method === "DELETE") throw new ApiError(404, "not_found", "This person has no access to the site", null);
        if (isPeoplePath(path)) return people as never;
        return invites as never;
      });
      renderTab();
      await userEvent.click(await screen.findByRole("button", { name: "Remove watcher@example.com" }));
      await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }));
      expect(await screen.findByText("This person has no access to the site")).toBeInTheDocument();
    });
  });
});
