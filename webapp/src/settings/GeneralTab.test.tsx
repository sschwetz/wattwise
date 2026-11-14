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

vi.mock("./SiteMap", () => ({
  default: ({ point, onPick }: { point: { latitude: number; longitude: number } | null; onPick: (p: { latitude: number; longitude: number }) => void }) => (
    <button type="button" onClick={() => onPick({ latitude: -37.8136, longitude: 144.9631 })}>
      Map {point ? `${point.latitude},${point.longitude}` : "empty"}
    </button>
  ),
}));

const site: Site = { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true, latitude: null, longitude: null };

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
      if (path === "/api/v1/sites/7/sun") return { estimated: true, sunrise: "2026-11-13T19:00:00Z", sunset: "2026-11-14T09:00:00Z" } as never;
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
    expect(await screen.findByText("✓ Saved")).toBeInTheDocument();
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
    expect(await screen.findByText("✓ Saved")).toBeInTheDocument();
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
  describe("location", () => {
    const editable = () => waitFor(() => expect(screen.getByLabelText("Latitude")).toBeEnabled());

    it("shows today's sunrise and sunset in the site's time zone", async () => {
      renderTab();
      expect(await screen.findByText(/Today: sunrise 06:00, sunset 20:00 \(estimated from the time zone\)/)).toBeInTheDocument();
    });

    it("saves a point picked on the map", async () => {
      const user = userEvent.setup();
      renderTab();
      await user.click(await screen.findByRole("button", { name: "Map empty" }));
      expect(screen.getByLabelText("Latitude")).toHaveValue("-37.8136");
      expect(screen.getByLabelText("Longitude")).toHaveValue("144.9631");
      await user.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(patchCalls()).toHaveLength(1));
      expect(JSON.parse(String(patchCalls()[0][1]?.body))).toMatchObject({ latitude: -37.8136, longitude: 144.9631 });
    });

    it("does not send the location when it is unchanged", async () => {
      const user = userEvent.setup();
      renderTab({ ...site, latitude: -33.8, longitude: 151.2 });
      await user.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(patchCalls()).toHaveLength(1));
      expect(JSON.parse(String(patchCalls()[0][1]?.body))).toEqual({ name: "Home", timezone: "Australia/Sydney" });
    });

    it("clears the location when both boxes are emptied", async () => {
      const user = userEvent.setup();
      renderTab({ ...site, latitude: -33.8, longitude: 151.2 });
      await editable();
      await user.clear(screen.getByLabelText("Latitude"));
      await user.clear(screen.getByLabelText("Longitude"));
      await user.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(patchCalls()).toHaveLength(1));
      expect(JSON.parse(String(patchCalls()[0][1]?.body))).toMatchObject({ latitude: null, longitude: null });
    });

    it("refuses half a location without calling the server", async () => {
      const user = userEvent.setup();
      renderTab();
      await editable();
      await user.type(screen.getByLabelText("Latitude"), "-33.8");
      await user.click(screen.getByRole("button", { name: "Save" }));
      expect(await screen.findByText(/Enter a latitude/)).toBeInTheDocument();
      expect(patchCalls()).toHaveLength(0);
    });

    it("fills the boxes from the device location", async () => {
      const user = userEvent.setup();
      const getCurrentPosition = vi.fn((ok: (p: unknown) => void) => ok({ coords: { latitude: -31.9523, longitude: 115.8613 } }));
      vi.stubGlobal("navigator", { ...navigator, geolocation: { getCurrentPosition } });
      renderTab();
      await editable();
      await user.click(screen.getByRole("button", { name: "Use my location" }));
      expect(screen.getByLabelText("Latitude")).toHaveValue("-31.9523");
      expect(screen.getByLabelText("Longitude")).toHaveValue("115.8613");
      vi.unstubAllGlobals();
    });

    it("explains a blocked location request", async () => {
      const user = userEvent.setup();
      const denied = { code: 1, PERMISSION_DENIED: 1 };
      const getCurrentPosition = vi.fn((_ok: unknown, fail: (e: unknown) => void) => fail(denied));
      vi.stubGlobal("navigator", { ...navigator, geolocation: { getCurrentPosition } });
      renderTab();
      await editable();
      await user.click(screen.getByRole("button", { name: "Use my location" }));
      expect(await screen.findByText(/Location access was blocked/)).toBeInTheDocument();
      vi.unstubAllGlobals();
    });

    it("is read-only for a viewer", async () => {
      vi.mocked(apiFetch).mockImplementation(async (path: string) => {
        if (path === "/api/v1/users/me") return { id: 99, email: "v@b.c" } as never;
        return [] as never;
      });
      renderTab();
      await waitFor(() => expect(screen.getByLabelText("Latitude")).toBeDisabled());
      expect(screen.queryByRole("button", { name: "Use my location" })).not.toBeInTheDocument();
    });
  });
});
