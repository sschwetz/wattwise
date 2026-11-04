import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "../api/client";
import type { ActorInstance, ActorSchedule, AuthState, Capabilities } from "../actors/types";
import { SchedulePage } from "./SchedulePage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const site = { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true };
const caps: Capabilities = { version: 1, actions: [], readings: [] };

function instance(overrides: Partial<ActorInstance> = {}): ActorInstance {
  return {
    id: 3, site_id: 7, provider: "actron_neo", name: "Lounge AC", enabled: true,
    capabilities: caps, capabilities_updated_at: null, latest_readings: null,
    last_seen_at: null, auth_state: null, ...overrides,
  };
}

function schedule(id: number, name: string): ActorSchedule {
  return {
    id, instance_id: 3, name, enabled: id === 1,
    trigger: { type: "time", time: "10:00", days_of_week: [1] },
    conditions: [], action: { id: "turn_off", params: {} },
    restore: { enabled: false, when: "window_end", minutes: null, time: null, only_if: [], early_if: [] },
    retry: { max_attempts: 3, interval_seconds: 60, give_up_minutes: 30 },
    notify_email: false,
  };
}

interface Data {
  instances?: ActorInstance[];
  schedules?: ActorSchedule[];
  userId?: number;
  createError?: Error;
}

function mockApi({ instances = [], schedules = [], userId = 1, createError }: Data) {
  vi.mocked(apiFetch).mockImplementation((path: string, init?: RequestInit) => {
    if (path.endsWith("/api/v1/sites")) return Promise.resolve([site]);
    if (path.endsWith("/users/me")) return Promise.resolve({ id: userId });
    if (init?.method === "POST" && createError) return Promise.reject(createError);
    if (init?.method) return Promise.resolve(instance());
    if (path.endsWith("/instances")) return Promise.resolve(instances);
    if (path.endsWith("/schedules")) return Promise.resolve(schedules);
    return Promise.resolve([]);
  });
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sites/7/schedule"]}>
        <Routes>
          <Route path="/sites/:siteId/schedule" element={<SchedulePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const code: AuthState = { user_code: "ABCD-1234", verification_uri: "https://example.test/device", expires_at: "2026-11-05T03:00:00Z" };

function calls(method: string) {
  return vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === method);
}

describe("SchedulePage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows empty state and add form with no instances", async () => {
    mockApi({});
    renderPage();
    expect(await screen.findByRole("button", { name: "Add air conditioner" })).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
  });

  it("posts the provider and name when adding an instance", async () => {
    mockApi({});
    renderPage();
    await userEvent.type(await screen.findByLabelText("Name"), "Lounge AC");
    await userEvent.click(screen.getByRole("button", { name: "Add air conditioner" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(JSON.parse(calls("POST")[0][1]?.body as string)).toEqual({ provider: "actron_neo", name: "Lounge AC" });
  });

  it("shows the server message when create fails", async () => {
    mockApi({ createError: new ApiError(409, "conflict", "Only one device per site.", null) });
    renderPage();
    await userEvent.type(await screen.findByLabelText("Name"), "Lounge AC");
    await userEvent.click(screen.getByRole("button", { name: "Add air conditioner" }));
    expect(await screen.findByText("Only one device per site.")).toBeInTheDocument();
  });

  it("shows the sign-in code and link while waiting", async () => {
    mockApi({ instances: [instance({ auth_state: code, capabilities: null })] });
    renderPage();
    expect(await screen.findByText("ABCD-1234")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Open the Actron sign-in page" });
    expect(link).toHaveAttribute("href", "https://example.test/device");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("lets the owner start a new sign-in and hides the button from a viewer", async () => {
    mockApi({ instances: [instance({ auth_state: { needs_sign_in: true } })] });
    const { unmount } = renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Sign in again" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(calls("POST")[0][0]).toBe("/api/v1/sites/7/actors/instances/3/sign-in/reset");
    unmount();
    mockApi({ instances: [instance({ auth_state: { needs_sign_in: true } })], userId: 2 });
    renderPage();
    expect(await screen.findByText(/needs to sign in again/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/Only the owner/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Sign in again" })).not.toBeInTheDocument();
  });

  it("says the actor has not reported capabilities yet", async () => {
    mockApi({ instances: [instance({ capabilities: null })] });
    renderPage();
    expect(await screen.findByText("Waiting for the actor to report what this device can do.")).toBeInTheDocument();
  });

  it("offers the owner a button to enable a disabled device", async () => {
    mockApi({ instances: [instance({ enabled: false })] });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(calls("PATCH")).toHaveLength(1));
    expect(JSON.parse(calls("PATCH")[0][1]?.body as string)).toEqual({ enabled: true });
  });

  it("selects the first schedule and switches on click", async () => {
    mockApi({ instances: [instance()], schedules: [schedule(1, "Evening peak"), schedule(2, "Morning")] });
    renderPage();
    const first = await screen.findByRole("button", { name: /Evening peak/ });
    await waitFor(() => expect(first).toHaveAttribute("aria-current", "true"));
    await userEvent.click(screen.getByRole("button", { name: /Morning/ }));
    expect(screen.getByRole("button", { name: /Morning/ })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("heading", { name: "Morning" })).toBeInTheDocument();
  });

  it("viewer sees schedules without edit buttons", async () => {
    mockApi({ instances: [instance()], schedules: [schedule(1, "Evening peak")], userId: 2 });
    renderPage();
    expect(await screen.findByRole("button", { name: /Evening peak/ })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/Only the owner/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "New schedule" })).not.toBeInTheDocument();
  });

  it("opens the new schedule view for the owner", async () => {
    mockApi({ instances: [instance()], schedules: [schedule(1, "Evening peak")] });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "New schedule" }));
    expect(screen.getByRole("heading", { name: "New schedule" , level: 2})).toBeInTheDocument();
  });
});
