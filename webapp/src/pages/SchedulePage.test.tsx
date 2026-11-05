import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, Link, MemoryRouter, Route, RouterProvider, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "../auth/AuthContext";
import { ProtectedRoute } from "../auth/ProtectedRoute";
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
  /** A POST waits for this before it answers. */
  createGate?: Promise<unknown>;
  /** Schedules served for site 8, when a test switches sites. */
  schedules8?: ActorSchedule[];
}

let current: Data = {};

function mockApi(data: Data) {
  current = data;
  vi.mocked(apiFetch).mockImplementation((path: string, init?: RequestInit) => {
    const { instances = [], schedules = [], userId = 1, createError, createGate, schedules8 = [] } = current;
    if (path.endsWith("/api/v1/sites")) return Promise.resolve([site, { ...site, id: 8 }]);
    if (path.endsWith("/users/me")) return Promise.resolve({ id: userId });
    if (init?.method === "POST" && createGate) return createGate.then(() => Promise.reject(createError));
    if (init?.method === "POST" && createError) return Promise.reject(createError);
    if (init?.method) return Promise.resolve(instance());
    if (path.endsWith("/instances")) return Promise.resolve(instances);
    if (path.endsWith("/schedules")) return Promise.resolve(path.includes("/sites/8/") ? schedules8 : schedules);
    return Promise.resolve([]);
  });
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sites/7/schedule"]}>
        <Link to="/sites/8/schedule">Other site</Link>
        <Routes>
          <Route path="/sites/:siteId/schedule" element={<SchedulePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...result, queryClient };
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

  it("resets the selection when switching sites", async () => {
    mockApi({ instances: [instance()], schedules: [schedule(1, "Evening peak"), schedule(2, "Morning")], schedules8: [schedule(5, "Other site job")] });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Morning/ }));
    await userEvent.click(screen.getByRole("button", { name: "New schedule" }));
    await userEvent.click(screen.getByRole("link", { name: "Other site" }));
    const row = await screen.findByRole("button", { name: /Other site job/ });
    expect(row).toHaveAttribute("aria-current", "true");
    expect(screen.queryByRole("heading", { name: "Morning" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Other site job" })).toBeInTheDocument();
  });

  it("falls back to the first schedule when the picked one disappears", async () => {
    mockApi({ instances: [instance()], schedules: [schedule(1, "Evening peak"), schedule(2, "Morning")] });
    const { queryClient } = renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Morning/ }));
    mockApi({ instances: [instance()], schedules: [schedule(1, "Evening peak")] });
    await queryClient.invalidateQueries();
    expect(await screen.findByRole("heading", { name: "Evening peak" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "New schedule" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Evening peak/ })).toHaveAttribute("aria-current", "true");
  });

  it("shows no detail when the picked schedule disappears and none are left", async () => {
    mockApi({ instances: [instance()], schedules: [schedule(1, "Evening peak")] });
    const { queryClient } = renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Evening peak/ }));
    mockApi({ instances: [instance()], schedules: [] });
    await queryClient.invalidateQueries();
    expect(await screen.findByText("No schedules yet.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "New schedule" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Evening peak" })).not.toBeInTheDocument();
  });

  it("keeps the new schedule view across a refetch", async () => {
    mockApi({ instances: [instance()], schedules: [schedule(1, "Evening peak")] });
    const { queryClient } = renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "New schedule" }));
    await queryClient.invalidateQueries();
    await waitFor(() => expect(vi.mocked(apiFetch).mock.calls.length).toBeGreaterThan(6));
    expect(screen.getByRole("heading", { name: "New schedule", level: 2 })).toBeInTheDocument();
  });

  it("shows the sign-in panel alone while there are no schedules", async () => {
    mockApi({ instances: [instance({ auth_state: code, capabilities: null })] });
    renderPage();
    await screen.findByText("ABCD-1234");
    expect(screen.queryByRole("heading", { name: "Schedules" })).not.toBeInTheDocument();
  });

  it("shows the needs-sign-in notice alone while there are no schedules", async () => {
    mockApi({ instances: [instance({ auth_state: { needs_sign_in: true } })] });
    renderPage();
    await screen.findByText(/needs to sign in again/);
    expect(screen.queryByRole("heading", { name: "Schedules" })).not.toBeInTheDocument();
  });

  it("shows the waiting line alone while there are no schedules", async () => {
    mockApi({ instances: [instance({ capabilities: null })] });
    renderPage();
    await screen.findByText(/Waiting for the actor/);
    expect(screen.queryByRole("heading", { name: "Schedules" })).not.toBeInTheDocument();
  });

  it("shows a viewer the signing-in line alone while there are no schedules", async () => {
    mockApi({ instances: [instance({ auth_state: { signing_in: true }, capabilities: null })], userId: 2 });
    renderPage();
    expect(await screen.findByText("The owner is signing the air conditioner in to Actron.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open the Actron sign-in page" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Schedules" })).not.toBeInTheDocument();
  });

  it("shows a viewer the signing-in line above existing schedules", async () => {
    mockApi({ instances: [instance({ auth_state: { signing_in: true } })], schedules: [schedule(1, "Evening peak")], userId: 2 });
    renderPage();
    expect(await screen.findByText("The owner is signing the air conditioner in to Actron.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Schedules" })).toBeInTheDocument();
  });

  it("keeps existing schedules visible below a sign-in notice", async () => {
    mockApi({ instances: [instance({ auth_state: { needs_sign_in: true } })], schedules: [schedule(1, "Evening peak")] });
    renderPage();
    expect(await screen.findByText(/needs to sign in again/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Schedules" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Evening peak/ })).toBeInTheDocument();
  });
});

const guardCaps: Capabilities = { version: 1, actions: [{ id: "turn_off", label: "Turn off", restorable: false, params: [] }], readings: [] };
const twoSchedules = { instances: [instance({ capabilities: guardCaps })], schedules: [schedule(1, "Evening peak"), schedule(2, "Morning")] };

/** True when the page asked the browser to confirm closing the tab. */
function leaveIsQuestioned(): boolean {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
}

async function editName(text: string) {
  const name = await screen.findByLabelText("Name");
  await userEvent.clear(name);
  await userEvent.type(name, text);
  return name;
}

function renderWithDataRouter() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (
    <>
      <Link to="/help">Help</Link>
      <Routes>
        <Route path="/sites/:siteId/schedule" element={<SchedulePage />} />
        <Route path="/help" element={<p>Help page</p>} />
      </Routes>
    </>
  );
  const router = createMemoryRouter([{ path: "*", element: tree }], { initialEntries: ["/help", "/sites/7/schedule"], initialIndex: 1 });
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>);
  return { router, queryClient };
}

