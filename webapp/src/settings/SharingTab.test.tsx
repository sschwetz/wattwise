import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "../api/client";
import type { Invite } from "../sites/useInvites";
import type { Site } from "../sites/useSites";
import { SharingTab } from "./SharingTab";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const site: Site = { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true };

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

let invites: Invite[];
let meId: number;

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
    meId = 1;
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/api/v1/users/me") return { id: meId, email: "o@example.com" } as never;
      if (init?.method === "POST") return invite(9, "invited") as never;
      return invites as never;
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
      if (path === "/api/v1/users/me") return { id: 1 } as never;
      if (init?.method === "POST") throw new ApiError(409, "conflict", "Cannot revoke", null);
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
      if (path === "/api/v1/users/me") return { id: 1 } as never;
      if (init?.method === "POST") throw new ApiError(409, "already_invited", "That address is already invited", null);
      return invites as never;
    });
    renderTab();
    await userEvent.type(await screen.findByLabelText("Email"), "p1@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Send invite" }));
    expect(await screen.findByText("That address is already invited")).toBeInTheDocument();
    expect(screen.queryByText("Invitation sent")).toBeNull();
  });

  it("gives a viewer no form and a revoke button on their accepted row", async () => {
    meId = 2;
    invites = [invite(2, "accepted", { accepted_at: "2026-03-10T00:00:00Z" })];
    renderTab();
    await screen.findByText("p2@example.com");
    expect(screen.queryByLabelText("Email")).toBeNull();
    expect(screen.queryByRole("button", { name: "Send invite" })).toBeNull();
    expect(within(row("p2@example.com")).getByRole("button", { name: /revoke/i })).toBeInTheDocument();
  });

  it("shows no form while the account loads or after it fails", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path === "/api/v1/users/me") throw new Error("boom");
      return invites as never;
    });
    renderTab();
    expect(screen.queryByLabelText("Email")).toBeNull();
    expect(await screen.findByText(/account details could not be loaded/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Email")).toBeNull();
  });

  it("shows an error with Retry when the list fails to load", async () => {
    let fail = true;
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path === "/api/v1/users/me") return { id: 1 } as never;
      if (fail) throw new Error("List down");
      return invites as never;
    });
    renderTab();
    expect(await screen.findByText("List down")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("p1@example.com")).toBeInTheDocument();
  });
});
