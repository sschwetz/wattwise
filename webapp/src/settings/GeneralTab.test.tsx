import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import type { Site } from "../sites/useSites";
import { GeneralTab } from "./GeneralTab";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const site: Site = { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true };

function renderTab(s: Site = site) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <GeneralTab site={s} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function patchCalls() {
  return vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === "PATCH");
}

describe("GeneralTab", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/api/v1/users/me") return { id: 1, email: "a@b.c" } as never;
      if (init?.method === "PATCH") return { ...site, ...JSON.parse(String(init.body)) } as never;
      return [] as never;
    });
  });

  it("offers IANA zones in a list and saves the chosen one", async () => {
    const user = userEvent.setup();
    renderTab();
    const select = screen.getByLabelText("Time zone");
    expect(select.tagName).toBe("SELECT");
    expect(within(select).getByRole("option", { name: "Australia/Perth" })).toBeInTheDocument();
    await waitFor(() => expect(select).toBeEnabled());
    await user.selectOptions(select, "Australia/Perth");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patchCalls()).toHaveLength(1));
    expect(JSON.parse(String(patchCalls()[0][1]?.body))).toMatchObject({ timezone: "Australia/Perth" });
  });

  it("saves the name and time zone with a PATCH", async () => {
    renderTab();
    const name = await screen.findByLabelText("Name");
    await waitFor(() => expect(name).toBeEnabled());
    await userEvent.clear(name);
    await userEvent.type(name, "Cabin");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patchCalls()).toHaveLength(1));
    expect(patchCalls()[0][0]).toBe("/api/v1/sites/7");
    expect(JSON.parse(String(patchCalls()[0][1]?.body))).toEqual({ name: "Cabin", timezone: "Australia/Sydney" });
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("shows an error when saving fails", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/api/v1/users/me") return { id: 1 } as never;
      if (init?.method === "PATCH") throw new Error("Invalid time zone");
      return [] as never;
    });
    renderTab();
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid time zone");
  });

  it("confirms before turning collection off, then sends enabled false", async () => {
    renderTab();
    await userEvent.click(await screen.findByRole("switch", { name: "Enabled" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent(/collection will stop/i);
    expect(dialog).toHaveTextContent(/existing data stays/i);
    expect(patchCalls()).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(patchCalls()).toHaveLength(1));
    expect(JSON.parse(String(patchCalls()[0][1]?.body))).toEqual({ enabled: false });
  });

  it("sends nothing when the confirmation is cancelled", async () => {
    renderTab();
    await userEvent.click(await screen.findByRole("switch", { name: "Enabled" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(patchCalls()).toHaveLength(0);
  });

  it("cancels the confirmation with Escape", async () => {
    renderTab();
    await userEvent.click(await screen.findByRole("switch", { name: "Enabled" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(patchCalls()).toHaveLength(0);
  });

  it("re-enables without a confirmation", async () => {
    renderTab({ ...site, enabled: false });
    await userEvent.click(await screen.findByRole("switch", { name: "Enabled" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await waitFor(() => expect(patchCalls()).toHaveLength(1));
    expect(JSON.parse(String(patchCalls()[0][1]?.body))).toEqual({ enabled: true });
  });

  it("shows the collection status", async () => {
    const { unmount } = renderTab();
    expect(await screen.findByText("Collecting")).toBeInTheDocument();
    unmount();
    renderTab({ ...site, enabled: false });
    expect(await screen.findByText("Collection is off")).toBeInTheDocument();
  });

  it("links to the dashboard", async () => {
    renderTab();
    expect(await screen.findByRole("link", { name: "Open dashboard" })).toHaveAttribute("href", "/sites/7");
  });

  it("disables the fields and toggle for a viewer", async () => {
    renderTab({ ...site, owner_user_id: 2 });
    await waitFor(() => expect(screen.getByLabelText("Name")).toBeDisabled());
    expect(screen.getByLabelText("Time zone")).toBeDisabled();
    expect(screen.getByRole("switch", { name: "Enabled" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("shows a failed toggle error near the toggle, not in the Save form", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/api/v1/users/me") return { id: 1 } as never;
      if (init?.method === "PATCH") throw new Error("Boom");
      return [] as never;
    });
    renderTab({ ...site, enabled: false });
    await userEvent.click(await screen.findByRole("switch", { name: "Enabled" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Boom");
    expect(alert).toHaveAttribute("data-testid", "toggle-error");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it("does not show Saved after a successful toggle", async () => {
    renderTab({ ...site, enabled: false });
    const toggle = await screen.findByRole("switch", { name: "Enabled" });
    await waitFor(() => expect(toggle).toBeEnabled());
    await userEvent.click(toggle);
    await waitFor(() => expect(patchCalls()).toHaveLength(1));
    // The switch is disabled while the PATCH is pending; once it is enabled
    // again the mutation has settled and any "Saved" would be on screen.
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("explains read-only mode to a viewer", async () => {
    renderTab({ ...site, owner_user_id: 2 });
    expect(await screen.findByText("Only the owner can change this site's settings.")).toBeInTheDocument();
  });

  it("explains read-only mode when the account lookup fails", async () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error("down"));
    renderTab();
    expect(await screen.findByText(/account details could not be loaded/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toBeDisabled();
  });

  it("shows no note for the owner", async () => {
    renderTab();
    await waitFor(() => expect(screen.getByLabelText("Name")).toBeEnabled());
    expect(screen.queryByText(/only the owner/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument();
  });

  it("returns focus to the toggle on Cancel and on Escape", async () => {
    renderTab();
    const toggle = await screen.findByRole("switch", { name: "Enabled" });
    await waitFor(() => expect(toggle).toBeEnabled());
    await userEvent.click(toggle);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(toggle).toHaveFocus();
    await userEvent.click(toggle);
    await userEvent.keyboard("{Escape}");
    expect(toggle).toHaveFocus();
  });

  it("cancels with Escape when focus is outside the dialog", async () => {
    renderTab();
    const toggle = await screen.findByRole("switch", { name: "Enabled" });
    await waitFor(() => expect(toggle).toBeEnabled());
    await userEvent.click(toggle);
    screen.getByLabelText("Name").focus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
});