describe("SchedulePage unsaved edits", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("questions closing the tab only while the form differs from the saved schedule", async () => {
    mockApi(twoSchedules);
    renderPage();
    expect(await screen.findByRole("heading", { name: "Evening peak" })).toBeInTheDocument();
    expect(leaveIsQuestioned()).toBe(false);
    await editName("Late peak");
    expect(leaveIsQuestioned()).toBe(true);
    await editName(" Evening peak ");
    expect(leaveIsQuestioned()).toBe(false);
  });

  it("switches schedules without asking while nothing changed", async () => {
    mockApi(twoSchedules);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Morning/ }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Morning" })).toBeInTheDocument();
  });

  it("asks before switching schedules, and Keep editing leaves the form as it was", async () => {
    mockApi(twoSchedules);
    renderPage();
    await editName("Late peak");
    const morning = screen.getByRole("button", { name: /Morning/ });
    await userEvent.click(morning);
    expect(screen.getByRole("alertdialog", { name: "Discard unsaved changes?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Late peak");
    expect(screen.getByRole("button", { name: /Evening peak/ })).toHaveAttribute("aria-current", "true");
    expect(morning).toHaveFocus();
  });

  it("switches schedules after Discard changes", async () => {
    mockApi(twoSchedules);
    renderPage();
    await editName("Late peak");
    await userEvent.click(screen.getByRole("button", { name: /Morning/ }));
    await userEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(screen.getByRole("heading", { name: "Morning" })).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Morning");
    expect(leaveIsQuestioned()).toBe(false);
  });

  it("asks before opening a new schedule", async () => {
    mockApi(twoSchedules);
    renderPage();
    await editName("Late peak");
    await userEvent.click(screen.getByRole("button", { name: "New schedule" }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByLabelText("Name")).toHaveValue("Late peak");
    await userEvent.click(screen.getByRole("button", { name: "New schedule" }));
    await userEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(screen.getByRole("heading", { name: "New schedule", level: 2 })).toBeInTheDocument();
  });

  it("stops asking once the edits are saved", async () => {
    mockApi(twoSchedules);
    renderPage();
    await editName("Late peak");
    await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(calls("PATCH")).toHaveLength(1));
    await waitFor(() => expect(leaveIsQuestioned()).toBe(false));
    await userEvent.click(screen.getByRole("button", { name: /Morning/ }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Morning" })).toBeInTheDocument();
  });

  it("does not ask when a new schedule is created", async () => {
    mockApi(twoSchedules);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "New schedule" }));
    expect(leaveIsQuestioned()).toBe(false);
    await editName("Night");
    await userEvent.type(screen.getByLabelText(/^Time/), "22:00");
    expect(leaveIsQuestioned()).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "New schedule", level: 2 })).not.toBeInTheDocument());
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(leaveIsQuestioned()).toBe(false);
  });

  it("never asks a viewer", async () => {
    mockApi({ ...twoSchedules, userId: 2 });
    renderPage();
    await waitFor(() => expect(screen.getByText(/Only the owner/)).toBeInTheDocument());
    expect(leaveIsQuestioned()).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: /Morning/ }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("locks the form while a new schedule is saving, and unlocks it with the edits kept if the save fails", async () => {
    let fail = () => {};
    const createGate = new Promise<void>((resolve) => { fail = resolve; });
    mockApi({ ...twoSchedules, createGate, createError: new ApiError(500, "server_error", "Try again.", null) });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "New schedule" }));
    await editName("Night");
    await userEvent.type(screen.getByLabelText(/^Time/), "22:00");
    await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    const name = screen.getByLabelText("Name");
    expect(name).toBeDisabled();
    expect(screen.getByRole("switch", { name: "Enabled" })).toBeDisabled();
    await userEvent.type(name, " shift");
    expect(name).toHaveValue("Night");
    await act(async () => fail());
    expect(await screen.findByText("Try again.")).toBeInTheDocument();
    expect(name).toBeEnabled();
    expect(name).toHaveValue("Night");
    expect(screen.getByLabelText(/^Time/)).toHaveValue("22:00");
    expect(leaveIsQuestioned()).toBe(true);
  });

  it("does not ask when the edited schedule is deleted, and is clean afterwards", async () => {
    mockApi(twoSchedules);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Morning/ }));
    await editName("Late morning");
    await userEvent.click(screen.getByRole("button", { name: "Delete schedule" }));
    const confirm = screen.getByRole("alertdialog", { name: "Delete this schedule?" });
    await userEvent.click(within(confirm).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls("DELETE")).toHaveLength(1));
    expect(await screen.findByRole("heading", { name: "Evening peak" })).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog", { name: "Discard unsaved changes?" })).not.toBeInTheDocument();
    expect(leaveIsQuestioned()).toBe(false);
  });

  it("closes the dialog when the edited schedule disappears underneath it", async () => {
    mockApi(twoSchedules);
    const { queryClient } = renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Morning/ }));
    await editName("Late morning");
    await userEvent.click(screen.getByRole("button", { name: /Evening peak/ }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    mockApi({ ...twoSchedules, schedules: [schedule(1, "Evening peak")] });
    await act(() => queryClient.invalidateQueries());
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Evening peak" })).toBeInTheDocument();
    await editName("Late peak");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("keeps the dialog and the edits while the schedules refetch", async () => {
    mockApi(twoSchedules);
    const { queryClient } = renderPage();
    await editName("Late peak");
    await userEvent.click(screen.getByRole("button", { name: /Morning/ }));
    await act(() => queryClient.invalidateQueries());
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Late peak");
  });
});

describe("SchedulePage leaving with unsaved edits", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("follows a link at once while nothing changed", async () => {
    mockApi(twoSchedules);
    renderWithDataRouter();
    await screen.findByRole("heading", { name: "Evening peak" });
    await userEvent.click(screen.getByRole("link", { name: "Help" }));
    expect(screen.getByText("Help page")).toBeInTheDocument();
  });

  it("holds a link until Discard changes", async () => {
    mockApi(twoSchedules);
    const { router } = renderWithDataRouter();
    await editName("Late peak");
    await userEvent.click(screen.getByRole("link", { name: "Help" }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(router.state.location.pathname).toBe("/sites/7/schedule");
    expect(screen.getByLabelText("Name")).toHaveValue("Late peak");
    await userEvent.click(screen.getByRole("link", { name: "Help" }));
    await userEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(await screen.findByText("Help page")).toBeInTheDocument();
    expect(leaveIsQuestioned()).toBe(false);
  });

  it("holds browser back and leaves the URL and the form as they were", async () => {
    mockApi(twoSchedules);
    const { router } = renderWithDataRouter();
    await editName("Late peak");
    await act(() => router.navigate(-1));
    expect(router.state.location.pathname).toBe("/sites/7/schedule");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Late peak");
  });

  it("shows one dialog when a second leave is tried while it is open", async () => {
    mockApi(twoSchedules);
    const { router } = renderWithDataRouter();
    await editName("Late peak");
    await userEvent.click(screen.getByRole("button", { name: /Morning/ }));
    await act(() => router.navigate(-1));
    expect(screen.getAllByRole("alertdialog")).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(await screen.findByText("Help page")).toBeInTheDocument();
  });
});

describe("SchedulePage signing out with unsaved edits", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    localStorage.setItem("access_token", "token");
  });

  function LogOff() {
    const { logout } = useAuth();
    return <button type="button" onClick={logout}>Log off</button>;
  }

  it("signs out without asking", async () => {
    mockApi(twoSchedules);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const tree = (
      <AuthProvider>
        <LogOff />
        <Routes>
          <Route path="/login" element={<p>Login page</p>} />
          <Route element={<ProtectedRoute />}>
            <Route path="/sites/:siteId/schedule" element={<SchedulePage />} />
          </Route>
        </Routes>
      </AuthProvider>
    );
    const router = createMemoryRouter([{ path: "*", element: tree }], { initialEntries: ["/sites/7/schedule"] });
    render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>);
    await editName("Late peak");
    await userEvent.click(screen.getByRole("button", { name: "Log off" }));
    expect(await screen.findByText("Login page")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(leaveIsQuestioned()).toBe(false);
  });
});
